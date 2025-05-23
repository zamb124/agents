# tools/courier_api.py
import logging
from datetime import datetime, timedelta
import random
from typing import Optional

logger = logging.getLogger(__name__)

# --- Глобальные переменные для моковых данных ---
MOCK_COURIERS_DB = {}
MOCK_SHIFTS_DB = {}
# MOCK_DIRECTORS_DB был здесь, но он больше относится к складам и логинам,
# и его использование для определения склада по логину директора теперь в warehouse_api.py.
# Если он нужен для чего-то еще, можно оставить, но для текущей логики он не используется в courier_api.
MOCK_DATA_GENERATED_FOR_COURIERS = False # Переименовал для ясности

FIRST_NAMES = ["Иван", "Петр", "Сергей", "Алексей", "Дмитрий", "Анна", "Елена", "Ольга", "Мария", "Светлана"]
LAST_NAMES_MALE_STEM = ["Иванов", "Петров", "Сидоров", "Смирнов", "Кузнецов", "Васильев", "Михайлов", "Новиков"]
LAST_NAMES_FEMALE_ENDING = "а" # Для образования женских фамилий типа Иванова, Петрова
PATRONYMICS_MALE = ["Иванович", "Петрович", "Сергеевич", "Андреевич", "Алексеевич", "Дмитриевич", "Владимирович"]
PATRONYMICS_FEMALE = ["Ивановна", "Петровна", "Сергеевна", "Андреевна", "Алексеевна", "Дмитриевна", "Владимировна"]


def generate_mock_data_for_warehouses(warehouses_dict: dict):
    global MOCK_COURIERS_DB, MOCK_SHIFTS_DB, MOCK_DATA_GENERATED_FOR_COURIERS
    if MOCK_DATA_GENERATED_FOR_COURIERS:
        logger.debug("Моковые данные для курьеров и смен уже были сгенерированы.")
        return

    if not warehouses_dict:
        logger.warning("Словарь складов пуст, моковые курьеры не будут сгенерированы.")
        return

    logger.info(f"Генерация моковых курьеров и смен для {len(warehouses_dict)} складов...")

    courier_id_counter = 100
    shift_id_counter = 1000

    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    possible_dates = [yesterday_str, today_str, tomorrow_str]


    for wh_id, wh_info in warehouses_dict.items():
        # Генерируем 5-10 курьеров на склад для разнообразия
        num_couriers_on_warehouse = random.randint(5, 10)
        current_warehouse_courier_count = 0

        for i in range(num_couriers_on_warehouse):
            courier_id_counter += 1
            courier_id_str = str(courier_id_counter)

            first_name = random.choice(FIRST_NAMES)
            is_male = not (first_name.endswith('а') or first_name.endswith('я')) # Простое определение пола

            if is_male:
                last_name_stem = random.choice(LAST_NAMES_MALE_STEM)
                last_name = last_name_stem # Мужская форма
                patronymic = random.choice(PATRONYMICS_MALE)
            else:
                last_name_stem = random.choice(LAST_NAMES_MALE_STEM)
                last_name = last_name_stem + LAST_NAMES_FEMALE_ENDING # Женская форма
                patronymic = random.choice(PATRONYMICS_FEMALE)

            full_name = f"{last_name} {first_name} {patronymic}"

            MOCK_COURIERS_DB[courier_id_str] = {
                "full_name": full_name,
                "status": random.choice(["active", "active", "active", "inactive", "on_vacation"]),
                "strikes": random.randint(0, 2),
                "warehouse_id": wh_id,
                "gender": "male" if is_male else "female" # Добавим для информации
            }
            current_warehouse_courier_count += 1

            # Генерируем 0-3 смены для курьера
            num_shifts = random.randint(1, 3)
            for _ in range(num_shifts):
                shift_id_counter += 1
                shift_id_str = "S" + str(shift_id_counter)

                shift_date = random.choice(possible_dates)
                start_hour = random.randint(8, 16) # С 8 до 16 начало
                shift_duration = random.choice([4, 6, 8, 10])
                end_hour = start_hour + shift_duration
                if end_hour > 23: end_hour = 23 # Ограничение до конца дня

                time_slot = f"{start_hour:02d}:00-{end_hour:02d}:00"

                # Статус смены (активные/запланированные чаще для сегодня/завтра)
                shift_status = "planned"
                if shift_date == today_str:
                    shift_status = random.choice(["active", "active", "planned", "completed_early"])
                elif shift_date == yesterday_str:
                    shift_status = random.choice(["completed", "completed_late", "noshow", "cancelled_by_courier"])

                # Только активные и запланированные имеют значение для большинства операций
                # Но для истории можно хранить и другие
                MOCK_SHIFTS_DB[shift_id_str] = {
                    "shift_id": shift_id_str,
                    "courier_id": courier_id_str,
                    "warehouse_id": wh_id, # Дублируем для удобства фильтрации, хотя есть у курьера
                    "date": shift_date,
                    "status": shift_status,
                    "time_slot": time_slot
                }
        logger.debug(f"Для склада {wh_id} ({wh_info.get('warehouse_name', 'N/A')}) сгенерировано {current_warehouse_courier_count} курьеров.")

    MOCK_DATA_GENERATED_FOR_COURIERS = True
    logger.info(f"Всего сгенерировано {len(MOCK_COURIERS_DB)} моковых курьеров и {len(MOCK_SHIFTS_DB)} моковых смен.")
    if MOCK_COURIERS_DB: logger.debug(f"Пример курьера: {list(MOCK_COURIERS_DB.items())[0]}")
    if MOCK_SHIFTS_DB: logger.debug(f"Пример смены: {list(MOCK_SHIFTS_DB.items())[0]}")


def search_courier_by_id_or_name(identifier: str, warehouse_id: Optional[str] = None) -> dict:
    logger.info(f"[API MOCK][COURIER] Поиск курьера: '{identifier}', Склад ID: {warehouse_id}")
    identifier_lower = identifier.lower().strip()

    if not MOCK_COURIERS_DB:
        logger.warning("[API MOCK][COURIER] База курьеров пуста (MOCK_COURIERS_DB).")
        return {"success": False, "message": "База данных курьеров временно недоступна или пуста."}

    # Поиск по ID (ID уникален глобально)
    if identifier in MOCK_COURIERS_DB:
        courier_data = MOCK_COURIERS_DB[identifier]
        # Если warehouse_id предоставлен, проверяем соответствие
        if warehouse_id and courier_data["warehouse_id"].lower() != warehouse_id.lower():
            logger.warning(f"Курьер с ID '{identifier}' найден, но он с другого склада ({courier_data['warehouse_id']}), а искали на {warehouse_id}.")
            # В зависимости от требований: либо ошибка, либо вернуть с предупреждением.
            # Для строгости, если склад указан, он должен совпадать.
            return {"success": False, "message": f"Курьер с ID '{identifier}' найден, но он привязан к другому складу. Проверьте ID курьера или склада."}

        courier_info = courier_data.copy()
        courier_info["id"] = identifier # Добавляем ID в возвращаемый словарь
        logger.info(f"Курьер найден по ID: {identifier} - {courier_info['full_name']}")
        return {"success": True, "courier_info": courier_info}

    # Для поиска по имени ТРЕБУЕТСЯ warehouse_id
    if not warehouse_id:
        logger.warning("Поиск курьера по имени без ID склада не поддерживается.")
        return {"success": False, "message": "Для поиска курьера по имени необходимо указать ID склада. Пожалуйста, сначала определите склад."}

    found_candidates = []
    for c_id, info in MOCK_COURIERS_DB.items():
        if info.get("warehouse_id", "").lower() == warehouse_id.lower(): # Ищем только на указанном складе
            if identifier_lower in info["full_name"].lower():
                candidate_info = info.copy()
                candidate_info["id"] = c_id
                found_candidates.append(candidate_info)

    if not found_candidates:
        logger.warning(f"Курьер с именем/частью имени '{identifier}' не найден на складе {warehouse_id}.")
        return {"success": False, "message": f"Курьер с именем '{identifier}' не найден на складе {warehouse_id}."}

    if len(found_candidates) == 1:
        courier_info = found_candidates[0]
        logger.info(f"Курьер найден по имени '{identifier}': {courier_info['full_name']} (ID: {courier_info['id']}) на складе {warehouse_id}.")
        return {"success": True, "courier_info": courier_info}

    logger.info(f"Найдено несколько ({len(found_candidates)}) курьеров по запросу '{identifier}' на складе {warehouse_id}.")
    candidates_details = [f"- {cand['full_name']} (ID: {cand['id']})" for cand in found_candidates]

    return {
        "success": "multiple_found", # Специальный статус
        "candidates": found_candidates,
        "message": (
                f"Найдено несколько курьеров, соответствующих '{identifier}' на складе {warehouse_id}:\n" +
                "\n".join(candidates_details) +
                "\nПожалуйста, уточните ID или более полное ФИО."
        )
    }

def get_courier_shifts(courier_id: str, date_str: Optional[str] = None) -> dict:
    logger.info(f"[API MOCK][COURIER] Запрос смен для курьера ID: {courier_id}, дата: {date_str if date_str else 'все активные/запланированные'}")

    if courier_id not in MOCK_COURIERS_DB:
        logger.warning(f"Попытка получить смены для несуществующего курьера ID: {courier_id}.")
        return {"success": False, "message": f"Курьер с ID {courier_id} не найден."}

    if not MOCK_SHIFTS_DB:
        logger.warning("База смен (MOCK_SHIFTS_DB) пуста.")
        return {"success": True, "shifts": [], "message": "База данных смен пуста."} # Успех, но смен нет

    # Фильтруем смены по courier_id и статусам, которые считаются "актуальными" для большинства запросов
    relevant_statuses = ["active", "planned"]
    courier_all_relevant_shifts = [
        shift for shift in MOCK_SHIFTS_DB.values()
        if shift["courier_id"] == courier_id and shift["status"] in relevant_statuses
    ]

    courier_name = MOCK_COURIERS_DB[courier_id]['full_name']

    if not courier_all_relevant_shifts:
        msg = f"У курьера {courier_name} (ID: {courier_id}) нет активных или запланированных смен."
        logger.info(msg)
        return {"success": True, "shifts": [], "message": msg}

    if date_str:
        try:
            # Проверка формата даты (хотя сам strptime не нужен, если просто сравниваем строки)
            datetime.strptime(date_str, "%Y-%m-%d")
            filtered_by_date = [s for s in courier_all_relevant_shifts if s["date"] == date_str]
            if not filtered_by_date:
                msg = f"У курьера {courier_name} (ID: {courier_id}) нет активных/запланированных смен на {date_str}."
                logger.info(msg)
                return {"success": True, "shifts": [], "message": msg}
            logger.info(f"Найдено {len(filtered_by_date)} релевантных смен для курьера {courier_id} на {date_str}.")
            return {"success": True, "shifts": filtered_by_date}
        except ValueError:
            msg = f"Неверный формат даты: '{date_str}'. Ожидается YYYY-MM-DD. Возвращены все активные/запланированные смены курьера."
            logger.warning(msg)
            # Возвращаем все релевантные, если дата неверная, или можно вернуть ошибку
            return {"success": True, "shifts": courier_all_relevant_shifts, "message": msg}

    logger.info(f"Возвращено {len(courier_all_relevant_shifts)} активных/запланированных смен для курьера {courier_id} (без фильтра по дате).")
    return {"success": True, "shifts": courier_all_relevant_shifts}