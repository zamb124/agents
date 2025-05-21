# tools/decision_actions.py
import logging
from .courier_api import MOCK_COURIERS_DB, MOCK_SHIFTS_DB

logger = logging.getLogger(__name__)

def take_action_on_courier(action_type: str, courier_id: str, reason: str, shift_id: str = None, warehouse_id: str = None) -> dict: # warehouse_id пока оставим, но для delete_shift он может быть не нужен если есть shift_id
    """
    Выполняет действие в отношении курьера: удалить смену, забанить, записать жалабу.
    action_type может быть: 'delete_shift', 'ban_courier', 'log_complaint'.
    Все действия логируюца.
    """
    logger.info(f"[ACTION MOCK] Действие: {action_type}, Курьер ID: {courier_id}, Причина: '{reason}', Смена ID: {shift_id}, Склад ID: {warehouse_id}")

    if courier_id not in MOCK_COURIERS_DB: # Проверка существования курьера
        return {"success": False, "message": f"Курьер с ID {courier_id} не найден для выполнения действия."}

    courier_name = MOCK_COURIERS_DB[courier_id]["full_name"]
    message = ""
    action_successful = False

    if action_type == "delete_shift":
        if not courier_id: # Это условие уже покрывается проверкой выше, но оставим для ясности
            return {"success": False, "message": "Не указан ID курьера для удаления смены."}

        shift_found_and_deleted = False
        if shift_id: # Если указан конкретный ID смены
            if shift_id in MOCK_SHIFTS_DB and MOCK_SHIFTS_DB[shift_id]["courier_id"] == courier_id:
                if MOCK_SHIFTS_DB[shift_id]["status"] == "active" or MOCK_SHIFTS_DB[shift_id]["status"] == "planned":
                    original_status = MOCK_SHIFTS_DB[shift_id]["status"]
                    MOCK_SHIFTS_DB[shift_id]["status"] = "cancelled_by_support"
                    message = f"Смена {shift_id} (была {original_status}) для курьера {courier_name} (ID: {courier_id}) успешно удалена. Причина: {reason}."
                    shift_found_and_deleted = True
                    action_successful = True
                else:
                    message = f"Смена {shift_id} для курьера {courier_name} (ID: {courier_id}) не активна и не запланирована (статус: {MOCK_SHIFTS_DB[shift_id]['status']})."
            else:
                message = f"Смена с ID {shift_id} не найдена или не принадлежит курьеру {courier_name} (ID: {courier_id})."
        else: # Если ID смены не указан, пытаемся удалить ближайшую активную/запланированную (упрощенно - первую попавшуюся)
            # В реальной системе здесь нужна была бы логика определения ближайшей смены
            for s_id, shift_data in MOCK_SHIFTS_DB.items():
                if shift_data["courier_id"] == courier_id and shift_data["status"] in ["active", "planned"]:
                    original_status = shift_data["status"]
                    shift_data["status"] = "cancelled_by_support"
                    message = f"Ближайшая {original_status} смена {s_id} для курьера {courier_name} (ID: {courier_id}) удалена. Причина: {reason}."
                    shift_found_and_deleted = True
                    action_successful = True
                    break
            if not shift_found_and_deleted:
                message = f"Активных или запланированных смен для курьера {courier_name} (ID: {courier_id}) для удаления не найдено."

        if not action_successful and not shift_found_and_deleted: # Если ничего не удалили, но и ошибки не было
            if not message: # Если сообщение не было установлено выше
                message = f"Не удалось выполнить удаление смены для курьера {courier_name} (ID: {courier_id})."


    elif action_type == "ban_courier":
        if MOCK_COURIERS_DB[courier_id]["status"] == "banned_by_support":
            message = f"Курьер {courier_name} (ID: {courier_id}) уже заблокирован."
            action_successful = True
        else:
            MOCK_COURIERS_DB[courier_id]["status"] = "banned_by_support"
            MOCK_COURIERS_DB[courier_id]["strikes"] = MOCK_COURIERS_DB[courier_id].get("strikes", 0) + 3 # Бан дает сразу 3 страйка, например
            message = f"Курьер {courier_name} (ID: {courier_id}) успешно заблокирован. Причина: {reason}."
            action_successful = True
            # Дополнительно отменяем все активные и запланированные смены забаненного курьера
            for s_id, shift_data in MOCK_SHIFTS_DB.items():
                if shift_data["courier_id"] == courier_id and shift_data["status"] in ["active", "planned"]:
                    shift_data["status"] = "cancelled_due_to_ban"
                    logger.info(f"[ACTION MOCK] Смена {s_id} курьера {courier_id} отменена из-за бана.")

    elif action_type == "log_complaint":
        MOCK_COURIERS_DB[courier_id]["strikes"] = MOCK_COURIERS_DB[courier_id].get("strikes", 0) + 1
        message = f"Жалоба на курьера {courier_name} (ID: {courier_id}) зарегистрирована. Причина: {reason}. Текущее количество страйков: {MOCK_COURIERS_DB[courier_id]['strikes']}."
        action_successful = True

    else:
        message = f"Неизвестный тип действия: {action_type}."
        action_successful = False

    return {"success": action_successful, "message": message}