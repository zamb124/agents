# scenarios/faq_general_scenario.py
import logging
from typing import Dict, Type, List, Optional, Any # Добавил List, Optional, Any
from datetime import datetime # Для current_date

from aiogram.types import Message
# FSMContext не нужен, если сценарий не хранит свое состояние FSM (а он не хранит для FAQ)
from aiogram.enums import ChatAction


from .base_scenario import BaseScenario
from agents.base_agent import BaseAgent # Для тайпхинта
from agents.faq_agent import FaqAgent   # <<< Импортируем нового агента

# Инструмент RAG (сценарий вызывает его перед передачей данных FaqAgent)
from tools.tool_definitions import query_rag_tool

logger = logging.getLogger(__name__)

class FaqGeneralScenario(BaseScenario):
    id: str = "faq_general"
    friendly_name: str = "Общие вопросы и инструкции"
    description: str = "Ответы на общие вопросы по работе системы, инструкциям, правилам, если вопрос не касается конкретного инцидента с курьером."

    RAG_COLLECTION_NAME = "general_instructions"

    def _get_required_agents_classes(self) -> Dict[str, Type[BaseAgent]]:
        return {
            "faq_responder": FaqAgent # Ключ для использования в self._get_agent_instance
        }

    async def handle_message(self, message: Message) -> None:
        user_current_input = message.text.strip()
        chat_id = message.chat.id # self.user_info["chat_id"] тоже доступен

        logger.info(f"[{self.id}] User '{self.user_info.get('login')}': '{user_current_input[:100]}...'")
        await self.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        rag_data_text_for_llm = None
        is_likely_specific_question = not (
                any(kw in user_current_input.lower() for kw in ["общий", "общие", "faq", self.friendly_name.lower()]) and \
                len(user_current_input) < 30
        )

        if is_likely_specific_question:
            logger.info(f"[{self.id}] Запрос к RAG для вопроса: '{user_current_input}'")
            try:
                rag_response_dict = await query_rag_tool._arun( # Используем _arun
                    query_text=user_current_input,
                    collection_name=self.RAG_COLLECTION_NAME,
                    top_k=3
                )
                if rag_response_dict and rag_response_dict.get("success") and rag_response_dict.get("data"):
                    chunks_data = rag_response_dict["data"]
                    if chunks_data:
                        chunks_texts = [
                            f"[ИСТОЧНИК {i+1}: {chunk.get('source', 'неизвестно')}]\n{chunk.get('text', '')}"
                            for i, chunk in enumerate(chunks_data)
                        ]
                        rag_data_text_for_llm = "\n\n".join(chunks_texts)
                        logger.info(f"[{self.id}] RAG вернул {len(chunks_data)} чанк(а).")
                    else:
                        logger.info(f"[{self.id}] RAG вернул пустой список данных.")
                else:
                    error_detail = rag_response_dict.get("error", "RAG error") if isinstance(rag_response_dict, dict) else "RAG response invalid"
                    logger.warning(f"[{self.id}] Ошибка или нет данных от RAG: {error_detail}")
            except Exception as e_rag:
                logger.error(f"[{self.id}] Исключение при вызове query_rag_tool: {e_rag}", exc_info=True)
        else:
            logger.info(f"[{self.id}] Вопрос слишком общий, RAG не вызывается.")

        # Получаем инстанс FaqAgent
        faq_agent_instance = self._get_agent_instance("faq_responder")

        agent_input_data = {
            "user_question": user_current_input,
            "rag_results_text": rag_data_text_for_llm,
            "current_date": datetime.now().strftime("%Y-%m-%d")
        }

        # Получаем общую историю чата из FSM, если FaqAgent ее использует
        # fsm_data = await self.state.get_data()
        # main_chat_history = fsm_data.get(MAIN_CHAT_HISTORY_FSM_KEY, []) # MAIN_CHAT_HISTORY_FSM_KEY из main_bot.py
        # Пока FaqAgent не использует chat_history явно в промпте, передадим None
        agent_response_dict = await faq_agent_instance.arun(agent_input_data, chat_history=None)

        final_llm_answer = agent_response_dict.get("agent_message", "Не удалось получить ответ от FAQ ассистента.")

        await message.answer(final_llm_answer)

        # main_bot.py теперь отвечает за добавление в основную историю чата
        # после того, как сценарий ответил.

        await self._mark_as_finished() # Используем метод из BaseScenario
        logger.info(f"[{self.id}] Сценарий FAQ завершен.")

    # is_finished() и clear_scenario_data() наследуются от BaseScenario