import asyncio
import logging
import json

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode, ChatAction

from config import TELEGRAM_BOT_TOKEN, OPENAI_API_KEY

# Импорты агентов и сценариев
from agents.router_agent import run_router_agent
from scenarios.base_scenario import BaseScenario # Базовый класс для type hinting
from scenarios.courier_complaint_scenario import CourierComplaintScenario
from scenarios.faq_general_scenario import FaqGeneralScenario
# from scenarios.shift_management_scenario import ShiftManagementScenario # Пример для будущего сценария

# Проверка наличия ключей API
if not TELEGRAM_BOT_TOKEN:
    logging.critical("Токен Telegram бота не найден! Укажите TELEGRAM_BOT_TOKEN в .env файле.")
    raise ValueError("Токен Telegram бота не найден. Укажите TELEGRAM_BOT_TOKEN в .env файле.")
if not OPENAI_API_KEY:
    logging.critical("Ключ OpenAI API не найден! Укажите OPENAI_API_KEY в .env файле.")
    raise ValueError("Ключ OpenAI API не найден. Укажите OPENAI_API_KEY в .env файле.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s"
)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

default_props = DefaultBotProperties(parse_mode=ParseMode.HTML)
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=default_props)

# Ключ для общей истории чата в FSM
CHAT_HISTORY_KEY = "chat_history_list_v2" # Используйте консистентное имя
# Ключ для хранения ID активного сценария в FSM
ACTIVE_SCENARIO_KEY = "active_scenario_id_v2" # Обновил для избежания конфликтов при тестах

# --- Функции для работы с общей историей чата ---
async def get_chat_history_as_list(state: FSMContext) -> list:
    """Извлекает общую историю чата из состояния FSM."""
    data = await state.get_data()
    return data.get(CHAT_HISTORY_KEY, [])

async def add_to_chat_history_list(state: FSMContext, user_message_content: str, ai_message_content: str):
    """Добавляет пару сообщений в общую историю чата в FSM."""
    data = await state.get_data()
    history = data.get(CHAT_HISTORY_KEY, [])
    history.append({"type": "human", "content": user_message_content})
    history.append({"type": "ai", "content": ai_message_content})
    if len(history) > 10: # Ограничиваем размер истории
        history = history[-10:]
    await state.update_data({CHAT_HISTORY_KEY: history})
    logger.debug(f"Общая история чата обновлена. Последнее сообщение AI: '{ai_message_content[:50]}...'")

# --- Реестр доступных сценариев ---
# Ключ - ID сценария (который должен совпадать с ScenarioClass.id), значение - класс сценария
AVAILABLE_SCENARIOS = {
    CourierComplaintScenario.id: CourierComplaintScenario, # Используем ID из класса
    FaqGeneralScenario.id: FaqGeneralScenario,         # Используем ID из класса
    # ShiftManagementScenario.id: ShiftManagementScenario, # Когда будет готов
}
logger.info(f"Зарегистрированные сценарии: {list(AVAILABLE_SCENARIOS.keys())}")


@dp.message(CommandStart())
async def command_start_handler(message: Message, state: FSMContext) -> None:
    """
    Обработчик команды /start. Очищает ВСЕ данные FSM для пользователя и приветствует.
    """
    await state.clear()
    logger.info(f"Пользователь {message.from_user.full_name} (ID: {message.from_user.id}) отправил /start. Состояние FSM очищено.")
    await message.answer(
        f"Здравствуйте, {message.from_user.full_name}! Я ваш ИИ-ассистент поддержки.\n"
        "Опишите вашу проблему или вопрос, и я постараюсь помочь."
    )

@dp.message(F.text)
async def handle_text_message(message: Message, state: FSMContext) -> None:
    """
    Основной обработчик текстовых сообщений.
    Маршрутизирует запрос к активному сценарию или запускает Агента-Маршрутизатора.
    """
    user_input = message.text
    director_login = message.from_user.username if message.from_user.username else f"user_id_{message.from_user.id}"
    chat_id = message.chat.id

    logger.info(f"Получено сообщение от {director_login} (chat_id: {chat_id}): '{user_input[:100]}...'")
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    fsm_data = await state.get_data()
    active_scenario_id = fsm_data.get(ACTIVE_SCENARIO_KEY)
    active_scenario_instance: BaseScenario | None = None

    # 1. Проверяем, есть ли уже активный сценарий
    if active_scenario_id and active_scenario_id in AVAILABLE_SCENARIOS:
        ScenarioClass = AVAILABLE_SCENARIOS[active_scenario_id]
        active_scenario_instance = ScenarioClass(state, bot, director_login, user_input)

        if await active_scenario_instance.is_finished():
            logger.info(f"Активный сценарий '{active_scenario_id}' был завершен ранее. Сбрасываем его.")
            await active_scenario_instance.clear_scenario_data()
            await state.update_data({ACTIVE_SCENARIO_KEY: None})
            active_scenario_id = None
            active_scenario_instance = None
        else:
            logger.info(f"Продолжаем работу в рамках активного сценария: {active_scenario_id}")

    # 2. Если нет активного сценария, запускаем Агента-Маршрутизатора
    if not active_scenario_instance:
        logger.info("Нет активного сценария или он завершен. Запускаем Агента-Маршрутизатора.")
        chat_history_for_router = await get_chat_history_as_list(state)

        # Передаем AVAILABLE_SCENARIOS в run_router_agent
        router_result = await run_router_agent(user_input, chat_history_for_router, AVAILABLE_SCENARIOS)

        if router_result["type"] == "scenario_id":
            chosen_scenario_id = router_result["value"]
            if chosen_scenario_id in AVAILABLE_SCENARIOS:
                logger.info(f"Агент-Маршрутизатор выбрал сценарий: {chosen_scenario_id}. Устанавливаю ACTIVE_SCENARIO_KEY.")
                await state.update_data({ACTIVE_SCENARIO_KEY: chosen_scenario_id})

                current_fsm_data_after_set = await state.get_data() # Лог для проверки
                logger.info(f"FSM data после установки ACTIVE_SCENARIO_KEY: {current_fsm_data_after_set}")

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
            logger.info(f"Агент-Маршрутизатор задал уточняющий вопрос: '{question_to_user}'. ACTIVE_SCENARIO_KEY НЕ установлен/изменен.")
            await message.answer(question_to_user)
            await add_to_chat_history_list(state, user_input, question_to_user)
            return
        else:
            error_msg = router_result.get("value", "Произошла ошибка при выборе нужного действия.")
            logger.error(f"Агент-Маршрутизатор вернул некорректный результат: {router_result}. ACTIVE_SCENARIO_KEY НЕ установлен/изменен.")
            await message.answer(error_msg)
            await add_to_chat_history_list(state, user_input, error_msg)
            return

    # 3. Если active_scenario_instance определен, передаем ему управление
    if active_scenario_instance:
        logger.info(f"Вызов handle_message для сценария {active_scenario_instance.id} с сообщением: '{message.text[:50]}...'")
        try:
            await active_scenario_instance.handle_message(message)

            if await active_scenario_instance.is_finished():
                logger.info(f"Сценарий '{active_scenario_instance.id}' завершил свою работу после обработки сообщения.")
                await active_scenario_instance.clear_scenario_data()
                await state.update_data({ACTIVE_SCENARIO_KEY: None})
        except Exception as e:
            scenario_id_for_error = "неизвестного сценария"
            try: # Пытаемся получить ID даже если инстанс "сломан"
                scenario_id_for_error = active_scenario_instance.id
            except: pass
            logger.error(f"Ошибка при выполнении сценария '{scenario_id_for_error}': {e}", exc_info=True)
            error_msg_to_user = "Произошла внутренняя ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже."
            await message.answer(error_msg_to_user)

            logger.info(f"Принудительно сбрасываем активный сценарий '{scenario_id_for_error}' из-за ошибки.")
            try:
                await active_scenario_instance.clear_scenario_data()
            except Exception as e_clear:
                logger.error(f"Ошибка при попытке очистить данные сценария '{scenario_id_for_error}' после ошибки: {e_clear}", exc_info=True)
            await state.update_data({ACTIVE_SCENARIO_KEY: None})
            await add_to_chat_history_list(state, user_input, f"Ошибка в сценарии: {type(e).__name__}")
    else:
        # Эта ветка не должна достигаться, если роутер всегда возвращает либо ID, либо вопрос, либо ошибку,
        # и мы не выходим из функции раньше.
        logger.critical("Критическая ошибка в логике handle_text_message: active_scenario_instance остался None, хотя не должен был.")
        critical_error_msg = "Произошла непредвиденная системная ошибка. Пожалуйста, сообщите администратору."
        await message.answer(critical_error_msg)
        await add_to_chat_history_list(state, user_input, critical_error_msg)


async def main() -> None:
    """Основная функция для запуска Telegram бота."""
    logger.info("Запуск Telegram бота с архитектурой маршрутизации сценариев...")
    # Проверка, что все ID сценариев в AVAILABLE_SCENARIOS уникальны и соответствуют ScenarioClass.id
    for key, ScenarioClass in AVAILABLE_SCENARIOS.items():
        if key != ScenarioClass.id:
            logger.error(
                f"Критическая ошибка конфигурации! Ключ '{key}' в AVAILABLE_SCENARIOS "
                f"не совпадает с ID класса '{ScenarioClass.__name__}': '{ScenarioClass.id}'"
            )
            # В реальном приложении здесь можно было бы прервать запуск
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную (KeyboardInterrupt/SystemExit).")
    except Exception as e_global:
        logger.critical(f"Глобальная ошибка при запуске или работе бота: {e_global}", exc_info=True)