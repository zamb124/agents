# tools/tool_definitions.py
from langchain.tools import Tool as LangchainTool
from langchain_core.tools import BaseTool
from typing import Type, List, Dict, Any, Optional
from pydantic import BaseModel, Field
import logging
import asyncio

from .courier_api import search_courier_by_id_or_name, get_courier_shifts
from .warehouse_api import get_warehouse_by_director_login, find_warehouse_by_name_or_id
from .decision_actions import take_action_on_courier
from .rag_client import query_rag_service

logger = logging.getLogger(__name__)

# --- Pydantic модели для аргументов инструментов (остаются как были) ---
class SearchCourierInput(BaseModel):
    identifier: str = Field(description="ID или ФИО курьера для поиска. Можно неполное ФИО.")
    warehouse_id: Optional[str] = Field(default=None, description="ID склада, на котором нужно искать курьера. Крайне рекомендуется указывать для точности поиска по имени.")

class GetWarehouseByLoginInput(BaseModel):
    director_login: str = Field(description="Логин директора (Telegram username) для определения его склада.")

class FindWarehouseInput(BaseModel):
    identifier: str = Field(description="ID или название (можно частичное) склада для поиска и проверки существования.")

class TakeActionInput(BaseModel):
    action_type: str = Field(description="Тип действия: 'delete_shift', 'ban_courier', 'log_complaint'.")
    courier_id: str = Field(description="ID курьера.")
    reason: str = Field(description="Подробное описание причины действия.")
    shift_id: Optional[str] = Field(default=None, description="ID смены для 'delete_shift'.")
    warehouse_id: Optional[str] = Field(default=None, description="ID склада (вспомогательно).")

class QueryRAGInput(BaseModel):
    query_text: str = Field(description="Текстовый запрос для поиска в базе знаний.")
    top_k: int = Field(default=3, description="Количество релевантных фрагментов.")
    collection_name: str = Field(description="Название коллекции в RAG (например, 'courier_job_description', 'support_agent_guidelines', 'general_instructions').")

class GetCourierShiftsInput(BaseModel):
    courier_id: str = Field(description="ID курьера.")
    date_str: Optional[str] = Field(default=None, description="Дата в формате YYYY-MM-DD. Если None, вернутся все активные/запланированные.")

# --- Создание экземпляров инструментов (классы-обертки BaseTool) ---

class SearchCourierTool(BaseTool):
    name: str = "search_courier_by_id_or_name"
    description: str = "Ищет курьера по ID/ФИО. Для ФИО нужен warehouse_id."
    args_schema: Type[BaseModel] = SearchCourierInput

    def _run(self, identifier: str, warehouse_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        # search_courier_by_id_or_name - синхронная функция
        return search_courier_by_id_or_name(identifier=identifier, warehouse_id=warehouse_id)
    async def _arun(self, identifier: str, warehouse_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return self._run(identifier=identifier, warehouse_id=warehouse_id) # Просто вызываем синхронную версию
search_courier_tool = SearchCourierTool()

class GetWarehouseByLoginTool(BaseTool):
    name: str = "get_warehouse_by_director_login"
    description: str = "Определяет склад по логину директора."
    args_schema: Type[BaseModel] = GetWarehouseByLoginInput
    def _run(self, director_login: str, **kwargs) -> Dict[str, Any]:
        return get_warehouse_by_director_login(director_login=director_login)
    async def _arun(self, director_login: str, **kwargs) -> Dict[str, Any]:
        return self._run(director_login=director_login)
get_warehouse_by_login_tool = GetWarehouseByLoginTool()

class FindWarehouseTool(BaseTool):
    name: str = "find_warehouse_by_name_or_id"
    description: str = "Ищет склад по ID или названию."
    args_schema: Type[BaseModel] = FindWarehouseInput
    def _run(self, identifier: str, **kwargs) -> Dict[str, Any]:
        # find_warehouse_by_name_or_id стала асинхронной.
        # Вызов asyncio.run() из синхронного _run в работающем цикле событий (как в aiogram) вызовет ошибку.
        # Langchain AgentExecutor, если он работает в асинхронном контексте (как в нашем боте),
        # будет пытаться вызвать _arun, если он есть. Если _arun нет, он может попытаться
        # запустить _run в отдельном потоке, но это не всегда хорошо для I/O bound операций.
        # **Лучшая практика: если базовая функция асинхронная, _run должен либо вызывать ошибку,
        # либо быть реализован так, чтобы не блокировать основной поток (сложно).**
        # **Агент должен вызывать _arun.**
        logger.error(f"Synchronous call (_run) to FindWarehouseTool which wraps an async function is not recommended in an async environment.")
        raise NotImplementedError("FindWarehouseTool._run is not meant to be called directly in an async environment. Use _arun.")
    async def _arun(self, identifier: str, **kwargs) -> Dict[str, Any]:
        return await find_warehouse_by_name_or_id(identifier=identifier)
find_warehouse_tool = FindWarehouseTool()

class GetCourierShiftsTool(BaseTool):
    name: str = "get_courier_shifts"
    description: str = "Получает смены курьера."
    args_schema: Type[BaseModel] = GetCourierShiftsInput
    def _run(self, courier_id: str, date_str: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return get_courier_shifts(courier_id=courier_id, date_str=date_str)
    async def _arun(self, courier_id: str, date_str: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return self._run(courier_id=courier_id, date_str=date_str)
get_courier_shifts_tool = GetCourierShiftsTool()

class TakeActionTool(BaseTool):
    name: str = "take_action_on_courier"
    description: str = "Выполняет действие: 'delete_shift', 'ban_courier', 'log_complaint'."
    args_schema: Type[BaseModel] = TakeActionInput
    def _run(self, action_type: str, courier_id: str, reason: str, shift_id: Optional[str] = None, warehouse_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return take_action_on_courier(action_type=action_type, courier_id=courier_id, reason=reason, shift_id=shift_id, warehouse_id=warehouse_id)
    async def _arun(self, action_type: str, courier_id: str, reason: str, shift_id: Optional[str] = None, warehouse_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return self._run(action_type=action_type, courier_id=courier_id, reason=reason, shift_id=shift_id, warehouse_id=warehouse_id)
take_action_tool = TakeActionTool()

class QueryKnowledgeBaseTool(BaseTool):
    name: str = "query_knowledge_base"
    description: str = "Ищет информацию в базе знаний (RAG)."
    args_schema: Type[BaseModel] = QueryRAGInput
    def _run(self, query_text: str, collection_name: str, top_k: int = 3, **kwargs) -> Dict[str, Any]:
        # query_rag_service асинхронная.
        logger.error(f"Synchronous call (_run) to QueryKnowledgeBaseTool which wraps an async function is not recommended.")
        raise NotImplementedError("QueryKnowledgeBaseTool._run is not meant to be called directly in an async environment. Use _arun.")
    async def _arun(self, query_text: str, collection_name: str, top_k: int = 3, **kwargs) -> Dict[str, Any]:
        return await query_rag_service(query_text=query_text, top_k=top_k, collection_name=collection_name)
query_rag_tool = QueryKnowledgeBaseTool()