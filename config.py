import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") # Для Gemini

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RAG_API_URL = os.getenv("RAG_API_URL", "http://localhost:8001/query")

# --- Настройки LLM ---
# Тип LLM провайдера: "openai", "google_gemini", "ollama" (в будущем) и т.д.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

# Имя модели для OpenAI (если LLM_PROVIDER="openai")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")
# Имя модели для Google Gemini (если LLM_PROVIDER="google_gemini")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash-latest") # или gemini-pro

# Общие параметры LLM
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))

# Настройки для Ollama (если будете добавлять)
# OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "llama3")