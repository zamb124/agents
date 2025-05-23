# tools/warehouse_api.py
import logging
import httpx
import asyncio # Для asyncio.run в get_warehouse_by_director_login, если нужно (не лучшая практика)

import config
# Импортируем generate_mock_data_for_warehouses из courier_api
from .courier_api import generate_mock_data_for_warehouses

logger = logging.getLogger(__name__)

WAREHOUSES = {}
WAREHOUSES_LOADED = False

# Моковая база директоров для get_warehouse_by_director_login
# Ключ - логин директора, значение - информация о его складе
# warehouse_id здесь должен совпадать с ID из WAREHOUSES после загрузки
MOCK_DIRECTORS_DB = {
    "director_main_wh": {"warehouse_id": "moscow_center_1", "warehouse_name": "Центральный Склад (Москва)", "director_name": "Иван Петров"},
    "director_north_spb": {"warehouse_id": "spb_north_3", "warehouse_name": "Северный Филиал (СПБ)", "director_name": "Мария Сидорова"},
    # Добавьте больше директоров по необходимости
}


async def load_warehouses_if_needed(force_reload: bool = False):
    global WAREHOUSES, WAREHOUSES_LOADED
    if WAREHOUSES_LOADED and not force_reload:
        logger.debug("Склады уже были загружены.")
        return WAREHOUSES

    logger.info(f"Загрузка списка складов... (force_reload={force_reload})")

    # Реальная логика загрузки из API (если WMS_TOKEN есть)
    if config.WMS_TOKEN:
        logger.info("WMS_TOKEN найден, попытка загрузки складов из WMS API.")
        payload  = {
            "cluster_id": "5d7f3dbe80ea485f940e327bb73f08be000200010000", # Пример
            "_fields": ["store_id", "external_id", "title", "type", "status", "cluster_id", "company_id", "tags","vars","errors"]
        }
        headers = {'authorization': f'Bearer {config.WMS_TOKEN}'}
        current_warehouses_from_api = {}
        page_count = 0
        max_pages = 10 # Ограничение для безопасности

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                while page_count < max_pages:
                    page_count += 1
                    logger.debug(f"Запрос WMS API, страница/курсор: {payload.get('cursor', 'начальная')}")
                    response = await client.post(url='https://wms.lavka.yandex.ru/api/admin/stores/list', headers=headers, json=payload)
                    response.raise_for_status()
                    result = response.json()

                    if result.get('result'):
                        for wh_data in result['result']:
                            wh_id = wh_data.get('external_id', wh_data.get('store_id', '')).lower()
                            if not wh_id: continue
                            current_warehouses_from_api[wh_id] = {
                                "warehouse_id": wh_id,
                                "warehouse_name": wh_data.get('title', 'N/A').strip(), # Убираем лишние пробелы
                                "city": wh_data.get('vars', {}).get('address_city', 'Город не указан'),
                                "status": wh_data.get('status', 'unknown'),
                                "raw_data": wh_data
                            }
                    cursor = result.get('cursor')
                    if cursor: payload['cursor'] = cursor
                    else: break
            WAREHOUSES = current_warehouses_from_api
            logger.info(f"Загружено {len(WAREHOUSES)} складов из WMS API.")
        except httpx.HTTPStatusError as e:
            logger.error(f"Ошибка HTTP при запросе к WMS API: {e.response.status_code} - {e.response.text}", exc_info=True)
        except Exception as e:
            logger.error(f"Неожиданная ошибка при загрузке складов из WMS API: {e}", exc_info=True)

    # Если WMS_TOKEN нет или загрузка из API не удалась, используем моки
    if not WAREHOUSES: # Если WAREHOUSES пуст после попытки загрузки из API
        logger.warning("Не удалось загрузить склады из WMS API или WMS_TOKEN не указан. Используются моковые склады.")
        WAREHOUSES = {
            "moscow_center_1": {"warehouse_id": "moscow_center_1", "warehouse_name": "Центральный Склад (Москва)", "city": "Москва", "status": "active"},
            "moscow_south_2": {"warehouse_id": "moscow_south_2", "warehouse_name": "Южный Склад (Москва)", "city": "Москва", "status": "active"},
            "spb_north_3": {"warehouse_id": "spb_north_3", "warehouse_name": "Северный Филиал (СПБ)", "city": "Санкт-Петербург", "status": "active"},
            "spb_center_4": {"warehouse_id": "spb_center_4", "warehouse_name": "Центральный Склад (СПБ)", "city": "Санкт-Петербург", "status": "inactive"},
            "ekb_main_5": {"warehouse_id": "ekb_main_5", "warehouse_name": "Главный Склад (Екатеринбург)", "city": "Екатеринбург", "status": "active"},
        }
        logger.info(f"Загружено {len(WAREHOUSES)} моковых складов.")

    # Генерация моковых данных для курьеров и смен на основе загруженных/моковых складов
    # Эта функция из courier_api.py
    generate_mock_data_for_warehouses(WAREHOUSES)

    WAREHOUSES_LOADED = True
    return WAREHOUSES


def get_warehouse_by_director_login(director_login: str) -> dict:
    global WAREHOUSES, WAREHOUSES_LOADED
    if not WAREHOUSES_LOADED:
        logger.warning("Склады еще не загружены. Попытка синхронной загрузки (может быть долго или вызвать ошибку в async среде)...")
        # Это плохая практика вызывать asyncio.run из синхронной функции в асинхронном приложении.
        # Лучше убедиться, что load_warehouses_if_needed() вызвана асинхронно при старте бота.
        try:
            asyncio.run(load_warehouses_if_needed())
        except RuntimeError as e: # "asyncio.run() cannot be called from a running event loop"
            logger.error(f"Ошибка при попытке синхронной загрузки складов: {e}. Склады могут быть недоступны.")
            return {"success": False, "message": "Ошибка инициализации данных о складах. Попробуйте позже."}


    logger.info(f"[API][WAREHOUSE] Запрос склада по логину директора: {director_login}")
    director_login_lower = director_login.lower()

    # Попытка 1: Использование MOCK_DIRECTORS_DB
    if director_login_lower in MOCK_DIRECTORS_DB:
        mock_director_entry = MOCK_DIRECTORS_DB[director_login_lower]
        wh_id_from_mock = mock_director_entry["warehouse_id"].lower()

        if wh_id_from_mock in WAREHOUSES:
            warehouse_data = WAREHOUSES[wh_id_from_mock].copy()
            # Можно дополнить именем директора из MOCK_DIRECTORS_DB, если нужно
            warehouse_data["director_name_from_mock"] = mock_director_entry.get("director_name")
            logger.info(f"Склад найден через MOCK_DIRECTORS_DB для логина {director_login}: {warehouse_data['warehouse_name']}")
            return {"success": True, "warehouse_info": warehouse_data}
        else:
            logger.warning(f"Склад ID '{wh_id_from_mock}' из MOCK_DIRECTORS_DB для логина {director_login} не найден в общем списке WAREHOUSES.")
            # Можно вернуть информацию из MOCK_DIRECTORS_DB как есть, если считаем ее валидной
            # return {"success": True, "warehouse_info": mock_director_entry.copy(), "message": "Информация о складе взята из справочника директоров, но не найдена в общем списке складов."}


    # Попытка 2: Логин директора как ID склада в WAREHOUSES (менее вероятно, но возможно)
    if director_login_lower in WAREHOUSES:
        logger.info(f"Склад найден по логину директора (совпал с ID склада): {director_login_lower}")
        return {"success": True, "warehouse_info": WAREHOUSES[director_login_lower].copy()}

    logger.warning(f"Логин директора '{director_login}' не найден ни в справочнике директоров, ни как ID склада.")
    return {"success": False, "message": f"Информация о складе для директора '{director_login}' не найдена. Пожалуйста, укажите название или ID вашего склада."}


async def find_warehouse_by_name_or_id(identifier: str) -> dict:
    global WAREHOUSES, WAREHOUSES_LOADED
    if not WAREHOUSES_LOADED:
        await load_warehouses_if_needed()

    logger.info(f"[API][WAREHOUSE] Поиск склада по идентификатору: '{identifier}'")
    identifier_lower = identifier.lower().strip()

    # Сначала точный поиск по ID
    if identifier_lower in WAREHOUSES:
        warehouse_info = WAREHOUSES[identifier_lower].copy()
        logger.info(f"Склад найден по ID: {identifier_lower} - {warehouse_info['warehouse_name']}")
        return {"success": True, "warehouse_info": warehouse_info}

    # Затем поиск по части названия
    found_candidates = []
    for wh_id, info in WAREHOUSES.items():
        if identifier_lower in info.get("warehouse_name", "").lower():
            candidate_info = info.copy()
            # candidate_info["warehouse_id"] = wh_id # Уже есть в info
            found_candidates.append(candidate_info)

    if not found_candidates:
        logger.warning(f"Склад с идентификатором '{identifier}' не найден.")
        return {"success": False, "message": f"Склад с названием или ID '{identifier}' не найден."}

    if len(found_candidates) == 1:
        warehouse_info = found_candidates[0]
        logger.info(f"Склад найден по названию '{identifier}': {warehouse_info['warehouse_name']} (ID: {warehouse_info['warehouse_id']})")
        return {"success": True, "warehouse_info": warehouse_info}

    logger.info(f"Найдено несколько ({len(found_candidates)}) складов по запросу '{identifier}'.")
    candidates_details = [
        f"- {cand['warehouse_name']} (ID: {cand['warehouse_id']})" +
        (f", Город: {cand.get('city', 'N/A')}" if cand.get('city') else "") +
        (f", Статус: {cand.get('status', 'N/A')}" if cand.get('status') else "")
        for cand in found_candidates
    ]

    return {
        "success": "multiple_found",
        "candidates": found_candidates,
        "message": (
                f"Найдено несколько складов, соответствующих '{identifier}':\n" +
                "\n".join(candidates_details) +
                "\nПожалуйста, уточните ID или более полное название."
        )
    }