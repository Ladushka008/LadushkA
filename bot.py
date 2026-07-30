import os
import time
import base64
import asyncio
import logging
import aiosqlite
import aiohttp
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO)

# ==========================================
# ⚙️ НАСТРОЙКИ (ENVIRONMENT VARIABLES)
# ==========================================
# ОБЯЗАТЕЛЬНО задайте эти переменные в панели Render / Environment!
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PING_URL = os.getenv("PING_URL", "")
PORT = int(os.getenv("PORT", 8080))

DB_FILE = "ladushki.db"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{DB_FILE}"

if not BOT_TOKEN:
    logging.warning("⚠️ BOT_TOKEN не установлен в переменных окружения!")

# ==========================================
# 🌐 ВЕБ-СЕРВЕР ДЛЯ RENDER (HEALTH CHECK)
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot is running!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/ping', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"🌐 Веб-сервер запущен на порту {PORT}")

# ==========================================
# 🔄 СИНХРОНИЗАЦИЯ С GITHUB
# ==========================================
async def download_db_from_github():
    if not all([GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO]):
        logging.info("ℹ️ GitHub настройки не заданы, пропуск скачивания DB.")
        return

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(GITHUB_API_URL, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = base64.b64decode(data['content'])
                    with open(DB_FILE, 'wb') as f:
                        f.write(content)
                    logging.info("✅ База данных успешно скачана из GitHub!")
                else:
                    logging.warning(f"⚠️ База данных не найдена на GitHub (статус: {resp.status}).")
        except Exception as e:
            logging.error(f"❌ Ошибка при скачивании базы из GitHub: {e}")

async def upload_db_to_github():
    if not all([GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO]):
        return

    if not os.path.exists(DB_FILE):
        return

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    try:
        with open(DB_FILE, 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')

        async with aiohttp.ClientSession() as session:
            sha = None
            async with session.get(GITHUB_API_URL, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sha = data['sha']

            payload = {
                "message": "Auto-update database",
                "content": content,
                "branch": GITHUB_BRANCH
            }
            if sha:
                payload["sha"] = sha

            async with session.put(GITHUB_API_URL, headers=headers, json=payload) as resp:
                if resp.status in (200, 201):
                    logging.info("✅ База данных успешно сохранена на GitHub!")
                else:
                    logging.error(f"❌ Ошибка сохранении базы на GitHub: {await resp.text()}")
    except Exception as e:
        logging.error(f"❌ Исключение при выгрузке в GitHub: {e}")

# ==========================================
# 🗄️ РАБОТА С БАЗОЙ ДАННЫХ (AIOSQLITE)
# ==========================================
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                balance INTEGER DEFAULT 0,
                last_daily INTEGER DEFAULT 0,
                daily_streak INTEGER DEFAULT 0,
                last_active INTEGER DEFAULT 0,
                experience INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                referrer_id INTEGER DEFAULT NULL
            )
        ''')
        await db.commit()

async def update_user_activity(user_id: int, name: str, referrer_id: int = None):
    now = int(time.time())
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, name, last_active, referrer_id) VALUES (?, ?, ?, ?)",
                (user_id, name, now, referrer_id)
            )
        else:
            await db.execute(
                "UPDATE users SET name = ?, last_active = ? WHERE user_id = ?",
                (name, now, user_id)
            )
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def update_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

async def update_daily(user_id: int, now_ts: int, streak: int, reward: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ?, last_daily = ?, daily_streak = ? WHERE user_id = ?",
            (reward, now_ts, streak, user_id)
        )
        await db.commit()

async def get_top_users(limit: int = 10):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT name, balance, user_id FROM users ORDER BY balance DESC LIMIT ?", 
            (limit,)
        ) as cursor:
            return await cursor.fetchall()

async def get_user_rank(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT COUNT(*) + 1 FROM users WHERE balance > (SELECT balance FROM users WHERE user_id = ?)",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "N/A"

# ==========================================
# ⏰ ФОНОВЫЕ ЗАДАЧИ
# ==========================================
async def background_tasks():
    while True:
        await asyncio.sleep(180)  # Каждые 3 минуты
        # Авто-пинг Render для предотвращения спящего режима
        if PING_URL:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.get(PING_URL)
            except Exception as e:
                logging.error(f" Ошибка self-ping: {e}")
        
        # Сохранение базы в GitHub
        await upload_db_to_github()

# ==========================================
# 🤖 ИНИЦИАЛИЗА БОТА И ХЕНДЛЕРЫ
# ==========================================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    referrer_id = None
    if command.args and command.args.isdigit():
        ref = int(command.args)
        if ref != message.from_user.id:
            referrer_id = ref

    await update_user_activity(message.from_user.id, message.from_user.full_name, referrer_id)
    
    welcome_text = (
        f"Привет, <b>{message.from_user.first_name}</b>! 👋\n\n"
        "Добро пожаловать в бота **Ладушки**! 🎉\n\n"
        "📜 <b>Доступные команды:</b>\n"
        "🔹 `/профиль` — Ваш баланс и статистика\n"
        "🔹 `/ежедневка` — Забрать ежедневный бонус\n"
        "🔹 `/топ` — Таблица лидеров по ладушкам\n"
        "🔹 `/передать <id> <кол-во>` — Перевести ладушки"
    )
    await message.answer(welcome_text)

@dp.message(Command("профиль"))
async def profile_cmd(message: Message):
    await update_user_activity(message.from_user.id, message.from_user.full_name)
    user = await get_user(message.from_user.id)
    
    if not user:
        await message.answer("Профиль не найден.")
        return

    rank = await get_user_rank(message.from_user.id)

    text = (
        f"👤 <b>Профиль: {user['name']}</b>\n\n"
        f"👏 <b>Баланс:</b> {user['balance']} ладушек\n"
        f"🏆 <b>Место в топе:</b> #{rank}\n"
        f"🔥 <b>Стрик ежедневок:</b> {user['daily_streak']} дн.\n"
        f"⭐ <b>Уровень:</b> {user['level']}\n"
    )
    await message.answer(text)

@dp.message(Command("ежедневка"))
async def daily_cmd(message: Message):
    await update_user_activity(message.from_user.id, message.from_user.full_name)
    user = await get_user(message.from_user.id)
    
    now = int(time.time())
    last = user['last_daily']
    elapsed = now - last

    if elapsed < 86400:
        remaining = 86400 - elapsed
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await message.answer(f"⏰ Вы уже получали бонус! Возвращайтесь через **{hours}ч {minutes}мин**.")
        return

    # Подсчет стрика (сброс если прошло более 48 часов)
    streak = user['daily_streak'] + 1 if elapsed < 172800 else 1
    reward = 25 + min(streak * 5, 50)  # Награда растет с каждым днем (+5 за день, максимум +50)

    await update_daily(message.from_user.id, now, streak, reward)
    
    await message.answer(
        f"🎁 Вы получили <b>{reward}</b> ладушек!\n"
        f"🔥 Серия дней подряд: <b>{streak}</b>"
    )

@dp.message(Command("топ"))
async def top_cmd(message: Message):
    await update_user_activity(message.from_user.id, message.from_user.full_name)
    users = await get_top_users(limit=10)

    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 <b>ТОП-10 ПО ЛАДУШКАМ</b>\n\n"
    
    for place, user in enumerate(users, start=1):
        icon = medals[place - 1] if place <= 3 else f"<b>{place}.</b>"
        text += f"{icon} {user[0]} — <b>{user[1]}</b> 👏\n"

    user_rank = await get_user_rank(message.from_user.id)
    text += f"\n🎯 Ваша позиция в рейтинге: <b>#{user_rank}</b>"

    await message.answer(text)

@dp.message(Command("передать"))
async def transfer_cmd(message: Message, command: CommandObject):
    await update_user_activity(message.from_user.id, message.from_user.full_name)
    
    if not command.args or len(command.args.split()) < 2:
        await message.answer("⚠️ Использование: `/передать <user_id> <сумма>`")
        return

    args = command.args.split()
    if not args[0].isdigit() or not args[1].isdigit():
        await message.answer("⚠️ Укажите корректный ID и числовое значение суммы.")
        return

    target_id = int(args[0])
    amount = int(args[1])

    if amount <= 0:
        await message.answer("⚠️ Сумма перевода должна быть больше 0.")
        return

    sender = await get_user(message.from_user.id)
    if sender['balance'] < amount:
        await message.answer("❌ У вас недостаточно ладушек на балансе.")
        return

    target = await get_user(target_id)
    if not target:
        await message.answer("❌ Получатель не найден в базе бота.")
        return

    await update_balance(message.from_user.id, -amount)
    await update_balance(target_id, amount)

    await message.answer(f"✅ Вы успешно перевели <b>{amount}</b> ладушек пользователю <b>{target['name']}</b>!")

# ==========================================
# 👑 АДМИН КОМАНДЫ
# ==========================================
@dp.message(Command("выдать"))
async def give_cmd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return

    args = command.args.split() if command.args else []
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await message.answer("⚠️ Использование: `/выдать <user_id> <сумма>`")
        return

    target_id = int(args[0])
    amount = int(args[1])
    
    await update_balance(target_id, amount)
    await message.answer(f"✅ Начислено {amount} ладушек пользователю ID {target_id}")

@dp.message(Command("штраф"))
async def penalty_cmd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return

    args = command.args.split() if command.args else []
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await message.answer("⚠️ Использование: `/штраф <user_id> <сумма>`")
        return

    target_id = int(args[0])
    amount = int(args[1])
    
    await update_balance(target_id, -amount)
    await message.answer(f"✅ Списано {amount} ладушек у пользователя ID {target_id}")

# ==========================================
# 🚀 ТОЧКА ВХОДА
# ==========================================
async def main():
    await start_web_server()
    await download_db_from_github()
    await init_db()

    asyncio.create_task(background_tasks())

    try:
        logging.info("🚀 Бот запущен!")
        await dp.start_polling(bot)
    finally:
        logging.info("⏳ Сохранение данных перед выключением...")
        await upload_db_to_github()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
