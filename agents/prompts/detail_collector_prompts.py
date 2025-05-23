# agents/prompts/detail_collector_prompts.py
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

# --- Ключи для JSON-объекта, который агент должен вернуть ---
AGENT_JSON_RESULT_FIELDS = {
    "courier_id": "courier_id",
    "courier_name": "courier_name",
    "warehouse_id": "warehouse_id",
    "warehouse_name": "warehouse_name",
    "incident_description": "incident_description",
    "incident_date": "incident_date",
    "incident_time": "incident_time",
    "incident_location": "incident_location",
    "witnesses_info": "witnesses_info",
    "director_actions_taken": "director_actions_taken",
    "incident_consequences": "incident_consequences",
    "complaint_source_details": "complaint_source_details"
}

# --- Маркер не используется в этом подходе, т.к. агент сам решает, когда завершать ---
# DETAILS_COLLECTED_MARKER = "[DETAILS_COLLECTED_BY_AGENT_V4_PYTHON_LOGIC]"

# --- Конфигурация аспектов для сбора (управляется Python-кодом) ---
ASPECTS_TO_COLLECT_CONFIG = [
    {
        "id": "aspect_what_happened",
        "description_for_question_generation": "суть инцидента: что именно произошло, какие конкретно действия курьера были неправильными, и как это было замечено директором.",
        "target_json_fields": [AGENT_JSON_RESULT_FIELDS["incident_description"]],
        "is_critical": True,
        "json_extraction_keys_hint": {AGENT_JSON_RESULT_FIELDS["incident_description"]: "Полное описание инцидента со слов пользователя"}
    },
    {
        "id": "aspect_when",
        "description_for_question_generation": "точная дата и время (или временной промежуток), когда произошел инцидент.",
        "target_json_fields": [AGENT_JSON_RESULT_FIELDS["incident_date"], AGENT_JSON_RESULT_FIELDS["incident_time"]],
        "is_critical": True,
        "json_extraction_keys_hint": {
            AGENT_JSON_RESULT_FIELDS["incident_date"]: "Дата в формате YYYY-MM-DD (если пользователь указал 'сегодня', используй {current_date})",
            AGENT_JSON_RESULT_FIELDS["incident_time"]: "Время в формате HH:MM или текстовое описание (например, 'утром', 'около 14:00')"
        }
    },
    {
        "id": "aspect_where",
        "description_for_question_generation": "конкретное место, где произошел инцидент (например, на территории склада, на маршруте, у клиента по адресу...).",
        "target_json_fields": [AGENT_JSON_RESULT_FIELDS["incident_location"]],
        "is_critical": True,
        "json_extraction_keys_hint": {AGENT_JSON_RESULT_FIELDS["incident_location"]: "Описание места инцидента"}
    },
    {
        "id": "aspect_witnesses",
        "description_for_question_generation": "наличие свидетелей инцидента (если да, то кто именно - ФИО или должность).",
        "target_json_fields": [AGENT_JSON_RESULT_FIELDS["witnesses_info"]],
        "is_critical": False,
        "json_extraction_keys_hint": {AGENT_JSON_RESULT_FIELDS["witnesses_info"]: "Информация о свидетелях, либо 'нет', 'не знаю'"}
    },
    {
        "id": "aspect_actions_taken",
        "description_for_question_generation": "действия, которые предпринял директор или другие сотрудники сразу после того, как стало известно об инциденте.",
        "target_json_fields": [AGENT_JSON_RESULT_FIELDS["director_actions_taken"]],
        "is_critical": False,
        "json_extraction_keys_hint": {AGENT_JSON_RESULT_FIELDS["director_actions_taken"]: "Описание предпринятых действий, либо 'никаких'"}
    },
    {
        "id": "aspect_consequences",
        "description_for_question_generation": "негативные последствия инцидента (например, жалобы от клиентов, задержки в доставках, повреждение товара).",
        "target_json_fields": [AGENT_JSON_RESULT_FIELDS["incident_consequences"]],
        "is_critical": False,
        "json_extraction_keys_hint": {AGENT_JSON_RESULT_FIELDS["incident_consequences"]: "Описание последствий, либо 'нет', 'не знаю'"}
    },
    {
        "id": "aspect_complaint_details",
        "description_for_question_generation": "детали жалоб от клиентов (если они были упомянуты ранее): от кого поступили жалобы и в чем заключалась их суть.",
        "target_json_fields": [AGENT_JSON_RESULT_FIELDS["complaint_source_details"]],
        "is_critical": False,
        "depends_on_field_value": { # Условие для задания этого вопроса
            "field_key": AGENT_JSON_RESULT_FIELDS["incident_consequences"], # Проверяем это поле в collected_data
            "contains_keywords": ["жалоб", "клиент", "пожаловался", "недоволен"] # Если значение содержит эти слова
        },
        "json_extraction_keys_hint": {AGENT_JSON_RESULT_FIELDS["complaint_source_details"]: "Детали жалобы от клиента, если были"}
    }
]

# --- Промпт №1: Генерация текста вопроса ---
GENERATE_QUESTION_PROMPT_TEMPLATE = """
Ты — ИИ-ассистент. Твоя задача — помочь сформулировать вежливый и понятный вопрос для директора магазина.
Директор предоставляет информацию об инциденте с курьером.
Текущая дата: {current_date}.

ИСТОРИЯ ДИАЛОГА (предыдущие вопросы этого агента и ответы пользователя):
{dialog_history_str}

КОНТЕКСТ ИНЦИДЕНТА (уже известная информация от других агентов или первоначальная жалоба):
- Склад: {warehouse_name} (ID: {warehouse_id})
- Курьер: {courier_name} (ID: {courier_id})
- Первоначальное описание проблемы (если было): {initial_complaint}

УЖЕ СОБРАННЫЕ ДЕТАЛИ (ключ: значение):
{collected_data_str}

Нужно задать вопрос, чтобы узнать у директора о следующем аспекте инцидента:
"{aspect_description_for_question}"

СФОРМУЛИРУЙ ТОЛЬКО ТЕКСТ ВОПРОСА. Кратко, вежливо и по существу. Не добавляй приветствий или лишних фраз.
Учитывай уже собранные детали, чтобы вопрос был максимально релевантным и не повторял уже известное.
Например, если аспект "точное время и дата инцидента", а пользователь уже сказал "сегодня утром", уточни "Когда именно сегодня утром это произошло? Пожалуйста, укажите приблизительное время."
Если аспект "суть инцидента", а пользователь сказал "пьяный", уточни "Расскажите, пожалуйста, подробнее, как вы это определили (запах, поведение, речь)?".
"""

def get_generate_question_prompt(
        aspect_description_for_question: str,
        dialog_history: List[Dict[str, str]],
        scenario_context: Dict[str, Any],
        collected_data: Dict[str, Any],
        current_date: str
) -> str:
    history_str = "\n".join([f"- {msg['type'].upper()}: {msg['content']}" for msg in dialog_history]) \
        if dialog_history else "Пока это первый вопрос по деталям."

    collected_data_parts = []
    for key, value in collected_data.items():
        if value is not None:
            collected_data_parts.append(f"  - {key}: {value}")
    collected_data_str = "\n".join(collected_data_parts) if collected_data_parts else "Пока нет."

    warehouse_info = scenario_context.get("warehouse_info", {})
    courier_info = scenario_context.get("courier_info", {})

    return GENERATE_QUESTION_PROMPT_TEMPLATE.format(
        current_date=current_date,
        aspect_description_for_question=aspect_description_for_question,
        dialog_history_str=history_str,
        warehouse_name=warehouse_info.get("warehouse_name", "N/A"),
        warehouse_id=warehouse_info.get("warehouse_id", "N/A"),
        courier_name=courier_info.get("full_name", "N/A"),
        courier_id=courier_info.get("id", "N/A"),
        initial_complaint=scenario_context.get("initial_complaint", "не указана"),
        collected_data_str=collected_data_str
    )

# --- Промпт №2: Извлечение данных из ответа пользователя ---
EXTRACT_DATA_PROMPT_TEMPLATE = """
Ты — ИИ-аналитик. Твоя задача — извлечь структурированную информацию из ответа пользователя на заданный вопрос.
Текущая дата: {current_date}. Если пользователь говорит "сегодня", это означает {current_date}. Если "вчера" - {yesterday_date}.

Контекст: Директору магазина был задан вопрос об инциденте с курьером.
Заданный вопрос: "{question_asked_to_user}"
Ответ пользователя: "{user_reply_text}"

Из ответа пользователя извлеки информацию для следующих полей. Верни JSON-объект.
Ключи в JSON должны быть ТОЛЬКО из этого списка: {json_keys_for_extraction_str}
Если информация для какого-то ключа в ответе отсутствует, неясна или пользователь ответил "не знаю", "никаких", "нет", используй значение `null` для этого ключа в JSON.
Если пользователь дал конкретный негативный ответ (например, "свидетелей не было", "последствий нет"), старайся отразить это в значении (например, "нет", "отсутствуют"), а не просто `null`.

Пример формата значений для ключей (если применимо):
{json_extraction_keys_hint_str}

ВАЖНО: Твой ответ должен быть ТОЛЬКО JSON-объектом и ничем больше. Не добавляй никаких объяснений или ```json ```.
"""

def get_extract_data_prompt(
        question_asked_to_user: str,
        user_reply_text: str,
        target_json_fields: List[str],
        json_extraction_keys_hint: Dict[str, str], # Подсказки для LLM о формате значений
        current_date: str,
        yesterday_date: str
) -> str:

    json_keys_for_extraction_str = ", ".join([f'"{k}"' for k in target_json_fields])

    hint_parts = []
    for key, hint_text in json_extraction_keys_hint.items():
        if key in target_json_fields: # Убедимся, что подсказка для нужного поля
            hint_parts.append(f"  - \"{key}\": \"{hint_text.replace('{current_date}', current_date)}\"") # Подставляем current_date в подсказку
    json_extraction_keys_hint_str = "\n".join(hint_parts) if hint_parts else "Нет специальных подсказок по формату."


    return EXTRACT_DATA_PROMPT_TEMPLATE.format(
        current_date=current_date,
        yesterday_date=yesterday_date,
        question_asked_to_user=question_asked_to_user,
        user_reply_text=user_reply_text,
        json_keys_for_extraction_str=json_keys_for_extraction_str,
        json_extraction_keys_hint_str=json_extraction_keys_hint_str
    )