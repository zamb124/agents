# agents/information_collector_agent.py
import re
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from typing import List, Dict, Any
import json
import logging
from datetime import datetime # Для получения текущей даты

from config import OPENAI_API_KEY
from tools.tool_definitions import collector_tools

logger = logging.getLogger(__name__)

COLLECTOR_SYSTEM_PROMPT = """
Ты - ИИ-агент, первая линия поддержки для директоров магазинов. Твоя задача - собрать всю необходимую информацию об инциденте с курьером.
Ты должен:
1.  Вежливо поприветствовать директора, если это начало диалога.
2.  Если неясно, о каком курьере идет речь, ЗАДАЙ УТОЧНЯЮЩИЙ ВОПРОС, чтобы получить ФИО или ID курьера. Используй инструмент `search_courier` для проверки существования курьера и получения его точного ID и ФИО.
3.  Если директор не указал свой склад, а это необходимо для контекста, ты можешь попытаться определить склад по его логину (telegram username) с помощью инструмента `get_warehouse_by_director_login`. Если не удалось, уточни у директора.
4.  Собери всю необходимую информацию об инциденте:
    - Что именно произошло.
    - Когда (дата инцидента в формате YYYY-MM-DD, время, если применимо). Если директор говорит "сегодня" или "только что", используй текущую дату. Если дата не ясна, ОБЯЗАТЕЛЬНО уточни ее.
    - Где (название склада или адрес, если применимо).
5.  После идентификации курьера и получения даты инцидента, используй инструмент `get_courier_shifts` с ID курьера и датой инцидента (YYYY-MM-DD), чтобы проверить, была ли у курьера смена в этот день. Запомни результат этой проверки (наличие смены, ID смены если есть).
6.  Используй инструмент `query_knowledge_base`, чтобы найти релевантные выдержки из должностной инструкции курьера и методических рекомендаций саппорта, касающиеся описанной проблемы. Передай описание проблемы в `query_text`.
7.  Твоя конечная цель - собрать полный пакет информации. Когда ты считаешь, что вся необходимая информация собрана (ID курьера, ФИО, описание инцидента, дата/время инцидента, информация о смене курьера на эту дату (была ли смена, ID смены), склад, выдержки из базы знаний), ты должен СФОРМУЛИРОВАТЬ ИТОГОВЫЙ ОТВЕТ, содержащий специальный маркер `[INFO_COLLECTED]` и после него JSON-объект со всей собранной информацией.
    Структура JSON должна включать поля, такие как: `courier_id`, `courier_name`, `warehouse_id`, `warehouse_name`, `incident_description`, `incident_date` (YYYY-MM-DD), `incident_time` (если есть), `courier_had_shift_on_incident_date` (boolean), `shift_details` (объект с деталями смены, если была, например `{{"shift_id": "S101", "status": "active"}}` или null), `knowledge_extracts`.
    Убедись, что в JSON есть `courier_id` и `courier_name`, если они были найдены. Если курьер не найден, укажи это.
8.  Если пользователь просто здоровается или задает общий вопрос, не связанный с инцидентом, отвечай вежливо и будь готов принять жалобу. Не пытайся сразу собирать информацию, если контекст не ясен.
9.  Не принимай никаких решений о наказаниях! Твоя задача только сбор информации.
10. Веди диалог естественно. Если информации достаточно, не задавай лишних вопросов.
"""

llm = ChatOpenAI(model="gpt-4o", temperature=0.1, openai_api_key=OPENAI_API_KEY)

try:
    collector_prompt = ChatPromptTemplate.from_messages([
        ("system", COLLECTOR_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    logger.info(f"Создан collector_prompt. Ожидаемые переменные: {collector_prompt.input_variables}")
except Exception as e:
    logger.error(f"Ошибка при создании ChatPromptTemplate для коллектора: {e}", exc_info=True)
    raise

collector_agent_runnable = create_openai_functions_agent(llm, collector_tools, collector_prompt)

collector_agent_executor = AgentExecutor(
    agent=collector_agent_runnable,
    tools=collector_tools,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=10, # Увеличим немного, т.к. добавился шаг
    return_intermediate_steps=False
)

async def run_information_collector(user_input: str, chat_history: List[Dict[str, str]], director_login: str = None) -> Dict[str, Any]:
    """
    Запускает агента сбора информации.
    Собирает историю чата, формирует инпут для агента и обрабатывает его ответ.
    Если информация собрана, возвращает статус 'completed' и данные.
    Иначе - 'in_progress' и сообщение для пользователя.
    """
    agent_input_text = user_input
    # Добавляем текущую дату в контекст для агента, если он ее запросит
    current_date_str = datetime.now().strftime("%Y-%m-%d")
    # Можно добавить это в input или в системный промпт, но агент должен сам догадаться спросить или использовать "сегодня"
    # Пока не будем явно добавлять, посмотрим, справится ли агент с инструкцией "используй текущую дату".
    # Если нет, можно будет добавить: agent_input_text = f"[Текущая дата: {current_date_str}] {user_input}"


    langchain_chat_history = []
    for msg in chat_history:
        if msg.get("type") == "human":
            langchain_chat_history.append(HumanMessage(content=msg["content"]))
        elif msg.get("type") == "ai":
            langchain_chat_history.append(AIMessage(content=msg["content"]))

    agent_invocation_input = {
        "input": agent_input_text,
        "chat_history": langchain_chat_history
    }

    if director_login:
        agent_invocation_input["input"] = f"[Логин директора: {director_login}] {user_input}"
        # Можно добавить и дату сюда, если агент не справляется
        # agent_invocation_input["input"] = f"[Логин директора: {director_login}] [Текущая дата: {current_date_str}] {user_input}"


    logger.info(f"Запуск InformationCollectorAgent с input: '{agent_invocation_input['input']}' и историей: {len(langchain_chat_history)} сообщений")
    logger.debug(f"Полный input для collector_agent_executor.ainvoke: {json.dumps(agent_invocation_input, indent=2, ensure_ascii=False, default=str)}")
    logger.debug(f"Ожидаемые переменные промптом collector_prompt перед вызовом: {collector_prompt.input_variables}")

    try:
        response = await collector_agent_executor.ainvoke(agent_invocation_input)
    except KeyError as e:
        logger.error(f"KeyError при вызове collector_agent_executor: {e}", exc_info=True)
        logger.error(f"Ожидаемые переменные промптом: {collector_prompt.input_variables if hasattr(collector_prompt, 'input_variables') else 'Не удалось получить'}")
        logger.error(f"Переданный input (ключи): {list(agent_invocation_input.keys())}")
        return {"status": "error", "agent_message": f"Внутренняя ошибка конфигурации агента (KeyError): {e}. Пожалуйста, сообщите администратору."}
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при вызове collector_agent_executor: {e}", exc_info=True)
        return {"status": "error", "agent_message": f"Произошла непредвиденная ошибка: {e}. Пожалуйста, попробуйте позже."}

    output = response.get("output", "Извините, не удалось обработать ваш запрос.")
    logger.info(f"InformationCollectorAgent output: {output}")

    if "[INFO_COLLECTED]" in output:
        try:
            raw_json_payload = output.split("[INFO_COLLECTED]", 1)[1]
            json_match = re.search(r"```json\s*([\s\S]*?)\s*```|```\s*([\s\S]*?)\s*```|(\{[\s\S]*\}|\[[\s\S]*\])", raw_json_payload, re.DOTALL)

            json_part = ""
            if json_match:
                json_part = next(filter(None, json_match.groups()), None)

            if not json_part:
                logger.warning(f"Не удалось извлечь JSON с помощью regex из: '{raw_json_payload}'. Пробуем простой split.")
                start_brace = raw_json_payload.find('{')
                start_bracket = raw_json_payload.find('[')
                start_index = -1
                if start_brace != -1 and start_bracket != -1: start_index = min(start_brace, start_bracket)
                elif start_brace != -1: start_index = start_brace
                elif start_bracket != -1: start_index = start_bracket

                if start_index != -1:
                    json_candidate = raw_json_payload[start_index:]
                    end_brace = json_candidate.rfind('}')
                    end_bracket = json_candidate.rfind(']')
                    end_index = max(end_brace, end_bracket)
                    if end_index != -1: json_part = json_candidate[:end_index+1].strip()
                    else: json_part = json_candidate.strip()
                else:
                    logger.error(f"Не удалось найти начало JSON ('{{' или '[') в '{raw_json_payload}'")

            if not json_part:
                logger.error(f"Не удалось извлечь JSON-строку из output после [INFO_COLLECTED]. Output: {output}")
                pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
                return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else "Ошибка извлечения данных от агента."}

            json_part = json_part.strip()
            logger.debug(f"Извлеченная строка для парсинга JSON: '{json_part}'")

            try:
                collected_data = json.loads(json_part)
            except json.JSONDecodeError as e_json:
                logger.warning(f"Ошибка парсинга JSON ({e_json}) от LLM, попытка исправить: {json_part}")
                try:
                    corrected_json_str = json_part.replace("'", '"')
                    corrected_json_str = corrected_json_str.replace("None", "null").replace("True", "true").replace("False", "false")
                    corrected_json_str = re.sub(r",\s*([\}\]])", r"\1", corrected_json_str)
                    corrected_json_str = re.sub(r"//.*", "", corrected_json_str)
                    corrected_json_str = re.sub(r"/\*[\s\S]*?\*/", "", corrected_json_str)
                    collected_data = json.loads(corrected_json_str)
                except json.JSONDecodeError as e_json_corrected:
                    logger.error(f"Повторная ошибка парсинга JSON после исправления: {e_json_corrected}. Оригинальный output: {output}", exc_info=True)
                    pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
                    return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else output}

            # Проверка наличия ключевых полей
            required_keys = ["courier_id", "incident_description", "incident_date", "courier_had_shift_on_incident_date"]
            if not isinstance(collected_data, dict) or not all(key in collected_data for key in required_keys):
                missing_keys = [key for key in required_keys if key not in collected_data]
                logger.warning(f"В собранных данных отсутствуют ключевые поля ({missing_keys}) или структура не является словарем. Data: {collected_data}")
                pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
                msg_to_user = pre_marker_output if pre_marker_output else "Кажется, я не смог полностью извлечь детали инцидента. Пожалуйста, попробуйте описать еще раз."
                return {"status": "in_progress", "agent_message": msg_to_user}

            return {"status": "completed", "data": collected_data, "agent_message": "Информация собрана. Передаю для анализа и принятия решения."}
        except Exception as e:
            logger.error(f"Ошибка обработки JSON от InformationCollectorAgent: {e}. Output: {output}", exc_info=True)
            pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
            return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else output}
    else:
        return {"status": "in_progress", "agent_message": output}