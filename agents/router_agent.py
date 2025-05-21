import logging
from langchain_openai import ChatOpenAI
from config import OPENAI_API_KEY
from llm_services import get_llm
from scenarios.base_scenario import BaseScenario
from typing import Dict, Type

logger = logging.getLogger(__name__)
router_llm = get_llm(temperature=0.1)

ROUTER_SYSTEM_PROMPT_TEMPLATE = """
Ты - ИИ-диспетчер службы поддержки директоров магазинов. Твоя задача - понять основной запрос пользователя и направить его в нужный раздел.
Проанализируй сообщение пользователя: "{user_input}"
Учитывай также предыдущую историю диалога, если она есть.

Доступные разделы (сценарии) и их **ИДЕНТИФИКАТОРЫ**:
{scenarios_formatted_for_prompt}

Если ты уверен, какой сценарий подходит, верни **ТОЛЬКО ИДЕНТИФИКАТОР** этого сценария (например, `courier_complaint` или `faq_general`). Не добавляй никакого другого текста.
Если пользователь просто поздоровался или его запрос слишком общий и неясный, задай уточняющий вопрос, чтобы помочь ему определиться. Ты можешь упомянуть некоторые из доступных сценариев и их идентификаторы в своем вопросе.
Если пользователь спрашивает, что ты умеешь, перечисли доступные сценарии с их идентификаторами.
Твой ответ должен быть либо ИДЕНТИФИКАТОРОМ сценария, либо вопросом.
"""

def generate_scenarios_text_for_prompt(available_scenarios_map: Dict[str, Type[BaseScenario]]) -> str:
    """
    Генерирует текст со списком сценариев для промпта роутера.
    `available_scenarios_map`: Словарь {scenario_id: ScenarioClass}
    """
    lines = []
    for scenario_id_key, ScenarioClass in available_scenarios_map.items():
        try:
            if ScenarioClass.id != scenario_id_key:
                logger.warning(f"Несовпадение ID для сценария! Ключ: '{scenario_id_key}', ScenarioClass.id: '{ScenarioClass.id}'. Используется ключ.")

            actual_id_for_prompt = scenario_id_key

            friendly_name = ScenarioClass.friendly_name
            description = ScenarioClass.description
            lines.append(f"- Сценарий '{friendly_name}' (ИДЕНТИФИКАТОР для ответа: `{actual_id_for_prompt}`): {description}")
        except AttributeError as e:
            logger.error(f"Ошибка получения метаданных для класса сценария {ScenarioClass.__name__} (ID из ключа: {scenario_id_key}): {e}. Убедитесь, что id, friendly_name и description определены как classmethod properties.")
            lines.append(f"- Сценарий с ID `{scenario_id_key}` (описание не доступно)")
    return "\n".join(lines)


async def run_router_agent(user_input: str, chat_history: list, available_scenarios_map: Dict[str, Type[BaseScenario]]) -> dict:
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_core.messages import HumanMessage, AIMessage

    scenarios_text = generate_scenarios_text_for_prompt(available_scenarios_map)

    system_prompt_content = ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
        scenarios_formatted_for_prompt=scenarios_text,
        user_input="{user_input_placeholder}"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt_content),
        MessagesPlaceholder(variable_name="chat_history_placeholder", optional=True),
        ("human", "{user_input_placeholder}")
    ])

    langchain_chat_history = []
    for msg in chat_history:
        if msg.get("type") == "human": langchain_chat_history.append(HumanMessage(content=msg["content"]))
        elif msg.get("type") == "ai": langchain_chat_history.append(AIMessage(content=msg["content"]))

    chain = prompt | router_llm
    logger.info(f"RouterAgent: обрабатываю '{user_input}' со списком сценариев:\n{scenarios_text}")
    try:
        response = await chain.ainvoke({
            "user_input_placeholder": user_input,
            "chat_history_placeholder": langchain_chat_history
        })
        llm_output_text = response.content.strip()
        logger.info(f"RouterAgent LLM output: '{llm_output_text}'")

        if llm_output_text.replace("'", '').replace('`', '').replace('"','') in available_scenarios_map.keys():
            logger.info(f"RouterAgent: LLM вернул точный ID сценария '{llm_output_text}'")
            return {"type": "scenario_id", "value": llm_output_text}
        else:
            logger.info(f"RouterAgent: ответ LLM ('{llm_output_text}') не является чистым ID, классифицирован как вопрос.")
            return {"type": "question", "value": llm_output_text}

    except Exception as e:
        logger.error(f"Ошибка в run_router_agent: {e}", exc_info=True)
        return {"type": "question", "value": "Произошла ошибка при маршрутизации. Попробуйте позже."}