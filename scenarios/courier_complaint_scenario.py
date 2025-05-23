# scenarios/courier_complaint_scenario.py
import logging
import json
from typing import Dict, List, Any, Type, Optional

from .base_scenario import BaseScenario
from agents.base_agent import BaseAgent
from agents.identification_agents import WarehouseIdentificationAgent, CourierIdentificationAgent
from agents.detail_collector_agent import DetailCollectorAgent
from agents.decision_maker_agent import DecisionMakerAgent
from tools.tool_definitions import ( # Только для конфигурации агентов
    find_warehouse_tool, search_courier_tool,
    take_action_tool, query_rag_tool, get_courier_shifts_tool
)

logger = logging.getLogger(__name__)

# Ключи для shared_scenario_data, используемые для передачи результатов между агентами
INITIAL_COMPLAINT_SHARED_KEY = "initial_complaint_text"
WAREHOUSE_INFO_SHARED_KEY = f"result_{WarehouseIdentificationAgent.get_id()}"
COURIER_INFO_SHARED_KEY = f"result_{CourierIdentificationAgent.get_id()}"
DC_RESULT_SHARED_KEY = f"result_{DetailCollectorAgent.get_id()}"


class CourierComplaintScenario(BaseScenario):
    id: str = "complaint_orchestrator_final"
    friendly_name: str = "Жалоба на курьера (Оркестратор)"
    description: str = "Обработка жалоб через последовательность автономных агентов."

    AGENT_SEQUENCE: List[str] = [
        WarehouseIdentificationAgent.get_id(),
        CourierIdentificationAgent.get_id(),
        DetailCollectorAgent.get_id(),
        DecisionMakerAgent.get_id()
    ]

    def _get_agents_config(self) -> Dict[str, Dict[str, Any]]:
        return {
            WarehouseIdentificationAgent.get_id(): {
                "class": WarehouseIdentificationAgent,
                "llm_config": {"temperature": 0.1},
                "tools": [find_warehouse_tool],
                # initial_context_keys - это ключи из shared_data, которые пойдут в scenario_context агента
                "initial_context_keys": {INITIAL_COMPLAINT_SHARED_KEY: "initial_complaint"}
            },
            CourierIdentificationAgent.get_id(): {
                "class": CourierIdentificationAgent,
                "llm_config": {"temperature": 0.1},
                "tools": [search_courier_tool],
                "initial_context_keys": {WAREHOUSE_INFO_SHARED_KEY: "warehouse_info"}
            },
            DetailCollectorAgent.get_id(): {
                "class": DetailCollectorAgent,
                "llm_config": {"temperature": 0.1}, # Очень низкая для точности
                "initial_context_keys": {
                    INITIAL_COMPLAINT_SHARED_KEY: "initial_complaint",
                    WAREHOUSE_INFO_SHARED_KEY: "warehouse_info",
                    COURIER_INFO_SHARED_KEY: "courier_info"
                }
            },
            DecisionMakerAgent.get_id(): {
                "class": DecisionMakerAgent,
                "llm_config": {"temperature": 0.2},
                "tools": [take_action_tool, query_rag_tool, get_courier_shifts_tool], # DM сам обогащает
                "initial_context_keys": {
                    # Передаем результат DC как 'details_from_collector' в scenario_context для get_initial_state DM
                    DC_RESULT_SHARED_KEY: "details_from_collector",
                }
            }
        }

    # Переопределяем _start_next_agent, чтобы правильно формировать first_input_for_agent
    async def _start_next_agent(self, chat_id: int, previous_agent_result: Optional[Any] = None, first_input_for_agent: Optional[str] = None):
        current_agent_idx = await self._get_scenario_fsm_data(self.fsm_key_current_agent_idx, default=-1)
        next_agent_to_start_idx = current_agent_idx + 1

        effective_first_input = first_input_for_agent # Используем то, что передал handle_message

        if next_agent_to_start_idx < len(self.AGENT_SEQUENCE):
            next_agent_key = self.AGENT_SEQUENCE[next_agent_to_start_idx]

            if next_agent_key == WarehouseIdentificationAgent.get_id():
                # Для первого агента first_input уже должен быть initial_complaint из handle_message
                pass
            elif next_agent_key == CourierIdentificationAgent.get_id():
                # CourierIdentificationAgent начинает диалог сам, ему не нужен user_input от предыдущего
                effective_first_input = "" # Пустой ввод, чтобы он задал свой первый вопрос
            elif next_agent_key == DetailCollectorAgent.get_id():
                # DetailCollectorAgent ожидает initial_complaint (который уже в его scenario_context)
                # и первый user_input для него - это тоже initial_complaint.
                # BaseScenario.handle_message передаст initial_complaint как first_input для первого агента.
                # Для DC, first_input - это initial_complaint, который он получит через scenario_context.
                # А user_input для process_message - это тоже initial_complaint (первый "ответ" пользователя).
                shared_data = await self._get_scenario_fsm_data(self.fsm_key_shared_data, default={})
                effective_first_input = shared_data.get(INITIAL_COMPLAINT_SHARED_KEY, "Проблема с курьером.")
            elif next_agent_key == DecisionMakerAgent.get_id():
                # DM ожидает JSON с результатом DC. Этот результат (previous_agent_result)
                # уже сохранен в shared_data под ключом DC_RESULT_SHARED_KEY.
                # DM.get_initial_state использует scenario_context для получения этого.
                # А первый user_input для DM.process_user_input - это этот же JSON.
                if isinstance(previous_agent_result, dict):
                    effective_first_input = json.dumps({"incident_data": previous_agent_result}, ensure_ascii=False)
                else:
                    logger.error(f"[{self.id}] Ожидался dict от DC, получен {type(previous_agent_result)}.")
                    await self.bot.send_message(chat_id, "Ошибка передачи данных для принятия решения.")
                    await self._mark_as_finished_with_error(chat_id, "Ошибка данных от DC")
                    return

        # Вызываем родительский метод с подготовленным effective_first_input
        await super()._start_next_agent(chat_id, previous_agent_result=previous_agent_result, first_input_for_agent=effective_first_input)

    # handle_message, is_finished, clear_scenario_data - всё наследуется от BaseScenario
    # Никакой другой сложной логики FSM здесь не нужно!