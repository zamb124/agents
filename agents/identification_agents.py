# agents/identification_agents.py
import logging
import json
import re
from typing import Dict, Any, List, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import AgentExecutor, create_openai_functions_agent

from .base_agent import BaseAgent
from tools.tool_definitions import find_warehouse_tool, search_courier_tool # search_courier_tool нужен
from .prompts.identification_prompts import (
    WAREHOUSE_ID_SYSTEM_PROMPT_FOR_EXECUTOR, # Старый промпт для склада, нужно заменить на GENERAL_WAREHOUSE_ID_SYSTEM_PROMPT
    COURIER_ID_AGENT_SYSTEM_MESSAGE, # Новый промпт для курьера
    format_id_dialog_history,
    JSON_WAREHOUSE_INFO_START_MARKER, JSON_WAREHOUSE_INFO_END_MARKER,
    JSON_COURIER_INFO_START_MARKER, JSON_COURIER_INFO_END_MARKER
)

logger = logging.getLogger(__name__)

# Используем более общий системный промпт для склада, как обсуждали
GENERAL_WAREHOUSE_ID_SYSTEM_PROMPT = """
Ты агент, задача которого - идентифицировать склад пользователя.
У тебя есть инструмент `find_warehouse_by_name_or_id`.
Проанализируй историю чата и текущий ввод пользователя.
Если пользователь указал склад, используй инструмент для поиска.
Если найдено: один - спроси подтверждение; несколько - попроси уточнить; не найдено - попроси ввести снова.
Если пользователь подтвердил склад, твой финальный ответ должен содержать JSON с информацией о складе, обернутый в маркеры [JSON_WAREHOUSE_INFO]...[/JSON_WAREHOUSE_INFO], и сообщение "Склад [название] подтвержден."
Если пользователь еще ничего не указал, спроси: "Пожалуйста, укажите название или ID вашего склада."
"""

class WarehouseIdentificationAgent(BaseAgent):
    agent_id: str = "agent_warehouse_identifier"

    def _get_default_tools(self) -> List[Any]: # List[BaseTool]
        return [find_warehouse_tool]

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        initial_input = ""
        if scenario_context: # scenario_context передается из BaseScenario._start_next_agent
            # initial_context_keys в _get_agents_config для этого агента определяет, что здесь будет
            # Например, {"initial_complaint_text": "initial_complaint"}
            initial_input = scenario_context.get("initial_complaint", "")
        return {
            "dialog_history": [],
            "initial_input_for_prompt": initial_input
        }

    async def process_user_input(
            self, user_input: str, current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        dialog_history = current_agent_state.get("dialog_history", [])
        initial_input_for_prompt = current_agent_state.get("initial_input_for_prompt", "")

        langchain_formatted_history = self._prepare_chat_history_for_llm(dialog_history)

        effective_input_for_executor = user_input
        # Если это первый содержательный вызов и initial_input_for_prompt есть
        if not dialog_history and initial_input_for_prompt and initial_input_for_prompt != user_input:
            effective_input_for_executor = (
                f"Контекст: Первоначальный запрос был: '{initial_input_for_prompt}'.\n"
                f"Текущий ответ пользователя: '{user_input}'"
            )
        elif not dialog_history and initial_input_for_prompt:
            effective_input_for_executor = initial_input_for_prompt

        prompt_template = ChatPromptTemplate.from_messages([
            ("system", GENERAL_WAREHOUSE_ID_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent_runnable = create_openai_functions_agent(self.llm, self.tools, prompt_template)
        executor = AgentExecutor(agent=agent_runnable, tools=self.tools, verbose=True, handle_parsing_errors="Output parser failed to parse")

        logger.info(f"[{self.agent_id}] Invoking. Effective Input: '{effective_input_for_executor}'. History for LLM len: {len(langchain_formatted_history)}")

        try:
            response = await executor.ainvoke({"input": effective_input_for_executor, "chat_history": langchain_formatted_history})
            agent_final_output = response.get("output", "Не удалось обработать запрос по складу.")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка executor: {e}", exc_info=True)
            return {"status": "error", "message_to_user": "Ошибка при поиске склада.", "next_agent_state": current_agent_state}

        next_agent_state = current_agent_state.copy()
        if user_input:
            next_agent_state["dialog_history"] = dialog_history + [{"type": "human", "content": user_input}]

        text_reply_to_user = agent_final_output
        confirmed_wh_info = None

        if JSON_WAREHOUSE_INFO_START_MARKER in agent_final_output and JSON_WAREHOUSE_INFO_END_MARKER in agent_final_output:
            try:
                text_reply_to_user = agent_final_output.split(JSON_WAREHOUSE_INFO_START_MARKER)[0].strip()
                json_str = agent_final_output.split(JSON_WAREHOUSE_INFO_START_MARKER)[1].split(JSON_WAREHOUSE_INFO_END_MARKER)[0]
                confirmed_wh_info = json.loads(json_str)
                if not text_reply_to_user and confirmed_wh_info:
                    text_reply_to_user = f"Склад {confirmed_wh_info.get('warehouse_name', '')} (ID: {confirmed_wh_info.get('warehouse_id','')}) подтвержден."
                logger.info(f"[{self.agent_id}] Склад идентифицирован агентом: {confirmed_wh_info}")
            except Exception as e_json:
                logger.error(f"[{self.agent_id}] Ошибка парсинга JSON от WarehouseID агента: {e_json}. Ответ: {agent_final_output}")
                text_reply_to_user = agent_final_output

        next_agent_state["dialog_history"].append({"type": "ai", "content": text_reply_to_user})

        if confirmed_wh_info and isinstance(confirmed_wh_info, dict) and "warehouse_id" in confirmed_wh_info :
            # next_agent_state["confirmed_warehouse_info"] = confirmed_wh_info # Это поле больше не нужно в состоянии агента
            return {"status": "completed", "message_to_user": text_reply_to_user,
                    "result": confirmed_wh_info, "next_agent_state": next_agent_state}

        return {"status": "in_progress", "message_to_user": text_reply_to_user, "next_agent_state": next_agent_state}


class CourierIdentificationAgent(BaseAgent):
    agent_id: str = "agent_courier_identifier"

    def _get_default_tools(self) -> List[Any]:
        return [search_courier_tool]

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        warehouse_info = {}
        if scenario_context and isinstance(scenario_context.get("warehouse_info"), dict):
            warehouse_info = scenario_context.get("warehouse_info")
            logger.info(f"[{self.agent_id}] Инициализация с контекстом склада: {warehouse_info.get('warehouse_name')}")
        else:
            logger.warning(f"[{self.agent_id}] warehouse_info не предоставлен в scenario_context при инициализации!")

        return {
            "dialog_history": [],
            "warehouse_context": warehouse_info # Сохраняем для использования в промпте
            # "suggested_courier_info": None, # Управляется логикой агента Langchain
            # "confirmed_courier_info": None  # Будет в result
        }

    async def process_user_input(
            self, user_input: str, current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None # warehouse_info может быть здесь, но лучше брать из состояния
    ) -> Dict[str, Any]:

        dialog_history = current_agent_state.get("dialog_history", [])
        warehouse_context = current_agent_state.get("warehouse_context", {})

        wh_name_for_prompt = warehouse_context.get("warehouse_name", "Неизвестный склад")
        wh_id_for_prompt = warehouse_context.get("warehouse_id", "UNKNOWN_WAREHOUSE_ID")

        if wh_id_for_prompt == "UNKNOWN_WAREHOUSE_ID":
            logger.error(f"[{self.agent_id}] Невозможно идентифицировать курьера без ID склада.")
            return {"status": "error", "message_to_user": "Ошибка: не определен склад для поиска курьера.", "next_agent_state": current_agent_state}

        # Формируем историю для LLM
        langchain_formatted_history = self._prepare_chat_history_for_llm(dialog_history)

        # Системный промпт для CourierIdentificationAgent
        # Он должен быть отформатирован с warehouse_name и warehouse_id
        # Но create_openai_functions_agent ожидает статический системный промпт.
        # Значит, информация о складе должна быть частью {input} для executor'а.

        # Общий системный промпт
        # COURIER_ID_AGENT_SYSTEM_MESSAGE из prompts файла

        # Формируем {input} для executor'а, включая контекст склада
        effective_input_for_executor = (
            f"Контекст: Ищем курьера на складе '{wh_name_for_prompt}' (ID: {wh_id_for_prompt}).\n"
            f"Ввод пользователя: '{user_input}'"
        )

        prompt_template = ChatPromptTemplate.from_messages([
            ("system", COURIER_ID_AGENT_SYSTEM_MESSAGE.format( # Форматируем здесь, если промпт содержит плейсхолдеры склада
                warehouse_name=wh_name_for_prompt,
                warehouse_id=wh_id_for_prompt,
                dialog_history_formatted=format_id_dialog_history(dialog_history), # Если промпт ожидает историю так
                user_current_reply=user_input # Если промпт ожидает текущий ответ так
            )
             ),
            # Если промпт более общий и ожидает историю и ввод через плейсхолдеры:
            # MessagesPlaceholder(variable_name="chat_history"),
            # ("human", "{input}"), # input = effective_input_for_executor
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        # Важно: COURIER_ID_AGENT_SYSTEM_MESSAGE должен быть написан так, чтобы он мог
        # либо сам форматироваться с warehouse_id/name, либо чтобы эта информация была в {input}.
        # Давайте предположим, что COURIER_ID_AGENT_SYSTEM_MESSAGE ожидает {warehouse_name} и {warehouse_id}
        # и мы их подставляем при создании prompt_template.
        # А {dialog_history_formatted} и {user_current_reply} тоже.
        # Тогда в executor.ainvoke input может быть пустым или просто user_input.

        # Переделаем: системный промпт статический, контекст в input
        COURIER_ID_STATIC_SYSTEM_PROMPT = """Твоя задача - точно определить курьера. Используй инструмент search_courier_by_id_or_name. ID склада и ФИО/ID курьера будут в input. Задавай уточняющие вопросы. Если курьер подтвержден, верни JSON с информацией о нем в маркерах [JSON_COURIER_INFO]...[/JSON_COURIER_INFO] и сообщение "Курьер [ФИО] подтвержден."."""

        prompt_template_courier = ChatPromptTemplate.from_messages([
            ("system", COURIER_ID_STATIC_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent_runnable = create_openai_functions_agent(self.llm, self.tools, prompt_template_courier)
        executor = AgentExecutor(agent=agent_runnable, tools=self.tools, verbose=True, handle_parsing_errors="Output parser failed to parse")

        logger.info(f"[{self.agent_id}] Invoking. Effective Input for executor: '{effective_input_for_executor}'. History for LLM len: {len(langchain_formatted_history)}")

        try:
            response = await executor.ainvoke({"input": effective_input_for_executor, "chat_history": langchain_formatted_history})
            agent_final_output = response.get("output", "Не удалось обработать запрос по курьеру.")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка executor: {e}", exc_info=True)
            return {"status": "error", "message_to_user": "Ошибка при поиске курьера.", "next_agent_state": current_agent_state}

        next_agent_state = current_agent_state.copy()
        if user_input:
            next_agent_state["dialog_history"] = dialog_history + [{"type": "human", "content": user_input}]

        text_reply_to_user = agent_final_output
        confirmed_cour_info = None

        if JSON_COURIER_INFO_START_MARKER in agent_final_output and JSON_COURIER_INFO_END_MARKER in agent_final_output:
            try:
                text_reply_to_user = agent_final_output.split(JSON_COURIER_INFO_START_MARKER)[0].strip()
                json_str = agent_final_output.split(JSON_COURIER_INFO_START_MARKER)[1].split(JSON_COURIER_INFO_END_MARKER)[0]
                confirmed_cour_info = json.loads(json_str)
                if not text_reply_to_user and confirmed_cour_info:
                    text_reply_to_user = f"Курьер {confirmed_cour_info.get('full_name', '')} (ID: {confirmed_cour_info.get('id','')}) подтвержден."
                logger.info(f"[{self.agent_id}] Курьер идентифицирован агентом: {confirmed_cour_info}")
            except Exception as e_json:
                logger.error(f"[{self.agent_id}] Ошибка парсинга JSON от CourierID агента: {e_json}. Ответ: {agent_final_output}")
                text_reply_to_user = agent_final_output

        next_agent_state["dialog_history"].append({"type": "ai", "content": text_reply_to_user})

        if confirmed_cour_info and isinstance(confirmed_cour_info, dict) and "id" in confirmed_cour_info:
            # next_agent_state["confirmed_courier_info"] = confirmed_cour_info # Не нужно в состоянии агента
            return {"status": "completed", "message_to_user": text_reply_to_user,
                    "result": confirmed_cour_info, "next_agent_state": next_agent_state}

        return {"status": "in_progress", "message_to_user": text_reply_to_user, "next_agent_state": next_agent_state}