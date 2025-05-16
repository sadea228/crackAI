import logging
import asyncio
import httpx
import io
import base64
import traceback
import time
import signal
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, Update, ErrorEvent

from config import BOT_TOKEN, VIP_CHANNEL_ID, WEBHOOK_URL, PORT, GEMINI_API_KEY
from fastapi import FastAPI, Request, Response
import uvicorn
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# Добавляем глобальный обработчик исключений
@dp.errors()
async def error_handler(event: ErrorEvent):
    logging.error(f"Произошла ошибка в обработчике: {event.exception}")
    logging.error(f"Трейсбек: {traceback.format_exc()}")
    # Пытаемся отправить сообщение пользователю
    update = event.update
    if update and update.message:
        try:
            await bot.send_message(
                chat_id=update.message.chat.id,
                text="Произошла внутренняя ошибка бота. Попробуйте позже или используйте команду /start."
            )
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение об ошибке: {e}")

# Переменные для мониторинга состояния
last_successful_update = time.time()
health_check_interval = 300  # 5 минут
max_inactive_time = 600  # 10 минут без активности считаем проблемой

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Код, выполняемый при запуске приложения
    logging.info("Устанавливаем вебхук...")
    await bot.set_webhook(WEBHOOK_URL + "/webhook", drop_pending_updates=True)
    logging.info("Webhook установлен с drop_pending_updates=True, старые обновления сброшены")
    # Добавляю логирование информации о вебхуке
    webhook_info = await bot.get_webhook_info()
    logging.info(f"Webhook info: {webhook_info}")
    
    # Запуск задачи мониторинга
    asyncio.create_task(health_check_task())
    
    yield
    # Код, выполняемый при остановке приложения
    logging.info("Удаляем вебхук...")
    await bot.delete_webhook()
    # Закрываем aiohttp-сеанс бота, чтобы избежать предупреждений об unclosed client session
    await bot.session.close()

# Функция для мониторинга состояния бота
async def health_check_task():
    global last_successful_update
    while True:
        try:
            await asyncio.sleep(health_check_interval)
            current_time = time.time()
            inactive_time = current_time - last_successful_update
            
            logging.info(f"Проверка состояния: последнее обновление {inactive_time:.1f} секунд назад")
            
            if inactive_time > max_inactive_time:
                logging.warning(f"Бот не отвечает в течение {inactive_time:.1f} секунд. Выполняем перезапуск...")
                # Отправляем сигнал SIGTERM для перезапуска (Render перезапустит сервис)
                os.kill(os.getpid(), signal.SIGTERM)
        except Exception as e:
            logging.error(f"Ошибка в проверке состояния: {str(e)}")

# Создаём приложение FastAPI с health check endpoint
app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "ok"}

@app.head("/")
async def head_root():
    return Response(status_code=200)

# Контекст сессий
# Хранение истории сообщений: dict[user_id, list[str]]
user_sessions: dict[int, list[str]] = {}

# Клавиатура
keyboard_main = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🆕 Новая сессия"), KeyboardButton(text="🆘 Помощь")],
        [KeyboardButton(text="🤖 О боте"), KeyboardButton(text="📞 Связаться с автором")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

@app.post("/webhook")
async def webhook_handler(request: Request):
    global last_successful_update
    # Обработка входящего обновления от Telegram
    try:
        data = await request.json()
        logging.info(f"Получено обновление от Telegram: {data}")
        update = Update.model_validate(data, context={"bot": bot})
        # Обрабатываем входящее обновление без таймаута
        await dp.feed_update(update)
        # Обновляем время последнего успешного обновления
        last_successful_update = time.time()
        return {"ok": True}
    except Exception as e:
        logging.error(f"Ошибка в webhook_handler: {str(e)}")
        return {"error": str(e)}

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запустил команду /start")
    # Проверка подписки пользователя в VIP-канале
    try:
        member = await bot.get_chat_member(chat_id=VIP_CHANNEL_ID, user_id=user_id)
        if member.status in ["creator", "administrator", "member"]:
            logging.info(f"Пользователь {user_id} имеет доступ")
            await message.answer(
                "<b>Привет!</b> Я бот для общения с ИИ через <i>Google Gemini API</i>. Отвечаю по-русски и кратко.\n\n"
                "Чтобы начать, отправьте любое сообщение.",
                reply_markup=keyboard_main
            )
        else:
            raise Exception("Пользователь не подписан на VIP-канал")
    except Exception as e:
        logging.info(f"Пользователь {user_id} не имеет доступа: {str(e)}")
        await message.answer(
            "Доступ к боту платный (150₽/мес).\n"
            "Получи ссылку у @sadea12 и вступи в VIP-канал для активации."
        )

@dp.message((F.text & ~F.text.in_(['🆕 Новая сессия', '🆘 Помощь', '🤖 О боте', '📞 Связаться с автором'])) | F.photo)
async def handle_user_message(message: Message):
    user_id = message.from_user.id
    logging.info(f"Получено сообщение от пользователя {user_id}")
    
    # Проверка подписки
    try:
        member = await bot.get_chat_member(chat_id=VIP_CHANNEL_ID, user_id=user_id)
        if member.status not in ["creator", "administrator", "member"]:
            raise Exception("Пользователь не подписан на VIP-канал")
    except Exception as e:
        logging.info(f"Отказано в доступе пользователю {user_id}: {str(e)}")
        await message.answer(
            "Доступ к боту платный (150₽/мес).\n"
            "Получи ссылку у @sadea12 и вступи в VIP-канал для активации."
        )
        return

    # Формируем входные данные
    if message.photo:
        logging.info(f"Пользователь {user_id} отправил фото")
        photo = message.photo[-1]
        buffer: io.BytesIO = await bot.download(photo)
        buffer.seek(0)
        img_b64 = base64.b64encode(buffer.read()).decode()
        user_input = (message.caption + "\n" if message.caption else "") + f"data:image/jpeg;base64,{img_b64}"
    else:
        logging.info(f"Пользователь {user_id} отправил текст: {message.text[:30]}...")
        user_input = message.text

    # Обновляем контекст
    session = user_sessions.setdefault(user_id, [])
    session.append(user_input)
    context_str = "\n".join(session)
    while len(context_str) > 1024:
        session.pop(0)
        context_str = "\n".join(session)

    # Подготавливаем сообщения для OpenRouter
    messages = [
        {"role": "system", "content": "Ты ИИ-ассистент, отвечай по-русски и кратко."},
        {"role": "user", "content": context_str}
    ]

    # Запрос к Google Gemini API
    try:
        logging.info(f"Запрос к Google Gemini API для пользователя {user_id}")
        payload = {"contents": [{"parts": [{"text": context_str}]}]}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        logging.info(f"Ответ Google Gemini API: {data}")
        try:
            if "candidates" not in data or len(data["candidates"]) == 0:
                logging.error(f"Неверный формат ответа API Gemini: {data}")
                await message.answer("Ошибка: неверный формат ответа от Gemini API.", reply_markup=keyboard_main)
                return
            
            candidate = data["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"] and len(candidate["content"]["parts"]) > 0:
                answer = candidate["content"]["parts"][0]["text"]
            else:
                logging.error(f"Неверный формат поля content в API Gemini: {candidate}")
                await message.answer("Не удалось обработать ответ от Gemini API.", reply_markup=keyboard_main)
                return
        except Exception as e:
            logging.error(f"Ошибка при обработке ответа Gemini API: {e}")
            await message.answer("Ошибка обработки ответа от Gemini API.", reply_markup=keyboard_main)
            return
        logging.info(f"Получен ответ от Gemini API для пользователя {user_id}")
    except Exception as e:
        logging.error(f"Ошибка при запросе к Gemini API: {e}")
        await message.answer("Произошла ошибка при обращении к Gemini API.", reply_markup=keyboard_main)
        return

    # Сохраняем и отправляем форматированный ответ
    session.append(answer)
    formatted_answer = f"💡 <b>Ответ ИИ:</b>\n{answer}"
    logging.info(f"Отправляем ответ пользователю {user_id}")
    try:
        await message.answer(
            formatted_answer,
            reply_markup=keyboard_main
        )
        logging.info(f"Ответ успешно отправлен пользователю {user_id}")
    except Exception as e:
        logging.error(f"Ошибка при отправке ответа пользователю {user_id}: {str(e)}")
        # Пробуем отправить без форматирования, если возникла ошибка
        try:
            await message.answer(
                f"💡 <b>Ответ ИИ:</b>\n{answer}",
                reply_markup=keyboard_main
            )
        except Exception as e2:
            logging.error(f"Повторная ошибка при отправке ответа: {str(e2)}")

# Обработчики специальных кнопок
@dp.message(F.text == "🆕 Новая сессия")
async def cmd_new_session(message: Message):
    user_id = message.from_user.id
    logging.info(f"cmd_new_session triggered by user {user_id}")
    logging.info(f"Пользователь {user_id} запросил новую сессию")
    # Сброс контекста пользователя
    user_sessions[user_id] = []
    try:
        await message.answer(
            "Новая сессия начата. Отправьте сообщение, чтобы начать диалог.",
            reply_markup=keyboard_main
        )
        logging.info(f"Сессия успешно сброшена для пользователя {user_id}")
    except Exception as e:
        logging.error(f"Ошибка при сбросе сессии для пользователя {user_id}: {str(e)}")

@dp.message(F.text == "🤖 О боте")
async def cmd_about_bot(message: Message):
    user_id = message.from_user.id
    logging.info(f"cmd_about_bot triggered by user {user_id}")
    logging.info(f"Пользователь {user_id} запросил информацию о боте")
    try:
        await message.answer(
            "<b>🤖 О боте</b>\n"
            "Этот бот позволяет общаться с ИИ на базе <i>Google Gemini API</i>. Отвечаю по-русски и кратко.",
            reply_markup=keyboard_main
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке информации о боте пользователю {user_id}: {str(e)}")

# Обработчики кнопок 'Помощь' и 'Связаться с автором'
@dp.message(F.text == "🆘 Помощь")
async def cmd_help(message: Message):
    user_id = message.from_user.id
    logging.info(f"cmd_help triggered by user {user_id}")
    logging.info(f"Пользователь {user_id} запросил помощь")
    try:
        await message.answer(
            "<b>🆘 Помощь</b>\n"
            "1. Отправьте сообщение или фото, чтобы получить ответ ИИ.\n"
            "2. 🆕 Новая сессия — сброс контекста диалога.\n"
            "3. 🤖 О боте — информация о возможностях.\n"
            "4. 📞 Связаться с автором — контакт разработчика.",
            reply_markup=keyboard_main
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке справки пользователю {user_id}: {str(e)}")

@dp.message(F.text == "📞 Связаться с автором")
async def cmd_contact(message: Message):
    user_id = message.from_user.id
    logging.info(f"cmd_contact triggered by user {user_id}")
    logging.info(f"Пользователь {user_id} запросил контакт автора")
    try:
        await message.answer(
            "<b>📞 Связаться с автором</b>\n"
            "Напишите автору: <a href=\"https://t.me/sadea12\">@sadea12</a>",
            reply_markup=keyboard_main
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке контакта автора пользователю {user_id}: {str(e)}")

if __name__ == "__main__":
    # Запуск FastAPI сервера для webhook
    uvicorn.run("main:app", host="0.0.0.0", port=PORT) 