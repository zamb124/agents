# tools/decision_actions.py
import logging
from datetime import datetime
from typing import Optional

from .courier_api import MOCK_COURIERS_DB, MOCK_SHIFTS_DB # Зависит от этих моковых баз

logger = logging.getLogger(__name__)

def take_action_on_courier(
        action_type: str,
        courier_id: str,
        reason: str,
        shift_id: Optional[str] = None,
        warehouse_id: Optional[str] = None # warehouse_id здесь больше для логгирования/контекста
) -> dict:
    logger.info(
        f"[ACTION MOCK] Применяется действие: '{action_type}', Курьер ID: {courier_id}, "
        f"Причина: '{reason[:100]}...', Смена ID: {shift_id}, Склад ID (контекст): {warehouse_id}"
    )

    if courier_id not in MOCK_COURIERS_DB:
        msg = f"Курьер с ID {courier_id} не найден для выполнения действия '{action_type}'."
        logger.warning(msg)
        return {"success": False, "message": msg}

    courier_name = MOCK_COURIERS_DB[courier_id]["full_name"]
    action_successful = False
    message_parts = [] # Собираем сообщения о выполненных действиях

    if action_type == "delete_shift":
        shift_found_and_deleted = False
        if shift_id: # Если указан конкретный ID смены
            if shift_id in MOCK_SHIFTS_DB and MOCK_SHIFTS_DB[shift_id]["courier_id"] == courier_id:
                current_shift_status = MOCK_SHIFTS_DB[shift_id]["status"]
                if current_shift_status in ["active", "planned"]:
                    MOCK_SHIFTS_DB[shift_id]["status"] = "cancelled_by_support"
                    MOCK_SHIFTS_DB[shift_id]["cancellation_reason"] = reason
                    message_parts.append(f"Смена {shift_id} (была {current_shift_status}) для курьера {courier_name} (ID: {courier_id}) удалена.")
                    shift_found_and_deleted = True
                    action_successful = True # Считаем успешным, если хотя бы это действие выполнено
                else:
                    message_parts.append(f"Смена {shift_id} для курьера {courier_name} уже не активна/запланирована (статус: {current_shift_status}). Удаление не требуется.")
                    # Можно считать это успехом, т.к. цель (неактивная смена) достигнута
                    action_successful = True
            else:
                message_parts.append(f"Смена с ID {shift_id} не найдена или не принадлежит курьеру {courier_name}.")
        else: # Если ID смены не указан, ищем ближайшую активную/запланированную
            # Это более сложная логика, для мока можно упростить или требовать shift_id
            # Пока что, если shift_id не указан, будем считать, что удалять нечего или нужна конкретика
            message_parts.append("ID смены для удаления не указан. Невозможно удалить неопределенную смену.")
            # action_successful остается False, если это единственное действие

        if not shift_found_and_deleted and not action_successful: # Если не нашли что удалять и это было единственное действие
            message_parts.append(f"Не удалось найти подходящую смену для удаления для курьера {courier_name}.")


    elif action_type == "ban_courier":
        if MOCK_COURIERS_DB[courier_id]["status"] == "banned_by_support":
            message_parts.append(f"Курьер {courier_name} (ID: {courier_id}) уже заблокирован.")
        else:
            MOCK_COURIERS_DB[courier_id]["status"] = "banned_by_support"
            MOCK_COURIERS_DB[courier_id]["ban_reason"] = reason
            # Увеличиваем страйки при бане (например)
            MOCK_COURIERS_DB[courier_id]["strikes"] = MOCK_COURIERS_DB[courier_id].get("strikes", 0) + 3
            message_parts.append(f"Курьер {courier_name} (ID: {courier_id}) успешно заблокирован.")
        action_successful = True # Блокировка всегда "успешна" в терминах выполнения

        # Дополнительно: отменить все активные/запланированные смены забаненного курьера
        cancelled_shifts_count = 0
        for s_id, shift_data in MOCK_SHIFTS_DB.items():
            if shift_data["courier_id"] == courier_id and shift_data["status"] in ["active", "planned"]:
                shift_data["status"] = "cancelled_due_to_ban"
                shift_data["cancellation_reason"] = f"Автоматическая отмена из-за блокировки курьера: {reason}"
                cancelled_shifts_count +=1
        if cancelled_shifts_count > 0:
            message_parts.append(f"Дополнительно отменено {cancelled_shifts_count} активных/запланированных смен курьера.")


    elif action_type == "log_complaint":
        MOCK_COURIERS_DB[courier_id]["strikes"] = MOCK_COURIERS_DB[courier_id].get("strikes", 0) + 1
        # Можно добавить лог жалоб в MOCK_COURIERS_DB[courier_id]["complaints_log"] = [...]
        if "complaints_log" not in MOCK_COURIERS_DB[courier_id]:
            MOCK_COURIERS_DB[courier_id]["complaints_log"] = []
        MOCK_COURIERS_DB[courier_id]["complaints_log"].append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "action_by": "support_agent_v2"
        })
        message_parts.append(
            f"Жалоба на курьера {courier_name} (ID: {courier_id}) зарегистрирована. "
            f"Причина: {reason}. Текущее количество страйков: {MOCK_COURIERS_DB[courier_id]['strikes']}."
        )
        action_successful = True

    # Можно добавить действие "предупреждение" (warning), которое не увеличивает страйки, но логируется
    elif action_type == "issue_warning":
        if "warnings_log" not in MOCK_COURIERS_DB[courier_id]:
            MOCK_COURIERS_DB[courier_id]["warnings_log"] = []
        MOCK_COURIERS_DB[courier_id]["warnings_log"].append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "action_by": "support_agent_v2"
        })
        message_parts.append(f"Курьеру {courier_name} (ID: {courier_id}) вынесено предупреждение. Причина: {reason}.")
        action_successful = True

    else:
        msg = f"Неизвестный тип действия: '{action_type}'. Доступные: 'delete_shift', 'ban_courier', 'log_complaint', 'issue_warning'."
        logger.error(msg)
        message_parts.append(msg)
        action_successful = False

    final_message = " ".join(message_parts) if message_parts else "Действие не привело к изменениям или не было выполнено."
    if not action_successful and not message_parts: # Если ничего не произошло и сообщений нет
        final_message = f"Не удалось выполнить действие '{action_type}' для курьера {courier_name}."

    return {"success": action_successful, "message": final_message.strip()}