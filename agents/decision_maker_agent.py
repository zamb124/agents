# agents/decision_maker_agent.py
import logging
import json
from datetime import datetime
from typing import Dict, Any, List, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import AgentExecutor, create_openai_functions_agent

from .base_agent import BaseAgent
from tools.tool_definitions import take_action_tool, query_rag_tool, get_courier_shifts_tool
from .prompts.decision_maker_prompts import get_dm_system_prompt, CONFIRMATION_REQUEST_MARKER

logger = logging.getLogger(__name__)

class DecisionMakerAgent(BaseAgent):
    agent_id: str = "agent_decision_maker"

    def _get_default_tools(self) -> List[Any]:
        return [take_action_tool, query_rag_tool, get_courier_shifts_tool]

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        details_from_collector = None
        if scenario_context and isinstance(scenario_context.get("details_from_collector"), dict):
            details_from_collector = scenario_context.get("details_from_collector")
            logger.info(f"[{self.agent_id}] Инициализация с details_from_collector: {str(details_from_collector)[:100]}...")
        else:
            logger.warning(f"[{self.agent_id}] details_from_collector не предоставлен или некорректен при инициализации!")


        return {
            "initial_incident_payload_for_dm": details_from_collector,
            "dm_dialog_history": [],
            "pending_confirmation_payload": None,
            "confirmation_requested": False
        }

    async def process_user_input(
            self, user_input: str, current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:

        initial_payload_from_state = current_agent_state.get("initial_incident_payload_for_dm")
        pending_confirmation_payload = current_agent_state.get("pending_confirmation_payload")
        dm_dialog_history = current_agent_state.get("dm_dialog_history", [])
        confirmation_requested = current_agent_state.get("confirmation_requested", False)

        current_date_str = datetime.now().strftime("%Y-%m-%d")

        dm_system_prompt_text: str
        input_for_lc_agent: str
        current_task_description_for_log: str

        if not confirmation_requested: # Первый вызов DM
            incident_data_to_process = None
            try:
                # user_input на первом вызове - это JSON-строка {"incident_data": ...} от сценария
                parsed_input_from_scenario = json.loads(user_input)
                if "incident_data" in parsed_input_from_scenario and isinstance(parsed_input_from_scenario["incident_data"], dict):
                    incident_data_to_process = parsed_input_from_scenario["incident_data"]
                    logger.info(f"[{self.agent_id}] Получены incident_data из user_input (первый вызов).")
                else:
                    logger.warning(f"[{self.agent_id}] user_input (первый вызов) не содержит 'incident_data' или это не словарь. Используем initial_payload_from_state.")
                    incident_data_to_process = initial_payload_from_state
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"[{self.agent_id}] Ошибка парсинга user_input как JSON ({e}), используем initial_payload_from_state.")
                incident_data_to_process = initial_payload_from_state

            if not isinstance(incident_data_to_process, dict):
                logger.error(f"[{self.agent_id}] Отсутствуют данные инцидента для первичного анализа (incident_data_to_process is not a dict).")
                return {"status":"error", "message_to_user":"Ошибка: нет данных для принятия решения.", "next_agent_state":current_agent_state}

            # Сохраняем для возможного шага подтверждения
            pending_confirmation_payload = incident_data_to_process

            incident_data_json_str_for_prompt = json.dumps(incident_data_to_process, ensure_ascii=False, indent=2)
            dm_system_prompt_text = get_dm_system_prompt(
                current_date=current_date_str,
                incident_data_json_str=incident_data_json_str_for_prompt
            )

            # Формируем input для Langchain агента
            # Можно передать описание инцидента или ключевые детали для фокуса
            incident_desc_for_input = incident_data_to_process.get('incident_description', 'Детали в системном промпте.')
            input_for_lc_agent = (
                f"Проанализируй следующий инцидент и прими решение на основе инструкций. "
                f"Описание: \"{incident_desc_for_input}\". "
                f"Полные данные об инциденте предоставлены в системном промпте."
            )
            current_task_description_for_log = "Первичный анализ и обогащение данных"
            # На первом вызове DM, user_input от сценария - это JSON, его не нужно добавлять в dm_dialog_history как "human"

        else: # Второй (и последующие, если будут) вызов DM - обработка ответа на подтверждение
            if not pending_confirmation_payload:
                logger.error(f"[{self.agent_id}] Отсутствует pending_confirmation_payload для обработки ответа пользователя на подтверждение.")
                return {"status":"error", "message_to_user":"Ошибка: потерян контекст инцидента для подтверждения.", "next_agent_state":current_agent_state}

            # user_input здесь - это ответ пользователя "да" / "нет"
            dm_dialog_history.append({"type": "human", "content": user_input}) # Добавляем ответ пользователя в историю DM

            incident_data_json_str_for_prompt = json.dumps(pending_confirmation_payload, ensure_ascii=False, indent=2)
            dm_system_prompt_text = get_dm_system_prompt(
                current_date=current_date_str,
                incident_data_json_str=incident_data_json_str_for_prompt # Передаем исходные данные инцидента
            )

            input_for_lc_agent = (
                f"Пользователь (директор) ответил на предыдущий запрос подтверждения действий: '{user_input}'. "
                f"Обработай этот ответ согласно инструкциям в системном промпте (ШАГ 5). "
                f"Исходные данные инцидента также находятся в системном промпте."
            )
            current_task_description_for_log = f"Обработка ответа на подтверждение: '{user_input}'"

        # --- Общая часть для вызова LLM агента ---
        prompt_for_lc_agent = ChatPromptTemplate.from_messages([
            ("system", dm_system_prompt_text),
            # MessagesPlaceholder(variable_name="chat_history"), # Если DM будет вести свою историю через LLM
            ("human", "{input}"), # {input} будет input_for_lc_agent
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent_runnable = create_openai_functions_agent(self.llm, self.tools, prompt_for_lc_agent)
        executor = AgentExecutor(agent=agent_runnable, tools=self.tools, verbose=True, handle_parsing_errors="Output parser failed to parse")

        logger.info(f"[{self.agent_id}] Invoking DM. Task: {current_task_description_for_log}. Input для LLM (часть): '{input_for_lc_agent[:150]}...'")
        # logger.debug(f"[{self.agent_id}] Полный системный промпт для DM:\n{dm_system_prompt_text}")

        try:
            # Передаем input_for_lc_agent. Если нужна история для LLM, ее нужно добавить в invoke_payload
            # и в MessagesPlaceholder в prompt_for_lc_agent.
            # Пока DM не использует свою внутреннюю историю для LLM (кроме как для логгирования).
            response = await executor.ainvoke({"input": input_for_lc_agent})
            agent_final_output = response.get("output", "Не удалось принять/обработать решение.")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка DM executor: {e}", exc_info=True)
            # Добавляем ошибку в историю агента для отладки
            dm_dialog_history.append({"type": "ai", "content": f"Ошибка executor: {e}"})
            next_error_state = {**current_agent_state, "dm_dialog_history": dm_dialog_history}
            return {"status": "error", "message_to_user": "Ошибка при принятии решения.", "next_agent_state": next_error_state}

        text_for_history_and_user = agent_final_output
        is_confirmation_step = False

        if CONFIRMATION_REQUEST_MARKER in agent_final_output:
            text_for_history_and_user = agent_final_output.split(CONFIRMATION_REQUEST_MARKER)[0].strip()
            is_confirmation_step = True
            logger.info(f"[{self.agent_id}] DM запросил подтверждение: '{text_for_history_and_user}'")
        else:
            logger.info(f"[{self.agent_id}] DM принял финальное решение/действие: '{text_for_history_and_user}'")


        dm_dialog_history.append({"type": "ai", "content": text_for_history_and_user})

        next_agent_state = {
            "initial_incident_payload_for_dm": initial_payload_from_state, # Остается тем же
            "dm_dialog_history": dm_dialog_history,
            "pending_confirmation_payload": pending_confirmation_payload if is_confirmation_step else None,
            "confirmation_requested": is_confirmation_step
        }

        if is_confirmation_step:
            return {
                "status": "in_progress", # Ожидаем ответа пользователя
                "message_to_user": text_for_history_and_user,
                "next_agent_state": next_agent_state
            }
        else: # Финальное решение, не требующее подтверждения, или результат выполнения подтвержденного действия
            return {
                "status": "completed",
                "message_to_user": text_for_history_and_user,
                "result": {"decision_outcome_message": text_for_history_and_user, "raw_dm_output": agent_final_output},
                "next_agent_state": next_agent_state
            }