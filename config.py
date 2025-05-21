import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файлика
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RAG_API_URL = os.getenv("RAG_API_URL", "http://localhost:8001/query")