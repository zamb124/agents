# agents/detail_collector_agent.py
import logging
import json
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from langchain_core.messages import SystemMessage
from .base_agent import BaseAgent
from .prompts.detail_collector_prompts import (
    ASPECTS_TO_COLLECT_CONFIG,
    AGENT_JSON_RESULT_FIELDS,
    get_generate_question_prompt,
    get_extract_data_prompt
)

logger = logging.getLogger(__name__)

class DetailCollectorAgent(BaseAgent):
    agent_id: str = "detail_collector_python_controlled_v5"

    def _get_default_tools(self) -> List[Any]:
        return []

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        collected_data = {field_name: None for field_name in AGENT_JSON_RESULT_FIELDS.values()}

        if scenario_context:
            initial_complaint = scenario_context.get("initial_complaint")
            if initial_complaint and len(initial_complaint) < 150:
                is_generic = any(kw in initial_complaint.lower() for kw in ["привет", "здравствуйте", "помогите", "вопрос"])
                if not is_generic:
                    collected_data[AGENT_JSON_RESULT_FIELDS["incident_description"]] = initial_complaint
                    logger.info(f"[{self.agent_id}] Предварительно заполнено 'incident_description' из initial_complaint.")

        return {
            "dialog_history": [],
            "collected_data": collected_data,
            "current_aspect_idx": -1,
            "last_question_text": None,
            "max_extraction_retries": 1,
            "current_extraction_retries": 0
        }

    async def _generate_question_text(
            self,
            aspect_config: Dict[str, Any],
            dialog_history: List[Dict[str,str]],
            scenario_context: Dict[str, Any],
            collected_data: Dict[str, Any],
            current_date_str: str
    ) -> str:
        prompt_text = get_generate_question_prompt(
            aspect_config["description_for_question_generation"],
            dialog_history,
            scenario_context,
            collected_data,
            current_date_str
        )
        try:
            response = await self.llm.ainvoke([SystemMessage(content=prompt_text)])
            question = response.content.strip()
            if not question:
                logger.warning(f"[{self.agent_id}] LLM вернула пустой вопрос для аспекта {aspect_config['id']}. Используем fallback.")
                return f"Расскажите, пожалуйста, подробнее про: {aspect_config['description_for_question_generation']}."
            return question
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка при генерации текста вопроса LLM для аспекта {aspect_config['id']}: {e}")
            return f"Не могли бы вы уточнить следующий момент: {aspect_config['description_for_question_generation']}?"

    async def _extract_data_from_reply(
            self,
            question_asked: str,
            user_reply: str,
            aspect_config: Dict[str, Any],
            current_date_str: str,
            yesterday_date_str: str
    ) -> tuple[Optional[Dict[str, Any]], bool]: # Возвращает (данные, был_ли_извлечен_не_null_ответ)

        prompt_text = get_extract_data_prompt(
            question_asked,
            user_reply,
            aspect_config["target_json_fields"],
            aspect_config["json_extraction_keys_hint"],
            current_date_str,
            yesterday_date_str
        )

        try:
            response = await self.llm.ainvoke([SystemMessage(content=prompt_text)])
            response_content = response.content.strip()

            json_match = re.search(r"(\{[\s\S]*\})", response_content)
            if json_match:
                json_str = json_match.group(1)
                try:
                    extracted_json = json.loads(json_str)
                    validated_data = {}
                    has_valuable_info = False
                    for key in aspect_config["target_json_fields"]:
                        value = extracted_json.get(key)
                        if isinstance(value, str) and value.lower() in ["null", "none"]:
                            validated_data[key] = None
                        else:
                            validated_data[key] = value

                        if validated_data[key] is not None:
                            has_valuable_info = True

                    logger.info(f"[{self.agent_id}] Извлеченные данные LLM для аспекта '{aspect_config['id']}': {validated_data}")
                    return validated_data, has_valuable_info
                except json.JSONDecodeError as e_json:
                    logger.error(f"[{self.agent_id}] Ошибка парсинга JSON ({e_json}) от LLM при извлечении для '{aspect_config['id']}'. Ответ LLM: {response_content}")
                    return None, False
            else:
                logger.warning(f"[{self.agent_id}] LLM не вернула JSON при извлечении данных для '{aspect_config['id']}'. Ответ: {response_content}")
                # Если LLM не вернула JSON, но ответ пользователя был "не знаю", "нет" и т.д.,
                # мы можем это обработать как "нет информации" для данного аспекта.
                if user_reply.lower().strip() in ["не знаю", "нет", "никаких", "не было", "не помню", "не скажу"]:
                    empty_data = {key: user_reply.strip() for key in aspect_config["target_json_fields"]} # Заполняем ответом пользователя
                    logger.info(f"[{self.agent_id}] Ответ пользователя '{user_reply}' интерпретирован как отсутствие данных для аспекта '{aspect_config['id']}'.")
                    return empty_data, True # Считаем, что "ценная информация" (отсутствие данных) получена
                return None, False
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка при вызове LLM для извлечения данных для '{aspect_config['id']}': {e}")
            return None, False


    async def process_user_input(
            self,
            user_input: str,
            current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:

        dialog_history = list(current_agent_state["dialog_history"])
        collected_data = current_agent_state["collected_data"].copy()
        current_aspect_idx = current_agent_state["current_aspect_idx"]
        last_question_text = current_agent_state["last_question_text"]
        current_extraction_retries = current_agent_state["current_extraction_retries"]
        max_extraction_retries = current_agent_state.get("max_extraction_retries", 1) # Безопасное получение

        if not scenario_context: scenario_context = {}

        now = datetime.now()
        current_date_str = now.strftime("%Y-%m-%d")
        yesterday_date_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        if last_question_text and user_input:
            dialog_history.append({"type": "human", "content": user_input})

            if 0 <= current_aspect_idx < len(ASPECTS_TO_COLLECT_CONFIG):
                answered_aspect_config = ASPECTS_TO_COLLECT_CONFIG[current_aspect_idx]

                extracted_data_map, has_valuable_info = await self._extract_data_from_reply(
                    last_question_text, user_input, answered_aspect_config, current_date_str, yesterday_date_str
                )

                if extracted_data_map:
                    for key, value in extracted_data_map.items():
                        if key in collected_data:
                            collected_data[key] = value
                    current_extraction_retries = 0
                elif current_extraction_retries < max_extraction_retries:
                    current_extraction_retries += 1
                    logger.warning(f"[{self.agent_id}] Не удалось извлечь данные для аспекта '{answered_aspect_config['id']}'. Попытка {current_extraction_retries}/{max_extraction_retries}. Повторяем вопрос.")

                    question_text_to_ask_again = await self._generate_question_text(
                        answered_aspect_config, dialog_history, scenario_context, collected_data, current_date_str
                    )
                    dialog_history.append({"type": "ai", "content": question_text_to_ask_again})
                    return {
                        "status": "in_progress",
                        "message_to_user": question_text_to_ask_again,
                        "next_agent_state": {
                            "dialog_history": dialog_history,
                            "collected_data": collected_data,
                            "current_aspect_idx": current_aspect_idx,
                            "last_question_text": question_text_to_ask_again,
                            "current_extraction_retries": current_extraction_retries,
                            "max_extraction_retries": max_extraction_retries # Передаем дальше
                        }
                    }
                else:
                    logger.error(f"[{self.agent_id}] Превышено количество попыток извлечения для аспекта '{answered_aspect_config['id']}'. Пропускаем аспект.")
                    current_extraction_retries = 0
                    for field_key in answered_aspect_config["target_json_fields"]:
                        if collected_data.get(field_key) is None:
                            collected_data[field_key] = f"не удалось уточнить ({user_input[:20]}...)"
            else:
                logger.error(f"[{self.agent_id}] Некорректный current_aspect_idx: {current_aspect_idx}")

        next_aspect_to_ask_config = None
        next_aspect_idx_to_set = current_aspect_idx

        if current_extraction_retries == 0: # Ищем новый аспект только если предыдущий успешно обработан или пропущен
            search_from_idx = current_aspect_idx + 1
            for i in range(search_from_idx, len(ASPECTS_TO_COLLECT_CONFIG)):
                candidate_aspect_config = ASPECTS_TO_COLLECT_CONFIG[i]
                all_fields_for_aspect_collected = True
                for field_key in candidate_aspect_config["target_json_fields"]:
                    if collected_data.get(field_key) is None:
                        all_fields_for_aspect_collected = False
                        break
                if all_fields_for_aspect_collected and not candidate_aspect_config.get("always_ask", False):
                    continue
                if "depends_on_field_value" in candidate_aspect_config:
                    dep_info = candidate_aspect_config["depends_on_field_value"]
                    dep_field_value_str = str(collected_data.get(dep_info["field_key"], "")).lower()
                    if not any(kw.lower() in dep_field_value_str for kw in dep_info["contains_keywords"]):
                        continue
                next_aspect_to_ask_config = candidate_aspect_config
                next_aspect_idx_to_set = i
                break

        if not next_aspect_to_ask_config: # Если не нашли новый аспект для вопроса (все собрано или пропущено)
            logger.info(f"[{self.agent_id}] Все аспекты пройдены. Завершение сбора деталей.")
            courier_info = scenario_context.get("courier_info", {})
            warehouse_info = scenario_context.get("warehouse_info", {})
            collected_data[AGENT_JSON_RESULT_FIELDS["courier_id"]] = courier_info.get("id")
            collected_data[AGENT_JSON_RESULT_FIELDS["courier_name"]] = courier_info.get("full_name")
            collected_data[AGENT_JSON_RESULT_FIELDS["warehouse_id"]] = warehouse_info.get("warehouse_id")
            collected_data[AGENT_JSON_RESULT_FIELDS["warehouse_name"]] = warehouse_info.get("warehouse_name")
            for _, field_name_in_json in AGENT_JSON_RESULT_FIELDS.items():
                if field_name_in_json not in collected_data:
                    collected_data[field_name_in_json] = None
            final_message_to_user = "Спасибо, все необходимые детали инцидента собраны. Передаю информацию для анализа."
            if not dialog_history or dialog_history[-1].get("content") != final_message_to_user:
                dialog_history.append({"type": "ai", "content": final_message_to_user})
            return {
                "status": "completed",
                "message_to_user": final_message_to_user,
                "result": collected_data,
                "next_agent_state": {
                    "dialog_history": dialog_history,
                    "collected_data": collected_data,
                    "current_aspect_idx": next_aspect_idx_to_set,
                    "last_question_text": None,
                    "current_extraction_retries": 0,
                    "max_extraction_retries": max_extraction_retries # Передаем дальше
                }
            }

        question_text_to_ask = await self._generate_question_text(
            next_aspect_to_ask_config, dialog_history, scenario_context, collected_data, current_date_str
        )
        if question_text_to_ask:
            dialog_history.append({"type": "ai", "content": question_text_to_ask})
        else:
            logger.error(f"Не удалось сгенерировать вопрос для аспекта {next_aspect_to_ask_config['id']}. Завершаем с ошибкой.")
            # Добавим текущее состояние в next_agent_state при ошибке
            error_next_state = {
                "dialog_history": dialog_history,
                "collected_data": collected_data,
                "current_aspect_idx": current_aspect_idx, # Остаемся на текущем, раз не смогли задать новый
                "last_question_text": last_question_text,
                "current_extraction_retries": current_extraction_retries,
                "max_extraction_retries": max_extraction_retries
            }
            return {"status": "error", "message_to_user": "Произошла ошибка при подготовке следующего вопроса.", "next_agent_state": error_next_state}

        return {
            "status": "in_progress",
            "message_to_user": question_text_to_ask,
            "next_agent_state": {
                "dialog_history": dialog_history,
                "collected_data": collected_data,
                "current_aspect_idx": next_aspect_idx_to_set,
                "last_question_text": question_text_to_ask,
                "current_extraction_retries": 0,
                "max_extraction_retries": max_extraction_retries # Передаем дальше
            }
        }