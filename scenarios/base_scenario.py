# scenarios/base_scenario.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Type, Optional, List

from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram import Bot
from aiogram.enums import ChatAction

from agents.base_agent import BaseAgent
from datetime import datetime # Для примера в _continue_with_active_agent

import logging

logger = logging.getLogger(__name__)

# --- Общие ключи FSM для BaseScenario ---
SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX = "_internal_scenario_state"
CURRENT_AGENT_INDEX_FSM_KEY_SUFFIX = "_current_agent_idx"
ACTIVE_AGENT_INTERNAL_STATE_FSM_KEY_SUFFIX = "_active_agent_internal_state"
SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX = "_shared_scenario_data"

# Внутренние состояния самого BaseScenario
SCENARIO_STATE_RUNNING_AGENT = "SCENARIO_RUNNING_AGENT"
SCENARIO_STATE_FINISHED = "SCENARIO_FINISHED"
SCENARIO_STATE_ERROR = "SCENARIO_ERROR" # Добавим состояние ошибки


class BaseScenario(ABC):
    # --- Атрибуты, определяемые наследниками ---
    id: str # Уникальный ID сценария, например "complaint_scenario"
    friendly_name: str = "Base Scenario"
    description: str = "Base scenario description."
    AGENT_SEQUENCE: List[str] = [] # Упорядоченный список ключей агентов

    @abstractmethod
    def _get_agents_config(self) -> Dict[str, Dict[str, Any]]:
        """
        Возвращает конфигурацию для каждого агента.
        Ключ - строковый ключ агента (из AGENT_SEQUENCE).
        Значение - словарь: {"class": AgentClass, "llm_config": {}, "tools": [], "initial_context_keys": {}}
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
        self.initial_message_text: Optional[str] = initial_message_text

        self._agents_instances_cache: Dict[str, BaseAgent] = {}
        self.agents_config: Dict[str, Dict[str, Any]] = self._get_agents_config()

        # Формируем полные ключи FSM
        self.fsm_key_scenario_state = self._build_fsm_key(SCENARIO_INTERNAL_STATE_FSM_KEY_SUFFIX)
        self.fsm_key_current_agent_idx = self._build_fsm_key(CURRENT_AGENT_INDEX_FSM_KEY_SUFFIX)
        self.fsm_key_active_agent_state = self._build_fsm_key(ACTIVE_AGENT_INTERNAL_STATE_FSM_KEY_SUFFIX)
        self.fsm_key_shared_data = self._build_fsm_key(SHARED_SCENARIO_DATA_FSM_KEY_SUFFIX)

        if not hasattr(self, 'id') or not self.id: # Проверка наличия ID
            raise NotImplementedError("Каждый класс сценария должен определить атрибут 'id'.")
        logger.info(f"Scenario '{self.id}' initialized for user '{self.user_info.get('login')}'.")

    def _build_fsm_key(self, suffix: str) -> str:
        return f"scenario_{self.id}{suffix}"

    async def _get_scenario_fsm_data(self, key: str, default: Any = None) -> Any:
        data = await self.state.get_data()
        return data.get(key, default)

    async def _update_scenario_fsm_data(self, updates: Dict[str, Any]):
        await self.state.update_data(updates)
        logger.debug(f"[{self.id}] FSM data updated: {updates}")

    def _get_agent_instance(self, agent_key: str) -> BaseAgent:
        if agent_key not in self._agents_instances_cache:
            if agent_key not in self.agents_config:
                raise ValueError(f"Конфигурация для агента с ключом '{agent_key}' не найдена в сценарии '{self.id}'.")

            config = self.agents_config[agent_key]
            AgentClass = config.get("class")
            if not AgentClass:
                raise ValueError(f"Класс для агента '{agent_key}' не определен в конфигурации.")

            llm_conf = config.get("llm_config")
            tools_list = config.get("tools")

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
            logger.error(f"[{self.id}] Critical: chat_id is missing in user_info. Cannot proceed."); return

        await self.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        scenario_fsm_internal_state = await self._get_scenario_fsm_data(self.fsm_key_scenario_state)

        if scenario_fsm_internal_state is None or \
                scenario_fsm_internal_state == SCENARIO_STATE_FINISHED or \
                scenario_fsm_internal_state == SCENARIO_STATE_ERROR:
            # Это первый вызов для этого сценария (после выбора роутером) или перезапуск
            logger.info(f"[{self.id}] Первый запуск или перезапуск сценария для пользователя {self.user_info.get('login')}.")
            # Инициализируем shared_data начальным сообщением (если есть)
            initial_shared_data = {"initial_complaint": self.initial_message_text or ""}
            await self._update_scenario_fsm_data({
                self.fsm_key_shared_data: initial_shared_data,
                self.fsm_key_current_agent_idx: -1 # Сброс индекса для начала с первого агента
            })
            # Первый ввод для первого агента - это initial_message_text
            await self._start_next_agent(chat_id, first_input_for_agent=initial_shared_data.get("initial_complaint"))
            return

        if scenario_fsm_internal_state == SCENARIO_STATE_RUNNING_AGENT:
            current_agent_idx = await self._get_scenario_fsm_data(self.fsm_key_current_agent_idx, default=-1)
            if 0 <= current_agent_idx < len(self.AGENT_SEQUENCE):
                agent_key = self.AGENT_SEQUENCE[current_agent_idx]
                await self._continue_with_active_agent(user_input, chat_id, agent_key)
            else:
                logger.error(f"[{self.id}] Некорректный current_agent_idx: {current_agent_idx} в состоянии RUNNING_AGENT.")
                await self._mark_as_finished_with_error(chat_id, "Внутренняя ошибка: нарушение последовательности агентов.")
        # SCENARIO_STATE_ERROR уже был обработан (приведет к is_finished -> True)

    async def _get_next_agent_key_in_sequence(self, last_completed_agent_key: Optional[str]) -> Optional[str]:
        """Определяет ключ следующего агента в AGENT_SEQUENCE."""
        if not self.AGENT_SEQUENCE: return None
        if last_completed_agent_key is None: # Если это запуск самого первого агента
            return self.AGENT_SEQUENCE[0]
        try:
            current_index = self.AGENT_SEQUENCE.index(last_completed_agent_key)
            if current_index + 1 < len(self.AGENT_SEQUENCE):
                return self.AGENT_SEQUENCE[current_index + 1]
            else: # Все агенты в последовательности выполнены
                return None
        except ValueError: # Если last_completed_agent_key не найден в AGENT_SEQUENCE
            logger.error(f"[{self.id}] Завершенный агент '{last_completed_agent_key}' не найден в AGENT_SEQUENCE: {self.AGENT_SEQUENCE}.")
            return None

    async def _start_next_agent(self, chat_id: int, previous_agent_result: Optional[Any] = None, first_input_for_agent: Optional[str] = None):
        """
        Запускает следующего агента в последовательности.
        Этот метод может быть переопределен в дочернем сценарии для специальной логики
        подготовки scenario_context или first_input_for_agent для конкретного следующего агента.
        """
        current_agent_idx_val = await self._get_scenario_fsm_data(self.fsm_key_current_agent_idx, default=-1)
        # Ключ предыдущего агента, если он был
        prev_agent_key_if_any = self.AGENT_SEQUENCE[current_agent_idx_val] if 0 <= current_agent_idx_val < len(self.AGENT_SEQUENCE) else None

        next_agent_key_to_run = await self._get_next_agent_key_in_sequence(prev_agent_key_if_any)

        if next_agent_key_to_run:
            agent_instance = self._get_agent_instance(next_agent_key_to_run)

            shared_data = await self._get_scenario_fsm_data(self.fsm_key_shared_data, default={})
            agent_cfg = self.agents_config.get(next_agent_key_to_run, {})

            initial_context_keys_config = agent_cfg.get("initial_context_keys", {})
            scenario_context_for_initial_state = {}
            if isinstance(initial_context_keys_config, dict): # Ожидаем словарь {shared_key: context_key_name}
                for shared_key, context_key_name in initial_context_keys_config.items():
                    if shared_key in shared_data:
                        scenario_context_for_initial_state[context_key_name] = shared_data.get(shared_key)
                    else:
                        logger.warning(f"[{self.id}] Ключ '{shared_key}' для initial_context агента '{next_agent_key_to_run}' не найден в shared_data.")
            elif isinstance(initial_context_keys_config, list): # Поддержка старого формата (список ключей)
                scenario_context_for_initial_state = {k: shared_data.get(k) for k in initial_context_keys_config if k in shared_data}


            initial_agent_internal_state = agent_instance.get_initial_state(scenario_context_for_initial_state)

            await self._update_scenario_fsm_data({
                self.fsm_key_scenario_state: SCENARIO_STATE_RUNNING_AGENT,
                self.fsm_key_current_agent_idx: self.AGENT_SEQUENCE.index(next_agent_key_to_run), # Сохраняем индекс нового агента
                self.fsm_key_active_agent_state: initial_agent_internal_state
            })

            # Определяем первый ввод для нового агента
            # Если first_input_for_agent не передан явно (например, из переопределенного метода в дочернем сценарии),
            # то по умолчанию он пустой. Агент должен сам инициировать диалог, если это необходимо.
            effective_first_input = first_input_for_agent if first_input_for_agent is not None else ""

            logger.info(f"[{self.id}] Запуск агента '{next_agent_key_to_run}' (индекс {self.AGENT_SEQUENCE.index(next_agent_key_to_run)}) с первым вводом: '{effective_first_input[:50]}'")

            await self._continue_with_active_agent(effective_first_input, chat_id, next_agent_key_to_run)
        else:
            logger.info(f"[{self.id}] Все агенты в последовательности выполнены. Завершение сценария.")
            final_shared_data = await self._get_scenario_fsm_data(self.fsm_key_shared_data, default={})
            logger.info(f"[{self.id}] Финальные данные сценария (shared_data): {final_shared_data}")
            await self._update_scenario_fsm_data({self.fsm_key_scenario_state: SCENARIO_STATE_FINISHED})
            await self._mark_as_finished() # Устанавливает флаг для main_bot.py

    async def _continue_with_active_agent(self, user_input: str, chat_id: int, active_agent_key: str):
        """Продолжает работу с текущим активным агентом, передавая ему ввод пользователя."""
        agent_instance = self._get_agent_instance(active_agent_key)
        agent_internal_state = await self._get_scenario_fsm_data(self.fsm_key_active_agent_state)

        if agent_internal_state is None: # Если состояние агента не найдено (не должно происходить)
            logger.error(f"[{self.id}] Состояние для активного агента '{active_agent_key}' не найдено в FSM! Попытка инициализации.")
            # Пытаемся восстановить контекст для get_initial_state
            shared_data = await self._get_scenario_fsm_data(self.fsm_key_shared_data, default={})
            agent_cfg = self.agents_config.get(active_agent_key, {})
            initial_context_keys_config = agent_cfg.get("initial_context_keys", {})
            scenario_context_for_initial_state = {}
            if isinstance(initial_context_keys_config, dict):
                for shared_key, context_key_name in initial_context_keys_config.items():
                    if shared_key in shared_data: scenario_context_for_initial_state[context_key_name] = shared_data.get(shared_key)
            elif isinstance(initial_context_keys_config, list):
                scenario_context_for_initial_state = {k: shared_data.get(k) for k in initial_context_keys_config if k in shared_data}
            agent_internal_state = agent_instance.get_initial_state(scenario_context_for_initial_state)
            if agent_internal_state is None: # Если и это не помогло
                logger.critical(f"[{self.id}] Не удалось даже инициализировать состояние для агента {active_agent_key}!")
                await self._mark_as_finished_with_error(chat_id, "Критическая ошибка состояния агента.")
                return


        # Формируем scenario_context для метода process_user_input агента
        shared_data = await self._get_scenario_fsm_data(self.fsm_key_shared_data, default={})
        agent_cfg = self.agents_config.get(active_agent_key, {})
        # initial_context_keys также используются для передачи контекста в process_user_input
        context_keys_config_for_process = agent_cfg.get("initial_context_keys", {})
        scenario_context_for_process = {}
        if isinstance(context_keys_config_for_process, dict):
            for shared_key, context_key_name in context_keys_config_for_process.items():
                if shared_key in shared_data: scenario_context_for_process[context_key_name] = shared_data.get(shared_key)
        elif isinstance(context_keys_config_for_process, list):
            scenario_context_for_process = {k: shared_data.get(k) for k in context_keys_config_for_process if k in shared_data}

        # Дополнительный специфичный контекст, если нужно
        # from agents.decision_maker_agent import DecisionMakerAgent # Избегаем импорта здесь
        if agent_instance.get_id() == "agent_decision_maker_v2": # Сравнение по ID
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
            await self._update_scenario_fsm_data({self.fsm_key_active_agent_state: next_agent_internal_state})

        if status == "completed":
            logger.info(f"[{self.id}] Агент '{active_agent_key}' завершил работу.")
            if agent_result is not None:
                current_shared_data = await self._get_scenario_fsm_data(self.fsm_key_shared_data, default={})
                current_shared_data[f"result_{active_agent_key}"] = agent_result # Сохраняем результат в shared_data
                await self._update_scenario_fsm_data({self.fsm_key_shared_data: current_shared_data})

            # Запускаем следующего агента в последовательности
            await self._start_next_agent(chat_id, previous_agent_result=agent_result)
        elif status == "in_progress":
            logger.debug(f"[{self.id}] Агент '{active_agent_key}' продолжает работу. Ожидание следующего ввода.")
            # Сценарий остается в состоянии SCENARIO_STATE_RUNNING_AGENT с тем же активным агентом
        elif status == "error":
            logger.error(f"[{self.id}] Агент '{active_agent_key}' вернул ошибку: {message_to_user}")
            await self._mark_as_finished_with_error(chat_id, message_to_user or "Ошибка в работе агента.")
        else:
            logger.error(f"[{self.id}] Неизвестный статус '{status}' от агента '{active_agent_key}'.")
            await self._mark_as_finished_with_error(chat_id, "Неизвестный статус от внутреннего компонента.")

    async def _mark_as_finished(self):
        """Устанавливает флаг, что сценарий логически завершен (успешно или с ошибкой)."""
        current_state = await self._get_scenario_fsm_data(self.fsm_key_scenario_state)
        if current_state != SCENARIO_STATE_ERROR: # Если не была уже установлена ошибка
            await self._update_scenario_fsm_data({self.fsm_key_scenario_state: SCENARIO_STATE_FINISHED})
        logger.info(f"Scenario '{self.id}' for user '{self.user_info.get('login')}' marked as finished (state: {await self._get_scenario_fsm_data(self.fsm_key_scenario_state)}).")
        # Очистка данных (clear_scenario_data) должна вызываться из main_bot.py после проверки is_finished(),
        # чтобы избежать очистки до того, как main_bot поймет, что сценарий завершен.

    async def _mark_as_finished_with_error(self, chat_id: int, error_message: str):
        """Завершает сценарий с ошибкой и уведомляет пользователя."""
        if chat_id: # Убедимся, что chat_id есть
            await self.bot.send_message(chat_id, f"Произошла ошибка: {error_message}. Обработка вашего запроса прервана.")
        await self._update_scenario_fsm_data({self.fsm_key_scenario_state: SCENARIO_STATE_ERROR})
        await self._mark_as_finished() # Установит SCENARIO_STATE_FINISHED, если не SCENARIO_STATE_ERROR

    async def is_finished(self) -> bool:
        """Проверяет, завершен ли сценарий (успешно или с ошибкой)."""
        scenario_state = await self._get_scenario_fsm_data(self.fsm_key_scenario_state)
        return scenario_state == SCENARIO_STATE_FINISHED or scenario_state == SCENARIO_STATE_ERROR

    async def clear_scenario_data(self):
        """Очищает ВСЕ данные этого сценария из FSM."""
        keys_to_clear = [
            self.fsm_key_scenario_state,
            self.fsm_key_current_agent_idx,
            self.fsm_key_active_agent_state,
            self.fsm_key_shared_data
        ]
        # Дополнительно, если дочерние сценарии определяют свои ключи, они должны их очистить
        # Но в этой модели все основные ключи управляются BaseScenario.

        update_with_nones = {key: None for key in keys_to_clear}
        await self.state.update_data(update_with_nones)
        logger.info(f"[{self.id}] Scenario FSM data (keys: {keys_to_clear}) nulled for user {self.user_info.get('login')}.")

    async def _transition_to_state(self, new_scenario_state: str, message_to_user: Optional[str] = None, chat_id: Optional[int] = None):
        """
        Вспомогательный метод для установки состояния FSM самого СЦЕНАРИЯ (не агента)
        и опциональной отправки сообщения. Используется для начальных этапов,
        которые не управляются агентами (если такие есть).
        В новой модели этот метод менее актуален, так как состояния сценария меняются в _start_next_agent.
        """
        effective_chat_id = chat_id or self.user_info.get("chat_id")
        await self._update_scenario_fsm_data({self.fsm_key_scenario_state: new_scenario_state})
        logger.info(f"[{self.id}] Scenario FSM state explicitly changed to: {new_scenario_state}")
        if message_to_user and effective_chat_id:
            await self.bot.send_message(effective_chat_id, message_to_user)