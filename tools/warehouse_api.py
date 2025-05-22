# tools/warehouse_api.py
import logging
import httpx
# sqlalchemy.util.await_only больше не нужен, если мы не используем SQLAlchemy здесь
import asyncio # Для await_only если бы он был нужен

import config
from tools.courier_api import generate_mock_data_for_warehouses, MOCK_DIRECTORS_DB  # Импортируем функцию генерации

logger = logging.getLogger(__name__)

WAREHOUSES = {} # Глобальный кэш для складов
WAREHOUSES_LOADED = False # Флаг, что склады загружены

async def load_warehouses_if_needed():
    """Загружает склады, если они еще не были загружены."""
    global WAREHOUSES, WAREHOUSES_LOADED
    if WAREHOUSES_LOADED:
        return WAREHOUSES

    logger.info("Загрузка списка складов из WMS API...")
    payload  = {
        "cluster_id": "5d7f3dbe80ea485f940e327bb73f08be000200010000",
        "_fields": ["store_id", "external_id", "title", "type", "status", "cluster_id", "company_id", "tags","vars","errors"]
    }
    headers = {
        'authorization': f'Bearer {config.WMS_TOKEN}',
    }

    if not config.WMS_TOKEN:
        logger.error("WMS_TOKEN не найден в конфигурации. Невозможно загрузить склады из API.")
        # Можно загрузить моковые склады по умолчанию в этом случае
        WAREHOUSES = {
            "w1_mock": {"warehouse_id": "w1_mock", "warehouse_name": "Мок Центральный склад", "city": "Москва"},
            "w2_mock": {"warehouse_id": "w2_mock", "warehouse_name": "Мок Северный филиал", "city": "СПБ"},
        }
        logger.info(f"Загружены моковые склады по умолчанию: {len(WAREHOUSES)} шт.")
        generate_mock_data_for_warehouses(WAREHOUSES) # Генерируем моки на основе моковых складов
        WAREHOUSES_LOADED = True
        return WAREHOUSES

    current_warehouses = {}
    page_count = 0
    max_pages = 100 # Ограничение, чтобы не уйти в бесконечный цикл при проблемах с API

    try:
        while page_count < max_pages:
            page_count += 1
            logger.info(f"Запрос WMS API, страница/курсор: {payload.get('cursor', 'начальная')}")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url='https://wms.lavka.yandex.ru/api/admin/stores/list', headers=headers, json=payload)
                response.raise_for_status() # Вызовет исключение для 4xx/5xx ответов
                result = response.json()

                if result.get('result'):
                    for war in result['result']:
                        # Используем external_id как основной ID, приводим к нижнему регистру для консистентности
                        wh_id = war.get('external_id', war.get('store_id', '')).lower()
                        if not wh_id:
                            logger.warning(f"Склад без external_id/store_id пропущен: {war.get('title')}")
                            continue

                        current_warehouses[wh_id] = {
                            "warehouse_id": wh_id,
                            "warehouse_name": war.get('title', 'Без названия').lower(), # Тоже к нижнему регистру
                            "city": war.get('vars', {}).get('address_city', 'Город не указан'), # Пример получения города
                            "raw_data": war # Сохраняем исходные данные на всякий случай
                        }

                cursor = result.get('cursor')
                if cursor:
                    payload['cursor'] = cursor
                else:
                    logger.info("Достигнут конец списка складов от WMS API.")
                    break
        else: # Если вышли по max_pages
            logger.warning(f"Загрузка складов прервана после {max_pages} запросов к API.")

    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка HTTP при запросе к WMS API: {e.response.status_code} - {e.response.text}", exc_info=True)
    except httpx.RequestError as e:
        logger.error(f"Ошибка сети при запросе к WMS API: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Неожиданная ошибка при загрузке складов из WMS API: {e}", exc_info=True)

    if not current_warehouses: # Если ничего не загрузилось из API
        logger.warning("Не удалось загрузить склады из WMS API, будут использованы моковые по умолчанию.")
        WAREHOUSES = { # Запасные моки
            "w1_default": {"warehouse_id": "w1_default", "warehouse_name": "Дефолтный Центральный", "city": "Москва"},
            "w2_default": {"warehouse_id": "w2_default", "warehouse_name": "Дефолтный Северный", "city": "СПБ"},
        }
    else:
        WAREHOUSES = current_warehouses

    logger.info(f"Загружено/используется {len(WAREHOUSES)} складов.")
    logger.debug(f"Первые несколько складов: {list(WAREHOUSES.items())[:2]}")

    # Генерируем моковые данные для курьеров и смен на основе загруженных складов
    generate_mock_data_for_warehouses(WAREHOUSES)

    WAREHOUSES_LOADED = True
    return WAREHOUSES


def get_warehouse_by_director_login(director_login: str) -> dict:
    """
    Определяет склад по логину директора.
    Сначала пытается найти точное совпадение логина директора с ID склада в загруженном списке WAREHOUSES.
    Затем, если не найдено, проверяет MOCK_DIRECTORS_DB.
    """
    global WAREHOUSES, WAREHOUSES_LOADED
    if not WAREHOUSES_LOADED: # Гарантируем, что склады загружены
        # В синхронной функции мы не можем делать await. Это проблема.
        # get_warehouse_by_director_login должна быть асинхронной или load_warehouses_if_needed должна вызываться заранее.
        # Пока сделаем так, что если не загружено, вернем ошибку, намекая на необходимость предварительной загрузки.
        logger.warning("Склады еще не загружены. get_warehouse_by_director_login может работать некорректно.")
        # Или можно попытаться загрузить синхронно, если это возможно (не рекомендуется для httpx)
        # asyncio.run(load_warehouses_if_needed()) # Плохая практика вызывать run из синхронной функции в асинхронном приложении

    logger.info(f"[API][WAREHOUSE] Запрос склада по логину директора: {director_login}")

    # Попытка 1: Логин директора как ID склада в WAREHOUSES
    director_login_lower = director_login.lower()
    if director_login_lower in WAREHOUSES:
        logger.info(f"Склад найден по логину директора (совпал с ID склада): {director_login_lower}")
        return {"success": True, "warehouse_info": WAREHOUSES[director_login_lower].copy()}

    # Попытка 2: Использование MOCK_DIRECTORS_DB
    if director_login in MOCK_DIRECTORS_DB:
        mock_director_entry = MOCK_DIRECTORS_DB[director_login]
        wh_id_from_mock = mock_director_entry["warehouse_id"].lower()
        if wh_id_from_mock in WAREHOUSES:
            logger.info(f"Склад найден через MOCK_DIRECTORS_DB (логин: {director_login}, ID склада: {wh_id_from_mock}) и подтвержден в WAREHOUSES.")
            # Берем данные из WAREHOUSES, так как они "реальные", но можем дополнить именем из MOCK_DIRECTORS_DB, если оно там "лучше"
            warehouse_data = WAREHOUSES[wh_id_from_mock].copy()
            warehouse_data["warehouse_name"] = mock_director_entry.get("warehouse_name", warehouse_data["warehouse_name"]) # Приоритет имени из MOCK_DIRECTORS_DB
            return {"success": True, "warehouse_info": warehouse_data}
        else:
            logger.warning(f"Склад ID {wh_id_from_mock} из MOCK_DIRECTORS_DB для логина {director_login} не найден в загруженном списке WAREHOUSES.")
            # Можно вернуть информацию из MOCK_DIRECTORS_DB как есть, если считаем ее валидной
            return {"success": True, "warehouse_info": mock_director_entry.copy(), "message": "Информация о складе взята из справочника директоров, но не найдена в общем списке складов."}


    logger.warning(f"[API][WAREHOUSE] Логин директора '{director_login}' не найден ни как ID склада, ни в справочнике директоров.")
    return {"success": False, "message": f"Информация о складе для директора с логином '{director_login}' не найдена. Пожалуйста, уточните название или ID вашего склада."}


async def find_warehouse_by_name_or_id(identifier: str) -> dict:
    """
    Ищет склад по ID или названию в загруженном списке WAREHOUSES.
    """
    global WAREHOUSES, WAREHOUSES_LOADED
    if not WAREHOUSES_LOADED:
        await load_warehouses_if_needed() # Загружаем, если еще не было

    logger.info(f"[API][WAREHOUSE] Поиск склада по идентификатору: '{identifier}'")
    identifier_lower = identifier.lower().strip()

    # Сначала точный поиск по ID (ключи в WAREHOUSES уже в lower case)
    if identifier_lower in WAREHOUSES:
        warehouse_info = WAREHOUSES[identifier_lower].copy()
        logger.info(f"[API][WAREHOUSE] Склад найден по ID: {identifier_lower}")
        return {"success": True, "warehouse_info": warehouse_info}

    # Затем поиск по названию (собираем всех кандидатов)
    found_candidates = []
    for wh_id, info in WAREHOUSES.items():
        # info["warehouse_name"] тоже должен быть в lower case при загрузке
        if identifier_lower in info.get("warehouse_name", "").lower():
            candidate_info = info.copy()
            found_candidates.append(candidate_info)

    if not found_candidates:
        logger.warning(f"[API][WAREHOUSE] Склад с идентификатором '{identifier}' не найден.")
        return {"success": False, "message": f"Склад с названием или ID '{identifier}' не найден. Пожалуйста, проверьте правильность ввода."}

    if len(found_candidates) == 1:
        warehouse_info = found_candidates[0]
        logger.info(f"[API][WAREHOUSE] Склад найден по названию '{identifier}': {warehouse_info['warehouse_name']} (ID: {warehouse_info['warehouse_id']})")
        return {"success": True, "warehouse_info": warehouse_info}

    logger.info(f"[API][WAREHOUSE] Найдено несколько ({len(found_candidates)}) складов по запросу '{identifier}'.")
    candidates_details = [f"- {cand['warehouse_name']} (ID: {cand['warehouse_id']})" + (f", Город: {cand.get('city', '')}" if cand.get('city') else "") for cand in found_candidates]
    candidate_list_str = "\n".join(candidates_details)

    return {
        "success": "multiple_found",
        "candidates": found_candidates,
        "message": (
            f"Найдено несколько складов, соответствующих запросу '{identifier}':\n{candidate_list_str}\n"
            "Пожалуйста, уточните, о каком именно складе идет речь, указав его ID или более полное название."
        )
    }