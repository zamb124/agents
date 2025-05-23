# main_bot.py
import asyncio
import logging
import sys
from typing import Dict, Type, Optional # Добавил Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode, ChatAction

import config

# --- Агенты ---
# Импортируем только те классы агентов, которые могут понадобиться для конфигурации в RouterAgent,
# но RouterAgent теперь сам получает классы сценариев, а не агентов.
# Так что прямые импорты агентов здесь не нужны, если только для RouterAgent.
from agents.router_agent import RouterAgent # RouterAgent остается отдельной сущностью

# --- Сценарии ---
from scenarios.base_scenario import BaseScenario
from scenarios.courier_complaint_scenario import CourierComplaintScenario
from scenarios.faq_general_scenario import FaqGeneralScenario

# --- Утилиты ---
from tools.warehouse_api import load_warehouses_if_needed

# --- Проверка API ключей ---
if not config.TELEGRAM_BOT_TOKEN:
    logging.critical("Токен Telegram бота не найден! Укажите TELEGRAM_BOT_TOKEN в .env файле.")
    raise ValueError("Токен Telegram бота не найден.")
# ... (остальная проверка ключей LLM как раньше) ...
llm_api_key_found = False
if config.OPENAI_API_KEY: llm_api_key_found = True; logging.info("Найден OpenAI API ключ.")
if config.GOOGLE_API_KEY: llm_api_key_found = True; logging.info("Найден Google API ключ.")
if not llm_api_key_found:
    logging.critical("API ключ для LLM не найден! Укажите OPENAI_API_KEY или GOOGLE_API_KEY в .env файле.")
    raise ValueError("API ключ для LLM не найден.")

# --- Настройка логирования ---
# ... (код логирования как раньше) ...
for handler in logging.root.handlers[:]: logging.root.removeHandler(handler)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s [%(module)s.%(funcName)s:%(lineno)d] - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# --- Инициализация Aiogram ---
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
# parse_mode можно убрать из DefaultBotProperties, если он не нужен глобально,
# и указывать его при отправке сообщений, где это необходимо.
# Оставим HTML для примера.
default_props = DefaultBotProperties(parse_mode=ParseMode.HTML)
bot = Bot(token=config.TELEGRAM_BOT_TOKEN, default=default_props)

# --- Ключи FSM для main_bot ---
MAIN_CHAT_HISTORY_FSM_KEY = "main_chat_history_for_router_v2" # Общая история для роутера
ACTIVE_SCENARIO_ID_FSM_KEY = "main_active_scenario_id_v2"    # ID текущего активного сценария

# --- Реестр доступных сценариев ---
AVAILABLE_SCENARIOS_MAP: Dict[str, Type[BaseScenario]] = {
    sc.id: sc for sc in [
        CourierComplaintScenario,
        FaqGeneralScenario,
    ]
}
logger.info(f"Зарегистрированные сценарии: {list(AVAILABLE_SCENARIOS_MAP.keys())}")
# Проверка консистентности ID сценариев
for sc_id, Sc_Class in AVAILABLE_SCENARIOS_MAP.items():
    if sc_id != Sc_Class.id:
        crit_msg = f"Критическая ошибка! Ключ '{sc_id}' не совпадает с ID класса '{Sc_Class.__name__}': '{Sc_Class.id}'"
        logger.critical(crit_msg)
        raise SystemExit(crit_msg)

# --- Инициализация RouterAgent ---
try:
    router_llm_config = {"provider": config.LLM_PROVIDER, "temperature": 0.1}
    router_agent_instance = RouterAgent(
        available_scenarios_map=AVAILABLE_SCENARIOS_MAP,
        llm_provider_config=router_llm_config
    )
    logger.info(f"RouterAgent (ID: {router_agent_instance.get_id()}) успешно инициализирован.")
except Exception as e:
    logger.critical(f"Не удалось инициализировать RouterAgent: {e}", exc_info=True)
    raise

# --- Функции для работы с общей историей чата ---
async def get_main_chat_history(state: FSMContext) -> list:
    data = await state.get_data()
    return data.get(MAIN_CHAT_HISTORY_FSM_KEY, [])

async def add_to_main_chat_history(state: FSMContext, user_msg: str, ai_msg: str):
    data = await state.get_data()
    history = data.get(MAIN_CHAT_HISTORY_FSM_KEY, [])
    history.append({"type": "human", "content": user_msg})
    history.append({"type": "ai", "content": ai_msg})
    if len(history) > 10: # Ограничение истории (5 пар)
        history = history[-10:]
    await state.update_data({MAIN_CHAT_HISTORY_FSM_KEY: history})
    logger.debug(f"Общая история чата обновлена. AI: '{ai_msg[:50]}...'")

# --- Обработчики Telegram ---
@dp.message(CommandStart())
async def command_start_handler(message: Message, state: FSMContext) -> None:
    user_full_name = message.from_user.full_name
    user_id = message.from_user.id
    user_login = message.from_user.username if message.from_user.username else f"id_{user_id}"

    logger.info(f"User {user_full_name} (Login: {user_login}, ID: {user_id}) sent /start.")

    # Очищаем данные предыдущего сценария, если он был активен
    active_scenario_id = (await state.get_data()).get(ACTIVE_SCENARIO_ID_FSM_KEY)
    if active_scenario_id and active_scenario_id in AVAILABLE_SCENARIOS_MAP:
        logger.info(f"Обнаружен активный сценарий '{active_scenario_id}' при команде /start. Очистка данных сценария.")
        ScenarioClass = AVAILABLE_SCENARIOS_MAP[active_scenario_id]
        # Создаем временный инстанс для вызова clear_scenario_data
        # user_info здесь не так важен, так как clear_scenario_data работает с self.id
        temp_scenario_instance = ScenarioClass(state, bot, {"id": user_id, "login": user_login, "chat_id": message.chat.id})
        await temp_scenario_instance.clear_scenario_data()

    # Очищаем основные ключи main_bot в FSM
    await state.update_data({
        ACTIVE_SCENARIO_ID_FSM_KEY: None,
        MAIN_CHAT_HISTORY_FSM_KEY: [] # Очищаем и общую историю
    })
    logger.info(f"FSM state для main_bot очищен для пользователя {user_login}.")

    await message.answer(
        f"Здравствуйте, {user_full_name}! Я ваш ИИ-ассистент поддержки.\n"
        "Опишите вашу проблему или вопрос."
    )

@dp.message(F.text)
async def handle_text_message(message: Message, state: FSMContext) -> None:
    user_input_text = message.text.strip()
    if not user_input_text: return # Игнорируем пустые сообщения

    user_id = message.from_user.id
    chat_id = message.chat.id
    user_login = message.from_user.username if message.from_user.username else f"id_{user_id}"

    user_info_for_scenario = {"id": user_id, "login": user_login, "chat_id": chat_id}

    logger.info(f"Сообщение от {user_login}: '{user_input_text[:100]}...'")
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    fsm_data = await state.get_data()
    active_scenario_id = fsm_data.get(ACTIVE_SCENARIO_ID_FSM_KEY)
    active_scenario_instance: Optional[BaseScenario] = None

    # 1. Проверяем, есть ли активный сценарий и не завершен ли он
    if active_scenario_id and active_scenario_id in AVAILABLE_SCENARIOS_MAP:
        ScenarioClass = AVAILABLE_SCENARIOS_MAP[active_scenario_id]
        active_scenario_instance = ScenarioClass(state, bot, user_info_for_scenario, initial_message_text=None)

        if await active_scenario_instance.is_finished():
            logger.info(f"Активный сценарий '{active_scenario_id}' был ранее завершен. Очистка и сброс.")
            await active_scenario_instance.clear_scenario_data()
            await state.update_data({ACTIVE_SCENARIO_ID_FSM_KEY: None})
            active_scenario_id = None
            active_scenario_instance = None
        else:
            logger.info(f"Продолжаем активный сценарий: {active_scenario_id}")

    # 2. Если нет активного сценария, обращаемся к RouterAgent
    if not active_scenario_instance:
        logger.info("Нет активного сценария. Вызов RouterAgent.")
        current_main_chat_history = await get_main_chat_history(state)

        router_scenario_context = {"main_chat_history": current_main_chat_history}
        router_initial_state = router_agent_instance.get_initial_state() # Роутер stateless

        router_agent_response = await router_agent_instance.process_user_input(
            user_input=user_input_text,
            current_agent_state=router_initial_state,
            scenario_context=router_scenario_context
        )

        router_status = router_agent_response.get("status")
        router_result_data = router_agent_response.get("result", {})
        router_message_to_user = router_agent_response.get("message_to_user")

        if router_status == "error":
            err_msg = router_message_to_user or "Ошибка роутера при обработке запроса."
            await message.answer(err_msg)
            await add_to_main_chat_history(state, user_input_text, err_msg)
            return

        # Роутер всегда должен возвращать status="completed"
        if router_result_data.get("type") == "scenario_id":
            chosen_scenario_id = router_result_data["value"]
            if chosen_scenario_id in AVAILABLE_SCENARIOS_MAP:
                logger.info(f"RouterAgent выбрал сценарий: {chosen_scenario_id}.")
                await state.update_data({ACTIVE_SCENARIO_ID_FSM_KEY: chosen_scenario_id})

                ScenarioClassToRun = AVAILABLE_SCENARIOS_MAP[chosen_scenario_id]
                active_scenario_instance = ScenarioClassToRun(
                    state, bot, user_info_for_scenario,
                    initial_message_text=user_input_text # Передаем для первого запуска сценария
                )
                # Первый вызов handle_message для нового сценария.
                # Он должен сам инициализировать своего первого агента и, возможно, ответить.
                await active_scenario_instance.handle_message(message)
                # Не добавляем в main_chat_history здесь, т.к. сценарий/агент сами отвечают.
            else:
                err_msg = f"Роутер вернул неизвестный ID сценария: {chosen_scenario_id}"
                logger.error(err_msg)
                await message.answer("Извините, не удалось подобрать подходящий раздел.")
                await add_to_main_chat_history(state, user_input_text, "Системная ошибка: неизвестный сценарий.")
                return
        elif router_result_data.get("type") == "question": # Роутер задал вопрос
            await message.answer(router_message_to_user)
            await add_to_main_chat_history(state, user_input_text, router_message_to_user)
            return
        else: # Неожиданный результат от роутера
            err_msg = "Произошла ошибка маршрутизации вашего запроса."
            logger.error(f"Неожиданный результат от RouterAgent: {router_result_data}")
            await message.answer(err_msg)
            await add_to_main_chat_history(state, user_input_text, err_msg)
            return

    # 3. Если сценарий уже был активен (и не завершился на шаге 1), передаем ему сообщение
    elif active_scenario_instance:
        try:
            await active_scenario_instance.handle_message(message)

            if await active_scenario_instance.is_finished():
                logger.info(f"Сценарий '{active_scenario_instance.id}' завершил свою работу.")
                # BaseScenario.clear_scenario_data() вызывается в _mark_as_finished,
                # который вызывается изнутри сценария при его логическом завершении.
                await state.update_data({ACTIVE_SCENARIO_ID_FSM_KEY: None}) # Сбрасываем ID в main_bot
        except Exception as e_scenario:
            scenario_id_err = active_scenario_id or "unknown_active"
            logger.error(f"Ошибка при выполнении сценария '{scenario_id_err}': {e_scenario}", exc_info=True)
            await message.answer("Произошла внутренняя ошибка в текущем разделе. Пожалуйста, попробуйте начать заново командой /start.")
            # Принудительно завершаем и очищаем сценарий
            if active_scenario_instance: # Если инстанс еще существует
                try: await active_scenario_instance.clear_scenario_data()
                except Exception as e_clear: logger.error(f"Ошибка очистки данных сценария '{scenario_id_err}': {e_clear}")
            await state.update_data({ACTIVE_SCENARIO_ID_FSM_KEY: None})
            # Можно добавить сообщение об ошибке в общую историю для контекста роутера
            await add_to_main_chat_history(state, user_input_text, f"Системная ошибка в разделе: {type(e_scenario).__name__}.")


async def on_startup_actions(): # << Убрали bot_instance из аргументов
    """Действия, выполняемые при запуске бота."""
    logger.info("Запуск действий при старте бота...")
    try:
        await load_warehouses_if_needed()
        logger.info("Начальные данные (склады и т.д.) успешно загружены/подготовлены.")
    except Exception as e:
        logger.error(f"Ошибка при выполнении on_startup_actions: {e}", exc_info=True)

async def main_runner() -> None:
    # Регистрация on_startup_actions для aiogram 3.x
    # dp.startup.register ожидает ссылку на функцию.
    # Если функция принимает аргументы, которые aiogram может предоставить (например, bot: Bot),
    # то они будут переданы. Если нет, то нет.
    dp.startup.register(on_startup_actions) # Передаем просто ссылку на функцию

    logger.info("Запуск Telegram бота...")
    try:
        # Передаем инстанс бота в start_polling
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Сессия Telegram бота закрыта.")

if __name__ == "__main__":
    try:
        asyncio.run(main_runner())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
    except Exception as e_global:
        logger.critical(f"Глобальная ошибка при запуске или работе бота: {e_global}", exc_info=True)