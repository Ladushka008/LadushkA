import os
import time
import base64
import sqlite3
import asyncio
import aiohttp

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ==========================================
# ⚙️ НАСТРОЙКИ (ENVIRONMENT VARIABLES)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8529768374:AAFgDuE2JZK0pztboi-jeS_prjvqdFyQgQw")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7837011810"))
PING_URL = os.getenv("PING_URL", "https://ladushka.onrender.com/")

# Файл базы данных SQLite
DB_FILE = "ladushki.db"

# Настройки интеграции с GitHub API
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{DB_FILE}"

# ==========================================
# 🗄️ БАЗА ДАННЫХ (SQLITE)
# ==========================================
def get_connection():
    return sqlite3.connect(DB_FILE)


def init_db():
    """Инициализация расширенной структуры базы данных."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Основная таблица пользователей
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            experience INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            last_daily INTEGER DEFAULT 0,
            last_active INTEGER DEFAULT 0
        )
        """)

        # Таблица инвентаря
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_name TEXT,
            quantity INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """)

        # Таблица достижений
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            achievement_name TEXT,
            unlocked_at INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """)

        # Таблица настроек пользователя
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            notifications_enabled INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """)
        
        conn.commit()


def update_user_activity(user_id: int, username: str):
    """Обновляет или регистрирует пользователя при любом взаимодействии."""
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users(user_id, username, last_active) VALUES(?,?,?)",
            (user_id, username, now)
        )
        cursor.execute(
            "UPDATE users SET username=?, last_active=? WHERE user_id=?",
            (username, now, user_id)
        )
        cursor.execute(
            "INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",
            (user_id,)
        )
        conn.commit()


def get_user_balance(user_id: int) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        return res[0] if res else 0


def update_balance(user_id: int, amount: int, mode: str = "add"):
    with get_connection() as conn:
        cursor = conn.cursor()
        if mode == "add":
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        elif mode == "subtract":
            cursor.execute("UPDATE users SET balance = MAX(balance - ?, 0) WHERE user_id=?", (amount, user_id))
        conn.commit()


def get_last_daily(user_id: int) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_daily FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        return res[0] if res else 0


def update_daily(user_id: int, now_time: int, reward: int = 25):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = balance + ?, last_daily=? WHERE user_id=?",
            (reward, now_time, user_id)
        )
        conn.commit()


def get_top_users(limit: int = 10):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT ?", (limit,))
        return cursor.fetchall()

# ==========================================
# ☁️ СИНХРОНИЗАЦИЯ С GITHUB
# ==========================================
async def download_db_from_github():
    """Скачивает последнюю версию базы данных из GitHub при запуске."""
    if not (GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO):
        print("⚠️ GitHub переменные не настроены. Синхронизация пропущена.")
        return

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{GITHUB_API_URL}?ref={GITHUB_BRANCH}", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    content = base64.b64decode(data['content'])
                    with open(DB_FILE, 'wb') as f:
                        f.write(content)
                    print("✅ База данных успешно загружена из GitHub.")
                elif response.status == 404:
                    print("ℹ️ База данных не найдена в репозитории. Создается новая.")
                else:
                    print(f"⚠️ Ошибка загрузки базы с GitHub (Status {response.status})")
        except Exception as e:
            print(f"❌ Ошибка при скачивании файла из GitHub: {e}")


async def upload_db_to_github():
    """Выгружает текущую базу данных в репозиторий GitHub."""
    if not (GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO):
        return

    if not os.path.exists(DB_FILE):
        return

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    with open(DB_FILE, 'rb') as f:
        content_b64 = base64.b64encode(f.read()).decode('utf-8')

    async with aiohttp.ClientSession() as session:
        try:
            sha = None
            async with session.get(f"{GITHUB_API_URL}?ref={GITHUB_BRANCH}", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    sha = data['sha']

            payload = {
                "message": "Auto-update database",
                "content": content_b64,
                "branch": GITHUB_BRANCH
            }
            if sha:
                payload["sha"] = sha

            async with session.put(GITHUB_API_URL, headers=headers, json=payload) as response:
                if response.status in [200, 201]:
                    print("☁️ База данных успешно синхронизирована с GitHub.")
                else:
                    print(f"⚠️ Ошибка выгрузки базы в GitHub (Status {response.status})")
        except Exception as e:
            print(f"❌ Ошибка при отправке файла в GitHub: {e}")


async def auto_github_sync_task():
    """Каждые 5 минут отправляет копию БД на GitHub."""
    while True:
        await asyncio.sleep(300)
        await upload_db_to_github()

# ==========================================
# 🔄 ОБСЛУЖИВАНИЕ И АВТОПИНГ
# ==========================================
async def auto_ping_task():
    """Фоновый пинг для предотвращения засыпания сервера Render."""
    if not PING_URL:
        return

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(PING_URL, timeout=10) as response:
                    print(f"Ping OK: {response.status}")
            except Exception as e:
                print("Ping error:", e)
            await asyncio.sleep(300)


async def auto_clean_db_task():
    """Автоочистка пользователей без активности более 180 дней и с нулевым балансом."""
    while True:
        try:
            now = int(time.time())
            max_inactivity = 180 * 86400  # 180 дней в секундах

            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                DELETE FROM users 
                WHERE balance = 0 AND (?-last_active > ? OR last_active = 0)
                """, (now, max_inactivity))
                conn.commit()

                cursor.execute("VACUUM")
                conn.commit()

            print("🧹 Очистка неактивных пользователей (>180 дней) и VACUUM выполнены.")
        except Exception as e:
            print("Ошибка при автоматической очистке БД:", e)

        await asyncio.sleep(86400)

# ==========================================
# 🤖 ИНИЦИАЛИЗАЦИЯ И ХЭНДЛЕРЫ БОТА
# ==========================================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()


@dp.message(Command("start"))
async def start(message: Message):
    update_user_activity(message.from_user.id, message.from_user.full_name)
    await message.answer("👏 Добро пожаловать в систему Ладушек!")


@dp.message(Command("баланс"))
async def balance(message: Message):
    update_user_activity(message.from_user.id, message.from_user.full_name)
    user_balance = get_user_balance(message.from_user.id)
    await message.answer(f"👏 Ваш баланс: <b>{user_balance}</b> ладушек")


@dp.message(Command("выдать"))
async def give(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
        await message.answer("Пример: /выдать 123456789 100")
        return

    user_id = int(args[1])
    amount = int(args[2])

    update_user_activity(user_id, "Пользователь")
    update_balance(user_id, amount, mode="add")

    await message.answer(f"✅ Выдано {amount} ладушек")


@dp.message(Command("забрать"))
async def take(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
        await message.answer("Пример: /забрать 123456789 100")
        return

    user_id = int(args[1])
    amount = int(args[2])

    update_balance(user_id, amount, mode="subtract")

    await message.answer(f"❌ Забрано {amount} ладушек")


@dp.message(Command("штраф"))
async def fine(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) < 4 or not args[1].isdigit() or not args[2].isdigit():
        await message.answer("Пример: /штраф 123456789 20 Опоздал")
        return

    user_id = int(args[1])
    amount = int(args[2])
    reason = " ".join(args[3:])

    update_balance(user_id, amount, mode="subtract")

    await message.answer(
        f"🚨 Штраф\n\n"
        f"➖ {amount} ладушек\n"
        f"📝 Причина: {reason}"
    )


@dp.message(Command("ежедневка"))
async def daily(message: Message):
    update_user_activity(message.from_user.id, message.from_user.full_name)

    last = get_last_daily(message.from_user.id)
    now = int(time.time())

    if now - last < 86400:
        await message.answer("⏰ Ежедневка уже получена")
        return

    update_daily(message.from_user.id, now, reward=25)
    await message.answer("🎁 Вы получили 25 ладушек!")


@dp.message(Command("топ"))
async def top(message: Message):
    update_user_activity(message.from_user.id, message.from_user.full_name)

    users = get_top_users(limit=10)

    text = "🏆 ТОП ЛАДУШЕК\n\n"
    for place, user in enumerate(users, start=1):
        text += f"{place}. {user[0]} — {user[1]} 👏\n"

    await message.answer(text)

# ==========================================
# 🚀 ТОЧКА ВХОДА
# ==========================================
async def main():
    # 1. Скачиваем актуальную версию SQLite из GitHub при старте
    await download_db_from_github()

    # 2. Инициализируем локальную структуру таблиц
    init_db()

    # 3. Запускаем фоновые задачи
    asyncio.create_task(auto_ping_task())
    asyncio.create_task(auto_clean_db_task())
    asyncio.create_task(auto_github_sync_task())

    # 4. Запускаем бота
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
