# agents/information_collector_agent.py
import re
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from typing import List, Dict, Any
import json
import logging
from datetime import datetime

from config import OPENAI_API_KEY
from llm_services import get_llm
from tools.tool_definitions import collector_tools # Убедимся, что find_warehouse_tool здесь

logger = logging.getLogger(__name__)

COLLECTOR_SYSTEM_PROMPT = """
Ты - ИИ-агент, первая линия поддержки для директоров магазинов. Твоя задача - собрать ВСЮ НЕОБХОДИМУЮ И ПОДРОБНУЮ информацию об инциденте с курьером.
Сегодня %s. Помни эту дату, если пользователь говорит "сегодня".

Ты будешь действовать поэтапно. Информация, собранная на предыдущих этапах, ДОЛЖНА использоваться на последующих.

.  **Начало / Запрос информации о складе:**
    а. Поприветствуй директора.
    б. **ВАЖНО: Если `confirmed_warehouse_id` уже был тобой установлен и подтвержден пользователем на одном из предыдущих шагов этого диалога, НЕ СПРАШИВАЙ О СКЛАДЕ СНОВА. Сразу переходи к ЭТАПУ 2.**
    в. Если `confirmed_warehouse_id` ЕЩЕ НЕ установлен:
        - Если пользователь в своем текущем сообщении УЖЕ УКАЗАЛ название или ID склада (например, "проблема на складе W1" или "на Центральном складе инцидент"):
            - Переходи сразу к пункту 2, используя предоставленный пользователем идентификатор.
        - В противном случае (если пользователь не указал склад):
            - Спроси у пользователя: "Пожалуйста, укажите название или ID вашего склада, чтобы я мог вам помочь."
            - Жди ответа пользователя. После получения ответа, переходи к пункту 2.

2.  **Поиск склада по названию/ID, введенному пользователем:**
    а. Возьми название или ID склада, которое предоставил пользователь.
    б. **ОБЯЗАТЕЛЬНО используй инструмент `find_warehouse_by_name_or_id`** с этим идентификатором.
    в. Обработка результата `find_warehouse_by_name_or_id`:
        i.  Если **однозначно найден склад** (есть `warehouse_info`):
            - Предложи пользователю: "Найден склад: [название склада из ответа инструмента] (ID: [ID из ответа инструмента]). Это ваш склад? (да/нет)".
            - Если пользователь отвечает 'да': Установи `confirmed_warehouse_id` и `confirmed_warehouse_name`. Сообщи "Склад подтвержден." Переходи к ЭТАПУ 2.
            - Если пользователь отвечает 'нет': Сообщи "Хорошо, попробуем еще раз. Укажите другое название или ID склада." Жди ответа и вернись к пункту 2.а.
            - Если ответ нечеткий: Повтори вопрос о подтверждении.
        ii. Если **склад не найден**:
            - Сообщи: "К сожалению, склад '[идентификатор]' не найден. Проверьте ввод или попробуйте другое название/ID." Жди ответа и вернись к пункту 2.а.
        iii.Если **найдено несколько складов**:
            - Передай сообщение от инструмента (список кандидатов). Попроси уточнить. После ответа пользователя, вернись к пункту 2.а с уточненным идентификатором.

**ЭТАП 2: ИДЕНТИФИКАЦИЯ И ПОДТВЕРЖДЕНИЕ КУРЬЕРА (требует `confirmed_warehouse_id`)**
Отслеживай `confirmed_courier_id` и `confirmed_courier_name`.

1.  **Убедись, что `confirmed_warehouse_id` определен. НЕ СПРАШИВАЙ О СКЛАДЕ СНОВА НА ЭТОМ ЭТАПЕ.**
    Сообщи: "Теперь, когда склад [confirmed_warehouse_name] определен, давайте найдем курьера."
2.  **ВАЖНО: Если `confirmed_courier_id` уже был тобой установлен и подтвержден пользователем на одном из предыдущих шагов этого диалога, НЕ СПРАШИВАЙ О КУРЬЕРЕ СНОВА. Сразу переходи к ЭТАПУ 3.**
3.  Если `confirmed_courier_id` ЕЩЕ НЕ установлен: Запроси ФИО/ID курьера.
4.  Используй `search_courier` с идентификатором и `confirmed_warehouse_id`.
5.  Обработка результата:
    а. **Однозначно найден:** Предложи: "Найден курьер: [ФИО] (ID: [ID]). Это он? (да/нет)".
        - Если 'да': Установи `confirmed_courier_id`, `confirmed_courier_name`. Сообщи "Курьер подтвержден." Переходи к ЭТАПУ 3.
        - Если 'нет': Сообщи "Понял. Уточните ФИО/ID." Вернись к пункту 3.
    б. **Не найден / Найдено несколько:** Обработай аналогично складу, возвращаясь к пункту 3.

**ЭТАП 3: СБОР ДЕТАЛЕЙ ИНЦИДЕНТА И ФОРМИРОВАНИЕ JSON (требует ПОДТВЕРЖДЕННЫХ данных о складе и курьере)**
1.  **Подтверждение контекста:** Начни этот этап с фразы, подтверждающей собранные данные о курьере и складе.
2.  **Детализация Описания Инцидента (Что именно произошло? Ключевой шаг!):**
    а. Получи от пользователя первоначальное описание инцидента.
    б. **Твоя главная задача на этом шаге — добиться МАКСИМАЛЬНОЙ ДЕТАЛИЗАЦИИ.** Задавай последовательные уточняющие вопросы, пока не получишь ясную и подробную картину. (Примеры вопросов для разных ситуаций остаются те же).
    в. **Не переходи к следующему пункту (Время и Дата), пока не будешь удовлетворен полнотой описания самого инцидента.** Если пользователь дает краткие ответы, продолжай задавать уточняющие вопросы по сути происшествия.
3.  **Время и Дата инцидента (Когда?):**
    а. Уточни точную дату инцидента (ГГГГ-ММ-ДД).
    б. **Получи максимально точное время или временной диапазон.** Уточняй общие ответы типа "до обеда".
4.  **Место инцидента (Где?):**
    а. Уточни место. Если "на лавке", уточни отношение к работе/складу.
5.  **Проверка смены курьера:**
    а. Если `courier_id` известен, используй `get_courier_shifts`.
6.  **Поиск нарушенных инструкций:**
    а. Используй `query_knowledge_base` с `collection_name="courier_job_description"` и детализированным описанием нарушения. Результат этого инструмента будет списком объектов, каждый с полями "text" и "source".
7.  **Финальное Резюме и Формирование JSON:**
    а. **Только когда ты УВЕРЕН, что собрал ВСЕ необходимые детали по всем пунктам (особенно по описанию инцидента, времени и месту),** подготовь краткое человекочитаемое резюме собранной информации для пользователя. Это резюме НЕ должно содержать сам JSON-объект или фразы типа "Теперь я формирую JSON".
    б. **Сразу после этого резюме, на новой строке, ОБЯЗАТЕЛЬНО поставь специальный маркер `[INFO_COLLECTED]`**.
    в. **Сразу ПОСЛЕ маркера `[INFO_COLLECTED]` (и ничего другого перед ним на той же строке, и ничего другого после JSON на той же строке) должен следовать JSON-объект в одну или несколько строк.** Этот JSON предназначен только для системы.
    г. Структура JSON: `courier_id`, `courier_name`, `warehouse_id`, `warehouse_name`, `incident_description` (ПОДРОБНОЕ описание), `incident_date`, `incident_time`, `courier_had_shift_on_incident_date`, `shift_details` (список объектов или `[]`), `job_instruction_extracts` (это должен быть **список объектов**, где каждый объект имеет ключ `text` со строковым значением выдержки из инструкции, и ключ `source` с источником. Если инструмент `query_knowledge_base` вернул несколько таких объектов, все они должны быть в этом списке. Если ничего не вернул, это должен быть пустой список `[]`).
    д. Формат каждого элемента в списке `job_instruction_extracts` должен быть таким: один объект содержит поле `text` с текстом пункта инструкции и поле `source` с указанием источника. Например, если есть два пункта, список будет содержать два таких объекта.

Общие правила: Не принимай решений о наказаниях. Будь настойчив в сборе деталей. Не завершай сбор информации (не ставь маркер `[INFO_COLLECTED]`), пока не получишь исчерпывающих ответов на свои уточняющие вопросы по сути инцидента.
История чата (`chat_history`) будет предоставлена.
""" % (datetime.now().strftime("%Y-%m-%d"))

llm  = get_llm(temperature=0.1)

try:
    collector_prompt = ChatPromptTemplate.from_messages([
        ("system", COLLECTOR_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    logger.info(f"InformationCollectorAgent: `collector_prompt` создан. `input_variables` = {collector_prompt.input_variables}")
    if any(var_name.startswith('"') or ('success' in var_name.lower() and var_name != 'input') for var_name in collector_prompt.input_variables):
        logger.error(f"ОШИБКА КОНФИГУРАЦИИ ПРОМПТА: Обнаружены некорректные переменные в collector_prompt.input_variables: {collector_prompt.input_variables}")

except Exception as e:
    logger.error(f"Ошибка при создании ChatPromptTemplate для коллектора информации: {e}", exc_info=True)
    raise

collector_agent_runnable = create_openai_functions_agent(llm, collector_tools, collector_prompt)

collector_agent_executor = AgentExecutor(
    agent=collector_agent_runnable,
    tools=collector_tools,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=30,
    return_intermediate_steps=False
)

async def run_information_collector(user_input: str, chat_history: List[Dict[str, str]], director_login: str = None) -> Dict[str, Any]:
    agent_input_text = user_input

    langchain_chat_history = []
    for msg in chat_history:
        if msg.get("type") == "human":
            langchain_chat_history.append(HumanMessage(content=msg["content"]))
        elif msg.get("type") == "ai":
            langchain_chat_history.append(AIMessage(content=msg["content"]))

    input_for_agent = agent_input_text
    if director_login:
        input_for_agent = f"[Логин директора: {director_login}] {agent_input_text}"

    agent_invocation_input = {"input": input_for_agent}
    if langchain_chat_history:
        agent_invocation_input["chat_history"] = langchain_chat_history

    logger.info(f"Запуск InformationCollectorAgent с input: '{input_for_agent}' и историей из {len(langchain_chat_history)} сообщений.")
    logger.debug(f"Ключи, передаваемые в agent_executor.ainvoke: {list(agent_invocation_input.keys())}")

    try:
        response = await collector_agent_executor.ainvoke(agent_invocation_input)
    except Exception as e:
        logger.error(f"Критическая ошибка при вызове collector_agent_executor: {e}", exc_info=True)
        if isinstance(e, KeyError) and hasattr(collector_prompt, 'input_variables'):
            logger.error(f"Ожидаемые переменные промптом collector_prompt: {collector_prompt.input_variables}")
            logger.error(f"Переданный input в executor (ключи): {list(agent_invocation_input.keys())}")
            error_message_str = str(e)
            missing_vars_in_error = re.findall(r"missing variables {([^}]+)}", error_message_str)
            if missing_vars_in_error:
                logger.error(f"Ошибка KeyError указывает на отсутствующие переменные: {missing_vars_in_error[0]}")
        return {"status": "error", "agent_message": f"Произошла внутренняя ошибка при обработке вашего запроса агентом сбора информации: {type(e).__name__} - {e}. Пожалуйста, попробуйте сформулировать запрос иначе или сообщите администратору."}

    output = response.get("output", "Извините, не удалось обработать ваш запрос. Попробуйте еще раз.")
    logger.info(f"InformationCollectorAgent вернул ответ: {output[:500]}...")

    # Улучшенная проверка на "in_progress"
    is_clarification_request = (
            "уточните" in output.lower() or
            "какой склад" in output.lower() or
            "какой курьер" in output.lower() or
            "не найден" in output.lower() or # Если это не финальное сообщение об ошибке
            "Найдено несколько" in output or # Общая фраза для нескольких кандидатов
            "несколько подходящих" in output.lower()
    )

    if is_clarification_request and "[INFO_COLLECTED]" not in output:
        logger.info("Ответ агента содержит запрос на уточнение или сообщение об ошибке поиска и не является финальным. Возвращаем как in_progress.")
        return {"status": "in_progress", "agent_message": output}

    if "[INFO_COLLECTED]" in output:
        try:
            # ... (остальная логика парсинга JSON без изменений) ...
            raw_json_payload = output.split("[INFO_COLLECTED]", 1)[1]
            json_match = re.search(r"```json\s*([\s\S]*?)\s*```|```\s*([\s\S]*?)\s*```|(\{[\s\S]*\}|\[[\s\S]*\])", raw_json_payload, re.DOTALL)
            json_part = ""
            if json_match:
                json_part = next(filter(None, json_match.groups()), None)

            if not json_part:
                logger.warning(f"Не удалось извлечь JSON с помощью regex из: '{raw_json_payload[:100]}...'. Пробуем простой поиск начала JSON.")
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
                    if end_index != -1:
                        json_part = json_candidate[:end_index+1].strip()
                    else:
                        json_part = json_candidate.strip()
                else:
                    logger.error(f"Не удалось найти начало JSON ('{{' или '[') в '{raw_json_payload[:100]}...'")

            if not json_part:
                logger.error(f"Не удалось извлечь JSON-строку из output после [INFO_COLLECTED]. Output: {output[:300]}...")
                pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
                return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else "Агент вернул данные в неверном формате. Попробуйте уточнить запрос."}

            json_part = json_part.strip()
            logger.debug(f"Извлеченная строка для парсинга JSON: '{json_part}'")

            try:
                collected_data = json.loads(json_part)
            except json.JSONDecodeError as e_json:
                logger.warning(f"Ошибка парсинга JSON ({e_json}) от LLM, пробую исправить: {json_part[:100]}...")
                try:
                    corrected_json_str = json_part.replace("'", '"').replace("None", "null").replace("True", "true").replace("False", "false")
                    corrected_json_str = re.sub(r",\s*([\}\]])", r"\1", corrected_json_str)
                    corrected_json_str = re.sub(r"//.*", "", corrected_json_str)
                    corrected_json_str = re.sub(r"/\*[\s\S]*?\*/", "", corrected_json_str)
                    collected_data = json.loads(corrected_json_str)
                    logger.info("JSON успешно распарсен после исправлений.")
                except json.JSONDecodeError as e_json_corrected:
                    logger.error(f"Повторная ошибка парсинга JSON после исправления: {e_json_corrected}. Оригинальный output: {output[:300]}...", exc_info=True)
                    pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
                    return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else output}

            all_expected_keys = [
                "courier_id", "courier_name", "warehouse_id", "warehouse_name",
                "incident_description", "incident_date", "incident_time",
                "courier_had_shift_on_incident_date", "shift_details", "job_instruction_extracts"
            ]

            if not isinstance(collected_data, dict) or not all(key in collected_data for key in all_expected_keys):
                missing_keys = [key for key in all_expected_keys if key not in collected_data] if isinstance(collected_data, dict) else all_expected_keys
                logger.warning(f"В собранных данных отсутствуют некоторые ключевые поля ({missing_keys}) или структура не является словарем. Data: {str(collected_data)[:200]}...")
                pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
                return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else "Кажется, я не смог полностью извлечь детали инцидента. Пожалуйста, попробуйте описать ситуацию еще раз, возможно, более подробно."}

            # Проверка, что warehouse_id не null, если инцидент требует его
            if collected_data.get("warehouse_id") is None and "курьер" in collected_data.get("incident_description","").lower() : # Примерная проверка
                logger.warning(f"Собранные данные не содержат warehouse_id, хотя он может быть нужен. Data: {str(collected_data)[:200]}...")
                # Можно вернуть in_progress, если это критично
                # return {"status": "in_progress", "agent_message": "Не удалось определить склад. Пожалуйста, уточните информацию о складе."}


            logger.info("Информация успешно собрана и распарсена.")
            agent_response_before_marker = output.split("[INFO_COLLECTED]", 1)[0].strip()
            final_agent_message = agent_response_before_marker if agent_response_before_marker else "Информация собрана. Передаю для анализа и принятия решения."

            return {"status": "completed", "data": collected_data, "agent_message": final_agent_message}

        except Exception as e:
            logger.error(f"Неожиданная ошибка при обработке JSON-части от InformationCollectorAgent: {e}. Output: {output[:300]}...", exc_info=True)
            pre_marker_output = output.split("[INFO_COLLECTED]",1)[0].strip()
            return {"status": "in_progress", "agent_message": pre_marker_output if pre_marker_output else output}
    else:
        return {"status": "in_progress", "agent_message": output}