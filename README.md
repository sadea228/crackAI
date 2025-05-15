# Telegram AI Bot

Бот для приватного общения с ИИ через OpenRouter.

## Настройка

1. Переименуйте файл `.env.example` в `.env` и заполните переменные:

```dotenv
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
OPENROUTER_API_KEY=YOUR_OPENROUTER_API_KEY
VIP_CHANNEL_ID=@your_vip_channel_username_or_id
```

2. Установите зависимости:

```bash
pip install -r requirements.txt
```

## Локальный запуск

```bash
python main.py
```

## Деплой на Render

1. Создайте новый сервис **Worker** на Render и подключите репозиторий.
2. В разделе **Environment** добавьте переменные:
   - `BOT_TOKEN`
   - `OPENROUTER_API_KEY`
   - `VIP_CHANNEL_ID`
3. Укажите команды:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
4. Запустите деплой и дождитесь статуса **Healthy**. 