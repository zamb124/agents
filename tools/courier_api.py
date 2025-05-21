import logging

logger = logging.getLogger(__name__)

# Мок базы данных курьеров
MOCK_COURIERS_DB = {
    "123": {"full_name": "Иванов Иван Иванович", "status": "active", "strikes": 0, "warehouse_id": "W1"},
    "456": {"full_name": "Петров Петр Петрович", "status": "active", "strikes": 1, "warehouse_id": "W2"},
    "789": {"full_name": "Сидорова Анна Васильевна", "status": "active", "strikes": 0, "warehouse_id": "W1"},
    "000": {"full_name": "Тестовый Курьер Неизвестный", "status": "active", "strikes": 0, "warehouse_id": "unknown"}
}

# Мок базы данных смен
MOCK_SHIFTS_DB = {
    "W1": [
        {"shift_id": "S101", "courier_id": "123", "date": "2024-07-30", "status": "active"},
        {"shift_id": "S102", "courier_id": "789", "date": "2024-07-30", "status": "active"},
    ],
    "W2": [
        {"shift_id": "S201", "courier_id": "456", "date": "2024-07-30", "status": "active"},
    ]
}

def search_courier_by_id_or_name(identifier: str) -> dict:
    """
    Ищет курьера по ID или ФИО.
    Возвращает информациею о курьере или саобщение об ошибке.
    """
    logger.info(f"[API MOCK][COURIER] Поиск курьера: {identifier}")
    # Поиск по ID
    if identifier in MOCK_COURIERS_DB:
        courier_info = MOCK_COURIERS_DB[identifier].copy()
        courier_info["id"] = identifier 
        return {"success": True, "courier_info": courier_info}

    # Поиск по ФИО (упрощенный, частичное совпадение без учета регистра)
    for courier_id, info in MOCK_COURIERS_DB.items():
        if identifier.lower() in info["full_name"].lower():
            found_info = info.copy()
            found_info["id"] = courier_id 
            return {"success": True, "courier_info": found_info}

    return {"success": False, "message": f"Курьер с идентификатором '{identifier}' не найден."}

def get_shifts_by_warehouse_id(warehouse_id: str) -> dict:
    """
    Получает список активных смен для указанног склада.
    (В текущей реализации возвращает все смены, фильтрация по статусу не добавлена для прастаты)
    """
    logger.info(f"[API MOCK][COURIER] Запрос смен для склада: {warehouse_id}")
    if warehouse_id in MOCK_SHIFTS_DB:
        return {"success": True, "shifts": MOCK_SHIFTS_DB[warehouse_id]}
    return {"success": False, "message": f"Смены для склада '{warehouse_id}' не найдены."}