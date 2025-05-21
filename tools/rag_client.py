import httpx
from typing import List, Dict, Any
from config import RAG_API_URL
import logging

logger = logging.getLogger(__name__)

# Моковые выдержки из базы знаний, типа наш RAG для тестов
MOCKED_KNOWLEDGE_EXTRACTS = {
    "courier_job_description": {
        "пьяный": [
            {"text": "Должностная инструкция, п.3.5: Курьеру запрещается находиться на рабочем месте или приступать к выполнению смены в состоянии алкогольного, наркотического или иного токсического опьянения.", "source": "courier_job_description.txt"},
        ],
        "не вышел": [
            {"text": "Должностная инструкция, п.4.1: Курьер обязан выходить на запланированные смены вовремя. О невозможности выхода на смену необходимо предупредить диспетчера не менее чем за 2 часа.", "source": "courier_job_description.txt"},
        ],
        "default": [
            {"text": "Общее правило из должностной инструкции: Курьер должен соблюдать все пункты инструкции. Это важно.", "source": "courier_job_description.txt"}
        ]
    },
    "support_agent_guidelines": {
        "пьяный": [
            {"text": "Методические рекомендации саппорта, раздел 'Критические нарушения': Появление курьера в нетрезвом виде - немедленное отстранение от смены, удаление текущей смены, блокировка курьера. Запросить фото/видео подтверждение у директора, если возможно.", "source": "support_agent_guidelines.txt"},
            {"text": "Методические рекомендации саппорта, раздел 'Действия при жалобе на опьянение': 1. Уточнить ФИО курьера и склад. 2. Запросить детали инцидента. 3. Применить санкции согласно правилам (см. 'Критические нарушения').", "source": "support_agent_guidelines.txt"}
        ],
        "не вышел": [
            {"text": "Методические рекомендации саппорта, раздел 'Невыход на смену': Если курьер не вышел на смену и не предупредил: 1-й случай - удалить смену, зарегистрировать жалобу (страйк). 2-й случай - удалить смену, блокировка на 7 дней. 3-й случай - блокировка навсегда.", "source": "support_agent_guidelines.txt"}
        ],
        "default": [
            {"text": "Общее правило из методички: При любом нарушении, сотрудник поддержки должен действовать согласно методическим рекомендациям. Всегда.", "source": "support_agent_guidelines.txt"},
        ]
    },
    "fallback_default": [
        {"text": "Важно: Всегда собирайте полную информацию об инциденте перед принятием решения. Не торопитесь.", "source": "internal_rules.txt"}
    ]
}

async def query_rag_service(query_text: str, top_k: int = 3, collection_name: str = "support_agent_guidelines") -> Dict[str, Any]:
    """
    Отправляет запрос к RAG-сервису или возвращает моковые данные, если `RAG_API_URL` не задан.
    Возвращает релевантные фрагменты текста. Типа умный поиск.
    """
    if not RAG_API_URL:
        logger.warning(f"RAG_API_URL не задан. Используется моковый RAG-ответ для коллекции '{collection_name}'. Это для тестов ок.")

        collection_mock = MOCKED_KNOWLEDGE_EXTRACTS.get(collection_name)
        if not collection_mock:
            logger.warning(f"Мок для коллекции '{collection_name}' не найден, используется fallback_default.")
            chunks = MOCKED_KNOWLEDGE_EXTRACTS["fallback_default"]
        elif "пьяный" in query_text.lower() or "нетрезв" in query_text.lower():
            chunks = collection_mock.get("пьяный", collection_mock.get("default", MOCKED_KNOWLEDGE_EXTRACTS["fallback_default"]))
        elif "не вышел" in query_text.lower() or "прогул" in query_text.lower():
            chunks = collection_mock.get("не вышел", collection_mock.get("default", MOCKED_KNOWLEDGE_EXTRACTS["fallback_default"]))
        else:
            chunks = collection_mock.get("default", MOCKED_KNOWLEDGE_EXTRACTS["fallback_default"])

        return {"success": True, "data": chunks[:top_k]}

    payload = {
        "collection_name": collection_name,
        "query": query_text,
        "top_k": top_k
    }
    logger.info(f"[RAG Client] Запрос к реальному RAG: {RAG_API_URL} с payload: {payload}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(RAG_API_URL, json=payload)
            response.raise_for_status()
            result = response.json()
            logger.info(f"[RAG Client] Ответ от RAG: {result}")

            agent_formatted_chunks = []
            if result.get("retrieved_chunks"):
                for chunk_data in result["retrieved_chunks"]:
                    source_info = chunk_data.get("metadata", {}).get("source", "RAG DB (источник не указан)")
                    if chunk_data.get("metadata", {}).get("section"):
                        source_info += f" (section: {chunk_data['metadata']['section']})"
                    elif chunk_data.get("metadata", {}).get("topic"):
                        source_info += f" (topic: {chunk_data['metadata']['topic']})"

                    agent_formatted_chunks.append({
                        "text": chunk_data["text"],
                        "source": source_info
                    })
            return {"success": True, "data": agent_formatted_chunks}
    except httpx.RequestError as e:
        logger.error(f"Ошибка HTTP запроса к RAG сервису {RAG_API_URL}: {e}")
        return {"success": False, "error": f"Ошибка сети при обращении к RAG: {e}. Может упал?"}
    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка HTTP статуса от RAG сервиса {RAG_API_URL}: {e.response.status_code} - {e.response.text}")
        return {"success": False, "error": f"Ошибка от RAG сервиса ({e.response.status_code}): {e.response.text}. Что-то там не так."}
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обращении к RAG сервису {RAG_API_URL}: {e}", exc_info=True)
        return {"success": False, "error": f"Неожиданная ошибка RAG: {e}. Разбираца надо."}