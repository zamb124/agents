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
        return {
            "initial_incident_payload_for_dm": details_from_collector,
            "dm_dialog_history": [],
            "pending_confirmation_payload": None, # Это будет сам словарь incident_data
            "confirmation_requested": False
        }

    async def process_user_input(
            self, user_input: str, current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:

        initial_payload_from_state = current_agent_state.get("initial_incident_payload_for_dm")
        pending_confirmation_payload_state = current_agent_state.get("pending_confirmation_payload")
        dm_dialog_history = current_agent_state.get("dm_dialog_history", [])
        confirmation_requested = current_agent_state.get("confirmation_requested", False)

        current_date_str = datetime.now().strftime("%Y-%m-%d")

        input_for_lc_agent: str
        current_task_description_for_log: str

        # Системный промпт теперь статический относительно данных инцидента
        dm_system_prompt_text = get_dm_system_prompt(current_date=current_date_str)

        if not confirmation_requested: # Первый вызов DM
            incident_data_to_process = None
            try:
                # user_input на первом вызове - это JSON-строка {"incident_data": ...} от сценария
                parsed_input_from_scenario = json.loads(user_input)
                if "incident_data" in parsed_input_from_scenario and isinstance(parsed_input_from_scenario["incident_data"], dict):
                    incident_data_to_process = parsed_input_from_scenario["incident_data"]
                else:
                    incident_data_to_process = initial_payload_from_state
            except (json.JSONDecodeError, TypeError):
                incident_data_to_process = initial_payload_from_state

            if not isinstance(incident_data_to_process, dict):
                logger.error(f"[{self.agent_id}] Отсутствуют данные инцидента для первичного анализа.")
                return {"status":"error", "message_to_user":"Ошибка: нет данных для принятия решения.", "next_agent_state":current_agent_state}

            # Сохраняем сам словарь данных для возможного шага подтверждения
            current_agent_state["pending_confirmation_payload"] = incident_data_to_process

            incident_data_json_for_input = json.dumps(incident_data_to_process, ensure_ascii=False, indent=2) # indent для читаемости в input
            input_for_lc_agent = (
                f"ИСХОДНЫЕ ДАННЫЕ ОБ ИНЦИДЕНТЕ:\n```json\n{incident_data_json_for_input}\n```\n\n"
                f"ЗАДАЧА: Проанализируй этот инцидент согласно инструкциям в системном промпте и прими решение."
            )
            current_task_description_for_log = "Первичный анализ инцидента"

        else: # Второй вызов DM - обработка ответа на подтверждение
            if not pending_confirmation_payload_state: # Должен быть словарь incident_data
                logger.error(f"[{self.agent_id}] Отсутствует pending_confirmation_payload для обработки ответа.")
                return {"status":"error", "message_to_user":"Ошибка: потерян контекст инцидента.", "next_agent_state":current_agent_state}

            dm_dialog_history.append({"type": "human", "content": user_input}) # Ответ пользователя

            initial_incident_data_json_for_input = json.dumps(pending_confirmation_payload_state, ensure_ascii=False, indent=2)
            input_for_lc_agent = (
                f"ОТВЕТ ПОЛЬЗОВАТЕЛЯ НА ЗАПРОС ПОДТВЕРЖДЕНИЯ: '{user_input}'\n\n"
                f"ИСХОДНЫЕ ДАННЫЕ ИНЦИДЕНТА (initial_incident_payload), по которому запрашивалось подтверждение:\n```json\n{initial_incident_data_json_for_input}\n```\n\n"
                f"ЗАДАЧА: Обработай ответ пользователя согласно ШАГУ 5 инструкций в системном промпте."
            )
            current_task_description_for_log = f"Обработка ответа на подтверждение: '{user_input}'"

        prompt_for_lc_agent = ChatPromptTemplate.from_messages([
            ("system", dm_system_prompt_text),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent_runnable = create_openai_functions_agent(self.llm, self.tools, prompt_for_lc_agent)
        executor = AgentExecutor(agent=agent_runnable, tools=self.tools, verbose=True, handle_parsing_errors="Output parser failed to parse")

        logger.info(f"[{self.agent_id}] Invoking DM. Task: {current_task_description_for_log}. Input для LLM (начало): '{input_for_lc_agent[:200]}...'")
        # logger.debug(f"[{self.agent_id}] Системный промпт для DM:\n{dm_system_prompt_text}")

        try:
            response = await executor.ainvoke({"input": input_for_lc_agent})
            agent_final_output = response.get("output", "Не удалось принять/обработать решение.")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка DM executor: {e}", exc_info=True)
            dm_dialog_history.append({"type": "ai", "content": f"Ошибка executor: {e}"})
            # Важно сохранить состояние правильно при ошибке
            current_agent_state["dm_dialog_history"] = dm_dialog_history
            return {"status": "error", "message_to_user": "Ошибка при принятии решения.", "next_agent_state": current_agent_state}

        text_for_history_and_user = agent_final_output
        is_confirmation_step = False

        if CONFIRMATION_REQUEST_MARKER in agent_final_output:
            text_for_history_and_user = agent_final_output.split(CONFIRMATION_REQUEST_MARKER)[0].strip()
            is_confirmation_step = True

        dm_dialog_history.append({"type": "ai", "content": text_for_history_and_user})

        # Обновляем состояние агента
        # initial_incident_payload_for_dm не меняется в рамках одного агента DM
        # pending_confirmation_payload обновляется на первом шаге, если будет запрос подтверждения
        next_agent_state = {
            "initial_incident_payload_for_dm": initial_payload_from_state,
            "dm_dialog_history": dm_dialog_history,
            "pending_confirmation_payload": current_agent_state["pending_confirmation_payload"] if is_confirmation_step else None, # Сохраняем, если запросили подтверждение
            "confirmation_requested": is_confirmation_step
        }

        if is_confirmation_step:
            return {"status": "in_progress", "message_to_user": text_for_history_and_user, "next_agent_state": next_agent_state}
        else:
            return {"status": "completed", "message_to_user": text_for_history_and_user, "result": {"decision_outcome_message": text_for_history_and_user}, "next_agent_state": next_agent_state}