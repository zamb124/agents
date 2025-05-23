# agents/identification_agents.py
import logging
import json
import re
from typing import Dict, Any, List, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import AgentExecutor, create_openai_functions_agent

from .base_agent import BaseAgent
from tools.tool_definitions import find_warehouse_tool, search_courier_tool
from .prompts.identification_prompts import (
    WAREHOUSE_ID_SYSTEM_PROMPT,
    COURIER_ID_SYSTEM_PROMPT,
    JSON_WAREHOUSE_INFO_START_MARKER, JSON_WAREHOUSE_INFO_END_MARKER,
    JSON_COURIER_INFO_START_MARKER, JSON_COURIER_INFO_END_MARKER
)

logger = logging.getLogger(__name__)

class WarehouseIdentificationAgent(BaseAgent):
    agent_id: str = "agent_warehouse_identifier" # Фиксированный ID

    def _get_default_tools(self) -> List[Any]: # List[BaseTool]
        return [find_warehouse_tool]

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # scenario_context может содержать "initial_complaint" или "user_input" от сценария
        initial_user_text = ""
        if scenario_context:
            initial_user_text = scenario_context.get("initial_complaint", scenario_context.get("user_input", ""))

        return {
            "dialog_history": [],
            "initial_user_text_context": initial_user_text # Для формирования первого input для executor
        }

    async def process_user_input(
            self, user_input: str, current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:

        dialog_history = current_agent_state.get("dialog_history", [])
        initial_user_text_context = current_agent_state.get("initial_user_text_context", "")

        langchain_formatted_history = self._prepare_chat_history_for_llm(dialog_history)

        # Формируем input для AgentExecutor
        effective_input_for_executor = user_input
        if not dialog_history and initial_user_text_context: # Если это первый вызов агента
            # и сценарий передал начальный текст пользователя (например, "проблема с курьером")
            # и текущий user_input от сценария - это тот же самый начальный текст
            if initial_user_text_context == user_input:
                effective_input_for_executor = initial_user_text_context
            else: # Если user_input - это уже ответ на какой-то общий вопрос роутера, а initial_text - контекст
                effective_input_for_executor = (
                    f"Первоначальный запрос пользователя был: '{initial_user_text_context}'.\n"
                    f"Текущий ответ пользователя (или его первый ввод для этого шага): '{user_input}'"
                )

        prompt_template = ChatPromptTemplate.from_messages([
            ("system", WAREHOUSE_ID_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent_runnable = create_openai_functions_agent(self.llm, self.tools, prompt_template)
        executor = AgentExecutor(agent=agent_runnable, tools=self.tools, verbose=True, handle_parsing_errors="Output parser failed to parse")

        logger.info(f"[{self.agent_id}] Invoking. Effective Input: '{effective_input_for_executor[:100]}...'. History for LLM len: {len(langchain_formatted_history)}")

        try:
            response = await executor.ainvoke({"input": effective_input_for_executor, "chat_history": langchain_formatted_history})
            agent_final_output = response.get("output", "Не удалось обработать запрос по складу.")
        except KeyError as e:
            logger.error(f"[{self.agent_id}] KeyError: {e}. Prompt input_vars: {prompt_template.input_variables if hasattr(prompt_template, 'input_variables') else 'N/A'}. Invoked with keys: {list({'input': '', 'chat_history': []}.keys())}")
            return {"status": "error", "message_to_user": f"Ошибка конфигурации агента склада: {e}", "next_agent_state": current_agent_state}
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка executor: {e}", exc_info=True)
            return {"status": "error", "message_to_user": "Ошибка при поиске склада.", "next_agent_state": current_agent_state}

        next_agent_state = current_agent_state.copy()
        # Логируем оригинальный user_input, который пришел в этот вызов process_user_input
        if user_input or not dialog_history : # Логируем, если есть ввод или это первый шаг (даже с пустым user_input)
            next_agent_state["dialog_history"] = dialog_history + [{"type": "human", "content": user_input}]

        text_reply_to_user = agent_final_output
        confirmed_wh_info = None

        if JSON_WAREHOUSE_INFO_START_MARKER in agent_final_output and JSON_WAREHOUSE_INFO_END_MARKER in agent_final_output:
            try:
                text_reply_to_user = agent_final_output.split(JSON_WAREHOUSE_INFO_START_MARKER)[0].strip()
                json_str = agent_final_output.split(JSON_WAREHOUSE_INFO_START_MARKER)[1].split(JSON_WAREHOUSE_INFO_END_MARKER)[0]
                parsed_json = json.loads(json_str)
                if isinstance(parsed_json, dict) and "warehouse_id" in parsed_json and "warehouse_name" in parsed_json:
                    confirmed_wh_info = {
                        "warehouse_id": parsed_json.get("warehouse_id"),
                        "warehouse_name": parsed_json.get("warehouse_name"),
                        "city": parsed_json.get("city")
                    }
                if not text_reply_to_user and confirmed_wh_info:
                    text_reply_to_user = f"Склад {confirmed_wh_info.get('warehouse_name', '')} (ID: {confirmed_wh_info.get('warehouse_id','')}) подтвержден."
                logger.info(f"[{self.agent_id}] Склад идентифицирован агентом: {confirmed_wh_info}")
            except Exception as e_json:
                logger.error(f"[{self.agent_id}] Ошибка парсинга JSON от WarehouseID агента: {e_json}. Ответ: {agent_final_output}")
                text_reply_to_user = agent_final_output

        next_agent_state["dialog_history"].append({"type": "ai", "content": text_reply_to_user})

        if confirmed_wh_info:
            return {"status": "completed", "message_to_user": text_reply_to_user,
                    "result": confirmed_wh_info, "next_agent_state": next_agent_state}

        return {"status": "in_progress", "message_to_user": text_reply_to_user, "next_agent_state": next_agent_state}


class CourierIdentificationAgent(BaseAgent):
    agent_id: str = "agent_courier_identifier"

    def _get_default_tools(self) -> List[Any]:
        return [search_courier_tool]

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        warehouse_info = {}
        if scenario_context and isinstance(scenario_context.get("warehouse_info"), dict): # Ожидаем от сценария
            warehouse_info = scenario_context.get("warehouse_info")
            logger.info(f"[{self.agent_id}] Инициализация с контекстом склада: ID={warehouse_info.get('warehouse_id')}")
        else:
            logger.warning(f"[{self.agent_id}] warehouse_info не предоставлен или некорректен при инициализации!")

        return {
            "dialog_history": [],
            "warehouse_context": warehouse_info
        }

    async def process_user_input(
            self, user_input: str, current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:

        dialog_history = current_agent_state.get("dialog_history", [])
        warehouse_context = current_agent_state.get("warehouse_context", {})

        wh_name_for_context = warehouse_context.get("warehouse_name", "N/A")
        wh_id_for_context = warehouse_context.get("warehouse_id")

        if not wh_id_for_context:
            logger.error(f"[{self.agent_id}] ID склада отсутствует в состоянии агента. Невозможно продолжить.")
            return {"status": "error",
                    "message_to_user": "Внутренняя ошибка: не определен склад для поиска курьера. Пожалуйста, начните сначала.",
                    "next_agent_state": current_agent_state}

        langchain_formatted_history = self._prepare_chat_history_for_llm(dialog_history)

        # Формируем input для executor'а, включая контекст склада
        # Промпт COURIER_ID_SYSTEM_PROMPT ожидает, что эта информация будет в {input}
        effective_input_for_executor = (
            f"Контекст для поиска курьера: работаем со складом '{wh_name_for_context}' (ID: {wh_id_for_context}).\n"
            f"Запрос или ответ пользователя: '{user_input}'"
        )

        prompt_template = ChatPromptTemplate.from_messages([
            ("system", COURIER_ID_SYSTEM_PROMPT), # Этот промпт ожидает {warehouse_id_context} и т.д.
            # Это неверно для create_openai_functions_agent, если мы хотим статический системный промпт.
            # Промпт должен быть статическим, а контекст склада передаваться в {input}.
            # Исправим COURIER_ID_SYSTEM_PROMPT в prompts файле, чтобы он был статичным.
            # А здесь будем использовать его.
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent_runnable = create_openai_functions_agent(self.llm, self.tools, prompt_template)
        executor = AgentExecutor(agent=agent_runnable, tools=self.tools, verbose=True, handle_parsing_errors="Output parser failed to parse")

        logger.info(f"[{self.agent_id}] Invoking. Effective Input: '{effective_input_for_executor[:150]}...'. History len: {len(langchain_formatted_history)}")
        invoke_payload = {
            "input": user_input,
            "chat_history": langchain_formatted_history,
            "warehouse_id_from_context": wh_id_for_context,
            "warehouse_name_from_context": wh_name_for_context
        }
        try:
            response = await executor.ainvoke(invoke_payload)
            agent_final_output = response.get("output", "Не удалось обработать запрос по курьеру.")
        except KeyError as e:
            logger.error(f"[{self.agent_id}] KeyError: {e}. Prompt input_vars: {prompt_template.input_variables if hasattr(prompt_template, 'input_variables') else 'N/A'}. Invoked with keys: {list({'input': '', 'chat_history': []}.keys())}")
            return {"status": "error", "message_to_user": f"Ошибка конфигурации агента курьера: {e}", "next_agent_state": current_agent_state}
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
                # Гарантируем, что warehouse_id в результате соответствует контекстному
                if confirmed_cour_info and wh_id_for_context:
                    confirmed_cour_info["warehouse_id"] = wh_id_for_context
            except Exception as e_json:
                logger.error(f"[{self.agent_id}] Ошибка парсинга JSON от CourierID агента: {e_json}. Ответ: {agent_final_output}")
                text_reply_to_user = agent_final_output

        next_agent_state["dialog_history"].append({"type": "ai", "content": text_reply_to_user})

        if confirmed_cour_info and isinstance(confirmed_cour_info, dict) and "id" in confirmed_cour_info:
            return {"status": "completed", "message_to_user": text_reply_to_user,
                    "result": confirmed_cour_info, "next_agent_state": next_agent_state}

        return {"status": "in_progress", "message_to_user": text_reply_to_user, "next_agent_state": next_agent_state}