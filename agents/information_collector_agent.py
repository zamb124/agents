import re
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage # AIMessage для истории
from typing import List, Dict, Any
import json
import logging
from datetime import datetime

from config import OPENAI_API_KEY
from tools.tool_definitions import collector_tools # Убедимся, что тут все нужные тулзы

logger = logging.getLogger(__name__)

COLLECTOR_SYSTEM_PROMPT = """
Ты - ИИ-агент, первая линия поддержки для директоров магазинов. Твоя задача - собрать всю необходимую информацию об инциденте с курьером.
Сегодня %s. Помни эту дату, если пользователь говорит "сегодня".
Ты должен:
1.  Вежливо поприветствовать директора, если это начало диалога или новый вопрос.
2.  Если неясно, о каком курьере идет речь, ЗАДАЙ УТОЧНЯЮЩИЙ ВОПРОС, чтобы получить ФИО или ID курьера. Используй инструмент `search_courier` для проверки существования курьера и получения его точного ID и ФИО. Если курьер не найден, сообщи об этом и уточни данные.
3.  Если директор не указал свой склад, а это необходимо для контекста, ты можешь попытаться определить склад по его логину (telegram username), который будет в начале сообщения пользователя в формате `[Логин директора: ...]`. Используй инструмент `get_warehouse_by_director_login`. Если не удалось определить или логина нет, уточни у директора название или ID склада.
4.  Собери всю необходимую информацию об инциденте:
    - Что именно произошло (суть нарушения, например, "курьер пришел пьяный", "не вышел на смену").
    - Когда (дата инцидента в формате ГГГГ-ММ-ДД, время, если применимо). Если директор говорит "сегодня", "только что", "вчера" - используй текущую дату (%s) или вчерашнюю для расчета. Если дата не ясна, ОБЯЗАТЕЛЬНО уточни ее.
    - Где (название склада или адрес, если применимо. Часто это будет склад директора).
5.  После идентификации курьера и получения даты инцидента, используй инструмент `get_courier_shifts` с ID курьера и датой инцидента (в формате ГГГГ-ММ-ДД), чтобы проверить, была ли у курьера смена в этот день. Запомни результат этой проверки (`courier_had_shift_on_incident_date`: true/false) и детали смены (`shift_details`), если она была.
6.  Используй инструмент `query_knowledge_base` с `collection_name="courier_job_description"`, чтобы найти пункты ДОЛЖНОСТНОЙ ИНСТРУКЦИИ КУРЬЕРА, которые могли быть нарушены. Передай краткое описание нарушения (например, "пьяный на работе", "не вышел на смену") в `query_text` этого инструмента. Результат сохрани в `job_instruction_extracts`.
7.  Твоя конечная цель - собрать полный пакет ФАКТОВ об инциденте. Когда ты считаешь, что вся необходимая информация собрана (ID курьера, ФИО курьера, описание инцидента, дата инцидента, время (если есть), информация о смене курьера на эту дату, информация о складе, выдержки из ДОЛЖНОСТНОЙ ИНСТРУКЦИИ), ты должен СФОРМУЛИРОВАТЬ ИТОГОВЫЙ ОТВЕТ, содержащий специальный маркер `[INFO_COLLECTED]` и ПОСЛЕ него JSON-объект со всей собранной информацией. Не пиши ничего после JSON.
    Структура JSON должна включать как минимум следующие поля: 
    `courier_id` (строка или null), `courier_name` (строка или null), 
    `warehouse_id` (строка или null), `warehouse_name` (строка или null), 
    `incident_description` (строка, подробное описание от директора), 
    `incident_date` (строка ГГГГ-ММ-ДД), `incident_time` (строка ЧЧ:ММ или null), 
    `courier_had_shift_on_incident_date` (boolean), `shift_details` (объект с деталями смены или null, если смены не было или не найдена), 
    `job_instruction_extracts` (список объектов, каждый с полями 'text' и 'source', или пустой список).
8.  Не принимай никаких решений о наказаниях и не ищи методические рекомендации саппорта! Твоя задача ТОЛЬКО сбор фактов и поиск нарушенных пунктов ДОЛЖНОСТНОЙ ИНСТРУКЦИИ.
9.  Веди диалог естественно. Если информации достаточно для формирования JSON, не задавай лишних вопросов. Сразу выводи `[INFO_COLLECTED]` и JSON.
10. Если пользователь просто здоровается или задает общий вопрос, не связанный с инцидентом, отвечай вежливо и жди описания проблемы. Не пытайся сразу собирать JSON.

История чата (`chat_history`) будет предоставлена. Используй ее для контекста.
""" % (datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))

# Инициализируем LLM
llm = ChatOpenAI(model="gpt-4o", temperature=0.1, openai_api_key=OPENAI_API_KEY)

try:
    collector_prompt = ChatPromptTemplate.from_messages([
        ("system", COLLECTOR_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history", optional=True), # История чата, может быть пустой
        ("human", "{input}"), # Текущее сообщение пользователя
        MessagesPlaceholder(variable_name="agent_scratchpad"), # Для "мыслей" агента
    ])
    logger.info(f"Создан `collector_prompt`. Ожидаемые переменные: {collector_prompt.input_variables}")
except Exception as e:
    logger.error(f"Ошибка при создании ChatPromptTemplate для коллектора информации: {e}", exc_info=True)
    raise

# Создаем runnable агента
collector_agent_runnable = create_openai_functions_agent(llm, collector_tools, collector_prompt)

# Создаем executor для агента
collector_agent_executor = AgentExecutor(
    agent=collector_agent_runnable,
    tools=collector_tools,
    verbose=True, # Полезно для отладки
    handle_parsing_errors=True, # Автоматическая обработка ошибок парсинга
    max_iterations=10, # Макс. количество шагов агента (вызовов тулзов)
    return_intermediate_steps=False # Нам нужен только финальный ответ
)

async def run_information_collector(user_input: str, chat_history: List[Dict[str, str]], director_login: str = None) -> Dict[str, Any]:
    """
    Запускает агента для сбора информации об инциденте.
    `user_input`: текущее сообщение от пользователя.
    `chat_history`: список предыдущих сообщений в диалоге.
    `director_login`: логин директора из Telegram.
    Возвращает словарь с результатами работы агента.
    """
    agent_input_text = user_input

    # Преобразуем историю чата в формат, понятный Langchain
    langchain_chat_history = []
    for msg in chat_history:
        if msg.get("type") == "human":
            langchain_chat_history.append(HumanMessage(content=msg["content"]))
        elif msg.get("type") == "ai":
            langchain_chat_history.append(AIMessage(content=msg["content"]))

    agent_invocation_input = {"input": agent_input_text, "chat_history": langchain_chat_history}

    # Добавляем логин директора в начало сообщения, если он есть
    if director_login:
        agent_invocation_input["input"] = f"[Логин директора: {director_login}] {user_input}"

    logger.info(f"Запуск InformationCollectorAgent с input: '{agent_invocation_input['input']}' и историей из {len(langchain_chat_history)} сообщений.")
    # logger.debug(f"Полный input для collector_agent_executor.ainvoke: {json.dumps(agent_invocation_input, indent=2, ensure_ascii=False, default=str)}") # Очень длинный лог может быть

    try:
        response = await collector_agent_executor.ainvoke(agent_invocation_input)
    except Exception as e: # Более общий перехват на случай непредвиденного
        logger.error(f"Критическая ошибка при вызове collector_agent_executor: {e}", exc_info=True)
        if isinstance(e, KeyError) and hasattr(collector_prompt, 'input_variables'):
            logger.error(f"Ожидаемые переменные промптом collector_prompt: {collector_prompt.input_variables}")
        logger.error(f"Переданный input в executor (ключи): {list(agent_invocation_input.keys())}")
        return {"status": "error", "agent_message": f"Произошла внутренняя ошибка при обработке вашего запроса агентом сбора информации: {e}. Пожалуйста, попробуйте сформулировать запрос иначе или сообщите администратору."}

    output = response.get("output", "Извините, не удалось обработать ваш запрос. Попробуйте еще раз.")
    logger.info(f"InformationCollectorAgent вернул ответ: {output[:300]}...") # Логируем начало ответа

    if "[INFO_COLLECTED]" in output:
        try:
            # Извлекаем часть строки после маркера
            raw_json_payload = output.split("[INFO_COLLECTED]", 1)[1]

            # Пытаемся найти JSON, даже если он в блоке кода ```json ... ```
            json_match = re.search(r"```json\s*([\s\S]*?)\s*```|```\s*([\s\S]*?)\s*```|(\{[\s\S]*\}|\[[\s\S]*\])", raw_json_payload, re.DOTALL)
            json_part = ""
            if json_match:
                # Берем первую непустую группу из найденного
                json_part = next(filter(None, json_match.groups()), None)

            if not json_part: # Если regex не сработал, пробуем наивный поиск
                logger.warning(f"Не удалось извлечь JSON с помощью regex из: '{raw_json_payload[:100]}...'. Пробуем простой поиск начала JSON.")
                start_brace = raw_json_payload.find('{')
                start_bracket = raw_json_payload.find('[')
                start_index = -1

                if start_brace != -1 and start_bracket != -1: start_index = min(start_brace, start_bracket)
                elif start_brace != -1: start_index = start_brace
                elif start_bracket != -1: start_index = start_bracket

                if start_index != -1:
                    json_candidate = raw_json_payload[start_index:]
                    # Пытаемся найти соответствующую закрывающую скобку (очень упрощенно)
                    # Для сложных JSON это может не сработать идеально
                    end_brace = json_candidate.rfind('}')
                    end_bracket = json_candidate.rfind(']')
                    end_index = max(end_brace, end_bracket)
                    if end_index != -1:
                        json_part = json_candidate[:end_index+1].strip()
                    else:
                        json_part = json_candidate.strip() # Если не нашли закрывающую, берем все до конца
                else:
                    logger.error(f"Не удалось найти начало JSON ('{{' или '[') в '{raw_json_payload[:100]}...'")

            if not json_part:
                logger.error(f"Не удалось извлечь JSON-строку из output после [INFO_COLLECTED]. Output: {output[:300]}...")
                pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip() # Текст до маркера
                # Возвращаем текст до маркера, если он есть, иначе стандартное сообщение
                return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else "Агент вернул данные в неверном формате. Попробуйте уточнить запрос."}

            json_part = json_part.strip() # Убираем лишние пробелы
            logger.debug(f"Извлеченная строка для парсинга JSON: '{json_part}'")

            try:
                collected_data = json.loads(json_part)
            except json.JSONDecodeError as e_json:
                logger.warning(f"Ошибка парсинга JSON ({e_json}) от LLM, пробую исправить некоторые частые проблемы: {json_part[:100]}...")
                try:
                    # Попытки "подлечить" JSON от LLM
                    corrected_json_str = json_part.replace("'", '"').replace("None", "null").replace("True", "true").replace("False", "false")
                    corrected_json_str = re.sub(r",\s*([\}\]])", r"\1", corrected_json_str) # Убрать висячие запятые
                    corrected_json_str = re.sub(r"//.*", "", corrected_json_str) # Убрать однострочные комментарии //
                    corrected_json_str = re.sub(r"/\*[\s\S]*?\*/", "", corrected_json_str) # Убрать многострочные комментарии /* ... */
                    collected_data = json.loads(corrected_json_str)
                    logger.info("JSON успешно распарсен после исправлений.")
                except json.JSONDecodeError as e_json_corrected:
                    logger.error(f"Повторная ошибка парсинга JSON после попытки исправления: {e_json_corrected}. Оригинальный output: {output[:300]}...", exc_info=True)
                    pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
                    return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else output} # Возвращаем как есть или часть до маркера

            # Проверка наличия ключевых полей (можно расширить)
            required_keys = ["courier_id", "incident_description", "incident_date", "courier_had_shift_on_incident_date", "job_instruction_extracts"]
            if not isinstance(collected_data, dict) or not all(key in collected_data for key in required_keys):
                missing_keys = [key for key in required_keys if key not in collected_data] if isinstance(collected_data, dict) else required_keys
                logger.warning(f"В собранных данных отсутствуют некоторые ключевые поля ({missing_keys}) или структура не является словарем. Data: {str(collected_data)[:200]}...")
                pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
                return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else "Кажется, я не смог полностью извлечь детали инцидента. Пожалуйста, попробуйте описать ситуацию еще раз, возможно, более подробно."}

            # Если все ок, возвращаем статус "completed" и собранные данные
            logger.info("Информация успешно собрана и распарсена.")
            return {"status": "completed", "data": collected_data, "agent_message": "Информация собрана. Передаю для анализа и принятия решения."}

        except Exception as e: # Если любая другая ошибка при обработке JSON
            logger.error(f"Неожиданная ошибка при обработке JSON-части от InformationCollectorAgent: {e}. Output: {output[:300]}...", exc_info=True)
            pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
            return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else output} # Возвращаем как есть или часть до маркера
    else:
        # Если маркера [INFO_COLLECTED] нет, значит агент продолжает диалог или задает уточняющие вопросы
        return {"status": "in_progress", "agent_message": output}