# llm_services.py
import logging
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
# from langchain_community.chat_models import ChatOllama # Если будете использовать Ollama

import config # Импортируем наш модуль конфигурации

logger = logging.getLogger(__name__)

_llm_cache = {}

def get_llm(
        provider: str = None,
        model_name: str = None,
        temperature: float = None,
        api_key: str = None,
        **kwargs
) -> BaseChatModel:
    provider = (provider or config.LLM_PROVIDER).lower()
    temperature = temperature if temperature is not None else config.LLM_TEMPERATURE

    if provider == "openai":
        model_name = model_name or config.OPENAI_MODEL_NAME
        resolved_api_key = api_key or config.OPENAI_API_KEY
        if not resolved_api_key:
            logger.error("OpenAI API ключ не найден.")
            raise ValueError("OpenAI API ключ не предоставлен.")
    elif provider == "google_gemini":
        model_name = model_name or config.GEMINI_MODEL_NAME
        resolved_api_key = api_key or config.GOOGLE_API_KEY
        if not resolved_api_key:
            logger.error("Google API ключ не найден.")
            raise ValueError("Google API ключ не предоставлен.")
    # elif provider == "ollama":
    #     model_name = model_name or config.OLLAMA_MODEL_NAME
    #     resolved_api_key = None # Ollama обычно не требует API ключа
    #     kwargs.setdefault("base_url", config.OLLAMA_BASE_URL)
    else:
        logger.error(f"Неподдерживаемый LLM провайдер: {provider}")
        raise ValueError(f"Неподдерживаемый LLM провайдер: {provider}")

    # Ключ для кэша должен учитывать все параметры, влияющие на инстанс
    kwargs_tuple = tuple(sorted(kwargs.items())) # Сортируем kwargs для консистентности ключа
    cache_key = (provider, model_name, temperature, resolved_api_key, kwargs_tuple)

    if cache_key in _llm_cache:
        logger.debug(f"Возвращаем LLM из кэша для ключа: {cache_key}")
        return _llm_cache[cache_key]

    logger.info(f"Создание нового инстанса LLM: провайдер='{provider}', модель='{model_name}', температура={temperature}, API ключ предоставлен: {bool(resolved_api_key)}, доп. параметры={kwargs}")

    llm_instance: BaseChatModel
    if provider == "openai":
        llm_instance = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            openai_api_key=resolved_api_key,
            max_retries=3,
            **kwargs
        )
    elif provider == "google_gemini":
        llm_instance = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=resolved_api_key,
            convert_system_message_to_human=True,
            **kwargs
        )
    # elif provider == "ollama":
    #     llm_instance = ChatOllama(
    #         model=model_name,
    #         temperature=temperature,
    #         **kwargs
    #     )
    else:
        raise ValueError(f"Не удалось создать LLM для провайдера: {provider}")

    _llm_cache[cache_key] = llm_instance
    return llm_instance