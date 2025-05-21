# llm_services.py
import logging
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
# from langchain_community.chat_models import ChatOllama # Если будете использовать Ollama

import config # Импортируем наш модуль конфигурации

logger = logging.getLogger(__name__)

# Словарь для кэширования инстансов LLM, чтобы не создавать их каждый раз
# Ключ - кортеж параметров (provider, model_name, temperature), значение - инстанс LLM
_llm_cache = {}

def get_llm(
        provider: str = None,
        model_name: str = None,
        temperature: float = None,
        api_key: str = None, # Позволяет переопределить ключ из конфига
        **kwargs # Дополнительные параметры для конструктора модели
) -> BaseChatModel:
    """
    Фабричная функция для получениа инстанса LLM на основе конфигурации или переданных параметров.
    Кэширует созданные инстансы.
    """
    provider = (provider or config.LLM_PROVIDER).lower()
    temperature = temperature if temperature is not None else config.LLM_TEMPERATURE

    # Определяем имя модели и API ключ на основе провайдера
    if provider == "openai":
        model_name = model_name or config.OPENAI_MODEL_NAME
        resolved_api_key = api_key or config.OPENAI_API_KEY
        if not resolved_api_key:
            logger.error("OpenAI API ключ не найден. Укажите OPENAI_API_KEY в .env или передайте в функцию.")
            raise ValueError("OpenAI API ключ не предоставлен.")
    elif provider == "google_gemini":
        model_name = model_name or config.GEMINI_MODEL_NAME
        resolved_api_key = api_key or config.GOOGLE_API_KEY
        if not resolved_api_key:
            logger.error("Google API ключ не найден. Укажите GOOGLE_API_KEY в .env или передайте в функцию.")
            raise ValueError("Google API ключ не предоставлен.")
    # elif provider == "ollama":
    #     model_name = model_name or config.OLLAMA_MODEL_NAME
    #     # Ollama обычно не требует API ключа, но может требовать base_url
    #     kwargs.setdefault("base_url", config.OLLAMA_BASE_URL)
    else:
        logger.error(f"Неподдерживаемый LLM провайдер: {provider}")
        raise ValueError(f"Неподдерживаемый LLM провайдер: {provider}")

    cache_key = (provider, model_name, temperature, tuple(sorted(kwargs.items()))) # Ключ для кэша

    if cache_key in _llm_cache:
        logger.debug(f"Возвращаем LLM из кэша для ключа: {cache_key}")
        return _llm_cache[cache_key]

    logger.info(f"Создание нового инстанса LLM: провайдер='{provider}', модель='{model_name}', температура={temperature}, доп. параметры={kwargs}")

    llm_instance: BaseChatModel
    if provider == "openai":
        llm_instance = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            openai_api_key=resolved_api_key,
            **kwargs
        )
    elif provider == "google_gemini":
        # Убедитесь, что GOOGLE_API_KEY установлен как переменная окружения
        # или ChatGoogleGenerativeAI сможет его найти
        llm_instance = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=resolved_api_key, # Явно передаем ключ
            convert_system_message_to_human=True, # Gemini может требовать это для системных сообщений
            **kwargs
        )
    # elif provider == "ollama":
    #     llm_instance = ChatOllama(
    #         model=model_name,
    #         temperature=temperature,
    #         **kwargs
    #     )
    else:
        # Эта ветка не должна достигаться из-за проверки выше, но на всякий случай
        raise ValueError(f"Не удалось создать LLM для провайдера: {provider}")

    _llm_cache[cache_key] = llm_instance
    return llm_instance

# Пример получения LLM по умолчанию из конфига
default_llm = get_llm()

# Примеры получения специфичных LLM (если нужно)
# openai_llm_gpt4 = get_llm(provider="openai", model_name="gpt-4", temperature=0.1)
# gemini_pro_llm = get_llm(provider="google_gemini", model_name="gemini-pro")