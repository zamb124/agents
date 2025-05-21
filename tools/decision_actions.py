import logging
from .courier_api import MOCK_COURIERS_DB, MOCK_SHIFTS_DB # Для имитаци изменений в мок-данных

logger = logging.getLogger(__name__)

def take_action_on_courier(action_type: str, courier_id: str, reason: str, shift_id: str = None, warehouse_id: str = None) -> dict:
    """
    Выполняет действие в отношении курьера: удалить смену, забанить, записать жалабу.
    action_type может быть: 'delete_shift', 'ban_courier', 'log_complaint'.
    Все действия логируюца.
    """
    logger.info(f"[ACTION MOCK] Действие: {action_type}, Курьер ID: {courier_id}, Причина: '{reason}', Смена ID: {shift_id}, Склад ID: {warehouse_id}")

    if courier_id not in MOCK_COURIERS_DB:
        return {"success": False, "message": f"Курьер с ID {courier_id} не найден для выполнения действия."}

    courier_name = MOCK_COURIERS_DB[courier_id]["full_name"]
    message = ""
    action_successful = False

    if action_type == "delete_shift":
        if not courier_id:
            return {"success": False, "message": "Не указан ID курьера для удаления смены."}

        shift_found_and_deleted = False
        if shift_id and warehouse_id:
            if warehouse_id in MOCK_SHIFTS_DB:
                for shift in MOCK_SHIFTS_DB[warehouse_id]:
                    if shift["shift_id"] == shift_id and shift["courier_id"] == courier_id and shift["status"] == "active":
                        shift["status"] = "cancelled_by_support"
                        message = f"Смена {shift_id} для курьера {courier_name} (ID: {courier_id}) на складе {warehouse_id} успешно удалена. Причина: {reason}."
                        shift_found_and_deleted = True
                        action_successful = True
                        break
                if not shift_found_and_deleted:
                    message = f"Активная смена {shift_id} для курьера {courier_name} (ID: {courier_id}) на складе {warehouse_id} не найдена или уже неактивна."
            else:
                message = f"Склад {warehouse_id} не найден для удаления смены."
        else:
            for wh_id, shifts_on_warehouse in MOCK_SHIFTS_DB.items():
                for shift in shifts_on_warehouse:
                    if shift["courier_id"] == courier_id and shift["status"] == "active":
                        if shift_id and shift["shift_id"] != shift_id:
                            continue 

                        shift["status"] = "cancelled_by_support"
                        message = f"Активная смена {shift['shift_id']} для курьера {courier_name} (ID: {courier_id}) на складе {wh_id} удалена. Причина: {reason}."
                        shift_found_and_deleted = True
                        action_successful = True
                        break 
                if shift_found_and_deleted:
                    break
            if not shift_found_and_deleted:
                message = f"Активных смен для курьера {courier_name} (ID: {courier_id}) для удаления не найдено."
                if shift_id:
                    message += f" (Включая поиск по ID смены: {shift_id})"

    elif action_type == "ban_courier":
        if MOCK_COURIERS_DB[courier_id]["status"] == "banned_by_support":
            message = f"Курьер {courier_name} (ID: {courier_id}) уже заблокирован."
            action_successful = True 
        else:
            MOCK_COURIERS_DB[courier_id]["status"] = "banned_by_support"
            MOCK_COURIERS_DB[courier_id]["strikes"] = MOCK_COURIERS_DB[courier_id].get("strikes", 0) + 1 
            message = f"Курьер {courier_name} (ID: {courier_id}) успешно заблокирован. Причина: {reason}."
            action_successful = True
            for wh_id, shifts_on_warehouse in MOCK_SHIFTS_DB.items():
                for shift in shifts_on_warehouse:
                    if shift["courier_id"] == courier_id and shift["status"] == "active":
                        shift["status"] = "cancelled_due_to_ban"
                        logger.info(f"[ACTION MOCK] Смена {shift['shift_id']} курьера {courier_id} отменена из-за бана.")

    elif action_type == "log_complaint":
        MOCK_COURIERS_DB[courier_id]["strikes"] = MOCK_COURIERS_DB[courier_id].get("strikes", 0) + 1
        message = f"Жалоба на курьера {courier_name} (ID: {courier_id}) зарегистрирована. Причина: {reason}. Текущее количество страйков: {MOCK_COURIERS_DB[courier_id]['strikes']}."
        action_successful = True

    else:
        message = f"Неизвестный тип действия: {action_type}."
        action_successful = False

    return {"success": action_successful, "message": message}