# scenarios/base_scenario.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Type, Optional, List
import json # Для _start_next_agent -> first_input_config

from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram import Bot
from aiogram.enums import ChatAction

from agents.base_agent import BaseAgent
from datetime import datetime # Для примера в _continue_with_active_agent

import logging

logger = logging.getLogger(__name__)

# --- Общие ключи FSM для BaseScenario ---
# Суффиксы будут добавляться к self.id сценария для уникальности
SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX = "_internal_scenario_state" # e.g., RUNNING_AGENT, FINISHED
CURRENT_AGENT_INDEX_FSM_KEY_SUFFIX = "_current_agent_idx"
ACTIVE_AGENT_INTERNAL_STATE_FSM_KEY_SUFFIX = "_active_agent_internal_state" # Сессия активного агента
SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX = "_shared_scenario_data" # Для результатов агентов и др. данных сценария

# Внутренние состояния самого BaseScenario (хранятся в fsm_key_scenario_state)
SCENARIO_STATE_INITIALIZING = "SCENARIO_INITIALIZING" # Начальное состояние перед запуском первого агента
SCENARIO_STATE_RUNNING_AGENT = "SCENARIO_RUNNING_AGENT"
SCENARIO_STATE_FINISHED = "SCENARIO_FINISHED"
SCENARIO_STATE_ERROR = "SCENARIO_ERROR"


class BaseScenario(ABC):
    # --- Атрибуты, определяемые наследниками ---
    id: str # Уникальный ID сценария, например "complaint_scenario_final_orchestrator"
    friendly_name: str = "Base Scenario"
    description: str = "Base scenario description."

    # Упорядоченный список КЛЮЧЕЙ агентов (строк).
    # Ключи должны соответствовать тем, что возвращает _get_agents_config().
    AGENT_SEQUENCE: List[str] = []

    @abstractmethod
    def _get_agents_config(self) -> Dict[str, Dict[str, Any]]:
        """
        Возвращает конфигурацию для каждого агента, используемого в сценарии.
        Ключ - строковый ключ агента (из AGENT_SEQUENCE).
        Значение - словарь:
            {
                "class": Type[BaseAgent],    // Класс агента
                "llm_config": Optional[Dict], // Конфигурация LLM для этого агента (провайдер, модель, температура)
                "tools": Optional[List[BaseTool]], // Список экземпляров инструментов для этого агента
                "initial_context_keys": Optional[Dict[str, str]], // Маппинг {shared_data_key: agent_scenario_context_key}
                "first_input_config": Optional[Union[str, Dict]] // Как формировать первый ввод для агента
            }
        """
        return {}
    # --- Конец атрибутов, определяемых наследниками ---

    def __init__(
            self,
            state: FSMContext,
            bot_instance: Bot,
            user_info: Dict[str, Any], # Должен содержать 'id', 'login', 'chat_id'
            initial_message_text: Optional[str] = None # Первое сообщение, запустившее сценарий
    ):
        self.state: FSMContext = state
        self.bot: Bot = bot_instance
        self.user_info: Dict[str, Any] = user_info
        # Сохраняем initial_message_text, он будет помещен в shared_data
        self.initial_message_text: Optional[str] = initial_message_text

        self._agents_instances_cache: Dict[str, BaseAgent] = {}
        self.agents_config: Dict[str, Dict[str, Any]] = self._get_agents_config()

        if not hasattr(self, 'id') or not self.id:
            raise NotImplementedError("Каждый класс сценария должен определить атрибут 'id'.")

        # Формируем полные ключи FSM
        self.fsm_key_scenario_state = self._build_fsm_key(SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX)
        self.fsm_key_current_agent_idx = self._build_fsm_key(CURRENT_AGENT_INDEX_FSM_KEY_SUFFIX)
        self.fsm_key_active_agent_state = self._build_fsm_key(ACTIVE_AGENT_INTERNAL_STATE_FSM_KEY_SUFFIX)
        self.fsm_key_shared_data = self._build_fsm_key(SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX)

        logger.info(f"Scenario '{self.id}' initialized for user '{self.user_info.get('login')}'.")

    def _build_fsm_key(self, suffix: str) -> str:
        return f"scenario_{self.id}{suffix}"

    async def _get_fsm_scenario_data(self, key_suffix: str, default: Any = None) -> Any:
        """Получает данные сценария из FSM по его полному ключу."""
        full_key = self._build_fsm_key(key_suffix)
        data = await self.state.get_data()
        return data.get(full_key, default)

    async def _update_fsm_scenario_data(self, data_map_suffix_to_value: Dict[str, Any]):
        """Обновляет данные сценария в FSM. data_map_suffix_to_value: {суффикс: значение}."""
        updates = {self._build_fsm_key(suffix): value for suffix, value in data_map_suffix_to_value.items()}
        await self.state.update_data(updates)
        logger.debug(f"[{self.id}] FSM data updated with: {updates}")

    def _get_agent_instance(self, agent_key: str) -> BaseAgent:
        if agent_key not in self._agents_instances_cache:
            if agent_key not in self.agents_config:
                raise ValueError(f"Конфигурация для агента с ключом '{agent_key}' не найдена в сценарии '{self.id}'.")

            config = self.agents_config[agent_key]
            AgentClass = config.get("class")
            if not AgentClass or not issubclass(AgentClass, BaseAgent): # Проверка типа
                raise ValueError(f"Класс для агента '{agent_key}' не определен или не является наследником BaseAgent.")

            llm_conf = config.get("llm_config") # Может быть None
            tools_list = config.get("tools")   # Может быть None

            self._agents_instances_cache[agent_key] = AgentClass(
                llm_provider_config=llm_conf,
                agent_specific_tools=tools_list
            )
            logger.info(f"[{self.id}] Создан инстанс агента '{AgentClass.get_id()}' (ключ сценария: {agent_key}).")
        return self._agents_instances_cache[agent_key]

    async def handle_message(self, message: Message) -> None:
        user_input = message.text.strip()
        chat_id = self.user_info.get("chat_id")
        if not chat_id:
            logger.error(f"[{self.id}] Critical: chat_id is missing in user_info. Cannot proceed for user {self.user_info.get('login')}.");
            return

        await self.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        scenario_fsm_internal_state = await self._get_fsm_scenario_data(SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX)

        if scenario_fsm_internal_state is None or \
                scenario_fsm_internal_state == SCENARIO_STATE_FINISHED or \
                scenario_fsm_internal_state == SCENARIO_STATE_ERROR:
            # Это первый вызов для этого сценария (после выбора роутером) или перезапуск
            logger.info(f"[{self.id}] Первый запуск или перезапуск сценария для пользователя {self.user_info.get('login')}.")

            initial_shared_data = {"initial_complaint": self.initial_message_text or ""} # Ключ "initial_complaint" важен
            await self._update_fsm_scenario_data({
                SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX: initial_shared_data,
                CURRENT_AGENT_INDEX_FSM_KEY_SUFFIX: -1, # Сброс индекса для начала с первого агента
                SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX: SCENARIO_STATE_INITIALIZING # Устанавливаем начальное состояние
            })
            # Первый ввод для первого агента будет определен в _start_next_agent на основе его first_input_config
            await self._start_next_agent(chat_id, first_input_for_agent=None) # Передаем None, _start_next_agent разберется
            return

        if scenario_fsm_internal_state == SCENARIO_STATE_RUNNING_AGENT:
            current_agent_idx = await self._get_fsm_scenario_data(CURRENT_AGENT_INDEX_FSM_KEY_SUFFIX, default=-1)
            if 0 <= current_agent_idx < len(self.AGENT_SEQUENCE):
                agent_key = self.AGENT_SEQUENCE[current_agent_idx]
                await self._continue_with_active_agent(user_input, chat_id, agent_key)
            else:
                logger.error(f"[{self.id}] Некорректный current_agent_idx: {current_agent_idx} в состоянии RUNNING_AGENT.")
                await self._mark_as_finished_with_error(chat_id, "Внутренняя ошибка: нарушение последовательности агентов.")
        else:
            logger.warning(f"[{self.id}] Сообщение получено в неожиданном состоянии сценария: {scenario_fsm_internal_state}")


    async def _get_next_agent_key_in_sequence(self, current_agent_idx_val: int) -> Optional[str]:
        """Определяет ключ следующего агента в AGENT_SEQUENCE на основе текущего индекса."""
        if not self.AGENT_SEQUENCE:
            logger.warning(f"[{self.id}] AGENT_SEQUENCE пуст!")
            return None

        next_agent_idx_to_run = current_agent_idx_val + 1
        if 0 <= next_agent_idx_to_run < len(self.AGENT_SEQUENCE):
            return self.AGENT_SEQUENCE[next_agent_idx_to_run]
        return None # Все агенты в последовательности выполнены или индекс некорректен


    async def _start_next_agent(self, chat_id: int, previous_agent_result: Optional[Any] = None, first_input_for_agent: Optional[str] = None):
        """
        Запускает следующего агента в последовательности.
        Формирует first_input_for_agent на основе конфигурации агента, если он не передан явно.
        """
        current_agent_idx_val = await self._get_fsm_scenario_data(CURRENT_AGENT_INDEX_FSM_KEY_SUFFIX, default=-1)
        next_agent_key_to_run = await self._get_next_agent_key_in_sequence(current_agent_idx_val)

        if not next_agent_key_to_run:
            logger.info(f"[{self.id}] Все агенты в последовательности выполнены. Завершение сценария.")
            final_shared_data = await self._get_fsm_scenario_data(SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX, default={})
            logger.info(f"[{self.id}] Финальные данные сценария (shared_data): {final_shared_data}")
            await self._update_fsm_scenario_data({SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX: SCENARIO_STATE_FINISHED})
            await self._mark_as_finished(); return

        agent_instance = self._get_agent_instance(next_agent_key_to_run)
        shared_data = await self._get_fsm_scenario_data(SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX, default={})
        agent_cfg = self.agents_config.get(next_agent_key_to_run, {})

        initial_context_keys_config = agent_cfg.get("initial_context_keys", {})
        scenario_context_for_initial_state = {}
        if isinstance(initial_context_keys_config, dict):
            for shared_key, context_key_name in initial_context_keys_config.items():
                if shared_key in shared_data:
                    scenario_context_for_initial_state[context_key_name] = shared_data.get(shared_key)
                # Если ключ не найден, он просто не будет добавлен в контекст агента
        elif isinstance(initial_context_keys_config, list):
            scenario_context_for_initial_state = {k: shared_data.get(k) for k in initial_context_keys_config if k in shared_data}

        initial_agent_internal_state = agent_instance.get_initial_state(scenario_context_for_initial_state)

        await self._update_fsm_scenario_data({
            SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX: SCENARIO_STATE_RUNNING_AGENT,
            CURRENT_AGENT_INDEX_FSM_KEY_SUFFIX: self.AGENT_SEQUENCE.index(next_agent_key_to_run),
            ACTIVE_AGENT_INTERNAL_STATE_FSM_KEY_SUFFIX: initial_agent_internal_state
        })

        effective_first_input = "" # По умолчанию
        if first_input_for_agent is not None: # Если передан явно (например, из handle_message для самого первого агента)
            effective_first_input = first_input_for_agent
        else:
            first_input_cfg = agent_cfg.get("first_input_config")
            if first_input_cfg:
                if isinstance(first_input_cfg, str):
                    if first_input_cfg == "EMPTY_STRING": effective_first_input = ""
                    else: effective_first_input = shared_data.get(first_input_cfg, "")
                elif isinstance(first_input_cfg, dict):
                    source_type = first_input_cfg.get("source")
                    if source_type == "shared_data_key":
                        effective_first_input = shared_data.get(first_input_cfg.get("key"), "")
                    elif source_type == "previous_agent_result_json_wrapped":
                        if previous_agent_result is not None:
                            wrapper_key = first_input_cfg.get("wrapper_key", "data")
                            try: effective_first_input = json.dumps({wrapper_key: previous_agent_result}, ensure_ascii=False)
                            except TypeError as e:
                                logger.error(f"[{self.id}] Не удалось сериализовать previous_agent_result в JSON: {e}. Результат: {previous_agent_result}")
                                effective_first_input = json.dumps({wrapper_key: {"error": "serialization_failed"}}, ensure_ascii=False)
                        else: # previous_agent_result is None
                            effective_first_input = json.dumps({first_input_cfg.get("wrapper_key", "data"): None}, ensure_ascii=False)
                    elif source_type == "static_string":
                        effective_first_input = first_input_cfg.get("value", "")

        logger.info(f"[{self.id}] Запуск агента '{next_agent_key_to_run}' с первым вводом: '{str(effective_first_input)[:70]}...'")
        await self._continue_with_active_agent(effective_first_input, chat_id, next_agent_key_to_run)

    async def _continue_with_active_agent(self, user_input: str, chat_id: int, active_agent_key: str):
        """Продолжает работу с текущим активным агентом, передавая ему ввод пользователя."""
        agent_instance = self._get_agent_instance(active_agent_key)
        agent_internal_state = await self._get_fsm_scenario_data(ACTIVE_AGENT_INTERNAL_STATE_FSM_KEY_SUFFIX)

        if agent_internal_state is None:
            logger.error(f"[{self.id}] Состояние для активного агента '{active_agent_key}' не найдено! Попытка инициализации.")
            shared_data = await self._get_fsm_scenario_data(SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX, default={})
            agent_cfg = self.agents_config.get(active_agent_key, {})
            initial_context_keys_config = agent_cfg.get("initial_context_keys", {})
            scenario_context_for_initial_state = {}
            if isinstance(initial_context_keys_config, dict):
                for shared_key, context_key_name in initial_context_keys_config.items():
                    if shared_key in shared_data: scenario_context_for_initial_state[context_key_name] = shared_data.get(shared_key)
            elif isinstance(initial_context_keys_config, list):
                scenario_context_for_initial_state = {k: shared_data.get(k) for k in initial_context_keys_config if k in shared_data}
            agent_internal_state = agent_instance.get_initial_state(scenario_context_for_initial_state)
            if agent_internal_state is None:
                logger.critical(f"[{self.id}] Не удалось инициализировать состояние для агента {active_agent_key}!")
                await self._mark_as_finished_with_error(chat_id, "Критическая ошибка состояния агента."); return

        # Формируем scenario_context для метода process_user_input агента
        shared_data = await self._get_fsm_scenario_data(SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX, default={})
        agent_cfg = self.agents_config.get(active_agent_key, {})
        context_keys_config_for_process = agent_cfg.get("initial_context_keys", {})
        scenario_context_for_process = {}
        if isinstance(context_keys_config_for_process, dict):
            for shared_key, context_key_name in context_keys_config_for_process.items():
                if shared_key in shared_data: scenario_context_for_process[context_key_name] = shared_data.get(shared_key)
        elif isinstance(context_keys_config_for_process, list):
            scenario_context_for_process = {k: shared_data.get(k) for k in context_keys_config_for_process if k in shared_data}

        # Дополнительный специфичный контекст, если нужно (например, для DecisionMakerAgent)
        # Это можно также вынести в конфигурацию агента, если потребуется.
        # from agents.decision_maker_agent import DecisionMakerAgent # Избегаем импорта здесь
        if agent_instance.get_id() == "agent_decision_maker_v2": # Сравнение по ID агента
            scenario_context_for_process["current_date"] = datetime.now().strftime("%Y-%m-%d")

        agent_response = await agent_instance.process_user_input(
            user_input=user_input,
            current_agent_state=agent_internal_state,
            scenario_context=scenario_context_for_process
        )

        message_to_user = agent_response.get("message_to_user")
        status = agent_response.get("status")
        next_agent_internal_state = agent_response.get("next_agent_state")
        agent_result = agent_response.get("result")

        if message_to_user: await self.bot.send_message(chat_id, message_to_user)

        if next_agent_internal_state is not None:
            await self._update_fsm_scenario_data({ACTIVE_AGENT_INTERNAL_STATE_FSM_KEY_SUFFIX: next_agent_internal_state})

        if status == "completed":
            logger.info(f"[{self.id}] Агент '{active_agent_key}' завершил работу. Результат (часть): {str(agent_result)[:100]}...")
            if agent_result is not None:
                current_shared_data = await self._get_fsm_scenario_data(SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX, default={})
                current_shared_data[f"result_{active_agent_key}"] = agent_result
                await self._update_fsm_scenario_data({SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX: current_shared_data})

            await self._start_next_agent(chat_id, previous_agent_result=agent_result)
        elif status == "in_progress":
            logger.debug(f"[{self.id}] Агент '{active_agent_key}' продолжает работу. Ожидание следующего ввода.")
        elif status == "error":
            logger.error(f"[{self.id}] Агент '{active_agent_key}' вернул ошибку: {message_to_user}")
            await self._mark_as_finished_with_error(chat_id, message_to_user or "Ошибка в работе агента.")
        else:
            logger.error(f"[{self.id}] Неизвестный статус '{status}' от агента '{active_agent_key}'.")
            await self._mark_as_finished_with_error(chat_id, "Неизвестный статус от внутреннего компонента.")

    async def _mark_as_finished(self):
        """Устанавливает флаг, что сценарий логически завершен."""
        current_state = await self._get_fsm_scenario_data(self.fsm_key_scenario_state)
        # Устанавливаем FINISHED только если не была установлена ошибка, чтобы не затереть ее
        final_state_to_set = SCENARIO_STATE_ERROR if current_state == SCENARIO_STATE_ERROR else SCENARIO_STATE_FINISHED
        await self._update_fsm_scenario_data({SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX: final_state_to_set})
        logger.info(f"Scenario '{self.id}' for user '{self.user_info.get('login')}' marked as finished (state: {final_state_to_set}).")

    async def _mark_as_finished_with_error(self, chat_id: int, error_message: Optional[str]):
        """Завершает сценарий с ошибкой и уведомляет пользователя."""
        if chat_id and error_message:
            await self.bot.send_message(chat_id, f"Произошла ошибка: {error_message}. Обработка вашего запроса прервана.")
        await self._update_fsm_scenario_data({SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX: SCENARIO_STATE_ERROR})
        # Вызываем _mark_as_finished, чтобы он установил финальный статус (который останется ERROR)
        # и залогировал общее завершение.
        await self._mark_as_finished()

    async def is_finished(self) -> bool:
        """Проверяет, завершен ли сценарий (успешно или с ошибкой)."""
        scenario_state = await self._get_fsm_scenario_data(self.fsm_key_scenario_state)
        return scenario_state == SCENARIO_STATE_FINISHED or scenario_state == SCENARIO_STATE_ERROR

    async def clear_scenario_data(self):
        """Очищает ВСЕ данные этого сценария из FSM."""
        keys_to_clear = [
            self.fsm_key_scenario_state,
            self.fsm_key_current_agent_idx,
            self.fsm_key_active_agent_state,
            self.fsm_key_shared_data
        ]
        update_with_nones = {key: None for key in keys_to_clear}
        # Используем set_data с merge=False чтобы удалить ключи, а не просто установить в None
        # Но для MemoryStorage установка в None эквивалентна удалению при следующем get_data.
        # Для других хранилищ может быть иначе. Пока оставим update_data.
        current_data = await self.state.get_data()
        new_data = {k: v for k, v in current_data.items() if k not in keys_to_clear}
        await self.state.set_data(new_data) # Перезаписываем данные без ключей сценария

        logger.info(f"[{self.id}] Scenario FSM data (keys: {keys_to_clear}) removed for user {self.user_info.get('login')}.")

    # _transition_to_state больше не нужен в BaseScenario, так как состояния сценария
    # управляются через _start_next_agent и _mark_as_finished.
    # Если он нужен для каких-то специфичных начальных шагов в дочернем сценарии (до запуска агентов),
    # то его можно оставить или перенести в дочерний класс.
    # В нашей текущей модели он не используется.