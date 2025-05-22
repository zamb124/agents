# scenarios/faq_general_scenario.py
import logging
import json # Для возможной передачи структурированных данных LLM
from aiogram.types import Message
from aiogram.fsm.context import FSMContext # Для доступа к self.state

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from llm_services import get_llm
# Инструмент RAG
from tools.tool_definitions import query_rag_tool # Будем использовать как инструмент Langchain

from .base_scenario import BaseScenario
from config import OPENAI_API_KEY # Для LLM этого сценария

logger = logging.getLogger(__name__)

# Ключ для общей истории чата (если сценарий будет ее модифицировать)
MAIN_CHAT_HISTORY_KEY = "chat_history_list_v2"
async def add_to_main_chat_history(state: FSMContext, user_message: str, ai_message: str):
    data = await state.get_data()
    history = data.get(MAIN_CHAT_HISTORY_KEY, [])
    history.append({"type": "human", "content": user_message})
    history.append({"type": "ai", "content": ai_message})
    if len(history) > 6: # Храним историю для контекста LLM FAQ (3 пары)
        history = history[-6:]
    await state.update_data({MAIN_CHAT_HISTORY_KEY: history})


# LLM для этого сценария
faq_llm = get_llm(temperature=0.3)

FAQ_EXPERT_SYSTEM_PROMPT = """
Ты — ИИ-ассистент, эксперт по базе знаний и инструкциям компании. Твоя задача — отвечать на вопросы пользователей, используя информацию из предоставленных тебе фрагментов базы знаний.
Сегодня: {current_date}

Правила:
1.  Внимательно прочитай вопрос пользователя.
2.  Если вопрос слишком общий или неясный (например, пользователь просто сказал "общие вопросы" или "расскажи что-нибудь"), вежливо попроси его задать конкретный вопрос по инструкциям или работе системы. В этом случае твой ответ должен быть только уточняющим вопросом.
3.  Если вопрос конкретный:
    а. Тебе будут предоставлены результаты поиска по базе знаний (RAG) в формате:
       `[ИСТОЧНИК: <название источника>] <текст фрагмента>`
    б. Основывай свой ответ ИСКЛЮЧИТЕЛЬНО на предоставленных фрагментах. Не придумывай информацию.
    в. Если предоставленные фрагменты не содержат ответа на вопрос пользователя, сообщи, что не можешь найти информацию по этому конкретному вопросу в базе знаний, и предложи переформулировать.
    г. Старайся отвечать полно и понятно, цитируя или ссылаясь на источник, если это уместно.
4.  Твой ответ должен быть вежливым и полезным.
5.  Не используй инструменты сам, тебе предоставят результаты поиска по базе знаний, если вопрос будет конкретным. Твоя задача - обработать этот результат и вопрос пользователя.
"""

class FaqGeneralScenario(BaseScenario):
    FINISHED_SUFFIX = "finished"
    RAG_COLLECTION_NAME = "general_instructions" # Имя колекции для FAQ в RAG
    id = "faq_general"
    friendly_name = "Общие вопросы и инструкции"
    description = "Ответы на общие вопросы по работе системы, инструкциям, правилам, если вопрос не касается конкретного инцидента с курьером."

    async def _call_faq_llm(self, user_question: str, rag_results_text: str | None) -> str:
        """Вызывает LLM для генерацыи ответа на основе вопроса и данных RAG."""
        from datetime import datetime

        system_prompt_filled = FAQ_EXPERT_SYSTEM_PROMPT.format(current_date=datetime.now().strftime("%Y-%m-%d"))

        messages = [
            SystemMessage(content=system_prompt_filled),
            HumanMessage(content=f"Вопрос пользователя: {user_question}")
        ]

        if rag_results_text:
            messages.append(AIMessage(content=f"Вот информация, найденная в базе знаний:\n{rag_results_text}"))
            messages.append(HumanMessage(content="Пожалуйста, сформируй ответ на вопрос пользователя на основе этой информации."))
        else:
            messages.append(HumanMessage(content="Пожалуйста, ответь на вопрос пользователя или задай уточняющий вопрос, если это необходимо."))

        try:
            response = await faq_llm.ainvoke(messages)
            return response.content.strip()
        except Exception as e:
            logger.error(f"[{self.id}] Ошибка при вызове LLM для FAQ: {e}", exc_info=True)
            return "К сожалению, произошла ошибка при обработке вашего вопроса. Попробуйте позже."


    async def handle_message(self, message: Message) -> None:
        user_current_input = message.text.strip()
        logger.info(f"[{self.id}] Получен ввод от {self.user_login}: '{user_current_input[:100]}...'")

        rag_data_text_for_llm = None
        # Эвристика: если ввод не похож на просто выбор сценария, идем в RAG
        is_likely_specific_question = not (
                any(kw in user_current_input.lower() for kw in ["общий", "общие", "faq", self.friendly_name.lower()]) and \
                len(user_current_input) < 30
        )

        if is_likely_specific_question:
            logger.info(f"[{self.id}] Запрос к RAG для вопроса: '{user_current_input}'")
            rag_response = await query_rag_tool._arun( # Используем инструмент Langchain
                query_text=user_current_input,
                collection_name=self.RAG_COLLECTION_NAME,
                top_k=20 # Возьмем пару чанков для лучшего ответа
            )

            if rag_response and rag_response.get("success") and rag_response.get("data"):
                if rag_response["data"]:
                    chunks_texts = []
                    for i, chunk_data in enumerate(rag_response["data"]):
                        text = chunk_data.get("text", "")
                        source = chunk_data.get("source", "неизвестный источник")
                        chunks_texts.append(f"[ИСТОЧНИК {i+1}: {source}]\n{text}")
                    rag_data_text_for_llm = "\n\n".join(chunks_texts)
                    logger.info(f"[{self.id}] RAG вернул {len(rag_response['data'])} чанк(а).")
                else:
                    logger.info(f"[{self.id}] RAG вернул пустой список данных для: '{user_current_input}'")
            else:
                error_detail = rag_response.get("error", "неизвестная ошибка RAG") if isinstance(rag_response, dict) else "RAG вернул некорректный ответ"
                logger.warning(f"[{self.id}] Ошибка или нет данных от RAG для '{user_current_input}'. Ошибка: {error_detail}")
        else:
            logger.info(f"[{self.id}] Вопрос '{user_current_input}' слишком общий, RAG не вызывается. LLM должна запросить уточнение.")


        final_llm_answer = await self._call_faq_llm(user_current_input, rag_data_text_for_llm)
        await message.answer(final_llm_answer)
        await add_to_main_chat_history(self.state, user_current_input, final_llm_answer)

        # Для одношаговости: ОДИН ВОПРОС - ОДИН ОТВЕТ И ЗАВЕРШЕНИЕ.
        await self.update_scenario_data(**{self.FINISHED_SUFFIX: True})
        logger.info(f"[{self.id}] Сценарий FAQ завершен после ответа LLM.")


    async def is_finished(self) -> bool:
        return await self.get_scenario_data(self.FINISHED_SUFFIX, default=False)