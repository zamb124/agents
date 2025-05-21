from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import Dict, Any
import json
import logging

from config import OPENAI_API_KEY
from llm_services import get_llm
from tools.tool_definitions import decision_tools

logger = logging.getLogger(__name__)

llm = get_llm(temperature=0.1)

DECISION_MAKER_SYSTEM_PROMPT = """
Ты - ИИ-агент, Тебя зовут Васька Помощьник ответственный за принятие решений по инцидентам с курьерами.
Сегодня: %s 
Ты получаешь на вход структурированную информацию в переменной {{input}} в виде JSON-строки.

Эта JSON-строка может представлять одну из двух структур:

Структура 1: **Первичный анализ инцидента**.
Это JSON объект, который содержит следующие поля:
- `courier_id` (ID курьера)
- `courier_name` (ФИО курьера)
- `warehouse_id` (ID склада)
- `warehouse_name` (название склада)
- `incident_description` (описание инцидента)
- `incident_date` (дата инцидента)
- `incident_time` (время инцидента, если есть)
- `courier_had_shift_on_incident_date` (была ли смена у курьера)
- `shift_details` (детали смены)
- `job_instruction_extracts` (выдержки из должностной инструкции).

Структура 2: **Ответ пользователя на запрос подтверждения**.
Это JSON объект, который содержит два ключа:
- Ключ 'initial_incident_payload': его значением является полный JSON объект, соответствующий Структуре 1 (первичный анализ инцидента).
- Ключ 'user_confirm_reply': его значением является текстовый ответ пользователя на запрос подтверждения.

Твоя задача:

**Если JSON в {{input}} соответствует Структуре 1 (Первичный анализ инцидента):**
1.  Внимательно проанализируй всю предоставленную информацию из JSON.
2.  На основе `incident_description` и `job_instruction_extracts` определи суть нарушения.
3.  Используй инструмент `query_knowledge_base` с `collection_name="support_agent_guidelines"`, чтобы найти МЕТОДИЧЕСКИЕ РЕКОМЕНДАЦИИ САППОРТА о том, какие действия предпринять.
4.  Определи ПЛАН ДЕЙСТВИЙ (например, какие вызовы инструмента `take_action_on_courier` ты бы сделал: 'delete_shift', 'ban_courier', 'log_complaint'). Укажи, какие конкретно параметры будут переданы в инструмент.
5.  Сформулируй для директора четкое описание инцидента, твой анализ, предлагаемые действия (какие именно и почему, со ссылкой на методичку, если применимо).
6.  В конце своего сообщения ОБЯЗАТЕЛЬНО задай вопрос: "Предлагаю следующие действия: [твой детальный план действий и их обоснование]. Вы подтверждаете эти действия? Пожалуйста, ответьте 'да' или 'нет'."
7.  Твой ответ ДОЛЖЕН начинаться со специального маркера: `[CONFIRMATION_REQUEST]` и содержать ТОЛЬКО это описание и вопрос. НЕ ИСПОЛЬЗУЙ `take_action_on_courier` на этом этапе.
8.  Если курьер не идентифицирован или методички не ясны, твой план может быть "зарегистрировать жалобу с такой-то причиной" или "сообщить о невозможности действий". Все равно запроси подтверждение этого плана.

**Если JSON в {{input}} соответствует Структуре 2 (Ответ пользователя на запрос подтверждения):**
1.  Проанализируй значение ключа 'user_confirm_reply' из JSON.
2.  Если ответ пользователя интерпретируется как 'да' (например, "да", "подтверждаю", "согласен"):
    а. Возьми данные из значения ключа 'initial_incident_payload' (это первоначальные данные об инциденте).
    б. Если в твоем первоначальном плане были действия, требующие `take_action_on_courier` (например, 'delete_shift', 'ban_courier', 'log_complaint'), ИСПОЛЬЗУЙ инструмент `take_action_on_courier` для их выполнения. В аргументе `reason` четко укажи причину, как ты планировал. Если для 'delete_shift' был `shift_id` в `initial_incident_payload.shift_details`, используй его.
    в. Сформулируй финальный ответ для директора, включающий информацию о том, какие действия были выполнены (или не выполнены, если план был таким) и их результат. Этот ответ НЕ должен содержать маркер `[CONFIRMATION_REQUEST]`.
3.  Если ответ пользователя интерпретируется как 'нет' (например, "нет", "не подтверждаю", "отмена"):
    а. Сообщи директору, что предложенные действия отменены. Этот ответ НЕ должен содержать маркер `[CONFIRMATION_REQUEST]`.
4.  Если ответ пользователя нечеткий (не 'да' и не 'нет', или содержит новый вопрос/информацию), вежливо попроси его уточнить свой ответ ('да' или 'нет') на первоначально предложенный план. Ты можешь кратко напомнить план. Этот ответ ДОЛЖЕН снова начинаться с `[CONFIRMATION_REQUEST]`.

Твой финальный ответ должен быть ТОЛЬКО сообщением для директора.
""" % datetime.now().strftime("%Y-%m-%d")

try:
    decision_prompt = ChatPromptTemplate.from_messages([
        ("system", DECISION_MAKER_SYSTEM_PROMPT),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    logger.info(f"DecisionMakerAgent: `decision_prompt` создан. `input_variables` = {decision_prompt.input_variables}")
except Exception as e:
    logger.error(f"Ошибка при создании ChatPromptTemplate для решающего агента: {e}", exc_info=True)
    raise

decision_agent_runnable = create_openai_functions_agent(llm, decision_tools, decision_prompt)

decision_agent_executor = AgentExecutor(
    agent=decision_agent_runnable,
    tools=decision_tools,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=10
)

async def run_decision_maker(collected_data: Dict[str, Any], user_confirmation_response: str = None) -> str:
    if user_confirmation_response:
        input_payload = {
            "initial_incident_payload": collected_data, # Ключ соответствует промпту
            "user_confirm_reply": user_confirmation_response # Ключ соответствует промпту
        }
        logger.info(f"Запуск DecisionMakerAgent (этап подтверждения) с ответом пользователя: '{user_confirmation_response}'")
    else:
        input_payload = collected_data # Это соответствует Структуре 1 в промпте
        logger.info("Запуск DecisionMakerAgent (первичный анализ).")

    input_str = json.dumps(input_payload, ensure_ascii=False, indent=2)
    logger.debug(f"Полный JSON для DecisionMakerAgent:\n{input_str}")

    invocation_input = {"input": input_str}
    logger.debug(f"Передаваемый input для decision_agent_executor.ainvoke (ключи): {list(invocation_input.keys())}")

    try:
        response = await decision_agent_executor.ainvoke(invocation_input)
    except Exception as e:
        logger.error(f"Критическая ошибка при вызове decision_agent_executor: {e}", exc_info=True)
        # Логируем ожидаемые и полученные переменные, если ошибка KeyError и есть input_variables у промпта
        if isinstance(e, KeyError) and hasattr(decision_prompt, 'input_variables'):
            logger.error(f"Ожидаемые переменные промптом decision_prompt: {decision_prompt.input_variables}")
            logger.error(f"Переданный input в executor (ключи): {list(invocation_input.keys())}")
        return f"Произошла серьезная ошибка при принятии решения агентом: {type(e).__name__} - {e}. Пожалуйста, попробуйте позже или сообщите администратору для проверки логов."

    output = response.get("output", "К сожалению, не удалось принять однозначное решение. Пожалуйста, обратитесь к администратору для разбора ситуации.")
    logger.info(f"DecisionMakerAgent завершил работу. Финальный ответ: {output}")
    return output