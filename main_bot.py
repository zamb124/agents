# main_bot.py
import asyncio
import logging
import json
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode, ChatAction

from config import TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, GOOGLE_API_KEY # Добавил GOOGLE_API_KEY для полноты

# Импорты агентов и сценариев
from agents.router_agent import run_router_agent
from scenarios.base_scenario import BaseScenario
from scenarios.courier_complaint_scenario import (
    CourierComplaintScenario,
    set_scenario_fsm_state, # Импортируем функцию установки состояния
    STATE_START as COURIER_COMPLAINT_STATE_START, # Импортируем начальное состояние
    DETAIL_CHAT_HISTORY_KEY as COURIER_COMPLAINT_DETAIL_CHAT_HISTORY_KEY # Импортируем ключ истории
)
from scenarios.faq_general_scenario import FaqGeneralScenario

# Импорт для инициализации данных при старте
from tools.warehouse_api import load_warehouses_if_needed

# Проверка наличия ключей API
if not TELEGRAM_BOT_TOKEN:
    logging.critical("Токен Telegram бота не найден! Укажите TELEGRAM_BOT_TOKEN в .env файле.")
    raise ValueError("Токен Telegram бота не найден. Укажите TELEGRAM_BOT_TOKEN в .env файле.")

# Проверяем хотя бы один ключ для LLM
llm_api_key_found = False
if OPENAI_API_KEY:
    llm_api_key_found = True
    logging.info("Найден OpenAI API ключ.")
if GOOGLE_API_KEY: # Если используется Gemini
    llm_api_key_found = True
    logging.info("Найден Google API ключ.")

if not llm_api_key_found:
    logging.critical("API ключ для LLM не найден! Укажите OPENAI_API_KEY или GOOGLE_API_KEY в .env файле.")
    raise ValueError("API ключ для LLM не найден.")


for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(name)s [%(module)s.%(funcName)s:%(lineno)d] - %(message)s",
    stream=sys.stderr, # Принудительно выводим в stderr, чтобы точно видеть
    # force=True # В Python 3.8+ это делает то же, что и цикл удаления обработчиков выше
)

# Немедленная проверка логгера
root_logger = logging.getLogger() # Получаем корневой логгер
logger = logging.getLogger(__name__) # Логгер для текущего модуля

root_logger.critical("КРИТИЧЕСКОЕ сообщение от корневого логгера СРАЗУ ПОСЛЕ basicConfig.")
logger.critical("КРИТИЧЕСКОЕ сообщение от main_logger СРАЗУ ПОСЛЕ basicConfig.")

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

default_props = DefaultBotProperties(parse_mode=ParseMode.HTML)
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=default_props)

CHAT_HISTORY_KEY = "chat_history_list_v2"
ACTIVE_SCENARIO_KEY = "active_scenario_id_v2"

# --- Функции для работы с общей историей чата ---
async def get_chat_history_as_list(state: FSMContext) -> list:
    data = await state.get_data()
    return data.get(CHAT_HISTORY_KEY, [])

async def add_to_chat_history_list(state: FSMContext, user_message_content: str, ai_message_content: str):
    data = await state.get_data()
    history = data.get(CHAT_HISTORY_KEY, [])
    history.append({"type": "human", "content": user_message_content})
    history.append({"type": "ai", "content": ai_message_content})
    if len(history) > 10:
        history = history[-10:]
    await state.update_data({CHAT_HISTORY_KEY: history})
    logger.debug(f"Общая история чата обновлена. Последнее сообщение AI: '{ai_message_content[:50]}...'")

# --- Реестр доступных сценариев ---
AVAILABLE_SCENARIOS = {
    CourierComplaintScenario.id: CourierComplaintScenario,
    FaqGeneralScenario.id: FaqGeneralScenario,
}
logger.info(f"Зарегистрированные сценарии: {list(AVAILABLE_SCENARIOS.keys())}")


@dp.message(CommandStart())
async def command_start_handler(message: Message, state: FSMContext) -> None:
    await state.clear() # Очищаем ВСЕ данные FSM для пользователя
    logger.info(f"Пользователь {message.from_user.full_name} (ID: {message.from_user.id}, Login: {message.from_user.username}) отправил /start. Состояние FSM очищено.")
    await message.answer(
        f"Здравствуйте, {message.from_user.full_name}! Я ваш ИИ-ассистент поддержки.\n"
        "Опишите вашу проблему или вопрос, и я постараюсь помочь."
    )

@dp.message(F.text)
async def handle_text_message(message: Message, state: FSMContext) -> None:
    user_input = message.text
    director_login = message.from_user.username if message.from_user.username else f"user_id_{message.from_user.id}"
    chat_id = message.chat.id

    logger.info(f"Получено сообщение от {director_login} (chat_id: {chat_id}): '{user_input[:100]}...'")
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    fsm_data = await state.get_data()
    active_scenario_id = fsm_data.get(ACTIVE_SCENARIO_KEY)
    active_scenario_instance: BaseScenario | None = None

    if active_scenario_id and active_scenario_id in AVAILABLE_SCENARIOS:
        ScenarioClass = AVAILABLE_SCENARIOS[active_scenario_id]
        # Передаем initial_message_text, хотя для CourierComplaintScenario он может быть не так важен после рефакторинга
        active_scenario_instance = ScenarioClass(state, bot, director_login, user_input)

        if await active_scenario_instance.is_finished():
            logger.info(f"Активный сценарий '{active_scenario_id}' был завершен ранее. Сбрасываем его.")
            await active_scenario_instance.clear_scenario_data() # Сценарий сам очищает свои данные
            await state.update_data({ACTIVE_SCENARIO_KEY: None})
            active_scenario_id = None
            active_scenario_instance = None
        else:
            logger.info(f"Продолжаем работу в рамках активного сценария: {active_scenario_id}")

    if not active_scenario_instance:
        logger.info("Нет активного сценария или он завершен. Запускаем Агента-Маршрутизатора.")
        chat_history_for_router = await get_chat_history_as_list(state)
        router_result = await run_router_agent(user_input, chat_history_for_router, AVAILABLE_SCENARIOS)

        if router_result["type"] == "scenario_id":
            chosen_scenario_id = router_result["value"]
            if chosen_scenario_id in AVAILABLE_SCENARIOS:
                logger.info(f"Агент-Маршрутизатор выбрал сценарий: {chosen_scenario_id}.")

                # Если был активен другой сценарий, очищаем его данные перед переключением
                if active_scenario_id and active_scenario_id != chosen_scenario_id and active_scenario_id in AVAILABLE_SCENARIOS:
                    logger.warning(f"Переключение со сценария '{active_scenario_id}' на '{chosen_scenario_id}'. Очистка данных старого сценария.")
                    OldScenarioClass = AVAILABLE_SCENARIOS[active_scenario_id]
                    old_scenario_instance_to_clear = OldScenarioClass(state, bot, director_login, "Переключение сценария")
                    await old_scenario_instance_to_clear.clear_scenario_data()

                await state.update_data({ACTIVE_SCENARIO_KEY: chosen_scenario_id})

                # Инициализация состояния для CourierComplaintScenario
                if chosen_scenario_id == CourierComplaintScenario.id:
                    await set_scenario_fsm_state(state, COURIER_COMPLAINT_STATE_START)
                    await state.update_data({COURIER_COMPLAINT_DETAIL_CHAT_HISTORY_KEY: []}) # Очищаем историю деталей
                    logger.info(f"Для сценария '{chosen_scenario_id}' установлено начальное состояние FSM и очищена история деталей.")

                ScenarioClass = AVAILABLE_SCENARIOS[chosen_scenario_id]
                active_scenario_instance = ScenarioClass(state, bot, director_login, user_input)
                logger.info(f"Создан экземпляр сценария: {active_scenario_instance.id}")
            else:
                logger.warning(f"Агент-Маршрутизатор вернул неизвестный ID сценария: '{chosen_scenario_id}'")
                error_msg = "Извините, я не смог подобрать подходящий раздел для вашего запроса. Попробуйте переформулировать."
                await message.answer(error_msg)
                await add_to_chat_history_list(state, user_input, error_msg)
                return
        elif router_result["type"] == "question":
            question_to_user = router_result["value"]
            logger.info(f"Агент-Маршрутизатор задал уточняющий вопрос: '{question_to_user}'.")
            await message.answer(question_to_user)
            await add_to_chat_history_list(state, user_input, question_to_user)
            return
        else: # Ошибка от роутера
            error_msg = router_result.get("value", "Произошла ошибка при выборе нужного действия.")
            logger.error(f"Агент-Маршрутизатор вернул некорректный результат: {router_result}.")
            await message.answer(error_msg)
            await add_to_chat_history_list(state, user_input, error_msg)
            return

    if active_scenario_instance:
        logger.info(f"Вызов handle_message для сценария {active_scenario_instance.id} с сообщением: '{message.text[:50]}...'")
        try:
            await active_scenario_instance.handle_message(message)

            if await active_scenario_instance.is_finished():
                logger.info(f"Сценарий '{active_scenario_instance.id}' завершил свою работу после обработки сообщения.")
                await active_scenario_instance.clear_scenario_data()
                await state.update_data({ACTIVE_SCENARIO_KEY: None})
                # Не добавляем в общую историю сообщение о завершении сценария, если он сам не отправил финальный ответ
        except Exception as e:
            scenario_id_for_error = "неизвестного сценария"
            try:
                scenario_id_for_error = active_scenario_instance.id
            except: pass
            logger.error(f"Ошибка при выполнении сценария '{scenario_id_for_error}': {e}", exc_info=True)
            error_msg_to_user = "Произошла внутренняя ошибка при обработке вашего запроса в сценарии. Пожалуйста, попробуйте позже или начните заново."
            await message.answer(error_msg_to_user)

            logger.info(f"Принудительно сбрасываем активный сценарий '{scenario_id_for_error}' из-за ошибки.")
            try:
                if active_scenario_instance: # Если инстанс еще существует
                    await active_scenario_instance.clear_scenario_data()
            except Exception as e_clear:
                logger.error(f"Ошибка при попытке очистить данные сценария '{scenario_id_for_error}' после ошибки: {e_clear}", exc_info=True)
            await state.update_data({ACTIVE_SCENARIO_KEY: None})
            # Добавляем информацию об ошибке в общую историю, чтобы роутер ее видел
            await add_to_chat_history_list(state, user_input, f"Системная ошибка в сценарии: {type(e).__name__}. Пожалуйста, попробуйте еще раз или опишите проблему иначе.")
    else:
        # Эта ветка не должна достигаться, если роутер всегда возвращает либо ID, либо вопрос, либо ошибку
        logger.critical("Критическая ошибка в логике handle_text_message: active_scenario_instance остался None.")
        critical_error_msg = "Произошла непредвиденная системная ошибка. Пожалуйста, сообщите администратору."
        await message.answer(critical_error_msg)
        await add_to_chat_history_list(state, user_input, critical_error_msg)

async def on_startup(): # bot_instance передается из dp.startup.register
    """Действия при запуске бота."""
    logger.info("Загрузка начальных данных (склады и генерация моков)...")
    try:
        await load_warehouses_if_needed()
        logger.info("Начальные данные успешно загружены/подготовлены.")
    except Exception as e:
        logger.error(f"Ошибка при загрузке начальных данных: {e}", exc_info=True)

async def main() -> None:
    # Регистрация on_startup для aiogram 3.x
    dp.startup.register(on_startup)

    logger.info("Запуск Telegram бота с архитектурой маршрутизации сценариев...")
    for key, ScenarioClass in AVAILABLE_SCENARIOS.items():
        if key != ScenarioClass.id:
            logger.error(
                f"Критическая ошибка конфигурации! Ключ '{key}' в AVAILABLE_SCENARIOS "
                f"не совпадает с ID класса '{ScenarioClass.__name__}': '{ScenarioClass.id}'"
            )
            # raise SystemExit("Ошибка конфигурации сценариев.") # Можно остановить запуск

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Сессия бота закрыта.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
    except Exception as e_global:
        logger.critical(f"Глобальная ошибка при запуске или работе бота: {e_global}", exc_info=True)