import logging
import asyncio
import httpx
import io
import base64

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, Update

from config import BOT_TOKEN, OPENROUTER_API_KEY, VIP_CHANNEL_ID, WEBHOOK_URL, PORT
from fastapi import FastAPI, Request, Response
import uvicorn
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Код, выполняемый при запуске приложения
    logging.info("Устанавливаем вебхук...")
    await bot.set_webhook(WEBHOOK_URL + "/webhook")
    yield
    # Код, выполняемый при остановке приложения
    logging.info("Удаляем вебхук...")
    await bot.delete_webhook()

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
    # Обработка входящего обновления от Telegram
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@dp.message(Command("start"))
async def cmd_start(message: Message):
    # Проверка подписки пользователя в VIP-канале
    try:
        member = await bot.get_chat_member(chat_id=VIP_CHANNEL_ID, user_id=message.from_user.id)
        if member.status in ["creator", "administrator", "member"]:
            await message.answer(
                "Привет! Я бот для общения с ИИ. Отправь сообщение, чтобы начать.",
                reply_markup=keyboard_main
            )
        else:
            raise Exception
    except Exception:
        await message.answer(
            "Доступ к боту платный (150₽/мес).\n"
            "Получи ссылку у @sadea12 и вступи в VIP-канал для активации."
        )

@dp.message((F.text & ~F.text.in_(['Новая сессия', 'О боте', 'О нас', 'Помощь', 'Связаться с автором'])) | F.photo)
async def handle_user_message(message: Message):
    # Проверка подписки
    try:
        member = await bot.get_chat_member(chat_id=VIP_CHANNEL_ID, user_id=message.from_user.id)
        if member.status not in ["creator", "administrator", "member"]:
            raise Exception
    except Exception:
        await message.answer(
            "Доступ к боту платный (150₽/мес).\n"
            "Получи ссылку у @sadea12 и вступи в VIP-канал для активации."
        )
        return

    # Формируем входные данные
    if message.photo:
        photo = message.photo[-1]
        buffer: io.BytesIO = await bot.download(photo)
        buffer.seek(0)
        img_b64 = base64.b64encode(buffer.read()).decode()
        user_input = (message.caption + "\n" if message.caption else "") + f"data:image/jpeg;base64,{img_b64}"
    else:
        user_input = message.text

    # Обновляем контекст
    session = user_sessions.setdefault(message.from_user.id, [])
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
                await message.answer("⏳ Генерирую ответ...", reply_markup=keyboard_main)
            
            # Увеличиваем таймаут для стабильности
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}", 
                        "Content-Type": "application/json",
                        "HTTP-Referer": WEBHOOK_URL # Рефереры для OpenRouter
                    },
                    json={
                        "model": "qwen/qwen3-235b-a22b:free", 
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 800
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                answer = data["choices"][0]["message"]["content"]
                # Успешный ответ, выходим из цикла
                break
                
        except Exception as e:
            logging.error(f"Ошибка при запросе к OpenRouter (попытка {attempt+1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                # Последняя попытка не удалась
                await message.answer("Произошла ошибка при обращении к ИИ. Попробуйте позже.")
                return
            # Ждем перед следующей попыткой
            await asyncio.sleep(retry_delay)
            # Увеличиваем задержку для следующей попытки
            retry_delay *= 2

    # Сохраняем и отправляем форматированный ответ
    session.append(answer)
    formatted_answer = f"💡 <b>Ответ ИИ:</b>\n{answer}"
    await message.answer(
        formatted_answer,
        parse_mode='HTML',
        reply_markup=keyboard_main
    )

# Обработчики специальных кнопок
@dp.message(F.text == "Новая сессия")
async def cmd_new_session(message: Message):
    # Сброс контекста пользователя
    user_sessions[message.from_user.id] = []
    await message.answer(
        "Новая сессия начата. Отправьте сообщение, чтобы начать диалог.",
        reply_markup=keyboard_main
    )

@dp.message(F.text.in_(['О боте', 'О нас']))
async def cmd_about_bot(message: Message):
    await message.answer(
        "Я бот для общения с ИИ через OpenRouter. Отвечаю по-русски и кратко.",
        reply_markup=keyboard_main
    )

# Обработчики кнопок 'Помощь' и 'Связаться с автором'
@dp.message(F.text == "Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "Чтобы начать работу, просто отправьте любое сообщение или фото. Кнопка 'Новая сессия' сбрасывает диалог, 'О боте' — информация о боте, 'Связаться с автором' — контакт автора.",
        reply_markup=keyboard_main
    )

@dp.message(F.text == "Связаться с автором")
async def cmd_contact(message: Message):
    await message.answer(
        "Если у вас есть вопросы или предложения, напишите @sadea12.",
        reply_markup=keyboard_main
    )

if __name__ == "__main__":
    # Запуск FastAPI сервера для webhook
    uvicorn.run("main:app", host="0.0.0.0", port=PORT) 