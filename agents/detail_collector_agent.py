# agents/detail_collector_agent.py
import logging
import json
import re
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from llm_services import get_llm # Используем тот же get_llm
from .detail_collector_prompts import get_detail_collector_prompt

logger = logging.getLogger(__name__)
details_llm = get_llm(temperature=0.3) # Можно настроить температуру

DETAILS_COLLECTED_MARKER = "[DETAILS_COLLECTED]"

async def run_detail_collector_llm(
        warehouse_name: str,
        courier_name: str,
        initial_user_complaint: str, # Первоначальная жалоба, с которой начался сбор деталей
        current_user_input: str,     # Текущий ответ пользователя на вопрос сборщика деталей
        detail_chat_history: list   # История диалога ТОЛЬКО на этапе сбора деталей
) -> dict:
    """
    Вызывает LLM для сбора деталей инцидента.
    Возвращает словарь:
    {
        "status": "in_progress" | "completed" | "error",
        "agent_message": "сообщение/вопрос для пользователя",
        "collected_details": {} # Если status="completed"
    }
    """
    system_prompt_content = get_detail_collector_prompt(warehouse_name, courier_name, initial_user_complaint)

    messages = [SystemMessage(content=system_prompt_content)]
    for msg in detail_chat_history: # Добавляем историю сбора деталей
        if msg.get("type") == "human": messages.append(HumanMessage(content=msg["content"]))
        elif msg.get("type") == "ai": messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=current_user_input))

    logger.info(f"DetailCollector: Запрос к LLM с {len(messages)} сообщениями. Последнее от пользователя: '{current_user_input[:100]}'")

    try:
        response = await details_llm.ainvoke(messages)
        llm_output = response.content.strip()
        logger.info(f"DetailCollector: Ответ LLM: '{llm_output[:200]}...'")

        if DETAILS_COLLECTED_MARKER in llm_output:
            try:
                json_part_str = llm_output.split(DETAILS_COLLECTED_MARKER, 1)[1].strip()
                # Простой парсинг JSON, можно улучшить как в InformationCollectorAgent
                json_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", json_part_str)
                if not json_match:
                    logger.error(f"DetailCollector: Не найден JSON после маркера. Output: {llm_output}")
                    # Возвращаем как in_progress, чтобы агент мог попытаться исправить
                    return {"status": "in_progress", "agent_message": "Кажется, я не смог правильно оформить собранные детали. Пожалуйста, попробуйте ответить на мой последний вопрос еще раз или уточните, если я что-то упустил."}

                parsed_details = json.loads(json_match.group(0))
                # Сообщение пользователю перед маркером (если есть)
                user_message_before_marker = llm_output.split(DETAILS_COLLECTED_MARKER, 1)[0].strip()

                return {
                    "status": "completed",
                    "agent_message": user_message_before_marker if user_message_before_marker else "Спасибо, детали инцидента собраны.",
                    "collected_details": parsed_details
                }
            except json.JSONDecodeError as e:
                logger.error(f"DetailCollector: Ошибка парсинга JSON от LLM: {e}. Output: {llm_output}")
                return {"status": "in_progress", "agent_message": "Произошла ошибка при обработке деталей. Пожалуйста, попробуйте ответить на мой последний вопрос еще раз."}
            except Exception as e_gen:
                logger.error(f"DetailCollector: Общая ошибка при обработке JSON: {e_gen}. Output: {llm_output}")
                return {"status": "in_progress", "agent_message": "Произошла ошибка при обработке деталей. Пожалуйста, попробуйте ответить на мой последний вопрос еще раз."}

        else: # LLM задает следующий уточняющий вопрос
            return {"status": "in_progress", "agent_message": llm_output}

    except Exception as e:
        logger.error(f"DetailCollector: Ошибка при вызове LLM: {e}", exc_info=True)
        return {"status": "error", "agent_message": "Произошла ошибка при сборе деталей инцидента. Попробуйте позже."}