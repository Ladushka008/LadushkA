import asyncio
import logging
import zoneinfo
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import Config
import database as db
from github_storage import download_database, upload_database
from handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


async def handle_ping(request: web.Request) -> web.Response:
    """Эндпоинт healthcheck для Render."""
    return web.Response(text="Bot is operational with async SQLite storage!")


async def start_web_server() -> None:
    """Запуск веб-сервера aiohttp."""
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORT)
    await site.start()
    logging.info(f"🌐 Web-server online on port {Config.PORT}")


async def daily_ladushki_task(bot: Bot) -> None:
    """Ежедневное фоновое задание отправки 'Ладушек' в 19:00 по киевскому времени."""
    kyiv_tz = zoneinfo.ZoneInfo("Europe/Kyiv")
    while True:
        now = datetime.now(kyiv_tz)
        target_time = now.replace(hour=19, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        logging.info(f"⏳ Следующее сообщение 'Ладушки' запланировано на {target_time.strftime('%d.%m.%Y %H:%M:%S')}")
        await asyncio.sleep(wait_seconds)

        try:
            text = (
                "👏 <b>19:00 — Начинаем ладушки!</b> 👏\n\n"
                "🎵 <i>Ладушки, ладушки, где были? У бабушки!</i> 🎵"
            )
            await bot.send_message(chat_id=Config.GROUP_CHAT_ID, text=text)
            logging.info("📢 Ежедневное сообщение отправлено!")
        except Exception as e:
            logging.error(f"🔴 Ошибка отправки ежедневного сообщения: {e}")


async def github_sync_task() -> None:
    """Фоновая выгрузка базы данных в GitHub каждые 5 минут."""
    while True:
        await asyncio.sleep(300)  # Интервал в секундах (5 минут)
        try:
            await upload_database()
        except Exception as e:
            logging.error(f"🔴 Ошибка фоновой выгрузки в GitHub: {e}")


async def main() -> None:
    # 1. Скачиваем базу данных из GitHub (если она там есть)
    await download_database()

    # 2. Инициализация БД
    await db.init_db()

    # 3. Запуск веб-сервера
    await start_web_server()

    bot = Bot(
        token=Config.TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.include_router(router)

    # Запуск фоновых задач
    asyncio.create_task(daily_ladushki_task(bot))
    asyncio.create_task(github_sync_task())

    logging.info("🚀 Бот запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        # Сохраняем актуальную БД при остановке бота
        logging.info("💾 Сохранение базы данных перед выключением...")
        await upload_database()


if __name__ == "__main__":
    asyncio.run(main())
