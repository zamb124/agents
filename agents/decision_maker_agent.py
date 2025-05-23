# agents/decision_maker_agent.py
import logging
import json
from datetime import datetime
from typing import Dict, Any, List, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder # Оставляем MessagesPlaceholder
from langchain.agents import AgentExecutor, create_openai_functions_agent

from .base_agent import BaseAgent
from tools.tool_definitions import take_action_tool, query_rag_tool, get_courier_shifts_tool # Добавили get_courier_shifts_tool
from .prompts.decision_maker_prompts import get_dm_system_prompt_v2, CONFIRMATION_REQUEST_MARKER # Используем V2

logger = logging.getLogger(__name__)

class DecisionMakerAgent(BaseAgent):
    agent_id: str = "agent_decision_maker_v2" # Обновим ID для ясности

    def _get_default_tools(self) -> List[Any]: # List[BaseTool]
        # Теперь DM сам получает смены и инструкции, если нужно
        return [take_action_tool, query_rag_tool, get_courier_shifts_tool]

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # scenario_context здесь будет содержать результат DetailCollector'а
        # под ключом, определенным в CourierComplaintScenario._get_agents_config()
        # (например, scenario_context["details_from_collector"])

        details_from_collector = None
        if scenario_context and isinstance(scenario_context.get("details_from_collector"), dict):
            details_from_collector = scenario_context["details_from_collector"]

        return {
            # initial_incident_payload будет сформирован на первом шаге process_user_input
            # и сохранен агентом (через scratchpad/memory Langchain) для второго шага.
            # Либо, если агент stateless между process_user_input, то сценарий должен передавать
            # initial_incident_payload на втором шаге.
            # Промпт V2 предполагает, что initial_incident_payload передается в input для второго шага.
            "dm_dialog_history": [], # Краткая история диалога с DM (запрос подтверждения -> ответ)
            "pending_confirmation_payload": None # Здесь будем хранить обогащенный payload, ожидающий подтверждения
        }

    async def process_user_input(
            self, user_input: str, current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:

        pending_confirmation_payload = current_agent_state.get("pending_confirmation_payload")
        dm_dialog_history = current_agent_state.get("dm_dialog_history", [])

        input_json_for_langchain_agent: str
        current_task_description_for_log: str

        # scenario_context для DM содержит 'details_from_collector' при первом вызове
        # и 'current_date'
        current_date_str = scenario_context.get("current_date", datetime.now().strftime("%Y-%m-%d"))


        if pending_confirmation_payload is None: # Первый вызов DM
            # user_input здесь - это JSON от DetailCollector (или пустая строка, если DC запускается первым)
            # Но по нашей логике, DC уже отработал, и его результат в scenario_context["details_from_collector"]
            details_from_collector = scenario_context.get("details_from_collector")
            if not isinstance(details_from_collector, dict):
                logger.error(f"[{self.agent_id}] 'details_from_collector' не найден или не словарь в scenario_context!")
                return {"status":"error", "message_to_user":"Ошибка: нет данных для принятия решения.", "next_agent_state":current_agent_state}

            # Формируем input для Структуры 1 промпта DM
            input_json_for_langchain_agent = json.dumps({"incident_data": details_from_collector}, ensure_ascii=False)
            current_task_description_for_log = "Первичный анализ и обогащение данных"
            # dm_dialog_history для LLM пока пуста
        else: # Второй вызов DM - пользователь ответил на запрос подтверждения
            # user_input здесь - это ответ пользователя "да" / "нет"
            payload_for_confirmation_step = {
                "initial_incident_payload": pending_confirmation_payload, # Обогащенный payload с предыдущего шага
                "user_confirm_reply": user_input
            }
            input_json_for_langchain_agent = json.dumps(payload_for_confirmation_step, ensure_ascii=False)
            current_task_description_for_log = f"Обработка ответа пользователя на подтверждение: '{user_input}'"
            dm_dialog_history.append({"type": "human", "content": user_input}) # Добавляем ответ юзера в историю DM

        dm_system_prompt_text = get_dm_system_prompt_v2() # Получаем промпт V2

        # Langchain агент будет использовать 'input' (наша JSON-строка)
        # и 'agent_scratchpad'. Он также может использовать 'chat_history', если мы ее передадим.
        # Промпт DM_SYSTEM_PROMPT_LANGCHAIN_V2 не имеет плейсхолдера для chat_history,
        # он ожидает всю информацию в 'input'.
        prompt_for_lc_agent = ChatPromptTemplate.from_messages([
            ("system", dm_system_prompt_text),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent_runnable = create_openai_functions_agent(self.llm, self.tools, prompt_for_lc_agent)
        executor = AgentExecutor(agent=agent_runnable, tools=self.tools, verbose=True, handle_parsing_errors="Output parser failed to parse")

        logger.info(f"[{self.agent_id}] Invoking DM. Task: {current_task_description_for_log}. Input JSON (part): '{input_json_for_langchain_agent[:200]}...'")

        try:
            response = await executor.ainvoke({"input": input_json_for_langchain_agent})
            agent_final_output = response.get("output", "Не удалось принять/обработать решение.")
            # intermediate_steps = response.get("intermediate_steps", []) # Могут быть полезны для извлечения обогащенных данных
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка DM executor: {e}", exc_info=True)
            return {"status": "error", "message_to_user": "Ошибка при принятии решения.", "next_agent_state": current_agent_state}

        # Обновляем состояние агента
        next_agent_state = {
            "dm_dialog_history": list(dm_dialog_history),
            "pending_confirmation_payload": None # Сбрасываем по умолчанию
        }
        # Ответ агента (вопрос или финальное сообщение) добавляется в историю
        next_agent_state["dm_dialog_history"].append({"type": "ai", "content": agent_final_output.split(CONFIRMATION_REQUEST_MARKER)[0].strip()})


        if CONFIRMATION_REQUEST_MARKER in agent_final_output:
            # Агент запросил подтверждение. Нужно сохранить обогащенный incident_data,
            # который агент должен был "запомнить" в своем scratchpad и использовать для формирования плана.
            # Это сложно извлечь из create_openai_functions_agent напрямую.
            # Промпт должен был бы инструктировать агента вернуть этот обогащенный payload
            # вместе с запросом на подтверждение, если бы мы хотели его явно сохранить.
            # Либо, мы передаем НЕОБОГАЩЕННЫЙ initial_payload на втором шаге, и агент СНОВА его обогащает перед выполнением.
            # Промпт V2 говорит: "Возьми ПОЛНЫЙ initial_incident_payload (который ты сохранил или который передан)".
            # Это означает, что если мы хотим избежать повторного обогащения, то initial_payload,
            # который мы передаем на втором шаге, должен быть уже обогащенным.
            # Значит, после первого вызова, если запрошено подтверждение, нам нужно как-то получить
            # обогащенный `incident_data` от агента.
            # Это можно сделать, если агент в своем `output` (где есть маркер) также возвращает этот payload.
            # Или мы можем попробовать извлечь его из `intermediate_steps`.

            # Упрощение: Предположим, что `initial_payload` (который был из DC) достаточно для второго шага,
            # и если DM обогащал его, он это сделал "в уме" для планирования, а для выполнения
            # ему снова передается исходный `initial_payload` и он снова его обогатит перед `take_action`.
            # Это неэффективно, но проще для начала.
            # Либо, сценарий должен передать `initial_payload` из `current_agent_state` в `pending_confirmation_payload`
            # если он не был изменен агентом.

            # Если агент сам обогатил данные и использовал их для плана, он должен их "запомнить"
            # для этапа выполнения. Langchain агенты с памятью могут это делать.
            # Для stateless вызова executor'а, мы должны передавать все данные каждый раз.
            # Промпт V2 просит агента "сохранить ПОЛНЫЙ incident_data ... в своем внутреннем состоянии (scratchpad или память агента)".
            # Если мы используем stateless executor, то это не сработает.
            # Значит, `initial_incident_payload` в Структуре 2 должен быть тем, что агент обогатил.
            # Это означает, что если агент запросил подтверждение, он должен в своем ответе
            # (помимо текста для юзера) вернуть и этот обогащенный payload.
            # Это усложняет парсинг ответа агента.

            # ВАРИАНТ ПРОЩЕ: Сценарий сохраняет `initial_payload` (необогащенный от DC)
            # и передает его DM на втором шаге. DM снова обогащает его перед `take_action`.
            next_agent_state["pending_confirmation_payload"] = initial_payload # Сохраняем то, что пришло от DC
            next_agent_state["confirmation_requested"] = True

            return {
                "status": "in_progress",
                "message_to_user": agent_final_output.replace(CONFIRMATION_REQUEST_MARKER, "").strip(),
                "next_agent_state": next_agent_state
            }
        else: # Финальный ответ от DM (действия выполнены или отменены)
            return {
                "status": "completed",
                "message_to_user": agent_final_output,
                "result": {"decision_outcome_message": agent_final_output},
                "next_agent_state": next_agent_state
            }