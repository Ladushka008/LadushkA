import asyncio
import logging
import random
import zoneinfo
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

dp = Dispatcher()

# Постоянная клавиатура (Reply Keyboard)
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🕖 Когда Минута ладушек?")],
        [KeyboardButton(text="ℹ️ Что такое Минута ладушек?")]
    ],
    resize_keyboard=True
)

# Переменные для живого общения
last_bot_message_time = datetime.min
last_replied_user_id = None


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    text = (
        "👋 Добро пожаловать в бот «Минута ладушек»!\n\n"
        "Здесь каждый день в 19:00 по киевскому времени проходит Минута ладушек.\n"
        "🕖 В назначенное время бот автоматически отправляет сообщение:\n"
        "🕖 19:00 — Время петь «Ладушки»!\n"
        "🎶 Ладушки, ладушки, где были? У бабушки!"
    )
    await message.answer(text, reply_markup=main_keyboard)


@dp.message(F.text == "🕖 Когда Минута ладушек?")
async def when_ladushka_handler(message: Message):
    await message.answer("👏 Минута ладушек проходит каждый день в 19:00 по киевскому времени.")


@dp.message(F.text == "ℹ️ Что такое Минута ладушек?")
async def about_ladushka_handler(message: Message):
    await message.answer("👏 Минута ладушек — это ежедневная традиция, которая проходит каждый день в 19:00 по киевскому времени.")


@dp.message(F.text.lower() == "минута ладушек")
async def ladushka_minute_handler(message: Message):
    await message.answer("👏 Минута ладушек проходит каждый день в 19:00 по киевскому времени.")


# --- АДМИН-КОМАНДА /sms [текст] ---
@dp.message(F.text.startswith("/sms"))
async def cmd_sms(message: Message):
    if message.from_user.id != Config.ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    
    try:
        await message.delete()
    except Exception as e:
        logging.error(f"Не удалось удалить сообщение с командой /sms: {e}")

    if len(parts) < 2:
        await message.answer("⚠️ Пожалуйста, укажите текст после команды /sms.")
        return

    sms_text = parts[1]

    try:
        await message.bot.send_message(chat_id=message.chat.id, text=sms_text)
        logging.info(f"📤 Администратор отправил через /sms: {sms_text}")
    except Exception as e:
        logging.error(f"🔴 Ошибка отправки сообщения /sms: {e}")


# --- ЖИВОЕ ОБЩЕНИЕ В ГРУППЕ ---
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def live_chat_handler(message: Message):
    global last_bot_message_time, last_replied_user_id

    if not message.from_user or message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return
    if not message.text:
        return

    if message.from_user.id == last_replied_user_id:
        return

    now = datetime.now()
    if now - last_bot_message_time < timedelta(minutes=3):
        return

    if random.random() > 0.07:
        return

    delay = random.randint(5, 30)
    await asyncio.sleep(delay)

    text_lower = message.text.lower()
    greetings = ["привет! 👋", "здарова!", "всем привет!", "приветули ✨"]
    nights = ["споки! 🌙", "сладких снов!", "доброй ночи!"]
    laughs = ["ахах, точно 😂", "жиза", "ору с этого 😂"]
    agrees = ["плюсую", "согласен", "это точно так"]
    defaults = ["понимаю 🙂", "круто!", "интересно 🤔", "ого", "хм, понятно"]

    if any(w in text_lower for w in ["привет", "здаров", "хай", "утро"]):
        reply_text = random.choice(greetings)
    elif any(w in text_lower for w in ["ночи", "спать", "пока", "спокойной"]):
        reply_text = random.choice(nights)
    elif any(w in text_lower for w in ["ахах", "ржу", "лол", "😂", "кек"]):
        reply_text = random.choice(laughs)
    elif any(w in text_lower for w in ["точно", "правда", "наверное"]):
        reply_text = random.choice(agrees)
    else:
        reply_text = random.choice(defaults)

    try:
        await message.reply(reply_text)
        last_bot_message_time = datetime.now()
        last_replied_user_id = message.from_user.id
    except Exception as e:
        logging.error(f"🔴 Ошибка живого ответа: {e}")


async def handle_ping(request: web.Request) -> web.Response:
    return web.Response(text="Bot is operational!")


async def start_web_server() -> None:
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORT)
    await site.start()
    logging.info(f"🌐 Web-server online on port {Config.PORT}")


async def auto_ping_task() -> None:
    url = "https://ladushka-b16l.onrender.com/"
    while True:
        await asyncio.sleep(240)
        success = False
        async with aiohttp.ClientSession() as session:
            for attempt in range(1, 3):
                try:
                    async with session.get(url, timeout=15) as response:
                        if response.status == 200:
                            success = True
                            break
                except Exception:
                    pass
                if attempt == 1:
                    await asyncio.sleep(5)


async def daily_ladushki_task(bot: Bot) -> None:
    kyiv_tz = zoneinfo.ZoneInfo("Europe/Kyiv")
    while True:
        now = datetime.now(kyiv_tz)
        target_1900 = now.replace(hour=19, minute=0, second=0, microsecond=0)
        target_1905 = now.replace(hour=19, minute=5, second=0, microsecond=0)

        if now < target_1900:
            target_time = target_1900
            task_type = "19:00"
        elif now < target_1905:
            target_time = target_1905
            task_type = "19:05"
        else:
            target_time = target_1900 + timedelta(days=1)
            task_type = "19:00"

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        try:
            if task_type == "19:00":
                text = "🕖 19:00 — Время петь «Ладушки»!\n🎶 Ладушки, ладушки, где были? У бабушки!"
            else:
                text = "❤️ Спасибо каждому, кто был сегодня с нами!\nДо встречи завтра в 19:00."
            
            await bot.send_message(chat_id=Config.GROUP_CHAT_ID, text=text)
        except Exception as e:
            logging.error(f"🔴 Ошибка ежедневной рассылки: {e}")


async def main() -> None:
    await start_web_server()

    bot = Bot(
        token=Config.TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    asyncio.create_task(daily_ladushki_task(bot))
    asyncio.create_task(auto_ping_task())

    logging.info("🚀 Бот успешно запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
