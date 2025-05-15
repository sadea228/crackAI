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

from config import BOT_TOKEN, OPENROUTER_API_KEY, VIP_CHANNEL_ID, WEBHOOK_URL, PORT
from fastapi import FastAPI, Request, Response
import uvicorn
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
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
    await bot.set_webhook(WEBHOOK_URL + "/webhook")
    
    # Запуск задачи мониторинга
    asyncio.create_task(health_check_task())
    
    yield
    # Код, выполняемый при остановке приложения
    logging.info("Удаляем вебхук...")
    await bot.delete_webhook()

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
        [KeyboardButton(text="Новая сессия"), KeyboardButton(text="Помощь")],
        [KeyboardButton(text="О боте"), KeyboardButton(text="Связаться с автором")],
    ],
    resize_keyboard=True
)

@app.post("/webhook")
async def webhook_handler(request: Request):
    global last_successful_update
    # Обработка входящего обновления от Telegram
    try:
        data = await request.json()
        update = Update.model_validate(data)
        # Устанавливаем таймаут на обработку обновления
        await asyncio.wait_for(dp.feed_update(bot, update), timeout=60.0)
        # Обновляем время последнего успешного обновления
        last_successful_update = time.time()
        return {"ok": True}
    except asyncio.TimeoutError:
        logging.error("Таймаут при обработке вебхука")
        return {"error": "timeout"}
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
                "Привет! Я бот для общения с ИИ. Отправь сообщение, чтобы начать.",
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

@dp.message((F.text & ~F.text.in_(['Новая сессия', 'О боте', 'О нас', 'Помощь', 'Связаться с автором'])) | F.photo)
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

    # Запрос к OpenRouter с повторными попытками
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            # Отправляем сообщение о начале обработки при первой попытке
            if attempt == 0:
                logging.info(f"Отправляем пользователю {user_id} сообщение о начале генерации")
                await message.answer("⏳ Генерирую ответ...", reply_markup=keyboard_main)
            
            logging.info(f"Запрос к OpenRouter (попытка {attempt+1}/{max_retries})")
            # Увеличиваем таймаут для стабильности
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "google/gemma-3-27b-it:free",
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 800
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                
                # Логируем ответ API для диагностики
                logging.info(f"Ответ API OpenRouter: {data}")
                
                # Проверяем наличие необходимых полей в ответе
                if "choices" not in data:
                    logging.error(f"Неверный формат ответа API: {data}")
                    if "error" in data:
                        raise Exception(f"API вернул ошибку: {data['error']}")
                    else:
                        raise Exception("Неожиданный формат ответа API")
                        
                if not data["choices"] or len(data["choices"]) == 0:
                    logging.error("API вернул пустой список choices")
                    raise Exception("API вернул пустой ответ")
                    
                if "message" not in data["choices"][0]:
                    logging.error(f"Отсутствует поле 'message' в choices: {data['choices'][0]}")
                    raise Exception("Неверный формат ответа API")
                    
                if "content" not in data["choices"][0]["message"]:
                    logging.error(f"Отсутствует поле 'content' в message: {data['choices'][0]['message']}")
                    raise Exception("Неверный формат ответа API")
                
                answer = data["choices"][0]["message"]["content"]
                logging.info(f"Получен ответ от OpenRouter для пользователя {user_id}")
                # Успешный ответ, выходим из цикла
                break
                
        except Exception as e:
            logging.error(f"Ошибка при запросе к OpenRouter (попытка {attempt+1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                # Последняя попытка не удалась
                logging.error(f"Все попытки запроса к API не удались для пользователя {user_id}")
                await message.answer(
                    "Произошла ошибка при обращении к ИИ. Попробуйте позже.",
                    reply_markup=keyboard_main
                )
                return
            # Ждем перед следующей попыткой
            await asyncio.sleep(retry_delay)
            # Увеличиваем задержку для следующей попытки
            retry_delay *= 2

    # Сохраняем и отправляем форматированный ответ
    session.append(answer)
    formatted_answer = f"💡 Ответ ИИ:\n{answer}"
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
                f"Ответ ИИ:\n{answer}",
                reply_markup=keyboard_main
            )
        except Exception as e2:
            logging.error(f"Повторная ошибка при отправке ответа: {str(e2)}")

# Обработчики специальных кнопок
@dp.message(F.text == "Новая сессия")
async def cmd_new_session(message: Message):
    user_id = message.from_user.id
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

@dp.message(F.text.in_(['О боте', 'О нас']))
async def cmd_about_bot(message: Message):
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил информацию о боте")
    try:
        await message.answer(
            "Я бот для общения с ИИ через OpenRouter. Отвечаю по-русски и кратко.",
            reply_markup=keyboard_main
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке информации о боте пользователю {user_id}: {str(e)}")

# Обработчики кнопок 'Помощь' и 'Связаться с автором'
@dp.message(F.text == "Помощь")
async def cmd_help(message: Message):
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил помощь")
    try:
        await message.answer(
            "Чтобы начать работу, просто отправьте любое сообщение или фото. Кнопка 'Новая сессия' сбрасывает диалог, 'О боте' — информация о боте, 'Связаться с автором' — контакт автора.",
            reply_markup=keyboard_main
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке справки пользователю {user_id}: {str(e)}")

@dp.message(F.text == "Связаться с автором")
async def cmd_contact(message: Message):
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил контакт автора")
    try:
        await message.answer(
            "Если у вас есть вопросы или предложения, напишите @sadea12.",
            reply_markup=keyboard_main
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке контакта автора пользователю {user_id}: {str(e)}")

if __name__ == "__main__":
    # Запуск FastAPI сервера для webhook
    uvicorn.run("main:app", host="0.0.0.0", port=PORT) 