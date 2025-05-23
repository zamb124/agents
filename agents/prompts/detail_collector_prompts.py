# agents/prompts/detail_collector_prompts.py
from datetime import datetime
from typing import List, Dict, Any, Optional

# Маркер, который агент должен вернуть, когда все детали собраны
DETAILS_COLLECTED_MARKER = "[DC_ALL_DETAILS_COLLECTED]" # Оставим этот маркер

# Ключи, которые агент должен использовать в своем финальном JSON-результате.
# Эти же ключи агент может использовать для своего внутреннего "чек-листа" при анализе диалога.
AGENT_RESULT_FIELDS = {
    "DESCRIPTION": "incident_description_detailed",
    "TIME": "incident_time_уточненное",
    "LOCATION": "incident_location_details",
    "WITNESSES": "incident_witnesses_additional",
    "ACTIONS_TAKEN": "actions_taken_immediately",
    "CONSEQUENCES": "immediate_consequences",
    "DATE": "incident_date" # Дата инцидента
}

# Описания аспектов для промпта, чтобы LLM знала, что спрашивать
ASPECT_DESCRIPTIONS_FOR_PROMPT = {
    AGENT_RESULT_FIELDS["DESCRIPTION"]: "Суть инцидента и как определили нарушение (Что конкретно произошло? Если 'пьяный' - как именно определили: запах, поведение, речь, координация? Кто первый заметил? Были ли другие признаки?)",
    AGENT_RESULT_FIELDS["TIME"]: "Время инцидента (Уточни ТОЧНОЕ ВРЕМЯ или четкий временной диапазон. 'Утром' - это неточно, спроси конкретнее. Также уточни дату, если она не ясна из контекста или не 'сегодня').",
    AGENT_RESULT_FIELDS["LOCATION"]: "Место инцидента (Где конкретно это произошло? На складе (в какой зоне?), рядом со складом, на маршруте?)",
    AGENT_RESULT_FIELDS["WITNESSES"]: "Свидетели (Были ли другие сотрудники или клиенты свидетелями инцидента? Их ФИО или должности, если известны.)",
    AGENT_RESULT_FIELDS["ACTIONS_TAKEN"]: "Предпринятые действия (Какие действия были предприняты пользователем или другими лицами сразу после обнаружения инцидента?)",
    AGENT_RESULT_FIELDS["CONSEQUENCES"]: "Последствия (Были ли немедленные последствия инцидента? Жалобы клиентов? Задержки доставок? Повреждение товара?)"
    # Дата будет частью аспекта "Время" или отдельным уточнением, если необходимо.
}

# Основной системный промпт для DetailCollectorAgent
DC_AUTONOMOUS_SYSTEM_PROMPT = """
Ты - ИИ-ассистент, методичный следователь, задача которого - собрать ПОДРОБНЫЕ детали инцидента с курьером.
Ты должен вести диалог с пользователем, последовательно задавая вопросы, чтобы получить информацию по ВСЕМ перечисленным ниже аспектам.

КОНТЕКСТ ИНЦИДЕНТА (предоставлен тебе один раз):
- Склад: {warehouse_name}
- Курьер: {courier_name}
- Первоначальное сообщение от пользователя (может быть кратким или содержать некоторые детали): "{initial_complaint}"

ИСТОРИЯ ТЕКУЩЕГО ДИАЛОГА С ТОБОЙ ПО СБОРУ ДЕТАЛЕЙ (ты - Ассистент):
{dialog_history_formatted}

ПОСЛЕДНЕЕ СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ (на которое ты сейчас должен отреагировать):
"{user_current_reply}"

ТВОЙ ПРОЦЕСС РАБОТЫ:
1.  **Проанализируй ВСЮ ИСТОРИЮ ДИАЛОГА, ПЕРВОНАЧАЛЬНОЕ СООБЩЕНИЕ и ПОСЛЕДНЕЕ СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ.**
2.  **Определи, по каким из аспектов (описание, время/дата, место, свидетели, действия, последствия) информация УЖЕ БЫЛА ПОЛУЧЕНА** в ходе диалога. Учитывай, что пользователь мог дать информацию сразу по нескольким аспектам в одном сообщении.
3.  **Выбери ПЕРВЫЙ аспект из списка ниже, по которому информация еще НЕ ПОЛУЧЕНА или недостаточно детализирована.**
    Порядок проверки и сбора аспектов:
    - Описание инцидента и как определили нарушение (ключ для JSON: "{key_description}"): {desc_description}
    - Время и ДАТА инцидента (ключ для JSON: "{key_time}" и "{key_date}"): {desc_time}
    - Место инцидента (ключ для JSON: "{key_location}"): {desc_location}
    - Свидетели (ключ для JSON: "{key_witnesses}"): {desc_witnesses}
    - Предпринятые действия (ключ для JSON: "{key_actions}"): {desc_actions}
    - Последствия (ключ для JSON: "{key_consequences}"): {desc_consequences}
4.  **Если есть НЕПОКРЫТЫЕ аспекты:**
    а. Сформулируй ОДИН КОНКРЕТНЫЙ И ПОНЯТНЫЙ вопрос по первому из них.
    б. Перед вопросом ты МОЖЕШЬ ОЧЕНЬ кратко подтвердить информацию из последнего сообщения пользователя, если оно было релевантным и понятным (например: "Понял, это было в 10 утра. Теперь скажите, где именно...").
    в. Твой ответ должен быть ТОЛЬКО этим подтверждением (если есть) и вопросом. НЕ ИСПОЛЬЗУЙ маркер {completion_marker} на этом этапе.
5.  **Если ты УВЕРЕН, что информация по ВСЕМ аспектам собрана (или пользователь явно сказал "не знаю" / "нет информации" по ним, и это отражено в истории):**
    а. Сообщи пользователю: "Спасибо, все необходимые детали по инциденту собраны."
    б. На НОВОЙ строке, СРАЗУ после сообщения: {completion_marker}
    в. На НОВОЙ строке, СРАЗУ после маркера: JSON-объект. Этот JSON должен содержать ВСЕ следующие ключи с собранной информацией (если по какому-то аспекту информации нет или пользователь не знает, ставь значение null или краткое описание типа "не указано", "свидетелей нет"):
       - "{key_description}"
       - "{key_time}"
       - "{key_location}"
       - "{key_witnesses}"
       - "{key_actions}"
       - "{key_consequences}"
       - "{key_date}" (Дата инцидента в формате ГГГГ-ММ-ДД. Если пользователь говорит "сегодня", используй {current_date}. Если дата уже упоминалась в диалоге, используй ее. Если не ясна, уточни ее как часть аспекта "Время" или установи null.)

ВАЖНО:
- НЕ ЗАДАВАЙ вопросы по аспектам, информация по которым уже четко дана в ИСТОРИИ ДИАЛОГА или ПОСЛЕДНЕМ СООБЩЕНИИ ПОЛЬЗОВАТЕЛЯ. Твоя задача - быть умным и не повторяться.
- Если пользователь в одном сообщении отвечает на несколько вопросов или дает много деталей, УЧТИ ВСЮ ЭТУ ИНФОРМАЦИЮ при определении следующего вопроса или решении о завершении.
- Твоя цель - получить полную картину за минимальное количество логичных шагов.

Текущая дата для справки (используй для "сегодня", если пользователь так говорит): {current_date}
"""

def format_dialog_history_for_dc_prompt(history: List[Dict[str, str]]) -> str:
    if not history:
        return "Это начало сбора деталей по инциденту."
    formatted_lines = []
    for item in history:
        role = "Ассистент (ты)" if item["type"] == "ai" else "Пользователь"
        formatted_lines.append(f"{role}: {item['content']}")
    return "\n".join(formatted_lines)

def get_detail_collector_prompt(
        scenario_context: Dict[str, Any], # Содержит warehouse_name, courier_name, initial_complaint
        agent_dialog_history: List[Dict[str, str]], # История диалога именно этого агента
        user_current_reply: str # Самое последнее сообщение от пользователя, на которое нужно отреагировать
) -> str:

    # Форматируем историю диалога для вставки в промпт
    history_str = format_dialog_history_for_dc_prompt(agent_dialog_history)

    # Подставляем все значения в шаблон
    return DC_AUTONOMOUS_SYSTEM_PROMPT.format(
        warehouse_name=scenario_context.get("warehouse_name", "N/A"),
        courier_name=scenario_context.get("courier_name", "N/A"),
        initial_complaint=scenario_context.get("initial_complaint", "Жалоба не детализирована."),
        dialog_history_formatted=history_str,
        user_current_reply=user_current_reply,
        current_date=datetime.now().strftime("%Y-%m-%d"),
        completion_marker=DETAILS_COLLECTED_MARKER,
        # Передаем ключи и описания аспектов
        key_description=AGENT_RESULT_FIELDS["DESCRIPTION"],
        desc_description=ASPECT_DESCRIPTIONS_FOR_PROMPT[AGENT_RESULT_FIELDS["DESCRIPTION"]],
        key_time=AGENT_RESULT_FIELDS["TIME"],
        desc_time=ASPECT_DESCRIPTIONS_FOR_PROMPT[AGENT_RESULT_FIELDS["TIME"]],
        key_location=AGENT_RESULT_FIELDS["LOCATION"],
        desc_location=ASPECT_DESCRIPTIONS_FOR_PROMPT[AGENT_RESULT_FIELDS["LOCATION"]],
        key_witnesses=AGENT_RESULT_FIELDS["WITNESSES"],
        desc_witnesses=ASPECT_DESCRIPTIONS_FOR_PROMPT[AGENT_RESULT_FIELDS["WITNESSES"]],
        key_actions=AGENT_RESULT_FIELDS["ACTIONS_TAKEN"],
        desc_actions=ASPECT_DESCRIPTIONS_FOR_PROMPT[AGENT_RESULT_FIELDS["ACTIONS_TAKEN"]],
        key_consequences=AGENT_RESULT_FIELDS["CONSEQUENCES"],
        desc_consequences=ASPECT_DESCRIPTIONS_FOR_PROMPT[AGENT_RESULT_FIELDS["CONSEQUENCES"]],
        key_date=AGENT_RESULT_FIELDS["DATE"]
    )