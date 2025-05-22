# scenarios/courier_complaint_scenario.py
import logging
import json
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.enums import ChatAction

from .base_scenario import BaseScenario
# Убираем InformationCollectorAgent, DecisionMakerAgent остается
from agents.decision_maker_agent import run_decision_maker
from agents.detail_collector_agent import run_detail_collector_llm, DETAILS_COLLECTED_MARKER # Новый импорт

# Инструменты для прямого вызова из сценария
from tools.warehouse_api import find_warehouse_by_name_or_id # Убрали get_warehouse_by_director_login
from tools.courier_api import search_courier_by_id_or_name, get_courier_shifts
from tools.rag_client import query_rag_service


logger = logging.getLogger(__name__)

MAIN_CHAT_HISTORY_KEY = "chat_history_list_v2" # Общая история для роутера и др.
SCENARIO_STATE_KEY = "courier_complaint_fsm_state" # Состояние FSM для этого сценария
CONFIRMED_WAREHOUSE_KEY = "confirmed_warehouse_info"
CONFIRMED_COURIER_KEY = "confirmed_courier_info"
INITIAL_COMPLAINT_KEY = "initial_complaint_text" # Первоначальное описание проблемы
DETAIL_CHAT_HISTORY_KEY = "detail_collector_chat_history" # История для сборщика деталей
COLLECTED_INCIDENT_DETAILS_KEY = "collected_incident_details" # Детали от сборщика
FINAL_COLLECTED_DATA_KEY = "final_incident_data_for_decision" # Полный JSON для DecisionMaker

# Состояния FSM
STATE_START = "START"
STATE_ASK_WAREHOUSE = "ASK_WAREHOUSE"
STATE_VALIDATE_WAREHOUSE = "VALIDATE_WAREHOUSE" # Ожидание ввода склада
STATE_CONFIRM_WAREHOUSE = "CONFIRM_WAREHOUSE"   # Ожидание да/нет по складу
STATE_ASK_COURIER = "ASK_COURIER"
STATE_VALIDATE_COURIER = "VALIDATE_COURIER"     # Ожидание ввода курьера
STATE_CONFIRM_COURIER = "CONFIRM_COURIER"       # Ожидание да/нет по курьеру
STATE_COLLECT_INCIDENT_DETAILS_INIT = "COLLECT_INCIDENT_DETAILS_INIT" # Начало сбора деталей
STATE_COLLECT_INCIDENT_DETAILS_ITERATE = "COLLECT_INCIDENT_DETAILS_ITERATE" # Итеративный сбор
STATE_FINALIZE_DATA = "FINALIZE_DATA"           # Сборка всего в JSON
STATE_AWAIT_DECISION_CONFIRMATION = "AWAIT_DECISION_CONFIRMATION"
STATE_PROCESS_DECISION = "PROCESS_DECISION"
STATE_FINISHED = "FINISHED"

# Маркер для DecisionMakerAgent
CONFIRMATION_MARKER_DM = "[CONFIRMATION_REQUEST]" # Переименовал, чтобы не путать с DETAILS_COLLECTED_MARKER

async def get_scenario_fsm_state(state: FSMContext) -> str:
    data = await state.get_data()
    return data.get(SCENARIO_STATE_KEY, STATE_START)

async def set_scenario_fsm_state(state: FSMContext, new_fsm_state: str):
    await state.update_data({SCENARIO_STATE_KEY: new_fsm_state})
    logger.info(f"[CourierComplaintScenario] FSM state changed to: {new_fsm_state}")

async def add_to_detail_chat_history(state: FSMContext, user_message: str, ai_message: str):
    data = await state.get_data()
    history = data.get(DETAIL_CHAT_HISTORY_KEY, [])
    history.append({"type": "human", "content": user_message})
    history.append({"type": "ai", "content": ai_message})
    if len(history) > 8: # Ограничиваем историю для сборщика деталей
        history = history[-8:]
    await state.update_data({DETAIL_CHAT_HISTORY_KEY: history})

class CourierComplaintScenario(BaseScenario):
    id = "courier_complaint"
    friendly_name = "Жалобы на курьеров"
    description = "Обработка жалоб и инцидентов, связанных с курьерами."

    async def handle_message(self, message: Message) -> None:
        user_input = message.text.strip()
        chat_id = message.chat.id
        current_fsm_state = await get_scenario_fsm_state(self.state)
        fsm_data = await self.state.get_data()

        logger.info(f"[{self.id}] Handling message in state '{current_fsm_state}'. User: '{user_input[:50]}'")
        await self.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        if current_fsm_state == STATE_START:
            # Пользователь выбрал этот сценарий. Сохраняем его первоначальный запрос, если он не просто "жалоба на курьера"
            if user_input.lower() not in ["жалоба на курьера", self.id, self.friendly_name.lower()]:
                await self.state.update_data({INITIAL_COMPLAINT_KEY: user_input})
            await message.answer("Пожалуйста, укажите название или ID вашего склада.")
            await set_scenario_fsm_state(self.state, STATE_VALIDATE_WAREHOUSE)
            return

        # --- Определение склада ---
        if current_fsm_state == STATE_VALIDATE_WAREHOUSE:
            warehouse_search_result = await find_warehouse_by_name_or_id(user_input)
            if warehouse_search_result.get("success") == True:
                wh_info = warehouse_search_result["warehouse_info"]
                await self.state.update_data({"temp_warehouse_info": wh_info}) # Временно сохраняем для подтверждения
                await message.answer(f"Найден склад: {wh_info['warehouse_name']} (ID: {wh_info['warehouse_id']}). Это ваш склад? (да/нет)")
                await set_scenario_fsm_state(self.state, STATE_CONFIRM_WAREHOUSE)
            elif warehouse_search_result.get("success") == "multiple_found":
                await message.answer(warehouse_search_result["message"]) # Сообщение уже содержит просьбу уточнить
                # Остаемся в STATE_VALIDATE_WAREHOUSE для получения уточненного ввода
            else: # success == False
                await message.answer(warehouse_search_result.get("message", "Склад не найден. Попробуйте еще раз."))
                # Остаемся в STATE_VALIDATE_WAREHOUSE
            return

        if current_fsm_state == STATE_CONFIRM_WAREHOUSE:
            temp_wh_info = fsm_data.get("temp_warehouse_info")
            if not temp_wh_info: # Не должно случиться
                await message.answer("Произошла ошибка, информация о предложенном складе потеряна. Пожалуйста, укажите склад заново.")
                await set_scenario_fsm_state(self.state, STATE_VALIDATE_WAREHOUSE)
                return

            if user_input.lower() in ["да", "да это он", "верно", "подтверждаю"]:
                await self.state.update_data({CONFIRMED_WAREHOUSE_KEY: temp_wh_info, "temp_warehouse_info": None})
                await message.answer(f"Склад {temp_wh_info['warehouse_name']} подтвержден. Теперь укажите ФИО или ID курьера.")
                await set_scenario_fsm_state(self.state, STATE_VALIDATE_COURIER)
            elif user_input.lower() in ["нет", "не он", "неверно", "не подтверждаю"]:
                await message.answer("Понял. Пожалуйста, укажите корректное название или ID вашего склада.")
                await self.state.update_data({"temp_warehouse_info": None})
                await set_scenario_fsm_state(self.state, STATE_VALIDATE_WAREHOUSE)
            else:
                await message.answer(f"Непонятный ответ. Найден склад: {temp_wh_info['warehouse_name']} (ID: {temp_wh_info['warehouse_id']}). Это ваш склад? (Пожалуйста, ответьте 'да' или 'нет')")
            return

        # --- Определение курьера ---
        confirmed_warehouse = fsm_data.get(CONFIRMED_WAREHOUSE_KEY)
        if not confirmed_warehouse and current_fsm_state not in [STATE_START, STATE_VALIDATE_WAREHOUSE, STATE_CONFIRM_WAREHOUSE]:
            logger.error(f"[{self.id}] Достигнуто состояние {current_fsm_state} без подтвержденного склада!")
            await message.answer("Сначала нужно определить склад. Пожалуйста, укажите название или ID вашего склада.")
            await set_scenario_fsm_state(self.state, STATE_VALIDATE_WAREHOUSE)
            return

        if current_fsm_state == STATE_VALIDATE_COURIER:
            courier_search_result = search_courier_by_id_or_name(user_input, confirmed_warehouse["warehouse_id"])
            if courier_search_result.get("success") == True:
                courier_info = courier_search_result["courier_info"]
                await self.state.update_data({"temp_courier_info": courier_info})
                await message.answer(f"Найден курьер: {courier_info['full_name']} (ID: {courier_info['id']}). Это он? (да/нет)")
                await set_scenario_fsm_state(self.state, STATE_CONFIRM_COURIER)
            elif courier_search_result.get("success") == "multiple_found":
                await message.answer(courier_search_result["message"])
            else: # success == False
                await message.answer(courier_search_result.get("message", "Курьер не найден на этом складе. Попробуйте еще раз."))
            return

        if current_fsm_state == STATE_CONFIRM_COURIER:
            temp_courier_info = fsm_data.get("temp_courier_info")
            if not temp_courier_info:
                await message.answer("Ошибка, информация о предложенном курьере потеряна. Укажите ФИО/ID заново.")
                await set_scenario_fsm_state(self.state, STATE_VALIDATE_COURIER)
                return

            if user_input.lower() in ["да", "да это он", "верно", "подтверждаю"]:
                await self.state.update_data({CONFIRMED_COURIER_KEY: temp_courier_info, "temp_courier_info": None})
                await message.answer(f"Курьер {temp_courier_info['full_name']} подтвержден.")
                # Сохраняем первоначальную жалобу, если она была до этого момента
                initial_complaint = fsm_data.get(INITIAL_COMPLAINT_KEY, "Общая жалоба на поведение курьера.")
                if not fsm_data.get(INITIAL_COMPLAINT_KEY): # Если пользователь просто выбрал сценарий
                    await self.state.update_data({INITIAL_COMPLAINT_KEY: "Проблема с курьером (детали уточняются)."})

                # Первый вызов LLM для сбора деталей
                detail_collector_response = await run_detail_collector_llm(
                    warehouse_name=confirmed_warehouse["warehouse_name"],
                    courier_name=temp_courier_info["full_name"],
                    initial_user_complaint=initial_complaint, # Это может быть первое сообщение пользователя
                    current_user_input=initial_complaint, # Начинаем с этого
                    detail_chat_history=[]
                )
                await message.answer(detail_collector_response["agent_message"])
                if detail_collector_response["status"] == "in_progress":
                    await add_to_detail_chat_history(self.state, initial_complaint, detail_collector_response["agent_message"])
                    await set_scenario_fsm_state(self.state, STATE_COLLECT_INCIDENT_DETAILS_ITERATE)
                elif detail_collector_response["status"] == "completed": # Маловероятно на первом шаге
                    await self.state.update_data({COLLECTED_INCIDENT_DETAILS_KEY: detail_collector_response["collected_details"]})
                    await set_scenario_fsm_state(self.state, STATE_FINALIZE_DATA)
                    await self.handle_message(message) # Повторный вызов для перехода к финализации
                else: # error
                    await set_scenario_fsm_state(self.state, STATE_FINISHED) # Завершаем при ошибке
            elif user_input.lower() in ["нет", "не он", "неверно", "не подтверждаю"]:
                await message.answer("Понял. Пожалуйста, укажите корректное ФИО или ID курьера.")
                await self.state.update_data({"temp_courier_info": None})
                await set_scenario_fsm_state(self.state, STATE_VALIDATE_COURIER)
            else:
                await message.answer(f"Непонятный ответ. Найден курьер: {temp_courier_info['full_name']} (ID: {temp_courier_info['id']}). Это он? (да/нет)")
            return

        # --- Сбор деталей инцидента ---
        if current_fsm_state == STATE_COLLECT_INCIDENT_DETAILS_ITERATE:
            confirmed_courier = fsm_data.get(CONFIRMED_COURIER_KEY)
            initial_complaint = fsm_data.get(INITIAL_COMPLAINT_KEY)
            detail_history = fsm_data.get(DETAIL_CHAT_HISTORY_KEY, [])

            detail_collector_response = await run_detail_collector_llm(
                warehouse_name=confirmed_warehouse["warehouse_name"],
                courier_name=confirmed_courier["full_name"],
                initial_user_complaint=initial_complaint,
                current_user_input=user_input,
                detail_chat_history=detail_history
            )
            await message.answer(detail_collector_response["agent_message"])
            if detail_collector_response["status"] == "in_progress":
                await add_to_detail_chat_history(self.state, user_input, detail_collector_response["agent_message"])
                # Остаемся в STATE_COLLECT_INCIDENT_DETAILS_ITERATE
            elif detail_collector_response["status"] == "completed":
                await self.state.update_data({COLLECTED_INCIDENT_DETAILS_KEY: detail_collector_response["collected_details"]})
                await set_scenario_fsm_state(self.state, STATE_FINALIZE_DATA)
                # Нужен "пустой" message или специальный вызов для финализации, т.к. user_input уже обработан
                # Создадим фиктивное сообщение или вызовем финализацию напрямую
                logger.info(f"[{self.id}] Детали собраны, переход к финализации.")
                await self.finalize_and_trigger_decision_maker(message) # Передаем оригинальное сообщение для контекста чата
            else: # error
                await set_scenario_fsm_state(self.state, STATE_FINISHED)
            return

        # --- Финализация данных и вызов DecisionMaker ---
        if current_fsm_state == STATE_FINALIZE_DATA:
            # Этот блок теперь вызывается из _trigger_decision_maker или напрямую после сбора деталей
            # await self.finalize_and_trigger_decision_maker(message) # Не здесь, а по событию
            logger.warning(f"[{self.id}] Неожиданное попадание в STATE_FINALIZE_DATA через handle_message.")
            return


        # --- Обработка подтверждения от DecisionMaker ---
        if current_fsm_state == STATE_AWAIT_DECISION_CONFIRMATION:
            final_data = fsm_data.get(FINAL_COLLECTED_DATA_KEY)
            if not final_data:
                logger.error(f"[{self.id}] Ожидалось подтверждение решения, но нет final_data.")
                await message.answer("Произошла внутренняя ошибка. Пожалуйста, начните заново.")
                await set_scenario_fsm_state(self.state, STATE_FINISHED)
                return

            decision_response = await run_decision_maker(final_data, user_confirmation_response=user_input)

            if CONFIRMATION_MARKER_DM in decision_response: # DecisionMaker снова просит подтверждения
                response_to_user = decision_response.replace(CONFIRMATION_MARKER_DM, "").strip()
                await message.answer(response_to_user)
                # Остаемся в STATE_AWAIT_DECISION_CONFIRMATION
            else: # Финальный ответ
                await message.answer(decision_response)
                await set_scenario_fsm_state(self.state, STATE_FINISHED)
            return

        if current_fsm_state == STATE_FINISHED:
            await message.answer("Этот инцидент уже обработан. Если у вас новый вопрос, пожалуйста, начните новый диалог или опишите его.")
            return

    async def finalize_and_trigger_decision_maker(self, original_message_for_context: Message):
        """Собирает все данные и вызывает DecisionMaker."""
        fsm_data = await self.state.get_data()
        confirmed_warehouse = fsm_data.get(CONFIRMED_WAREHOUSE_KEY)
        confirmed_courier = fsm_data.get(CONFIRMED_COURIER_KEY)
        collected_details = fsm_data.get(COLLECTED_INCIDENT_DETAILS_KEY)
        initial_complaint = fsm_data.get(INITIAL_COMPLAINT_KEY) # Для даты, если не уточнена

        if not all([confirmed_warehouse, confirmed_courier, collected_details]):
            logger.error(f"[{self.id}] Недостаточно данных для финализации: WH={bool(confirmed_warehouse)}, C={bool(confirmed_courier)}, Details={bool(collected_details)}")
            await original_message_for_context.answer("Произошла ошибка: не все данные были собраны для принятия решения.")
            await set_scenario_fsm_state(self.state, STATE_FINISHED)
            return

        # Определение даты инцидента (приоритет из деталей, потом из initial_complaint, потом сегодня)
        incident_date_str = collected_details.get("incident_date") # Предполагаем, что сборщик деталей может вернуть и дату
        if not incident_date_str:
            # Пытаемся извлечь из initial_complaint или ставим сегодня (упрощенно)
            # В реальном мире сборщик деталей должен был бы это уточнить.
            # Для примера, если сборщик деталей не вернул дату, ставим сегодняшнюю.
            # В промпте сборщика деталей нужно добавить сбор даты, если она еще не ясна.
            # Пока что, если сборщик деталей не вернул дату, мы ее не имеем.
            # Это должно быть частью collected_details.incident_date
            # Для простоты, если detail_collector не вернул дату, мы ее не знаем.
            # Это должно быть частью его JSON.
            # Допустим, сборщик деталей ОБЯЗАН вернуть incident_date.
            # Если нет, то это ошибка логики сборщика.
            # Для примера, если initial_complaint содержит "сегодня", можно взять сегодняшнюю дату.
            # Но лучше, чтобы detail_collector вернул дату.
            # Пока что, если нет, то это проблема.
            # Для теста, если нет, поставим сегодня.
            # collected_details["incident_date"] = datetime.now().strftime("%Y-%m-%d")
            # НО! Лучше, чтобы detail_collector это делал.
            # В промпте detail_collector есть "Точная дата уже должна быть известна или уточнена ранее."
            # Значит, мы должны были ее получить до этапа сбора деталей или на нем.
            # Для простоты, если ее нет в collected_details, это ошибка.
            # Но для работы примера, если ее нет, то это будет null.
            pass


        # Вызов get_courier_shifts
        shifts_data = {}
        if confirmed_courier.get("id") and collected_details.get("incident_date"):
            shifts_result = await get_courier_shifts(confirmed_courier["id"], collected_details["incident_date"])
            if shifts_result.get("success"):
                shifts_data = {"courier_had_shift_on_incident_date": bool(shifts_result.get("shifts")), "shift_details": shifts_result.get("shifts")}
            else:
                shifts_data = {"courier_had_shift_on_incident_date": False, "shift_details": [], "error_message": shifts_result.get("message")}
        else:
            shifts_data = {"courier_had_shift_on_incident_date": False, "shift_details": []}


        # Вызов query_knowledge_base
        rag_extracts = []
        if collected_details.get("incident_description_detailed"):
            rag_result = await query_rag_service(
                query_text=collected_details["incident_description_detailed"],
                collection_name="courier_job_description"
            )
            if rag_result.get("success") and rag_result.get("data"):
                rag_extracts = rag_result["data"] # Это уже список объектов {"text": ..., "source": ...}

        final_json_payload = {
            "courier_id": confirmed_courier.get("id"),
            "courier_name": confirmed_courier.get("full_name"),
            "warehouse_id": confirmed_warehouse.get("warehouse_id"),
            "warehouse_name": confirmed_warehouse.get("warehouse_name"),
            "incident_description": collected_details.get("incident_description_detailed"),
            "incident_date": collected_details.get("incident_date"), # Должно быть от сборщика деталей
            "incident_time": collected_details.get("incident_time_уточненное"),
            "incident_location_details": collected_details.get("incident_location_details"),
            "incident_witnesses_additional": collected_details.get("incident_witnesses_additional"),
            "actions_taken_immediately": collected_details.get("actions_taken_immediately"),
            "immediate_consequences": collected_details.get("immediate_consequences"),
            **shifts_data, # Добавляем courier_had_shift_on_incident_date и shift_details
            "job_instruction_extracts": rag_extracts
        }
        await self.state.update_data({FINAL_COLLECTED_DATA_KEY: final_json_payload})

        logger.info(f"[{self.id}] Финальный JSON для DecisionMaker: {json.dumps(final_json_payload, ensure_ascii=False, indent=2)}")
        await original_message_for_context.answer("Вся информация собрана. Передаю для анализа и принятия решения...")

        decision_response = await run_decision_maker(final_json_payload)
        if CONFIRMATION_MARKER_DM in decision_response:
            response_to_user = decision_response.replace(CONFIRMATION_MARKER_DM, "").strip()
            await original_message_for_context.answer(response_to_user)
            await set_scenario_fsm_state(self.state, STATE_AWAIT_DECISION_CONFIRMATION)
        else: # Финальный ответ без запроса подтверждения
            await original_message_for_context.answer(decision_response)
            await set_scenario_fsm_state(self.state, STATE_FINISHED)


    async def is_finished(self) -> bool:
        current_fsm_state = await get_scenario_fsm_state(self.state)
        return current_fsm_state == STATE_FINISHED

    async def clear_scenario_data(self):
        # Очищаем специфичные для сценария ключи FSM
        await self.state.update_data({
            SCENARIO_STATE_KEY: None,
            CONFIRMED_WAREHOUSE_KEY: None,
            CONFIRMED_COURIER_KEY: None,
            INITIAL_COMPLAINT_KEY: None,
            DETAIL_CHAT_HISTORY_KEY: None,
            COLLECTED_INCIDENT_DETAILS_KEY: None,
            FINAL_COLLECTED_DATA_KEY: None,
            "temp_warehouse_info": None, # Очищаем временные ключи тоже
            "temp_courier_info": None,
        })
        # Вызов родительского метода для очистки ключей с префиксом scenario_self.id_
        # Это не нужно, если мы управляем всеми ключами явно.
        # await super().clear_scenario_data()
        logger.info(f"[{self.id}] Данные сценария CourierComplaint очищены из FSM.")