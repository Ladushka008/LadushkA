import asyncio
import logging
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

# Создание обычной клавиатуры (Reply Keyboard)
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🕖 Когда Минута ладушек?")],
        [KeyboardButton(text="ℹ️ Что такое Минута ладушек?")]
    ],
    resize_keyboard=True
)


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


@dp.message(F.text.startswith("/sms"))
async def cmd_sms(message: Message):
    # Проверяем, является ли пользователь администратором
    # (Используем ADMIN_ID из config, который по умолчанию равен вашему ID)
    if message.from_user.id != Config.ADMIN_ID:
        # Если не администратор — тихо игнорируем или можно уведомить
        return

    # Извлекаем текст после команды /sms
    parts = message.text.split(maxsplit=1)
    
    # Удаляем сообщение администратора с командой
    try:
        await message.delete()
    except Exception as e:
        logging.error(f"Не удалось удалить сообщение с командой /sms: {e}")

    # Если текст после /sms не указан
    if len(parts) < 2:
        await message.answer("⚠️ Пожалуйста, укажите текст после команды /sms.")
        return

    sms_text = parts[1]

    # Отправляем сообщение от имени бота в чат
    try:
        await message.bot.send_message(chat_id=message.chat.id, text=sms_text)
        logging.info(f"📤 Администратор отправил через /sms: {sms_text}")
    except Exception as e:
        logging.error(f"🔴 Ошибка отправки сообщения /sms: {e}")


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
    """Фоновая задача для автопинга сайта каждые 4 минуты с одной повторной попыткой."""
    url = "https://ladushka-b16l.onrender.com/"
    while True:
        await asyncio.sleep(240)  # 4 минуты = 240 секунд
        success = False
        
        async with aiohttp.ClientSession() as session:
            for attempt in range(1, 3):
                try:
                    async with session.get(url, timeout=15) as response:
                        if response.status == 200:
                            logging.info(f"🟢 Автопинг успешен (попытка {attempt}): {url}")
                            success = True
                            break
                        else:
                            logging.warning(f"🟡 Автопинг вернул статус {response.status} (попытка {attempt})")
                except Exception as e:
                    logging.warning(f"🔴 Ошибка автопинга (попытка {attempt}): {e}")
                
                if attempt == 1:
                    await asyncio.sleep(5)

        if not success:
            logging.error("❌ Не удалось достучаться до сайта после 2 попыток.")


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
        logging.info(f"⏳ Следующая рассылка ({task_type}) запланирована на {target_time.strftime('%d.%m.%Y %H:%M:%S')}")
        await asyncio.sleep(wait_seconds)

        try:
            if task_type == "19:00":
                text = (
                    "🕖 19:00 — Время петь «Ладушки»!\n"
                    "🎶 Ладушки, ладушки, где были? У бабушки!"
                )
            else:
                text = (
                    "❤️ Спасибо каждому, кто был сегодня с нами!\n"
                    "До встречи завтра в 19:00."
                )
            
            await bot.send_message(chat_id=Config.GROUP_CHAT_ID, text=text)
            logging.info(f"📢 Ежедневное сообщение ({task_type}) успешно отправлено!")
        except Exception as e:
            logging.error(f"🔴 Ошибка отправки сообщения ({task_type}): {e}")


async def main() -> None:
    await start_web_server()

    bot = Bot(
        token=Config.TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # Запуск фоновых задач
    asyncio.create_task(daily_ladushki_task(bot))
    asyncio.create_task(auto_ping_task())

    logging.info("🚀 Бот успешно запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
