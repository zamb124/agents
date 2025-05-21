from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import Dict, Any
import json
import logging

from config import OPENAI_API_KEY
from tools.tool_definitions import decision_tools

logger = logging.getLogger(__name__)

# Инициализируем LLM один раз
llm = ChatOpenAI(model="gpt-4o", temperature=0.2, openai_api_key=OPENAI_API_KEY)

DECISION_MAKER_SYSTEM_PROMPT = """
Ты - ИИ-агент, Тебя зовут Васька Помощьник ответственный за принятие решений по инцидентам с курьерами.
Сегодня: %s 
Ты получаешь на вход структурированную информацию об инциденте в формате JSON. 
Эта информация включает следующие поля:
- `courier_id`, `courier_name`
- `warehouse_id`, `warehouse_name`
- `incident_description` (суть нарушения)
- `incident_date`, `incident_time`
- `courier_had_shift_on_incident_date` (была ли смена у курьера в день инцидента), `shift_details` (детали смены, если была)
- `job_instruction_extracts` (выдержки из ДОЛЖНОСТНОЙ ИНСТРУКЦИИ курьера, которые были нарушены).

Твоя задача:
1.  Внимательно проанализировать всю предоставленную информацию.
2.  На основе `incident_description` и `job_instruction_extracts` (нарушенные пункты должностной инструкции) определить суть нарушения.
3.  Использовать инструмент `query_knowledge_base` с `collection_name="support_agent_guidelines"`, чтобы найти МЕТОДИЧЕСКИЕ РЕКОМЕНДАЦИИ САППОРТА о том, какие действия предпринять в данной ситуации (на основе `incident_description`). Ищи релевантные правила.
4.  На основе полученных методических рекомендаций и информации о смене принять решение о необходимых действиях (например, 'delete_shift', 'ban_courier', 'log_complaint').
5.  Если действие 'delete_shift' необходимо, и в `shift_details` есть актуальный `shift_id` для смены на дату инцидента, используй этот `shift_id` при вызове инструмента `take_action_on_courier`.
6.  Использовать инструмент `take_action_on_courier` для выполнения выбранного действия. В аргументе `reason` четко укажи причину, основываясь на инциденте и методических рекомендациях. Причина должна быть понятна человеку.
7.  Сформулировать четкий и вежливый ответ для директора, включающий суть инцидента, принятое решение, основание для решения (можно сослаться на пункт методички, если он есть в тексте из RAG) и результат выполнения действия.
8.  Если методические рекомендации не найдены или не дают четкого указания, что делать, предложи `log_complaint` (зарегистрировать жалобу) для дальнейшего разбирательства человеком.
9.  Если курьер не идентифицирован (например, `courier_id` отсутствует или null), то персональные санкции (бан, удаление смены) невозможны. В этом случае, если есть описание нарушения, можно только `log_complaint` с общим описанием, либо сообщить, что без идентификации курьера действия невозможны.

Входные данные передаются в переменную {{input}} в виде JSON-строки.
Твой финальный ответ должен быть ТОЛЬКО сообщением для директора. Не включай в ответ сам JSON или свои рассуждения, если это не часть ответа для директора.
""" % datetime.now().strftime("%Y-%m-%d")

try:
    decision_prompt = ChatPromptTemplate.from_messages([
        ("system", DECISION_MAKER_SYSTEM_PROMPT),
        ("human", "{input}"), # Сюда придет JSON с собранной инфой
        MessagesPlaceholder(variable_name="agent_scratchpad"), # Для мыслей агента и вызовов инструментов
    ])
    logger.info(f"Создан `decision_prompt`. Ожидаемые переменные: {decision_prompt.input_variables}")
except Exception as e:
    logger.error(f"Ошибка при создании ChatPromptTemplate для решающего агента: {e}", exc_info=True)
    raise

# Создаем runnable агента
decision_agent_runnable = create_openai_functions_agent(llm, decision_tools, decision_prompt)

# Создаем executor для агента
decision_agent_executor = AgentExecutor(
    agent=decision_agent_runnable,
    tools=decision_tools, # Убедимся, что query_rag_tool тут есть и доступен
    verbose=True, # Для отладки полезно видеть ход мыслей агента
    handle_parsing_errors=True, # Обработка ошибок парсинга ответа LLM
    max_iterations=7 # Может потребоваться больше итераций из-за доп. вызова RAG и take_action
)

async def run_decision_maker(collected_data: Dict[str, Any]) -> str:
    """
    Запускает агента для принятия решения на основе собраной информации.
    `collected_data` - словарь с информацией от агента-сборщика.
    Возвращает строковый ответ для пользователя (директора).
    """
    # Преобразуем собранные данные в JSON-строку для передачи агенту
    input_str = json.dumps(collected_data, ensure_ascii=False, indent=2)
    logger.info(f"Запуск DecisionMakerAgent со следующими входными данными:\n{input_str}")

    invocation_input = {"input": input_str} # Оборачиваем в словарь, как ожидает prompt
    logger.debug(f"Передаваемый input для decision_agent_executor.ainvoke (ключи): {invocation_input.keys()}")

    try:
        response = await decision_agent_executor.ainvoke(invocation_input)
    except Exception as e: # Общий перехват на всякий случай
        logger.error(f"Критическая ошибка при вызове decision_agent_executor: {e}", exc_info=True)
        if isinstance(e, KeyError) and hasattr(decision_prompt, 'input_variables'):
            logger.error(f"Ожидаемые переменные промптом decision_prompt: {decision_prompt.input_variables}")
        logger.error(f"Переданный input в executor (ключи): {list(invocation_input.keys())}")
        return f"Произошла серьезная ошибка при принятии решения агентом: {e}. Пожалуйста, попробуйте позже или сообщите администратору для проверки логов."

    # Извлекаем финальный ответ агента для пользователя
    output = response.get("output", "К сожалению, не удалось принять однозначное решение. Пожалуйста, обратитесь к администратору для разбора ситуации.")
    logger.info(f"DecisionMakerAgent завершил работу. Финальный ответ: {output}")
    return output