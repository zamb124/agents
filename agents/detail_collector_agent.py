# agents/detail_collector_agent.py
import logging
import json
import re
from typing import Dict, Any, List, Optional

from langchain_core.messages import SystemMessage
from .base_agent import BaseAgent # Используем финальный BaseAgent
from .prompts.detail_collector_prompts import (
    get_detail_collector_prompt, # Функция, возвращающая отформатированный DC_AUTONOMOUS_SYSTEM_PROMPT
    DETAILS_COLLECTED_MARKER,    # Маркер завершения
    AGENT_RESULT_FIELDS          # Карта стандартных ключей для финального JSON
)

logger = logging.getLogger(__name__)

class DetailCollectorAgent(BaseAgent):
    agent_id: str = "detail_collector_autonomous" # Обновленный ID для ясности

    def _get_default_tools(self) -> List[Any]: # List[BaseTool]
        # Этот агент не использует инструменты Langchain AgentExecutor,
        # он напрямую вызывает LLM для ведения диалога.
        return []

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Начальное состояние для этого агента - это только его история диалога.
        scenario_context здесь не используется для инициализации *внутреннего* состояния агента,
        так как вся необходимая информация (wh, courier, initial_complaint) будет передаваться
        в scenario_context для каждого вызова process_user_input и использоваться в промпте.
        """
        return {
            "dialog_history": [], # История диалога именно с этим агентом
            # "collected_aspects" больше не хранится в состоянии агента,
            # LLM сама анализирует историю для определения прогресса.
        }

    async def process_user_input(
            self,
            user_input: str,
            current_agent_state: Dict[str, Any], # Содержит 'dialog_history'
            scenario_context: Optional[Dict[str, Any]] = None # Содержит 'warehouse_name', 'courier_name', 'initial_complaint'
    ) -> Dict[str, Any]:

        dialog_history = current_agent_state.get("dialog_history", [])

        # Контекст от сценария, необходимый для промпта
        if not scenario_context: scenario_context = {} # Обеспечиваем наличие словаря
        warehouse_name = scenario_context.get("warehouse_name", "N/A")
        courier_name = scenario_context.get("courier_name", "N/A")
        initial_complaint = scenario_context.get("initial_complaint", "Проблема с курьером.")

        # Обновляем историю диалога текущим вводом пользователя,
        # НО только если это не первый вызов агента, где user_input - это initial_complaint.
        # Сценарий должен это разруливать:
        # - Первый вызов: user_input = initial_complaint, dialog_history = []
        # - Последующие: user_input = ответ пользователя, dialog_history = предыдущая история

        current_dialog_history_for_llm = list(dialog_history) # Копируем
        # Если история не пуста, значит, агент уже задавал вопрос, и user_input - это ответ.
        # Если история пуста, то user_input - это initial_complaint, который уже есть в scenario_context.
        # Промпт get_detail_collector_prompt ожидает user_current_reply отдельно.
        if current_dialog_history_for_llm: # Если это не первый шаг диалога с агентом
            current_dialog_history_for_llm.append({"type": "human", "content": user_input})

        # Формируем системный промпт
        system_prompt = get_detail_collector_prompt(
            scenario_context={ # Передаем контекст сценария в функцию формирования промпта
                "warehouse_name": warehouse_name,
                "courier_name": courier_name,
                "initial_complaint": initial_complaint
            },
            agent_dialog_history=dialog_history, # История ДО текущего ответа пользователя
            user_current_reply=user_input        # Текущий ответ пользователя
        )

        messages = [SystemMessage(content=system_prompt)]

        logger.info(f"[{self.agent_id}] Запрос к LLM. User input: '{user_input[:100]}...'. History len: {len(dialog_history)}")
        # logger.debug(f"[{self.agent_id}] System prompt (autonomous):\n{system_prompt}") # Может быть очень длинным

        try:
            llm_response = await self.llm.ainvoke(messages)
            llm_output_text = llm_response.content.strip()
            logger.info(f"[{self.agent_id}] Ответ LLM: '{llm_output_text[:300]}...'")

            # Формируем следующее состояние агента
            next_agent_state = {"dialog_history": list(current_dialog_history_for_llm)} # История с последним ответом юзера

            # Сообщение для пользователя (вопрос или подтверждение завершения)
            # Отсекаем JSON, если он есть, из сообщения для пользователя и для истории
            message_to_user_for_display = llm_output_text
            if DETAILS_COLLECTED_MARKER in llm_output_text:
                message_to_user_for_display = llm_output_text.split(DETAILS_COLLECTED_MARKER)[0].strip()
                if not message_to_user_for_display: # Если LLM вернула только маркер и JSON
                    message_to_user_for_display = "Спасибо, все детали инцидента уточнены."

            next_agent_state["dialog_history"].append({"type": "ai", "content": message_to_user_for_display})


            if DETAILS_COLLECTED_MARKER in llm_output_text:
                parts = llm_output_text.split(DETAILS_COLLECTED_MARKER, 1)
                # message_to_user уже определен как parts[0]
                json_part_str = parts[1].strip() if len(parts) > 1 else ""

                final_json_result = {}
                if json_part_str:
                    try:
                        json_match = re.search(r"(\{[\s\S]*\})", json_part_str) # Ищем любой JSON объект
                        if json_match:
                            parsed_llm_json = json.loads(json_match.group(0))
                            # Приводим к нашей стандартной структуре AGENT_RESULT_FIELDS
                            # Промпт уже инструктирует LLM использовать нужные ключи.
                            # Здесь можно добавить валидацию или заполнение недостающих null'ами.
                            for _, field_name in AGENT_RESULT_FIELDS.items():
                                final_json_result[field_name] = parsed_llm_json.get(field_name) # Берем то, что вернула LLM
                                if final_json_result[field_name] is None and field_name != AGENT_RESULT_FIELDS["DATE"]:
                                    logger.debug(f"[{self.agent_id}] Поле '{field_name}' null в финальном JSON от LLM.")
                            # Убедимся, что поле даты есть, даже если null (LLM должна была его добавить)
                            if AGENT_RESULT_FIELDS["DATE"] not in final_json_result:
                                final_json_result[AGENT_RESULT_FIELDS["DATE"]] = parsed_llm_json.get(AGENT_RESULT_FIELDS["DATE"])

                        else:
                            logger.warning(f"[{self.agent_id}] Не найден JSON после маркера '{DETAILS_COLLECTED_MARKER}'.")
                            # Если маркер есть, но JSON нет, это ошибка LLM. Возвращаем пустой JSON.
                    except json.JSONDecodeError as e_json:
                        logger.error(f"[{self.agent_id}] Ошибка парсинга финального JSON: {e_json}. JSON-строка: '{json_part_str}'")
                        message_to_user_for_display += "\n(Системная ошибка: не удалось обработать собранные детали в структурированном виде.)"
                        # Возвращаем пустой JSON или то, что успели собрать (если бы собирали пошагово)
                else: # Если JSON части нет, но маркер есть
                    logger.warning(f"[{self.agent_id}] Маркер завершения '{DETAILS_COLLECTED_MARKER}' есть, но нет JSON части от LLM.")
                    message_to_user_for_display += "\n(Системная ошибка: детали не были предоставлены в ожидаемом формате.)"

                return {
                    "status": "completed",
                    "message_to_user": message_to_user_for_display,
                    "result": final_json_result, # Это финальный JSON от агента
                    "next_agent_state": next_agent_state # Сохраняем финальное состояние с историей
                }
            else: # LLM задает следующий уточняющий вопрос
                return {
                    "status": "in_progress",
                    "message_to_user": llm_output_text, # Это вопрос от LLM
                    "next_agent_state": next_agent_state
                }
        except Exception as e:
            logger.error(f"[{self.agent_id}] Критическая ошибка в process_user_input: {e}", exc_info=True)
            # Возвращаем текущее состояние, чтобы не потерять историю, и сообщаем об ошибке
            error_state = current_agent_state.copy()
            if "error_log" not in error_state: error_state["error_log"] = []
            error_state["error_log"].append(f"Exception: {type(e).__name__} - {e}")
            return {
                "status": "error",
                "message_to_user": "Произошла внутренняя ошибка при обработке вашего запроса агентом сбора деталей.",
                "next_agent_state": error_state
            }