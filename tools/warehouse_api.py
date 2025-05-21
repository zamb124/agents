import logging

logger = logging.getLogger(__name__)

# Мок базы данных директоров и их складов
MOCK_DIRECTORS_DB = {
    "director_login_center": {"warehouse_id": "W1", "warehouse_name": "Центральный склад"},
    "director_login_north": {"warehouse_id": "W2", "warehouse_name": "Северный филиал"},
    "director_test_user": {"warehouse_id": "W_TEST", "warehouse_name": "Тестовый Склад"},
}

def get_warehouse_by_director_login(director_login: str) -> dict:
    """
    Определяет склад по логину дериктора (например, Telegram username).
    Нужен чтобы понять откуда пришол запрос.
    """
    logger.info(f"[API MOCK][WAREHOUSE] Запрос склада по логину директора: {director_login}")
    if director_login in MOCK_DIRECTORS_DB:
        return {"success": True, "warehouse_info": MOCK_DIRECTORS_DB[director_login]}

    # Если логин директора не найден, можно вернуть дефолтный или ошибку
    # В данном случае, если агент не сможет определить склад, он должен будет уточнить у пользователя.
    return {"success": False, "message": f"Информация о складе для директора '{director_login}' не найдена. Пожалуйста, уточните название или ID вашего склада."}