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
from agents.information_collector_agent import run_information_collector
from agents.decision_maker_agent import run_decision_maker

# Проверка наличия ключей API
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Токен Telegram бота не найден. Укажите TELEGRAM_BOT_TOKEN в .env файле.")
if not OPENAI_API_KEY:
    raise ValueError("Ключ OpenAI API не найден. Укажите OPENAI_API_KEY в .env файле.")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Хранилище состояний и данных
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

default_props = DefaultBotProperties(parse_mode=ParseMode.HTML)
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=default_props)

CHAT_HISTORY_KEY = "chat_history_list" # Ключь для хранения истории чата в FSM

async def get_chat_history_as_list(state: FSMContext) -> list:
    """Извлекает историю чата из состояния FSM как список."""
    data = await state.get_data()
    return data.get(CHAT_HISTORY_KEY, [])

async def add_to_chat_history_list(state: FSMContext, user_message_content: str, ai_message_content: str):
    """Добовляет пару сообщений (пользователь, ИИ) в историю чата в FSM."""
    data = await state.get_data()
    history = data.get(CHAT_HISTORY_KEY, [])
    history.append({"type": "human", "content": user_message_content})
    history.append({"type": "ai", "content": ai_message_content})
    if len(history) > 10: # Храним последние 5 пар сообщений (10 записей)
        history = history[-10:]
    await state.update_data({CHAT_HISTORY_KEY: history})

@dp.message(CommandStart())
async def command_start_handler(message: Message, state: FSMContext) -> None:
    """Обработчик команды /start. Очищает состояние и приветствует пользователя."""
    await state.clear()
    await message.answer(
        f"Здравствуйте, {message.from_user.full_name}! Я ваш ИИ-ассистент поддержки.\n"
        "Готов помочь с вопросами по работе курьеров. Опишите вашу проблему."
    )

@dp.message(F.text)
async def handle_text_message(message: Message, state: FSMContext) -> None:
    """
    Основной обработчик текстовых сообщений.
    Запускает цепочку агентов: сначала сбор информации, затем принятие решения.
    """
    user_input = message.text
    chat_id = message.chat.id
    director_login = message.from_user.username or f"user_{message.from_user.id}"

    logger.info(f"Получено сообщение от {director_login} (chat_id: {chat_id}): '{user_input}'")
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    current_chat_history_list = await get_chat_history_as_list(state)

    try:
        # Шаг 1: Сбор информации
        logger.info(f"Запуск InformationCollectorAgent для {director_login}")
        collector_response = await run_information_collector(
            user_input, current_chat_history_list, director_login
        )

        agent_message_to_user = collector_response.get("agent_message")

        if collector_response["status"] == "in_progress":
            await message.answer(agent_message_to_user)
            await add_to_chat_history_list(state, user_input, agent_message_to_user)
            logger.info(f"InformationCollectorAgent ответил {director_login}: '{agent_message_to_user}'")

        elif collector_response["status"] == "completed":
            collected_data = collector_response["data"]
            if agent_message_to_user and agent_message_to_user != "Информация собрана. Передаю для анализа и принятия решения.":
                await message.answer(agent_message_to_user)
                await add_to_chat_history_list(state, user_input, agent_message_to_user) # Добавляем промежуточный ответ

            status_update_msg = "Спасибо, информация собрана. Анализирую и принимаю решение..."
            await message.answer(status_update_msg)
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

            logger.info(f"Информация собрана для {director_login}. Запуск DecisionMakerAgent.")
            logger.debug(f"Собранные данные: {json.dumps(collected_data, ensure_ascii=False, indent=2)}")

            # Шаг 2: Принятие решения
            decision_message = await run_decision_maker(collected_data)
            await message.answer(decision_message)

            # В историю добавляем исходный запрос пользователя и финальное решение + статусное сообщение
            # Это важно, чтобы следующий вызов InformationCollectorAgent имел правильный контекст,
            # если пользователь продолжит диалог на основе принятого решения.
            # Если collector_response["agent_message"] был промежуточным, он уже добавлен.
            # Если нет, то user_input еще не был добавлен с финальным ответом.
            # Чтобы избежать дублирования user_input, если он уже был с промежуточным ответом,
            # можно либо всегда добавлять user_input + status_update_msg + decision_message,
            # либо иметь более сложную логику. Пока оставим так:
            current_history_for_final_log = await get_chat_history_as_list(state)
            if not current_history_for_final_log or current_history_for_final_log[-2].get("content") != user_input:
                # Если user_input еще не в истории с каким-либо AI ответом
                await add_to_chat_history_list(state, user_input, f"{status_update_msg}\n{decision_message}")
            else:
                # Если user_input уже был с промежуточным ответом, обновим последний AI ответ
                history = await get_chat_history_as_list(state)
                history[-1]["content"] = f"{history[-1]['content']}\n{status_update_msg}\n{decision_message}" # Дополняем последний ответ ИИ
                await state.update_data({CHAT_HISTORY_KEY: history})


            logger.info(f"DecisionMakerAgent ответил {director_login}: '{decision_message}'")

        elif collector_response["status"] == "error": # Обработка статуса "error"
            logger.error(f"Ошибка от InformationCollectorAgent: {agent_message_to_user}")
            await message.answer(agent_message_to_user) # Отправляем сообщение об ошибке пользователю
            # Не добавляем в историю, чтобы не засорять ее ошибочными состояниями для LLM

        else: # Неизвестный статус
            logger.error(f"Неизвестный статус от InformationCollectorAgent: {collector_response.get('status')}")
            await message.answer("Произошла ошибка в логике агента. Попробуйте позже.")

    except Exception as e:
        logger.error(f"Критическая ошибка при обработке сообщения от {director_login}: {e}", exc_info=True)
        await message.answer("Извините, произошла серьезная внутренняя ошибка. Мы уже работаем над этим.")

async def main() -> None:
    """Основная функция для запуска бота."""
    logger.info("Запуск бота...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")