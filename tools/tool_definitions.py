from langchain.tools import Tool
from langchain_core.tools import BaseTool
from typing import Type, List, Dict, Any
from pydantic import BaseModel, Field
import logging
import asyncio

from .courier_api import search_courier_by_id_or_name, get_courier_shifts
from .warehouse_api import get_warehouse_by_director_login
from .decision_actions import take_action_on_courier
from .rag_client import query_rag_service

logger = logging.getLogger(__name__)


class SearchCourierInput(BaseModel):
    identifier: str = Field(description="ID или ФИО курьера для поиска. Можно неполное ФИО.")

class GetWarehouseInput(BaseModel):
    director_login: str = Field(description="Логин директора (Telegram username) для определения его склада. Ну чтоб понять откуда он.")

class TakeActionInput(BaseModel):
    action_type: str = Field(description="Тип действия: 'delete_shift' (удалить смену), 'ban_courier' (забанить курьера), 'log_complaint' (записать жалобу).")
    courier_id: str = Field(description="ID курьера, к которому применяем действие. Обязательно.")
    reason: str = Field(description="Подробное описание причины действия/инцидента, основанное на правилах и фактах. Писать понятно.")
    shift_id: str = Field(default=None, description="ID конкретной смены для действия (например, для 'delete_shift'). Если не указан, может быть выбрана ближайшая смена автоматом.")
    warehouse_id: str = Field(default=None, description="ID склада (вспомогательная информация, может быть не обязательна, если есть shift_id, но не помешает).")

class QueryRAGInput(BaseModel):
    query_text: str = Field(description="Текстовый запрос (описание проблемы или вопрос) для поиска релевантной информации в базе знаний. Что ищем кароче.")
    top_k: int = Field(default=3, description="Количество наиболее релевантных фрагментов для возврата. Сколько штук.")
    collection_name: str = Field(description="Название коллекции в базе знаний для поиска (например, 'courier_job_description' или 'support_agent_guidelines'). Где ищем.")

class GetCourierShiftsInput(BaseModel):
    courier_id: str = Field(description="ID курьера для поиска его смен. Чьи смены смотрим.")
    date_str: str = Field(default=None, description="Дата в формате YYYY-MM-DD для фильтрации смен. Если не указана, вернутся все активные/запланированные смены курьера. Можно без даты.")


search_courier_tool = Tool.from_function(
    func=search_courier_by_id_or_name,
    name="search_courier",
    description="Используется для поиска информации о курьере по его ID или ФИО. Или задает уточняющие вопросы чтобы это получить. Возвращает данные курьера или саобщение об ошибке.",
    args_schema=SearchCourierInput
)

get_warehouse_tool = Tool.from_function(
    func=get_warehouse_by_director_login,
    name="get_warehouse_by_director_login",
    description="Используется для определения склада, к которому привязан директор, по его логину (например, telegram username). Чтобы знать, кто спрашивает.",
    args_schema=GetWarehouseInput
)

class GetCourierShiftsTool(BaseTool):
    name: str = "get_courier_shifts"
    description: str = "Получает список активных или запланированных смен для указанного курьера. Можно указать дату (в формате ГГГГ-ММ-ДД) для фильтрации, если надо конкретный день."
    args_schema: Type[BaseModel] = GetCourierShiftsInput

    def _run(self, courier_id: str, date_str: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"[{self.name}._run] Вызван синхронно с: courier_id={courier_id}, date_str={date_str}")
        if kwargs: logger.warning(f"[{self.name}._run] Получены неожиданные kwargs: {kwargs}")
        return get_courier_shifts(courier_id=courier_id, date_str=date_str)

    async def _arun(self, courier_id: str, date_str: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"[{self.name}._arun] Вызван асинхронно (вызывает _run) с: courier_id={courier_id}, date_str={date_str}")
        if kwargs: logger.warning(f"[{self.name}._arun] Получены неожиданные kwargs: {kwargs}")
        return self._run(courier_id=courier_id, date_str=date_str)

get_courier_shifts_tool = GetCourierShiftsTool()

class TakeActionTool(BaseTool):
    name: str = "take_action_on_courier"
    description: str = (
        "Используется для выполнения одного из следующих действий: 'delete_shift' (удалить смену курьера), "
        "'ban_courier' (заблокировать курьера), 'log_complaint' (зарегистрировать жалобу на курьера). "
        "Всегда указывай причину, почему так делаешь. Для 'delete_shift' желательно указать shift_id конкретной смены, если она известна и должна быть удалена."
    )
    args_schema: Type[BaseModel] = TakeActionInput

    def _run(self, action_type: str, courier_id: str, reason: str, shift_id: str = None, warehouse_id: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"[{self.name}._run] Вызван синхронно: action_type={action_type}, courier_id={courier_id}, reason='{reason[:50]}...', shift_id={shift_id}, warehouse_id={warehouse_id}")
        if kwargs: logger.warning(f"[{self.name}._run] Получены неожиданные kwargs: {kwargs}")
        return take_action_on_courier(action_type=action_type, courier_id=courier_id, reason=reason, shift_id=shift_id, warehouse_id=warehouse_id)

    async def _arun(self, action_type: str, courier_id: str, reason: str, shift_id: str = None, warehouse_id: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"[{self.name}._arun] Вызван асинхронно (вызывает _run): action_type={action_type}, courier_id={courier_id}, reason='{reason[:50]}...', shift_id={shift_id}, warehouse_id={warehouse_id}")
        if kwargs: logger.warning(f"[{self.name}._arun] Получены неожиданные kwargs: {kwargs}")
        return self._run(action_type=action_type, courier_id=courier_id, reason=reason, shift_id=shift_id, warehouse_id=warehouse_id)

take_action_tool = TakeActionTool()


class QueryKnowledgeBaseTool(BaseTool):
    name: str = "query_knowledge_base"
    description: str = (
        "Используется для поиска релевантной информации в базе знаний (типа RAG). "
        "Необходимо указать 'query_text' (текст запроса, что ищем) и 'collection_name' (название коллекции, например, 'courier_job_description' для должностных инструкций или 'support_agent_guidelines' для методичек саппорта). "
        "Опционально можно указать 'top_k' (сколько результатов вернуть, по умолчанию 3)."
    )
    args_schema: Type[BaseModel] = QueryRAGInput

    def _run(self, query_text: str, collection_name: str, top_k: int = 3, **kwargs) -> Dict[str, Any]:
        logger.warning(f"[{self.name}._run] Синхронный вызов асинхронной по сути тулзы. Запускаю _arun через asyncio.run().")
        if kwargs:
            logger.warning(f"[{self.name}._run] Получены неожиданные kwargs: {kwargs}")
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                logger.error(f"[{self.name}._run] Обнаружен запущенный event loop. Такой синхронный вызов не сработает.")
                raise NotImplementedError(f"{self.name} - это асинхронный инструмент и не может быть корректно вызван синхронно из уже запущенного event loop.")
            else:
                return asyncio.run(self._arun(query_text=query_text, collection_name=collection_name, top_k=top_k))
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e) or "asyncio.run() cannot be called from a running event loop" in str(e) :
                logger.error(f"[{self.name}._run] RuntimeError: Нельзя вызывать asyncio.run из уже запущенного event loop. Инструмент предназначен для асинхронного использования.")
                raise NotImplementedError(f"{self.name} - это асинхронный инструмент и не может быть вызван синхронно из запущенного event loop.")
            raise e

    async def _arun(self, query_text: str, collection_name: str, top_k: int = 3, **kwargs) -> Dict[str, Any]:
        logger.info(
            f"[{self.name}._arun] Вызван асинхронно с: "
            f"query_text='{query_text}', collection_name='{collection_name}', top_k={top_k}"
        )
        if kwargs:
            logger.warning(f"[{self.name}._arun] Получены неожиданные kwargs: {kwargs}")

        return await query_rag_service(
            query_text=query_text,
            top_k=top_k,
            collection_name=collection_name
        )

query_rag_tool = QueryKnowledgeBaseTool()


# Инструменты для агента-сборщика информации
collector_tools = [
    search_courier_tool,
    get_warehouse_tool,
    get_courier_shifts_tool,
    query_rag_tool
]

# Инструменты для агента-принимающего решения
decision_tools = [
    take_action_tool,
    query_rag_tool
]