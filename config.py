import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

BOT_TOKEN: str = os.getenv('BOT_TOKEN')
OPENROUTER_API_KEY: str = os.getenv('OPENROUTER_API_KEY')
VIP_CHANNEL_ID: str = os.getenv('VIP_CHANNEL_ID')

if not all([BOT_TOKEN, OPENROUTER_API_KEY, VIP_CHANNEL_ID]):
    raise RuntimeError('Отсутствуют обязательные переменные окружения в .env') 