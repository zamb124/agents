# agents/router_agent.py
import logging
from typing import Dict, Any, List, Optional, Type

from langchain_core.messages import SystemMessage, HumanMessage
# AIMessage не используется, если история передается как список словарей

from .base_agent import BaseAgent
from scenarios.base_scenario import BaseScenario # Для тайпхинта в конструкторе

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT_TEMPLATE = """
Ты - ИИ-диспетчер службы поддержки директоров магазинов. Твоя задача - понять основной запрос пользователя и направить его в нужный раздел.
Проанализируй ПОСЛЕДНЕЕ СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ. Учитывай также предыдущую историю диалога, если она есть.

ПОСЛЕДНЕЕ СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ:
"{user_input_for_router}"

ИСТОРИЯ ДИАЛОГА (если есть):
{dialog_history_formatted}

Доступные разделы (сценарии) и их **ИДЕНТИФИКАТОРЫ**:
{scenarios_formatted_for_prompt}

ТВОИ ДЕЙСТВИЯ:
- Если ты уверен, какой сценарий подходит на основе сообщения пользователя, верни **ТОЛЬКО ИДЕНТИФИКАТОР** этого сценария (например, `complaint_scenario_orchestrator` или `faq_general`). Не добавляй никакого другого текста.
- Если пользователь просто поздоровался или его запрос слишком общий и неясный (например, "помоги", "есть проблема"), задай уточняющий вопрос, чтобы помочь ему определиться. Ты можешь упомянуть пару доступных сценариев.
- **Если пользователь спрашивает о твоих возможностях (например, "что ты умеешь?", "чем можешь помочь?"), ПЕРЕЧИСЛИ ВСЕ доступные сценарии с их кратким описанием и идентификаторами, как они даны тебе выше.**
- Твой ответ должен быть либо ИДЕНТИФИКАТОРОМ сценария, либо вопросом/перечислением возможностей.
"""

def format_router_dialog_history(history: List[Dict[str, str]]) -> str:
    if not history: return "Нет предыдущей истории диалога."
    return "\n".join([f"{msg['type'].capitalize()}: {msg['content']}" for msg in history])

class RouterAgent(BaseAgent):
    agent_id: str = "router_agent_main" # Убедимся, что ID есть

    def __init__(self, available_scenarios_map: Dict[str, Type[BaseScenario]],
                 llm_provider_config: Optional[Dict[str, Any]] = None): # Добавил llm_config
        super().__init__(llm_provider_config=llm_provider_config) # Передаем llm_config
        self.available_scenarios_map = available_scenarios_map
        self.scenarios_text_for_prompt = self._generate_scenarios_text()

    def _generate_scenarios_text(self) -> str:
        # ... (код без изменений, генерирует список сценариев для промпта)
        lines = []
        for scenario_id_key, ScenarioClass in self.available_scenarios_map.items():
            try:
                actual_id_for_prompt = ScenarioClass.id
                if ScenarioClass.id != scenario_id_key:
                    logger.warning(f"RouterAgent: Mismatch for scenario ID! Key: '{scenario_id_key}', ScenarioClass.id: '{ScenarioClass.id}'. Using ScenarioClass.id: '{actual_id_for_prompt}'")

                friendly_name = ScenarioClass.friendly_name
                description = ScenarioClass.description
                lines.append(f"- Сценарий '{friendly_name}' (ИДЕНТИФИКАТОР для ответа: `{actual_id_for_prompt}`): {description}")
            except AttributeError as e:
                logger.error(f"RouterAgent: Error getting metadata for scenario class {ScenarioClass.__name__} (ID from key: {scenario_id_key}): {e}.")
                lines.append(f"- Сценарий с ID `{scenario_id_key}` (описание не доступно)")
        return "\n".join(lines)


    def _get_default_tools(self) -> List[Any]: # List[BaseTool]
        return [] # RouterAgent не использует инструменты

    def get_initial_state(self, scenario_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # RouterAgent stateless в рамках своей сессии, ему не нужно хранить что-то между вызовами process_user_input.
        # Но для соответствия интерфейсу, возвращаем пустой словарь.
        return {"dialog_history_for_router": []} # Или просто {}

    async def process_user_input(
            self,
            user_input: str,
            current_agent_state: Dict[str, Any], # Будет содержать dialog_history_for_router
            scenario_context: Optional[Dict[str, Any]] = None # scenario_context здесь может содержать общую историю чата
    ) -> Dict[str, Any]:

        # RouterAgent использует общую историю чата, которую ему передает main_bot через scenario_context,
        # а не свою внутреннюю "сессионную" историю, так как он вызывается каждый раз заново для маршрутизации.
        # current_agent_state["dialog_history_for_router"] здесь не используется.
        # Вместо этого, main_bot должен передавать историю в scenario_context.

        # Предположим, main_bot передает историю в scenario_context["main_chat_history"]
        main_chat_history = scenario_context.get("main_chat_history", []) if scenario_context else []

        system_prompt = ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
            user_input_for_router=user_input,
            dialog_history_formatted=format_router_dialog_history(main_chat_history),
            scenarios_formatted_for_prompt=self.scenarios_text_for_prompt
        )

        messages = [SystemMessage(content=system_prompt)]
        # user_input уже включен в системный промпт как {user_input_for_router}
        # и история тоже {dialog_history_formatted}.
        # Поэтому для LLM достаточно одного системного сообщения.

        logger.info(f"[{self.agent_id}] Routing input: '{user_input[:100]}...'. History len: {len(main_chat_history)}")

        try:
            response = await self.llm.ainvoke(messages)
            llm_output_text = response.content.strip()
            logger.info(f"[{self.agent_id}] LLM output: '{llm_output_text}'")

            cleaned_llm_output = llm_output_text.replace("'", "").replace("`", "").replace('"', '')

            # Результат для RouterAgent - это либо ID сценария, либо вопрос
            # Это не совсем "финальный результат" в смысле данных, а скорее команда.
            # Статус всегда "completed", так как роутер выполняет свою задачу за один проход.

            next_agent_state = current_agent_state # Роутер не меняет свое состояние

            if cleaned_llm_output in self.available_scenarios_map:
                return {
                    "status": "completed",
                    "message_to_user": None, # Роутер сам не отвечает пользователю, если выбрал сценарий
                    "result": {"type": "scenario_id", "value": cleaned_llm_output},
                    "next_agent_state": next_agent_state
                }
            else: # LLM задала вопрос или не смогла определить сценарий
                return {
                    "status": "completed", # Задача роутера (попытка маршрутизации) выполнена
                    "message_to_user": llm_output_text, # Это вопрос/сообщение для пользователя
                    "result": {"type": "question", "value": llm_output_text}, # Результат - это вопрос
                    "next_agent_state": next_agent_state
                }
        except Exception as e:
            logger.error(f"[{self.agent_id}] Ошибка: {e}", exc_info=True)
            return {
                "status": "error",
                "message_to_user": "Произошла ошибка при маршрутизации вашего запроса.",
                "next_agent_state": current_agent_state,
                "result": {"type": "error", "value": "Routing error"}
            }