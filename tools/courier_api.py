# tools/courier_api.py
import logging
from datetime import datetime, timedelta
import random # Оставим random для выбора дат смен и их статусов

logger = logging.getLogger(__name__)

# --- Глобальные переменные для моковых данных ---
MOCK_COURIERS_DB = {}
MOCK_SHIFTS_DB = {}
MOCK_DATA_GENERATED = False

# Списки для генерации имен курьеров
FIRST_NAMES = ["Иван", "Петр", "Сергей", "Анна", "Елена"] # Немного сократил для примера, можете вернуть свои
LAST_NAMES = ["Иванов", "Петров", "Сидоров", "Смирнова"] # Добавил женскую фамилию
PATRONYMICS_MALE = ["Иванович", "Петрович", "Сергеевич", "Андреевич"]
PATRONYMICS_FEMALE = ["Ивановна", "Петровна", "Сергеевна", "Андреевна"]


def generate_mock_data_for_warehouses(warehouses_dict: dict):
    """
    Генерирует моковых курьеров и их смены на основе списка складов.
    Создает все комбинации ФИО на каждом складе.
    warehouses_dict: Словарь складов, где ключ - warehouse_id, значение - информация о складе.
    """
    global MOCK_COURIERS_DB, MOCK_SHIFTS_DB, MOCK_DATA_GENERATED
    if MOCK_DATA_GENERATED:
        logger.info("Моковые данные для курьеров и смен уже были сгенерированы.")
        return

    logger.info(f"Генерация моковых данных (все комбинации ФИО) для {len(warehouses_dict)} складов...")

    courier_id_counter = 100 # Начальный ID для курьеров, чтобы были уникальные ID
    shift_id_counter = 1000  # Начальный ID для смен

    today_str = datetime.now().strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    for wh_id, wh_info in warehouses_dict.items():
        logger.info(f"Генерация курьеров для склада: {wh_id} ({wh_info.get('warehouse_name', 'Без имени')})")

        current_warehouse_courier_count = 0
        for last_name in LAST_NAMES:
            for first_name in FIRST_NAMES:
                # Определяем пол по имени для выбора правильного списка отчеств
                # Это упрощенное определение, можно улучшить
                is_male_name = first_name.endswith(('н', 'р', 'ей', 'ий', 'др')) and not first_name.endswith('на')

                current_patronymics = PATRONYMICS_MALE if is_male_name else PATRONYMICS_FEMALE

                # Адаптируем фамилию к полу, если это возможно просто (например, добавление 'а')
                # Это очень упрощенно и не покрывает все случаи русской морфологии!
                processed_last_name = last_name
                if not is_male_name and last_name.endswith('ов'): # Иванов -> Иванова
                    processed_last_name = last_name[:-2] + "ова"
                elif not is_male_name and last_name.endswith('ин'): # Ильин -> Ильина
                    processed_last_name = last_name + "а"


                for patronymic in current_patronymics:
                    courier_id_counter += 1
                    courier_id_str = str(courier_id_counter)

                    full_name = f"{processed_last_name} {first_name} {patronymic}"

                    MOCK_COURIERS_DB[courier_id_str] = {
                        "full_name": full_name,
                        "status": "active",
                        "strikes": 0, # Для простоты у всех 0 страйков
                        "warehouse_id": wh_id
                    }
                    current_warehouse_courier_count +=1

                    # Генерируем 1-2 смены для курьера (оставим немного рандома здесь)
                    num_shifts = random.randint(1, 2)
                    for _ in range(num_shifts):
                        shift_id_counter +=1
                        shift_id_str = "S" + str(shift_id_counter)

                        shift_date = random.choice([today_str, tomorrow_str])
                        start_hour = random.randint(8, 14)
                        shift_duration = random.choice([4, 6, 8])
                        end_hour = start_hour + shift_duration
                        time_slot = f"{start_hour:02d}:00-{end_hour:02d}:00"

                        MOCK_SHIFTS_DB[shift_id_str] = {
                            "shift_id": shift_id_str,
                            "courier_id": courier_id_str,
                            "warehouse_id": wh_id,
                            "date": shift_date,
                            "status": random.choice(["active", "planned"]),
                            "time_slot": time_slot
                        }
        logger.info(f"Для склада {wh_id} сгенерировано {current_warehouse_courier_count} курьеров.")

    # Добавление "особых" курьеров можно оставить, если они нужны для специфических тестов
    # и если их warehouse_id существуют в warehouses_dict
    # Пример:
    # test_wh_id_for_special_couriers = next(iter(warehouses_dict.keys()), None) # Берем ID первого попавшегося склада
    # if test_wh_id_for_special_couriers:
    #     MOCK_COURIERS_DB["001"] = {"full_name": "Тест Курьер Пьяный Особый", "status": "active", "strikes": 0, "warehouse_id": test_wh_id_for_special_couriers}
    #     MOCK_SHIFTS_DB["S999"] = {"shift_id": "S999", "courier_id": "001", "warehouse_id": test_wh_id_for_special_couriers, "date": today_str, "status": "active", "time_slot": "10:00-14:00"}

    MOCK_DATA_GENERATED = True
    logger.info(f"Всего сгенерировано {len(MOCK_COURIERS_DB)} моковых курьеров и {len(MOCK_SHIFTS_DB)} моковых смен.")
    if MOCK_COURIERS_DB:
        logger.debug(f"Пример MOCK_COURIERS_DB: {list(MOCK_COURIERS_DB.items())[0]}")
    if MOCK_SHIFTS_DB:
        logger.debug(f"Пример MOCK_SHIFTS_DB: {list(MOCK_SHIFTS_DB.items())[0]}")


# ... (остальной код courier_api.py: MOCK_DIRECTORS_DB, search_courier_by_id_or_name, get_courier_shifts)
# Важно: search_courier_by_id_or_name и get_courier_shifts теперь будут работать с этими сгенерированными данными.
# MOCK_DIRECTORS_DB остается для первоначального определения склада по логину,
# но теперь он может быть не так важен, если логины не совпадают с warehouse_id.
# В реальной системе должна быть связь директор -> склад.
MOCK_DIRECTORS_DB = {}


def search_courier_by_id_or_name(identifier: str, warehouse_id: str = None) -> dict:
    logger.info(f"[API MOCK][COURIER] Ищем курьера: '{identifier}', Склад ID: {warehouse_id}")
    identifier_lower = identifier.lower().strip()

    if not MOCK_COURIERS_DB:
        logger.warning("[API MOCK][COURIER] База курьеров пуста. Данные могли быть еще не сгенерированы.")
        return {"success": False, "message": "База данных курьеров временно недоступна или пуста. Попробуйте позже."}

    # Поиск по ID (ID уникален глобально в нашей моковой генерации)
    if identifier in MOCK_COURIERS_DB:
        courier_data = MOCK_COURIERS_DB[identifier]
        # Проверяем, принадлежит ли курьер указанному складу, если warehouse_id предоставлен
        if warehouse_id and courier_data["warehouse_id"].lower() != warehouse_id.lower():
            logger.warning(f"[API MOCK][COURIER] Курьер с ID '{identifier}' найден, но он с другого склада ({courier_data['warehouse_id']}), а искали на {warehouse_id}.")
            # Если мы хотим строгий поиск по складу даже для ID:
            # return {"success": False, "message": f"Курьер с ID '{identifier}' найден, но он привязан к другому складу. Пожалуйста, проверьте ID курьера или склада."}
            # Если ID глобален, а склад - это доп. фильтр, то можно вернуть, но с предупреждением или без.
            # Для текущей задачи, если ID найден, вернем его, но агент должен был передать правильный warehouse_id.
            pass # Продолжаем, ID найден, агент должен был проверить склад ранее.

        courier_info = courier_data.copy()
        courier_info["id"] = identifier
        logger.info(f"[API MOCK][COURIER] Курьер найден по ID: {identifier}")
        return {"success": True, "courier_info": courier_info}

    # Для поиска по имени ТРЕБУЕМ warehouse_id
    if not warehouse_id:
        logger.warning("[API MOCK][COURIER] Поиск по имени без указания ID склада не поддерживается.")
        return {"success": False, "message": "Для поиска курьера по имени необходимо указать ID склада. Пожалуйста, сначала определите склад."}

    found_candidates = []
    for courier_id_loop, info in MOCK_COURIERS_DB.items():
        # Фильтруем по складу
        if info.get("warehouse_id", "").lower() != warehouse_id.lower():
            continue

        if identifier_lower in info["full_name"].lower():
            candidate_info = info.copy()
            candidate_info["id"] = courier_id_loop
            found_candidates.append(candidate_info)

    if not found_candidates:
        logger.warning(f"[API MOCK][COURIER] Курьер с именем '{identifier}' не найден на складе {warehouse_id}.")
        return {"success": False, "message": f"Курьер с именем '{identifier}' не найден на складе {warehouse_id}."}

    if len(found_candidates) == 1:
        courier_info = found_candidates[0]
        logger.info(f"[API MOCK][COURIER] Курьер найден по имени '{identifier}': {courier_info['full_name']} (ID: {courier_info['id']}) на складе {warehouse_id}.")
        return {"success": True, "courier_info": courier_info}

    logger.info(f"[API MOCK][COURIER] Найдено несколько ({len(found_candidates)}) курьеров по запросу '{identifier}' на складе {warehouse_id}.")

    candidates_details = [f"- {cand['full_name']} (ID: {cand['id']})" for cand in found_candidates]
    candidate_list_str = "\n".join(candidates_details)

    return {
        "success": "multiple_found",
        "candidates": found_candidates,
        "message": (
            f"Найдено несколько курьеров, соответствующих запросу '{identifier}' на складе {warehouse_id}:\n{candidate_list_str}\n"
            "Пожалуйста, уточните, о ком именно идет речь, указав его ID или более полное ФИО."
        )
    }

def get_courier_shifts(courier_id: str, date_str: str = None) -> dict:
    logger.info(f"[API MOCK][COURIER] Запрос смен для курьера ID: {courier_id}, дата: {date_str if date_str else 'все активные/запланированные'}")

    if not MOCK_COURIERS_DB or courier_id not in MOCK_COURIERS_DB:
        logger.warning(f"[API MOCK][COURIER] Попытка получить смены для несуществующего курьера ID: {courier_id} или база курьеров пуста.")
        return {"success": False, "message": f"Курьер с ID {courier_id} не найден или база курьеров пуста."}

    if not MOCK_SHIFTS_DB:
        logger.warning(f"[API MOCK][COURIER] База смен пуста.")
        return {"success": True, "shifts": [], "message": "База данных смен временно недоступна или пуста."}

    courier_shifts_all = [shift for shift in MOCK_SHIFTS_DB.values() if shift["courier_id"] == courier_id and shift["status"] in ["active", "planned"]]

    courier_name = MOCK_COURIERS_DB[courier_id]['full_name']
    if not courier_shifts_all:
        logger.info(f"[API MOCK][COURIER] У курьера {courier_name} (ID: {courier_id}) нет активных или запланированных смен.")
        return {"success": True, "shifts": [], "message": f"У курьера {courier_name} (ID: {courier_id}) нет активных или запланированных смен."}

    if date_str:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            filtered_shifts = [shift for shift in courier_shifts_all if shift["date"] == date_str]
            if not filtered_shifts:
                logger.info(f"[API MOCK][COURIER] У курьера {courier_name} (ID: {courier_id}) нет активных или запланированных смен на дату {date_str}.")
                return {"success": True, "shifts": [], "message": f"У курьера {courier_name} (ID: {courier_id}) нет активных или запланированных смен на дату {date_str}."}
            logger.info(f"[API MOCK][COURIER] Найдено {len(filtered_shifts)} смен для курьера {courier_id} на {date_str}.")
            return {"success": True, "shifts": filtered_shifts}
        except ValueError:
            logger.warning(f"[API MOCK][COURIER] Неверный формат даты: {date_str}. Возвращаем все активные/запланированные смены курьера.")
            return {"success": True, "shifts": courier_shifts_all, "message": f"Дата '{date_str}' указана в неверном формате. Возвращены все активные/запланированные смены курьера."}

    logger.info(f"[API MOCK][COURIER] Возвращено {len(courier_shifts_all)} активных/запланированных смен для курьера {courier_id} (без фильтра по дате).")
    return {"success": True, "shifts": courier_shifts_all}