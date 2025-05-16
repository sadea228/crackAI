import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

BOT_TOKEN: str = os.getenv('BOT_TOKEN')
VIP_CHANNEL_ID: str = os.getenv('VIP_CHANNEL_ID')
PORT: int = int(os.getenv('PORT', '10000'))

# Ключ для Google Gemini API (с дефолтным значением)
GEMINI_API_KEY: str = os.getenv('GEMINI_API_KEY', 'AIzaSyA7iBgf46Fj0xfGgww2gPs6I1SmJla2UUE')
if not GEMINI_API_KEY:
    raise RuntimeError('Отсутствует обязательная переменная окружения: GEMINI_API_KEY')

if not all([BOT_TOKEN, VIP_CHANNEL_ID]):
    raise RuntimeError('Отсутствуют обязательные переменные окружения: BOT_TOKEN, VIP_CHANNEL_ID') 
