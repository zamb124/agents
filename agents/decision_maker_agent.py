# agents/decision_maker_agent.py
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

llm = ChatOpenAI(model="gpt-4o", temperature=0.2, openai_api_key=OPENAI_API_KEY)

DECISION_MAKER_SYSTEM_PROMPT = """
Ты - ИИ-агент, ответственный за принятие решений по инцидентам с курьерами.
Сегодня: %s 
Ты получаешь на вход структурированную информацию об инциденте в формате JSON. 
Эта информация включает следующие поля:
- `courier_id`: ID курьера (строка, если идентифицирован).
- `courier_name`: ФИО курьера (строка, если идентифицирован).
- `warehouse_id`: ID склада (строка, если идентифицирован).
- `warehouse_name`: Название склада (строка, если идентифицирован).
- `incident_description`: Подробное описание инцидента (строка).
- `incident_date`: Дата инцидента в формате YYYY-MM-DD (строка).
- `incident_time`: Время инцидента, если известно (строка или null).
- `courier_had_shift_on_incident_date`: Была ли у курьера смена в день инцидента (boolean).
- `shift_details`: Объект с деталями смены, если она была (например, может содержать `shift_id`, `date`, `status`), или null, если смены не было или не найдена.
- `knowledge_extracts`: Список объектов, каждый из которых содержит `text` (выдержка из инструкции) и `source` (источник инструкции).

Твоя задача:
1.  Внимательно проанализировать всю предоставленную информацию в JSON. Обрати особое внимание на `courier_had_shift_on_incident_date` и `shift_details`. Если у курьера не было смены в указанный день, это важный факт.
2.  На основе `incident_description`, `knowledge_extracts` и информации о смене определить степень нарушения.
3.  Принять решение о необходимых действиях (например, 'delete_shift', 'ban_courier', 'log_complaint') СТРОГО В СООТВЕТСТВИИ С МЕТОДИЧЕСКИМИ РЕКОМЕНДАЦИЯМИ.
4.  Если действие 'delete_shift' необходимо, и в `shift_details` есть актуальный `shift_id` для смены на дату инцидента, используй этот `shift_id` при вызове инструмента `take_action_on_courier`. Если `shift_id` не известен или смена не на дату инцидента, но нужно удалить ближайшую смену, не передавай `shift_id` в инструмент.
5.  Использовать инструмент `take_action_on_courier` для выполнения выбранного действия. В аргументе `reason` четко укажи причину.
6.  Сформулировать четкий и вежливый ответ для директора, включающий суть инцидента, принятое решение, основание для решения и результат выполнения действия.
7.  Если информации или выдержек из базы знаний недостаточно, или методичка рекомендует передать случай на рассмотрение, укажи это и предложи `log_complaint`.
8.  Если курьер не идентифицирован, персональные санкции невозможны.

Входные данные передаются в переменную {{input}} в виде JSON-строки.
Твой финальный ответ должен быть только сообщением для директора. Не включай в ответ сам JSON.
""" % datetime.now().strftime("%Y-%m-%d")

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