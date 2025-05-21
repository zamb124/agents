# tools/tool_definitions.py
from langchain.tools import Tool
from langchain_core.tools import BaseTool
from typing import Type, List, Dict, Any
from pydantic import BaseModel, Field
import json
import logging

from .courier_api import search_courier_by_id_or_name, get_courier_shifts
from .warehouse_api import get_warehouse_by_director_login
from .decision_actions import take_action_on_courier
from .rag_client import query_rag_service

logger = logging.getLogger(__name__)


class SearchCourierInput(BaseModel):
    identifier: str = Field(description="ID или ФИО курьера для поиска.")

class GetWarehouseInput(BaseModel):
    director_login: str = Field(description="Логин директора (Telegram username) для определения его склада.")

class TakeActionInput(BaseModel):
    action_type: str = Field(description="Тип действия: 'delete_shift', 'ban_courier', 'log_complaint'.")
    courier_id: str = Field(description="ID курьера, к которому применяется действие.")
    reason: str = Field(description="Подробное описание причины действия/инцидента, основанное на правилах и фактах.")
    shift_id: str = Field(default=None, description="ID конкретной смены для действия (например, для 'delete_shift'). Если не указан, может быть выбрана ближайшая смена.")
    warehouse_id: str = Field(default=None, description="ID склада (вспомогательная информация, может быть не обязательна, если есть shift_id).")

class QueryRAGInput(BaseModel):
    query_text: str = Field(description="Текстовый запрос (описание проблемы или вопрос) для поиска релевантной информации в базе знаний (должностные инструкции, методички).")
    top_k: int = Field(default=3, description="Количество наиболее релевантных фрагментов для возврата.")

class GetCourierShiftsInput(BaseModel):
    courier_id: str = Field(description="ID курьера для поиска его смен.")
    date_str: str = Field(default=None, description="Дата в формате YYYY-MM-DD для фильтрации смен. Если не указана, вернутся все активные/запланированные смены курьера.")



search_courier_tool = Tool.from_function(
    func=search_courier_by_id_or_name,
    name="search_courier",
    description="Используется для поиска информации о курьере по его ID или ФИО. Или задает уточняющие вопросы что бы это получить. Возвращает данные курьера или саобщение об ошибке.",
    args_schema=SearchCourierInput
)

get_warehouse_tool = Tool.from_function(
    func=get_warehouse_by_director_login,
    name="get_warehouse_by_director_login",
    description="Используется для определения склада, к которому привязан директор, по его логину (например, telegram username).",
    args_schema=GetWarehouseInput
)


class GetCourierShiftsTool(BaseTool):
    name: str = "get_courier_shifts"
    description: str = "Получает список активных или запланированных смен для указанного курьера. Можно указать дату (YYYY-MM-DD) для фильтрации."
    args_schema: Type[BaseModel] = GetCourierShiftsInput

    def _run(self, courier_id: str, date_str: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(
            f"[{self.name}._run] Invoked with: "
            f"courier_id={courier_id}, date_str={date_str}"
        )
        if kwargs:
            logger.warning(f"[{self.name}._run] Received unexpected kwargs: {kwargs}")
        return get_courier_shifts(courier_id=courier_id, date_str=date_str)

    async def _arun(self, courier_id: str, date_str: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(
            f"[{self.name}._arun] Invoked (calling _run) with: "
            f"courier_id={courier_id}, date_str={date_str}"
        )
        if kwargs:
            logger.warning(f"[{self.name}._arun] Received unexpected kwargs: {kwargs}")
        # Поскольку get_courier_shifts синхронная, вызываем _run
        return self._run(courier_id=courier_id, date_str=date_str)

get_courier_shifts_tool = GetCourierShiftsTool()


class TakeActionTool(BaseTool):
    name: str = "take_action_on_courier"
    description: str = (
        "Используется для выполнения одного из следующих действий: 'delete_shift' (удалить смену курьера), "
        "'ban_courier' (заблокировать курьера), 'log_complaint' (зарегистрировать жалобу на курьера). "
        "Всегда указывай причину. Для 'delete_shift' желательно указать shift_id конкретной смены, если она известна и должна быть удалена."
    )
    args_schema: Type[BaseModel] = TakeActionInput

    def _run(self, action_type: str, courier_id: str, reason: str, shift_id: str = None, warehouse_id: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(
            f"[{self.name}._run] Invoked with: "
            f"action_type={action_type}, courier_id={courier_id}, reason='{reason[:50]}...', "
            f"shift_id={shift_id}, warehouse_id={warehouse_id}"
        )
        if kwargs:
            logger.warning(f"[{self.name}._run] Received unexpected kwargs: {kwargs}")

        return take_action_on_courier(
            action_type=action_type,
            courier_id=courier_id,
            reason=reason,
            shift_id=shift_id,
            warehouse_id=warehouse_id
        )

    async def _arun(self, action_type: str, courier_id: str, reason: str, shift_id: str = None, warehouse_id: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(
            f"[{self.name}._arun] Invoked (calling _run) with: "
            f"action_type={action_type}, courier_id={courier_id}, reason='{reason[:50]}...', "
            f"shift_id={shift_id}, warehouse_id={warehouse_id}"
        )
        if kwargs:
            logger.warning(f"[{self.name}._arun] Received unexpected kwargs: {kwargs}")

        return self._run(
            action_type=action_type,
            courier_id=courier_id,
            reason=reason,
            shift_id=shift_id,
            warehouse_id=warehouse_id
        )

take_action_tool = TakeActionTool()


async def _query_rag_service_wrapper(tool_input: Any) -> Dict[str, Any]:
    logger.debug(f"[_query_rag_service_wrapper] Received tool_input: {tool_input} (type: {type(tool_input)})")
    query_text_val: str
    top_k_val: int = 3
    if isinstance(tool_input, dict):
        query_text_val = tool_input.get("query_text")
        top_k_val = tool_input.get("top_k", 3)
        if not query_text_val:
            logger.error(f"[_query_rag_service_wrapper] 'query_text' not found in tool_input dictionary: {tool_input}")
            return {"success": False, "error": "Ошибка входных данных: отсутствует 'query_text'."}
    elif isinstance(tool_input, str):
        try:
            data = json.loads(tool_input)
            if isinstance(data, dict):
                query_text_val = data.get("query_text")
                top_k_val = data.get("top_k", 3)
                if not query_text_val:
                    logger.error(f"[_query_rag_service_wrapper] 'query_text' not found after parsing JSON string: {tool_input}")
                    return {"success": False, "error": "Ошибка входных данных: отсутствует 'query_text' в JSON."}
            else:
                query_text_val = tool_input
        except json.JSONDecodeError:
            query_text_val = tool_input
    else:
        logger.error(f"[_query_rag_service_wrapper] Unexpected type for tool_input: {type(tool_input)}. Value: {tool_input}")
        return {"success": False, "error": f"Неожиданный тип входных данных для RAG: {type(tool_input)}."}

    logger.info(f"[_query_rag_service_wrapper] Calling query_rag_service with query_text='{query_text_val}', top_k={top_k_val}")
    return await query_rag_service(query_text=query_text_val, top_k=top_k_val)


query_rag_tool = Tool.from_function(
    func=None,
    coroutine=_query_rag_service_wrapper,
    name="query_knowledge_base",
    description=(
        "Используется для поиска релевантной информации в базе знаний (должностные инструкции, методички) "
        "по текстовому описанию проблемы или вопросу. Возвращает фрагменты текста. "
        "Входные данные могут быть словарем с ключами 'query_text' (строка) и опционально 'top_k' (число), "
        "или просто строкой, которая будет использована как 'query_text'."
    ),
    args_schema=QueryRAGInput,
)

collector_tools = [
    search_courier_tool,
    get_warehouse_tool,
    get_courier_shifts_tool,
    query_rag_tool
]

decision_tools = [
    take_action_tool,
]