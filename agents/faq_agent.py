# agents/faq_agent.py
import logging
from typing import Dict, Any, List, Optional

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

FAQ_AGENT_SYSTEM_PROMPT_TEMPLATE = """
Ты — ИИ-ассистент, эксперт по базе знаний и инструкциям компании. Твоя задача — отвечать на вопросы пользователей.
Сегодня: {current_date}
Информация из базы знаний (RAG), если была найдена:
{rag_results_text_formatted}

ПОСЛЕДНЕЕ СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ (его вопрос):
"{user_question}"

ИСТОРИЯ ДИАЛОГА С ТОБОЙ (если есть, для контекста):
{dialog_history_formatted}

ПРАВИЛА:
1.  Если вопрос слишком общий или неясный, вежливо попроси его задать конкретный вопрос.
2.  Если вопрос конкретный:
    а. Если есть информация из RAG, основывай свой ответ ИСКЛЮЧИТЕЛЬНО на ней.
    б. Если RAG-информация нерелевантна или отсутствует, но ты можешь ответить на основе общих знаний, сделай это, указав, что это общая информация.
    в. Если не можешь ответить, сообщи, что не нашел информацию, и предложи переформулировать.
3.  Отвечай полно, понятно и вежливо. Цитируй источник из RAG, если уместно.
"""

def format_faq_rag_results(rag_text: Optional[str]) -> str:
    if not rag_text: return "Информация из специальной базы знаний не предоставлена."
    return rag_text

def format_faq_dialog_history(history: List[Dict[str, str]]) -> str:
    if not history: return "Это начало диалога по FAQ."
    return "\n".join([f"{msg['type'].capitalize()}: {msg['content']}" for msg in history])


class FaqAgent(BaseAgent):
    agent_id: str = "faq_agent_main"

    def _get_default_tools(self) -> List[Any]: return []

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # FaqAgent может вести короткую историю диалога с ним, если это многошаговый FAQ.
        # Для одношагового FAQ состояние не нужно.
        return {"dialog_history_for_faq": []} # Или просто {}

    async def process_user_input(
            self,
            user_input: str, # Это вопрос пользователя
            current_agent_state: Dict[str, Any],
            scenario_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:

        # scenario_context должен содержать:
        # "rag_results_text": результат RAG-поиска (может быть None)
        # "current_date": текущая дата
        # "main_chat_history": опционально, общая история для контекста LLM

        rag_results = scenario_context.get("rag_results_text") if scenario_context else None
        current_date = scenario_context.get("current_date", "неизвестна") if scenario_context else "неизвестна"
        # FaqAgent может использовать свою короткую историю или общую
        dialog_history = current_agent_state.get("dialog_history_for_faq", [])
        # или dialog_history = scenario_context.get("main_chat_history", [])

        # Обновляем историю FaqAgent
        updated_dialog_history = list(dialog_history)
        if user_input: # Добавляем вопрос пользователя
            updated_dialog_history.append({"type": "human", "content": user_input})

        system_prompt = FAQ_AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            current_date=current_date,
            rag_results_text_formatted=format_faq_rag_results(rag_results),
            user_question=user_input,
            dialog_history_formatted=format_faq_dialog_history(dialog_history) # История до текущего вопроса
        )

        messages = [SystemMessage(content=system_prompt)]
        # user_input уже в системном промпте.

        logger.info(f"[{self.agent_id}] Answering FAQ: '{user_input[:100]}...'. RAG: {'Yes' if rag_results else 'No'}")

        try:
            response = await self.llm.ainvoke(messages)
            final_answer = response.content.strip()

            # Обновляем историю ответом агента
            updated_dialog_history.append({"type": "ai", "content": final_answer})
            next_agent_state = {"dialog_history_for_faq": updated_dialog_history}

            # FaqAgent обычно завершает свою работу за один шаг
            return {
                "status": "completed",
                "message_to_user": final_answer,
                "result": {"answer": final_answer}, # Результат - это сам ответ
                "next_agent_state": next_agent_state
            }
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка: {e}", exc_info=True)
            return {
                "status": "error",
                "message_to_user": "Ошибка при обработке вашего FAQ вопроса.",
                "next_agent_state": current_agent_state # Возвращаем старое состояние
            }