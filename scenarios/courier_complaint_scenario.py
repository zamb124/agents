# scenarios/courier_complaint_scenario.py
import logging
import json
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.enums import ChatAction

from .base_scenario import BaseScenario
from agents.information_collector_agent import run_information_collector
from agents.decision_maker_agent import run_decision_maker

logger = logging.getLogger(__name__)

MAIN_CHAT_HISTORY_KEY = "chat_history_list_v2"
CONFIRMATION_MARKER = "[CONFIRMATION_REQUEST]"
# Строки, указывающие на ошибку от агентов
AGENT_ERROR_INDICATORS = [
    "Произошла серьезная ошибка при принятии решения агентом:",
    "Произошла внутренняя ошибка при обработке вашего запроса агентом сбора информации:",
    "К сожалению, не удалось принять однозначное решение.", # Общая ошибка от decision_maker
    "Нет ответа от агента сбора." # Если коллектор вернул пустой message
]


async def get_main_chat_history(state: FSMContext) -> list:
    data = await state.get_data()
    return data.get(MAIN_CHAT_HISTORY_KEY, [])

async def add_to_main_chat_history(state: FSMContext, user_message: str, ai_message: str):
    data = await state.get_data()
    history = data.get(MAIN_CHAT_HISTORY_KEY, [])
    history.append({"type": "human", "content": user_message})
    history.append({"type": "ai", "content": ai_message})
    if len(history) > 10:
        history = history[-10:]
    await state.update_data({MAIN_CHAT_HISTORY_KEY: history})


class CourierComplaintScenario(BaseScenario):
    COLLECTOR_DONE_SUFFIX = "collector_done"
    COLLECTED_DATA_SUFFIX = "collected_data"
    DECISION_PENDING_CONFIRMATION_SUFFIX = "decision_pending_confirmation"
    FINISHED_SUFFIX = "finished"

    id = "courier_complaint"
    friendly_name = "Жалобы на курьеров"
    description = "Обработка жалоб и инцидентов, связанных с курьерами (опоздания, невыход, поведение и т.д.)."

    def _is_agent_error_response(self, response_text: str) -> bool:
        """Проверяет, является ли ответ сообщением об ошибке от агента."""
        if not response_text: return True # Пустой ответ тоже ошибка
        return any(indicator in response_text for indicator in AGENT_ERROR_INDICATORS)

    async def handle_message(self, message: Message) -> None:
        user_input = message.text
        chat_id = message.chat.id

        if await self.get_scenario_data(self.FINISHED_SUFFIX, default=False):
            logger.warning(f"[{self.id}] Сообщение '{user_input[:50]}...' пришло в уже завершенный сценарий.")
            await message.answer("Этот инцидент уже был обработан. Если у вас новый вопрос или инцидент, пожалуйста, опишите его.")
            return

        current_main_chat_history = await get_main_chat_history(self.state)
        collector_is_done = await self.get_scenario_data(self.COLLECTOR_DONE_SUFFIX, default=False)
        decision_pending_confirmation = await self.get_scenario_data(self.DECISION_PENDING_CONFIRMATION_SUFFIX, default=False)
        collected_data_for_decision = await self.get_scenario_data(self.COLLECTED_DATA_SUFFIX)


        if decision_pending_confirmation:
            logger.info(f"[{self.id}] Получен ответ пользователя ('{user_input[:50]}...') на запрос подтверждения.")
            if not collected_data_for_decision:
                logger.error(f"[{self.id}] Ожидалось подтверждение, но нет сохраненных collected_data.")
                await message.answer("Произошла внутренняя ошибка: не найдены данные для подтверждения. Пожалуйста, начните описание инцидента заново.")
                await self.update_scenario_data(**{self.FINISHED_SUFFIX: True, self.DECISION_PENDING_CONFIRMATION_SUFFIX: False})
                return

            await self.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            decision_response_raw = await run_decision_maker(collected_data_for_decision, user_confirmation_response=user_input)

            if self._is_agent_error_response(decision_response_raw):
                await message.answer(decision_response_raw)
                await add_to_main_chat_history(self.state, user_input, decision_response_raw)
                await self.update_scenario_data(**{self.FINISHED_SUFFIX: True, self.DECISION_PENDING_CONFIRMATION_SUFFIX: False})
                logger.error(f"[{self.id}] DecisionMakerAgent (на этапе подтверждения) вернул ошибку: {decision_response_raw}")
                return

            if CONFIRMATION_MARKER in decision_response_raw:
                response_to_user = decision_response_raw.replace(CONFIRMATION_MARKER, "").strip()
                await message.answer(response_to_user)
                await add_to_main_chat_history(self.state, user_input, response_to_user)
                logger.info(f"[{self.id}] DecisionMakerAgent снова запросил подтверждение: '{response_to_user[:100]}...'")
            else:
                await message.answer(decision_response_raw)
                await add_to_main_chat_history(self.state, user_input, decision_response_raw)
                await self.update_scenario_data(**{
                    self.FINISHED_SUFFIX: True,
                    self.DECISION_PENDING_CONFIRMATION_SUFFIX: False
                })
                logger.info(f"[{self.id}] DecisionMakerAgent ответил после подтверждения. Сценарий завершен.")
            return

        if not collector_is_done:
            logger.info(f"[{self.id}] Запуск InformationCollectorAgent для {self.user_login} с сообщением: '{user_input[:50]}...'")
            collector_response = await run_information_collector(
                user_input, current_main_chat_history, self.user_login
            )
            agent_message_to_user = collector_response.get("agent_message", "Нет ответа от агента сбора.")

            if self._is_agent_error_response(agent_message_to_user) or collector_response["status"] == "error":
                actual_error_message = agent_message_to_user if agent_message_to_user else "Произошла неизвестная ошибка в агенте сбора информации."
                await message.answer(actual_error_message)
                await add_to_main_chat_history(self.state, user_input, actual_error_message)
                await self.update_scenario_data(**{self.FINISHED_SUFFIX: True})
                logger.error(f"[{self.id}] InformationCollectorAgent вернул ошибку: {actual_error_message}")
                return

            if collector_response["status"] == "in_progress":
                await message.answer(agent_message_to_user)
                await add_to_main_chat_history(self.state, user_input, agent_message_to_user)
                logger.info(f"[{self.id}] InformationCollectorAgent (in_progress): '{agent_message_to_user[:100]}...'")
                return
            elif collector_response["status"] == "completed":
                collected_data_from_agent = collector_response["data"]
                await self.update_scenario_data(
                    **{
                        self.COLLECTED_DATA_SUFFIX: collected_data_from_agent,
                        self.COLLECTOR_DONE_SUFFIX: True,
                    }
                )
                collected_data_for_decision = collected_data_from_agent
                logger.info(f"[{self.id}] InformationCollectorAgent завершил сбор.")
                if agent_message_to_user and agent_message_to_user != "Информация собрана. Передаю для анализа и принятия решения.":
                    await message.answer(agent_message_to_user)
                    await add_to_main_chat_history(self.state, user_input, agent_message_to_user)
            # 'error' status is handled above by _is_agent_error_response
            elif collector_response["status"] != "error": # Should not happen if all statuses are covered
                logger.error(f"[{self.id}] Неизвестный статус '{collector_response.get('status')}' от InformationCollectorAgent.")
                await message.answer("Произошла ошибка в агенте сбора информации (неизвестный статус).")
                await self.update_scenario_data(**{self.FINISHED_SUFFIX: True})
                return

        if await self.get_scenario_data(self.COLLECTOR_DONE_SUFFIX, default=False) and \
                not await self.get_scenario_data(self.DECISION_PENDING_CONFIRMATION_SUFFIX, default=False):

            if not collected_data_for_decision:
                logger.error(f"[{self.id}] Сборщик помечен как завершенный, но нет данных для DecisionMaker.")
                await message.answer("Внутренняя ошибка: данные не переданы для решения.")
                await self.update_scenario_data(**{self.FINISHED_SUFFIX: True})
                return

            status_update_msg = "Спасибо, информация собрана. Готовлю предложение по дальнейшим действиям..."
            await message.answer(status_update_msg)
            await self.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

            # Определяем, нужно ли добавлять user_input и status_update_msg в историю
            # Это нужно, если последний AI ответ не был от коллектора о завершении сбора
            # или если это не наше сообщение о подготовке.
            history = await get_main_chat_history(self.state)
            add_status_update_to_history = True
            if history:
                last_ai_message_in_history = history[-1]["content"]
                if "Информация собрана. Передаю для анализа и принятия решения." in last_ai_message_in_history or \
                        status_update_msg in last_ai_message_in_history:
                    add_status_update_to_history = False

            if add_status_update_to_history:
                # Если последнее сообщение в истории не от пользователя, или оно не совпадает с текущим user_input,
                # то добавляем user_input и status_update_msg.
                # Это предотвращает дублирование user_input, если коллектор ответил на предыдущее сообщение.
                if not history or history[-2].get("content") != user_input:
                    await add_to_main_chat_history(self.state, user_input, status_update_msg)
                else: # Если user_input уже есть, просто обновляем последний AI ответ
                    history[-1]["content"] = status_update_msg
                    await self.state.update_data({MAIN_CHAT_HISTORY_KEY: history})


            logger.info(f"[{self.id}] Запуск DecisionMakerAgent (первичный анализ) с данными: {json.dumps(collected_data_for_decision, ensure_ascii=False, indent=2)}")
            decision_response_raw = await run_decision_maker(collected_data_for_decision)

            if self._is_agent_error_response(decision_response_raw):
                await message.answer(decision_response_raw)
                # Обновляем историю: после status_update_msg пришла ошибка
                history = await get_main_chat_history(self.state)
                if history and history[-1]["content"] == status_update_msg:
                    history.append({"type": "human", "content": "(ответ на запрос подтверждения не получен - ошибка агента)"}) # Техническая запись
                    history.append({"type": "ai", "content": decision_response_raw})
                    await self.state.update_data({MAIN_CHAT_HISTORY_KEY: history})
                else: # Если status_update_msg не был последним, добавляем как обычно
                    await add_to_main_chat_history(self.state, user_input, decision_response_raw)

                await self.update_scenario_data(**{self.FINISHED_SUFFIX: True})
                logger.error(f"[{self.id}] DecisionMakerAgent (первичный анализ) вернул ошибку: {decision_response_raw}")
                return

            if CONFIRMATION_MARKER in decision_response_raw:
                response_to_user = decision_response_raw.replace(CONFIRMATION_MARKER, "").strip()
                await message.answer(response_to_user)
                # Обновляем историю: после status_update_msg пришел запрос на подтверждение
                history = await get_main_chat_history(self.state)
                if history and history[-1]["content"] == status_update_msg:
                    # Заменяем status_update_msg на реальный ответ агента, если user_input уже был
                    if history[-2].get("content") == user_input:
                        history[-1]["content"] = response_to_user
                    else: # Если user_input не был, добавляем его и ответ агента
                        history.append({"type": "human", "content": user_input}) # или техническая запись
                        history.append({"type": "ai", "content": response_to_user})
                    await self.state.update_data({MAIN_CHAT_HISTORY_KEY: history})
                else: # Если status_update_msg не был последним, добавляем как обычно
                    await add_to_main_chat_history(self.state, user_input, response_to_user)

                await self.update_scenario_data(**{self.DECISION_PENDING_CONFIRMATION_SUFFIX: True})
                logger.info(f"[{self.id}] DecisionMakerAgent запросил подтверждение: '{response_to_user[:100]}...'")
            else:
                await message.answer(decision_response_raw)
                await add_to_main_chat_history(self.state, user_input, decision_response_raw) # или status_update_msg, в зависимости от того, что было последним от AI
                await self.update_scenario_data(**{self.FINISHED_SUFFIX: True})
                logger.info(f"[{self.id}] DecisionMakerAgent сразу принял решение (без явного запроса подтверждения). Сценарий завершен.")


    async def is_finished(self) -> bool:
        return await self.get_scenario_data(self.FINISHED_SUFFIX, default=False)