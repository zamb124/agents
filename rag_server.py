# rag_server.py
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import chromadb
from chromadb.utils import embedding_functions
from chromadb.errors import InvalidCollectionException # <--- Импортируем конкретное исключение
import os
from typing import List, Dict, Optional
import asyncio

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
# Используем модель, указанную в логах, если она предпочтительнее
EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-large" # Из лога видно, что эта модель была выбрана
CHROMA_DATA_PATH = "chroma_data"
os.makedirs(CHROMA_DATA_PATH, exist_ok=True)

# --- Инициализация FastAPI и ChromaDB ---
app = FastAPI(
    title="Simple RAG Service",
    description="RAG service using FastAPI, ChromaDB, and Sentence Transformers for Russian language support.",
    version="0.1.0"
)

try:
    chroma_client = chromadb.PersistentClient(path=CHROMA_DATA_PATH)
    logger.info(f"ChromaDB PersistentClient инициализирован. Данные в: {CHROMA_DATA_PATH}")
except Exception as e:
    logger.error(f"Ошибка инициализации ChromaDB PersistentClient: {e}", exc_info=True)
    raise RuntimeError(f"Не удалось инициализировать ChromaDB: {e}")

try:
    sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL_NAME)
    logger.info(f"Функция эмбеддингов для модели '{EMBEDDING_MODEL_NAME}' успешно создана.")
    EMBEDDING_FUNCTION = sentence_transformer_ef
except Exception as e:
    logger.error(f"Ошибка при создании функции эмбеддингов SentenceTransformerEmbeddingFunction: {e}", exc_info=True)
    logger.warning("Попытка использовать DefaultEmbeddingFunction в качестве запасного варианта...")
    try:
        EMBEDDING_FUNCTION = embedding_functions.DefaultEmbeddingFunction()
        logger.info("Используется DefaultEmbeddingFunction (может быть менее точной для русского).")
    except Exception as e_default:
        logger.error(f"Ошибка при создании DefaultEmbeddingFunction: {e_default}", exc_info=True)
        raise RuntimeError(f"Не удалось создать ни одну из функций эмбеддингов: {e} / {e_default}")


# --- Модели данных Pydantic ---
class DocumentInput(BaseModel):
    text: str
    metadata: Optional[Dict[str, str]] = None
    doc_id: Optional[str] = None

class AddDocumentsRequest(BaseModel):
    collection_name: str
    documents: List[DocumentInput]

class QueryRequest(BaseModel):
    collection_name: str
    query: str
    top_k: int = 20

class RetrievedChunk(BaseModel):
    text: str
    metadata: Optional[Dict[str, str]] = None
    distance: Optional[float] = None
    doc_id: Optional[str] = None

class QueryResponse(BaseModel):
    retrieved_chunks: List[RetrievedChunk]

# --- Эндпоинты FastAPI ---

@app.post("/add_documents", summary="Add documents to a ChromaDB collection")
async def add_documents_to_collection(request: AddDocumentsRequest):
    try:
        logger.info(f"Запрос на добавление документов в коллекцию: {request.collection_name}")
        collection = chroma_client.get_or_create_collection(
            name=request.collection_name,
            embedding_function=EMBEDDING_FUNCTION
        )
        docs_to_add = [doc.text for doc in request.documents]
        metadatas_to_add = [doc.metadata for doc in request.documents]
        ids_to_add = [doc.doc_id if doc.doc_id else f"{request.collection_name}_{hash(doc.text)}_{i}" for i, doc in enumerate(request.documents)]

        existing_ids_in_batch = set()
        unique_ids_to_add = []
        unique_docs_to_add = []
        unique_metadatas_to_add = []

        for i, doc_id in enumerate(ids_to_add):
            if doc_id not in existing_ids_in_batch:
                existing_ids_in_batch.add(doc_id)
                unique_ids_to_add.append(doc_id)
                unique_docs_to_add.append(docs_to_add[i])
                unique_metadatas_to_add.append(metadatas_to_add[i])
            else:
                logger.warning(f"Дублирующийся ID документа '{doc_id}' в пакете, пропущен.")

        if not unique_ids_to_add:
            return {"message": "Нет уникальных документов для добавления (возможно, все ID дублируются)."}

        collection.add(
            documents=unique_docs_to_add,
            metadatas=unique_metadatas_to_add,
            ids=unique_ids_to_add
        )
        logger.info(f"{len(unique_docs_to_add)} документов успешно добавлено/обновлено в коллекцию '{request.collection_name}'.")
        return {"message": f"{len(unique_docs_to_add)} документов успешно добавлено/обновлено в коллекцию '{request.collection_name}'."}
    except Exception as e:
        logger.error(f"Ошибка при добавлении документов в коллекцию '{request.collection_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка сервера при добавлении документов: {str(e)}")

@app.post("/query", response_model=QueryResponse, summary="Query a ChromaDB collection")
async def query_collection(request: QueryRequest):
    try:
        logger.info(f"Запрос к коллекции '{request.collection_name}': '{request.query}', top_k={request.top_k}")
        collection = chroma_client.get_collection(
            name=request.collection_name,
            embedding_function=EMBEDDING_FUNCTION
        )
        results = collection.query(
            query_texts=[request.query],
            n_results=request.top_k,
            include=['documents', 'metadatas', 'distances']
        )
        retrieved_chunks = []
        if results and results.get('documents') and results.get('ids') and results['documents'][0]:
            for i, doc_text in enumerate(results['documents'][0]):
                chunk = RetrievedChunk(
                    text=doc_text,
                    metadata=results['metadatas'][0][i] if results.get('metadatas') and results['metadatas'][0] else None,
                    distance=results['distances'][0][i] if results.get('distances') and results['distances'][0] else None,
                    doc_id=results['ids'][0][i]
                )
                retrieved_chunks.append(chunk)
        logger.info(f"Найдено {len(retrieved_chunks)} чанков для запроса к '{request.collection_name}'.")
        return QueryResponse(retrieved_chunks=retrieved_chunks)
    except InvalidCollectionException:
        logger.warning(f"Коллекция '{request.collection_name}' не найдена.")
        return QueryResponse(retrieved_chunks=[])
    except Exception as e:
        logger.error(f"Общая ошибка при выполнении запроса к коллекции '{request.collection_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка сервера при выполнении запроса: {str(e)}")

@app.get("/list_collections", summary="List all collections in ChromaDB")
async def list_collections_endpoint():
    try:
        collections = chroma_client.list_collections()
        collection_names = [col.name for col in collections]
        logger.info(f"Доступные коллекции: {collection_names}")
        return {"collections": collection_names}
    except Exception as e:
        logger.error(f"Ошибка при получении списка коллекций: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка сервера при получении списка коллекций: {str(e)}")

async def populate_initial_data_async():
    logger.info("Проверка и наполнение начальными данными...")
    collection_names_to_populate = {
        "courier_job_description": [
            DocumentInput(text="Пункт 3.5: Курьеру категорически запрещается находиться на рабочем месте или приступать к выполнению смены в состоянии алкогольного, наркотического или иного токсического опьянения. Любое подозрение должно быть немедленно доложено руководству.", metadata={"source": "Должностная инструкция курьера v1.2", "section": "3. Обязанности и запреты"}, doc_id="courier_instr_3_5"),
            DocumentInput(text="Пункт 4.1: Курьер обязан выходить на запланированные смены строго вовремя. О любой невозможности выхода на смену или опоздании необходимо заблаговременно (не менее чем за 2 часа до начала смены) предупредить своего непосредственного руководителя или диспетчера.", metadata={"source": "Должностная инструкция курьера v1.2", "section": "4. Рабочее время и дисциплина"}, doc_id="courier_instr_4_1"),
            DocumentInput(text="Пункт 2.3: Курьер должен быть вежлив и корректен в общении с клиентами и коллегами. Запрещаются любые формы грубости, хамства или нецензурной брани.", metadata={"source": "Должностная инструкция курьера v1.2", "section": "2. Общие положения"}, doc_id="courier_instr_2_3")
        ],
        "support_agent_guidelines": [
            DocumentInput(text="Раздел 'Критические нарушения': Появление курьера в нетрезвом виде на рабочем месте классифицируется как критическое нарушение. Действия сотрудника поддержки: немедленное отстранение курьера от выполнения обязанностей, информирование руководителя склада, удаление текущей смены курьера из системы, инициирование процедуры блокировки курьера. По возможности, запросить у директора фото/видео фиксацию состояния курьера.", metadata={"source": "Методичка саппорта v2.0", "topic": "Критические нарушения"}, doc_id="support_guide_critical_drunk"),
            DocumentInput(text="Раздел 'Невыход на смену': Если курьер не вышел на запланированную смену и не предупредил об этом заранее (согласно п.4.1 Должностной инструкции): Первый случай - удалить текущую смену, зарегистрировать официальную жалобу (страйк). Уведомить курьера о последствиях. Второй случай (повторный) - удалить смену, временная блокировка курьера на 7 дней. Третий случай - удаление смены, перманентная блокировка курьера.", metadata={"source": "Методичка саппорта v2.0", "topic": "Невыход на смену"}, doc_id="support_guide_no_show"),
            DocumentInput(text="Раздел 'Опоздания': Опоздание курьера на смену до 30 минут - устное предупреждение. Опоздание от 30 минут до 1 часа - зарегистрировать жалобу (страйк). Опоздание более 1 часа - удалить текущую смену, зарегистрировать жалобу (страйк).", metadata={"source": "Методичка саппорта v2.0", "topic": "Опоздания"}, doc_id="support_guide_late")
        ]
    }

    for collection_name, docs_to_add in collection_names_to_populate.items():
        try:
            collection = chroma_client.get_collection(name=collection_name, embedding_function=EMBEDDING_FUNCTION)
            if collection.count() == 0:
                logger.info(f"Коллекция '{collection_name}' пуста. Наполнение...")
                await add_documents_to_collection(AddDocumentsRequest(collection_name=collection_name, documents=docs_to_add))
            else:
                logger.info(f"Коллекция '{collection_name}' уже содержит данные ({collection.count()} документов).")
        except InvalidCollectionException:
            logger.info(f"Коллекция '{collection_name}' не найдена (InvalidCollectionException). Создание и наполнение...")
            await add_documents_to_collection(AddDocumentsRequest(collection_name=collection_name, documents=docs_to_add))
        except Exception as e:
            logger.error(f"Ошибка при проверке/наполнении коллекции '{collection_name}': {e}", exc_info=True)


@app.on_event("startup")
async def startup_event():
    logger.info("FastAPI приложение запускается...")
    try:
        await populate_initial_data_async()
    except Exception as e:
        logger.error(f"Ошибка при наполнении начальными данными во время стартапа: {e}", exc_info=True)

if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск RAG FastAPI сервера...")
    uvicorn.run("rag_server:app", host="0.0.0.0", port=8001, reload=True, log_level="info")