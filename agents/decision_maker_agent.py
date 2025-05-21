from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import Dict, Any
import json
import logging

from config import OPENAI_API_KEY
from tools.tool_definitions import decision_tools

logger = logging.getLogger(__name__)

llm = ChatOpenAI(model="gpt-4o", temperature=0.2, openai_api_key=OPENAI_API_KEY)

DECISION_MAKER_SYSTEM_PROMPT = """
Ты - ИИ-агент, ответственный за принятие решений по инцидентам с курьерами.
Ты получаешь на вход структурированную информацию об инциденте в формате JSON, собранную другим агентом. Эта информация включает:
- `courier_id`, `courier_name` (если курьер идентифицирован)
- `warehouse_id`, `warehouse_name` (если склад идентифицирован)
- `incident_description` (подробное описание инцидента)
- `incident_date_time` (дата и время инцидента)
- `knowledge_extracts` (список релевантных выдержек из должностной инструкции курьера и методических рекомендаций саппорта, полученных из RAG).

Твоя задача:
1.  Внимательно проанализировать всю предоставленную информацию в JSON.
2.  На основе `incident_description` и особенно `knowledge_extracts` (методические рекомендации имеют приоритет) определить степень нарушения.
3.  Принять решение о необходимых действиях (например, 'delete_shift', 'ban_courier', 'log_complaint') СТРОГО В СООТВЕТСТВИИ С МЕТОДИЧЕСКИМИ РЕКОМЕНДАЦИЯМИ, найденными в `knowledge_extracts`.
4.  Использовать инструмент `take_action_on_courier` для выполнения выбранного действия. В аргументе `reason` для инструмента `take_action_on_courier` четко укажи причину, основываясь на инциденте и правилах из `knowledge_extracts`.
5.  Сформулировать четкий и вежливый ответ для директора. В ответе ОБЯЗАТЕЛЬНО должно быть:
    - Упоминание курьера (ФИО и/или ID).
    - Суть инцидента.
    - Принятое решение (какое действие выполнено).
    - Основание для решения (краткая ссылка на правило из методички, если это уместно и было в `knowledge_extracts`).
    - Результат выполнения действия (сообщение от инструмента `take_action_on_courier`).
6.  Если предоставленной информации или выдержек из базы знаний (`knowledge_extracts`) недостаточно для однозначного решения согласно методичке, или если методичка рекомендует передать случай на рассмотрение, ты должен указать на это в своем ответе и предложить зарегистрировать жалобу (`log_complaint`) для дальнейшего разбирательства человеком, если это наиболее подходящее действие по методичке. Не выдумывай правила и не предпринимай действий, не описанных в методичке.
7.  Если курьер не был идентифицирован (`courier_id` отсутствует или null), ты не можешь применить к нему персональные санкции (`ban_courier`, `delete_shift`). В таком случае, если проблема общая, предложи зарегистрировать инцидент (`log_complaint` с общей причиной) или сообщи, что без идентификации курьера меры невозможны.

Входные данные (передаются в {{input}}) будут выглядеть примерно так (это просто текстовый пример структуры, не используй фигурные скобки для переменных внутри этого примера):
`{{`
`  "courier_id": "123",`
`  "courier_name": "Иванов Иван Иванович",`
`  "warehouse_id": "W1",`
`  "warehouse_name": "Центральный склад",`
`  "incident_description": "Курьер не вышел на смену...",`
`  "incident_date_time": "2024-07-29 утро",`
`  "knowledge_extracts": [`
`    {{"text": "...", "source": "..."}},`
`    {{"text": "...", "source": "..."}}`
`  ]`
`}}`
Твой финальный ответ должен быть только сообщением для директора. Не включай в ответ сам JSON.
"""

try:
    decision_prompt = ChatPromptTemplate.from_messages([
        ("system", DECISION_MAKER_SYSTEM_PROMPT),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    logger.info(f"Создан decision_prompt. Ожидаемые переменные: {decision_prompt.input_variables}")
except Exception as e:
    logger.error(f"Ошибка при создании ChatPromptTemplate для решающего агента: {e}", exc_info=True)
    raise

decision_agent_runnable = create_openai_functions_agent(llm, decision_tools, decision_prompt)

decision_agent_executor = AgentExecutor(
    agent=decision_agent_runnable,
    tools=decision_tools,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=5
)

async def run_decision_maker(collected_data: Dict[str, Any]) -> str:
    """
    Запускает агента принятия решений.
    Принимает сабранные данные, преобразует их в JSON-строку и передает агенту.
    Возвращает ответ агента для пользователя.
    """
    input_str = json.dumps(collected_data, ensure_ascii=False, indent=2)
    logger.info(f"Запуск DecisionMakerAgent с input:\n{input_str}")

    logger.debug(f"Ожидаемые переменные decision_prompt перед вызовом: {decision_prompt.input_variables}")
    invocation_input = {"input": input_str}
    logger.debug(f"Передаваемый input для decision_agent_executor.ainvoke: {invocation_input.keys()}")

    try:
        response = await decision_agent_executor.ainvoke(invocation_input)
    except KeyError as e:
        logger.error(f"KeyError при вызове decision_agent_executor: {e}", exc_info=True)
        logger.error(f"Ожидаемые переменные промптом: {decision_prompt.input_variables if hasattr(decision_prompt, 'input_variables') else 'Не удалось получить'}")
        logger.error(f"Переданный input (ключи): {list(invocation_input.keys())}")
        return f"Внутренняя ошибка конфигурации агента принятия решений (KeyError): {e}. Пожалуйста, сообщите администратору."
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при вызове decision_agent_executor: {e}", exc_info=True)
        return f"Произошла непредвиденная ошибка при принятии решения: {e}. Пожалуйста, попробуйте позже."

    output = response.get("output", "Не удалось принять решение. Пожалуйста, обратитесь к администратору.")
    logger.info(f"DecisionMakerAgent output: {output}")
    return output