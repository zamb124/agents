import httpx
from typing import List, Dict, Any
from config import RAG_API_URL
import logging

logger = logging.getLogger(__name__)

# Флаг для включения/выключения заглушки RAG
USE_RAG_MOCK = True # Установите в False, когда реальный RAG-сервис будет гатов

MOCKED_KNOWLEDGE_EXTRACTS = {
    "курьер пьяный": [
        {"text": "Должностная инструкция, п.3.5: Курьеру запрещается находиться на рабочем месте или приступать к выполнению смены в состоянии алкогольного, наркотического или иного токсического опьянения.", "source": "courier_job_description.txt"},
        {"text": "Методические рекомендации саппорта, раздел 'Критические нарушения': Появление курьера в нетрезвом виде - немедленное отстранение от смены, удаление текущей смены, блокировка курьера. Запросить фото/видео подтверждение у директора, если возможно.", "source": "support_agent_guidelines.txt"},
        {"text": "Методические рекомендации саппорта, раздел 'Действия при жалобе на опьянение': 1. Уточнить ФИО курьера и склад. 2. Запросить детали инцидента. 3. Применить санкции согласно правилам (см. 'Критические нарушения').", "source": "support_agent_guidelines.txt"}
    ],
    "курьер не вышел на смену": [
        {"text": "Должностная инструкция, п.4.1: Курьер обязан выходить на запланированные смены вовремя. О невозможности выхода на смену необходимо предупредить диспетчера не менее чем за 2 часа.", "source": "courier_job_description.txt"},
        {"text": "Методические рекомендации саппорта, раздел 'Невыход на смену': Если курьер не вышел на смену и не предупредил: 1-й случай - удалить смену, зарегистрировать жалобу (страйк). 2-й случай - удалить смену, блокировка на 7 дней. 3-й случай - блокировка навсегда.", "source": "support_agent_guidelines.txt"}
    ],
    "default": [
        {"text": "Общее правило: При любом нарушении должностной инструкции курьером, сотрудник поддержки должен действовать согласно методическим рекомендациям.", "source": "support_agent_guidelines.txt"},
        {"text": "Важно: Всегда собирайте полную информацию об инциденте перед принятием решения.", "source": "internal_rules.txt"}
    ]
}

async def query_rag_service(query_text: str, top_k: int = 3) -> Dict[str, Any]:
    """
    Отправляет запрос к внешнему RAG-сервису или возвращает моковые данные.
    Возвращает релевантные фрагменты текста. Если RAG_API_URL не задан, всегда будет мок.
    """
    if USE_RAG_MOCK or not RAG_API_URL:
        if not RAG_API_URL and not USE_RAG_MOCK:
            logger.warning("RAG_API_URL не задан, но USE_RAG_MOCK=False. Используется мок RAG.")

        logger.info(f"[RAG MOCK] Запрос: '{query_text}', top_k: {top_k}. Возвращаем моковые данные.")
        # Простая логика для выбора моковых данных на основе ключевых слов
        if "пьяный" in query_text.lower() or "нетрезв" in query_text.lower():
            chunks = MOCKED_KNOWLEDGE_EXTRACTS["курьер пьяный"]
        elif "не вышел" in query_text.lower() or "прогул" in query_text.lower():
            chunks = MOCKED_KNOWLEDGE_EXTRACTS["курьер не вышел на смену"]
        else:
            chunks = MOCKED_KNOWLEDGE_EXTRACTS["default"]

        return {"success": True, "data": chunks[:top_k]}

    # Код для реального RAG-сервиса
    payload = {"query": query_text, "top_k": top_k}
    logger.info(f"[RAG Client] Запрос к RAG: {RAG_API_URL} с payload: {payload}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(RAG_API_URL, json=payload)
            response.raise_for_status()
            result = response.json()
            logger.info(f"[RAG Client] Ответ от RAG: {result}")
            return {"success": True, "data": result.get("retrieved_chunks", [])}
    except httpx.RequestError as e:
        logger.error(f"Ошибка HTTP запроса к RAG сервису {RAG_API_URL}: {e}")
        return {"success": False, "error": f"Ошибка сети при обращении к RAG: {e}"}
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обращении к RAG сервису {RAG_API_URL}: {e}", exc_info=True)
        return {"success": False, "error": f"Неожиданная ошибка RAG: {e}"}