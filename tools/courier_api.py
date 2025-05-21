import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Это наша типа база данных курьеров
MOCK_COURIERS_DB = {
    "123": {"full_name": "Иванов Иван Иванович", "status": "active", "strikes": 0, "warehouse_id": "W1"},
    "456": {"full_name": "Петров Петр Петрович", "status": "active", "strikes": 1, "warehouse_id": "W2"},
    "789": {"full_name": "Сидорова Анна Васильевна", "status": "active", "strikes": 0, "warehouse_id": "W1"},
    "000": {"full_name": "Тестовый Курьер Неизвестный", "status": "active", "strikes": 0, "warehouse_id": "unknown"}
}

# А это наша типа база данных смен
MOCK_SHIFTS_DB = {
    "S101": {"shift_id": "S101", "courier_id": "123", "warehouse_id": "W1", "date": datetime.now().strftime("%Y-%m-%d") , "status": "active", "time_slot": "09:00-18:00"},
    "S102": {"shift_id": "S102", "courier_id": "789", "warehouse_id": "W1", "date": datetime.now().strftime("%Y-%m-%d"), "status": "active", "time_slot": "10:00-19:00"},
    "S103": {"shift_id": "S103", "courier_id": "123", "warehouse_id": "W1", "date": datetime.now().strftime("%Y-%m-%d"), "status": "active", "time_slot": "09:00-18:00"},
    "S201": {"shift_id": "S201", "courier_id": "456", "warehouse_id": "W2", "date": datetime.now().strftime("%Y-%m-%d"), "status": "active", "time_slot": "12:00-21:00"},
    "S202": {"shift_id": "S202", "courier_id": "456", "warehouse_id": "W2", "date": datetime.now().strftime("%Y-%m-%d"), "status": "planned", "time_slot": "12:00-21:00"},
}

def search_courier_by_id_or_name(identifier: str) -> dict:
    """
    Ищет курьера по ID или ФИО. ФИО можно искать неполностью.
    Возвращает информациею о курьере или саобщение об ошибке, если нету такого.
    """
    logger.info(f"[API MOCK][COURIER] Ищем курьера: {identifier}")
    if identifier in MOCK_COURIERS_DB:
        courier_info = MOCK_COURIERS_DB[identifier].copy()
        courier_info["id"] = identifier
        logger.info(f"[API MOCK][COURIER] Курьер найден по ID: {identifier}")
        return {"success": True, "courier_info": courier_info}

    for courier_id, info in MOCK_COURIERS_DB.items():
        if identifier.lower() in info["full_name"].lower():
            found_info = info.copy()
            found_info["id"] = courier_id
            logger.info(f"[API MOCK][COURIER] Курьер найден по имени '{identifier}': {found_info['full_name']} (ID: {courier_id})")
            return {"success": True, "courier_info": found_info}

    logger.warning(f"[API MOCK][COURIER] Курьер с идентификатором '{identifier}' не найден.")
    return {"success": False, "message": f"Курьер с идентификатором '{identifier}' не найден."}

def get_courier_shifts(courier_id: str, date_str: str = None) -> dict:
    """
    Получает смены для конкретного курьера.
    Если указана `date_str` (в формате ГГГГ-ММ-ДД), то фильтрует по этой дате.
    Возвращает только активные или запланированые смены.
    """
    logger.info(f"[API MOCK][COURIER] Запрос смен для курьера ID: {courier_id}, дата: {date_str if date_str else 'все активные/запланированные'}")
    if courier_id not in MOCK_COURIERS_DB:
        logger.warning(f"[API MOCK][COURIER] Попытка получить смены для несуществующего курьера ID: {courier_id}")
        return {"success": False, "message": f"Курьер с ID {courier_id} не найден."}

    courier_shifts_all = [shift for shift in MOCK_SHIFTS_DB.values() if shift["courier_id"] == courier_id and shift["status"] in ["active", "planned"]]

    if not courier_shifts_all:
        logger.info(f"[API MOCK][COURIER] У курьера {MOCK_COURIERS_DB[courier_id]['full_name']} (ID: {courier_id}) нет активных или запланированных смен.")
        return {"success": True, "shifts": [], "message": f"У курьера {MOCK_COURIERS_DB[courier_id]['full_name']} (ID: {courier_id}) нет активных или запланированных смен."}

    if date_str:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            filtered_shifts = [shift for shift in courier_shifts_all if shift["date"] == date_str]
            if not filtered_shifts:
                logger.info(f"[API MOCK][COURIER] У курьера {MOCK_COURIERS_DB[courier_id]['full_name']} (ID: {courier_id}) нет активных или запланированных смен на дату {date_str}.")
                return {"success": True, "shifts": [], "message": f"У курьера {MOCK_COURIERS_DB[courier_id]['full_name']} (ID: {courier_id}) нет активных или запланированных смен на дату {date_str}."}
            logger.info(f"[API MOCK][COURIER] Найдено {len(filtered_shifts)} смен для курьера {courier_id} на {date_str}.")
            return {"success": True, "shifts": filtered_shifts}
        except ValueError:
            logger.warning(f"[API MOCK][COURIER] Неверный формат даты: {date_str}. Возвращаем все активные/запланированные смены курьера.")
            return {"success": True, "shifts": courier_shifts_all, "message": f"Дата {date_str} указана не правильно, возвращены все активные/запланированные смены курьера."}

    logger.info(f"[API MOCK][COURIER] Возвращено {len(courier_shifts_all)} активных/запланированных смен для курьера {courier_id} (без фильтра по дате).")
    return {"success": True, "shifts": courier_shifts_all}