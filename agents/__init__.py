# agents/__init__.py

from .base_agent import BaseAgent
from .router_agent import RouterAgent
from .faq_agent import FaqAgent # <<< Добавили FaqAgent
from .detail_collector_agent import DetailCollectorAgent
from .decision_maker_agent import DecisionMakerAgent

__all__ = [
    "BaseAgent",
    "RouterAgent",
    "FaqAgent",
    "DetailCollectorAgent",
    "DecisionMakerAgent",
]