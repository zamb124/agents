# scenarios/base_scenario.py
import logging
from abc import ABC, abstractmethod
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

logger = logging.getLogger(__name__)

class BaseScenario(ABC):
    def __init__(self, state: FSMContext, bot_instance, user_login: str, initial_message_text: str):
        self.state = state
        self.bot = bot_instance
        self.user_login = user_login
        self.initial_message = initial_message_text

    id: str  = 'some_name'
    friendly_name: str = "some friendly name"
    description: str = "some description"

    @abstractmethod
    async def handle_message(self, message: Message) -> None:
        """Обрабатывает текущее сообщение пользователя в рамках этого сценария."""
        pass

    @abstractmethod
    async def is_finished(self) -> bool:
        """Возвращает True, если сценарий завершил свою работу."""
        pass

    def _get_fsm_key(self, key_suffix: str) -> str:
        """Формирует полный ключ для FSM с использованием ID сценария."""
        return f"scenario_{self.id}_{key_suffix}"

    async def get_scenario_data(self, key_suffix: str, default=None):
        """Получает данные сценария из FSM по суффиксу ключа."""
        full_key = self._get_fsm_key(key_suffix)
        data = await self.state.get_data()
        return data.get(full_key, default)

    async def update_scenario_data(self, **kwargs_suffix):
        """
        Обновляет данные сценария в FSM.
        kwargs_suffix: пары {суффикс_ключа: значение}.
        """
        data_to_update = {self._get_fsm_key(key_suffix): value for key_suffix, value in kwargs_suffix.items()}
        await self.state.update_data(data_to_update)
        logger.debug(f"[{self.id}] FSM data updated: {data_to_update}")

    async def clear_scenario_data(self):
        """
        Очищает ВСЕ данные этого сценария из FSM.
        Вызываеца из main_bot.py при сбросе сценария или его завершении.
        """
        current_fsm_data = await self.state.get_data()
        prefix_to_remove = f"scenario_{self.id}_"
        keys_of_this_scenario = [key for key in current_fsm_data.keys() if key.startswith(prefix_to_remove)]

        if not keys_of_this_scenario:
            logger.info(f"[{self.id}] Нет данных для очистки из FSM (префикс: {prefix_to_remove}).")
            return

        update_with_nones = {key: None for key in keys_of_this_scenario}
        await self.state.update_data(update_with_nones) # Это удалит ключи или установит их в None

        logger.info(f"[{self.id}] Данные сценария (ключи: {keys_of_this_scenario}) очищены/обнулены из FSM.")