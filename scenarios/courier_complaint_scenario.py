# scenarios/courier_complaint_scenario.py
import logging
import json # Для first_input_config для DecisionMakerAgent
from typing import Dict, List, Any, Type # Optional убран, если не используется

from .base_scenario import BaseScenario # Используем финальный BaseScenario
from agents.base_agent import BaseAgent
# Импортируем классы агентов
from agents.identification_agents import WarehouseIdentificationAgent, CourierIdentificationAgent
from agents.detail_collector_agent import DetailCollectorAgent
from agents.decision_maker_agent import DecisionMakerAgent
# Инструменты для конфигурации агентов (передаются в BaseScenario, который передает их агентам)
from tools.tool_definitions import (
    find_warehouse_tool, search_courier_tool,
    take_action_tool, query_rag_tool, get_courier_shifts_tool
)
# Карта полей от DC может быть не нужна здесь, если DM сам разбирает результат DC
# from agents.prompts.detail_collector_prompts import AGENT_RESULT_FIELDS as DC_AGENT_RESULT_FIELDS

logger = logging.getLogger(__name__)

# Ключи для shared_scenario_data, которые сценарий использует для передачи результатов
# и для формирования initial_context_keys и first_input_config
INITIAL_COMPLAINT_SHARED_KEY = "initial_complaint_text" # Устанавливается в BaseScenario.handle_message
WAREHOUSE_AGENT_RESULT_KEY = f"result_{WarehouseIdentificationAgent.get_id()}"
COURIER_AGENT_RESULT_KEY = f"result_{CourierIdentificationAgent.get_id()}"
DC_AGENT_RESULT_KEY = f"result_{DetailCollectorAgent.get_id()}"
# Результат DecisionMakerAgent обычно не передается дальше в этой цепочке, он финальный для пользователя

class CourierComplaintScenario(BaseScenario):
    id: str = "complaint_orchestrator_final_v2" # Обновим ID для полной ясности
    friendly_name: str = "Жалоба на курьера (Оркестратор)"
    description: str = "Обработка жалоб через последовательность автономных агентов с минимальной логикой в сценарии."

    # --- Последовательность КЛЮЧЕЙ агентов, которые должны быть выполнены ---
    AGENT_SEQUENCE: List[str] = [
        WarehouseIdentificationAgent.get_id(),
        CourierIdentificationAgent.get_id(),
        DetailCollectorAgent.get_id(),
        DecisionMakerAgent.get_id()
    ]

    def _get_agents_config(self) -> Dict[str, Dict[str, Any]]:
        """
        Определяет конфигурацию для каждого агента в AGENT_SEQUENCE.
        - class: Класс агента.
        - llm_config: Опциональная конфигурация LLM (провайдер, модель, температура).
        - tools: Опциональный список экземпляров инструментов для агента.
        - initial_context_keys: Опциональный словарь {ключ_из_shared_data: имя_ключа_в_scenario_context_агента}
                                 для инициализации состояния агента (метод get_initial_state)
                                 и для передачи контекста в process_user_input.
        - first_input_config: Опциональная конфигурация для первого ввода агента (см. BaseScenario).
        """
        return {
            WarehouseIdentificationAgent.get_id(): {
                "class": WarehouseIdentificationAgent,
                "llm_config": {"temperature": 0.1},
                "tools": [find_warehouse_tool],
                "initial_context_keys": {INITIAL_COMPLAINT_SHARED_KEY: "initial_complaint"},
                "first_input_config": INITIAL_COMPLAINT_SHARED_KEY
            },
            CourierIdentificationAgent.get_id(): {
                "class": CourierIdentificationAgent,
                "llm_config": {"temperature": 0.1},
                "tools": [search_courier_tool],
                "initial_context_keys": {WAREHOUSE_AGENT_RESULT_KEY: "warehouse_info"},
                "first_input_config": "EMPTY_STRING" # Начинает диалог сам
            },
            DetailCollectorAgent.get_id(): {
                "class": DetailCollectorAgent,
                "llm_config": {"temperature": 0.1},
                "initial_context_keys": {
                    INITIAL_COMPLAINT_SHARED_KEY: "initial_complaint",
                    WAREHOUSE_AGENT_RESULT_KEY: "warehouse_info",
                    COURIER_AGENT_RESULT_KEY: "courier_info"
                },
                # Первый ввод для DC - это initial_complaint.
                # Он уже будет в scenario_context через initial_context_keys.
                # Агент сам должен начать диалог на основе этого контекста.
                # Поэтому first_input_config может быть "EMPTY_STRING" или initial_complaint,
                # в зависимости от того, как написан process_user_input агента для первого вызова.
                # Если агент ожидает initial_complaint в user_input на первом шаге:
                "first_input_config": INITIAL_COMPLAINT_SHARED_KEY
            },
            DecisionMakerAgent.get_id(): {
                "class": DecisionMakerAgent,
                "llm_config": {"temperature": 0.2},
                "tools": [take_action_tool, query_rag_tool, get_courier_shifts_tool], # DM сам обогащает
                "initial_context_keys": {
                    # Передаем результат DC как 'details_from_collector' в scenario_context
                    # для метода DM.get_initial_state() и process_user_input()
                    DC_AGENT_RESULT_KEY: "details_from_collector",
                    # Также передаем warehouse_info и courier_info, если DM их использует для обогащения
                    # (хотя он может их найти и в details_from_collector, если DC их туда положил)
                    WAREHOUSE_AGENT_RESULT_KEY: "warehouse_info",
                    COURIER_AGENT_RESULT_KEY: "courier_info"
                },
                # Первый ввод для DM - это JSON с результатом DC, обернутый
                "first_input_config": {
                    "source": "previous_agent_result_json_wrapped",
                    "wrapper_key": "incident_data" # Ключ, под которым будет результат DC в JSON
                }
            }
        }

    # В этой "ультра-простой" модели сценария, мы НЕ переопределяем _start_next_agent.
    # Вся логика по запуску агентов, формированию их первого ввода (на основе first_input_config)
    # и передаче контекста (на основе initial_context_keys) находится в BaseScenario.
    # Если для какого-то сценария потребуется очень специфичная логика подготовки данных
    # МЕЖДУ агентами, которая не укладывается в first_input_config или initial_context_keys,
    # ТОЛЬКО ТОГДА можно переопределить _start_next_agent, вызвать super() и добавить свою логику.
    # Но мы стремимся этого избежать. Обогащение данных для DM теперь внутри самого DM.

    # handle_message, is_finished, clear_scenario_data - всё наследуется от BaseScenario.
    # Никакой другой логики здесь не нужно!