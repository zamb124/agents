# agents/base_agent.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
# AgentExecutor может понадобиться некоторым агентам
from langchain.agents import AgentExecutor

from llm_services import get_llm
import logging

logger = logging.getLogger(__name__)

class BaseAgent(ABC):
    agent_id: str # Должен быть уникальным и переопределен наследниками

    def __init__(
            self,
            llm_provider_config: Optional[Dict[str, Any]] = None,
            agent_specific_tools: Optional[List[BaseTool]] = None
    ):
        # Инициализация LLM для агента
        # llm_provider_config может содержать 'provider', 'model_name', 'temperature'
        self.llm: BaseChatModel = self._initialize_llm(llm_provider_config)

        # Инструменты, специфичные для этого агента
        self.tools: List[BaseTool] = agent_specific_tools if agent_specific_tools is not None else self._get_default_tools()

        # AgentExecutor создается по необходимости внутри агента, если он использует инструменты Langchain
        logger.info(f"Agent '{self.get_id()}' initialized. LLM config: {llm_provider_config or 'default'}. Tools: {[tool.name for tool in self.tools]}")

    def _initialize_llm(self, llm_provider_config: Optional[Dict[str, Any]] = None) -> BaseChatModel:
        cfg = llm_provider_config or {}
        return get_llm(
            provider=cfg.get("provider"),
            model_name=cfg.get("model_name"),
            temperature=cfg.get("temperature")
        )

    def _get_default_tools(self) -> List[BaseTool]:
        """Возвращает список инструментов по умолчанию для этого агента, если они не переданы в конструктор."""
        return []

    @classmethod
    def get_id(cls) -> str:
        """Возвращает уникальный идентификатор класса агента."""
        if not hasattr(cls, 'agent_id') or not cls.agent_id:
            raise NotImplementedError("Каждый класс агента должен определить атрибут agent_id.")
        return cls.agent_id

    @abstractmethod
    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Возвращает начальное внутреннее состояние (сессию) агента.
        scenario_context: данные от сценария, которые могут быть нужны для инициализации состояния
                          (например, initial_complaint, результат предыдущего агента).
        """
        pass

    @abstractmethod
    async def process_user_input(
            self,
            user_input: str,
            current_agent_state: Dict[str, Any], # Текущее полное состояние агента
            scenario_context: Optional[Dict[str, Any]] = None # Дополнительный контекст от сценария
    ) -> Dict[str, Any]:
        """
        Обрабатывает ввод пользователя и текущее состояние агента.
        Должен вернуть словарь:
        {
            "status": "in_progress" | "completed" | "error",
            "message_to_user": Optional[str],      // Сообщение для пользователя
            "next_agent_state": Dict[str, Any],    // Обновленное полное состояние агента
            "result": Optional[Any]                // Финальный результат, если status="completed"
        }
        """
        pass

    # Вспомогательный метод для форматирования истории для LLM, если нужен
    def _prepare_chat_history_for_llm(self, dialog_history: List[Dict[str, str]]) -> list:
        from langchain_core.messages import HumanMessage, AIMessage # Локальный импорт
        langchain_messages = []
        for msg in dialog_history:
            if msg.get("type") == "human":
                langchain_messages.append(HumanMessage(content=msg["content"]))
            elif msg.get("type") == "ai":
                langchain_messages.append(AIMessage(content=msg["content"]))
        return langchain_messages