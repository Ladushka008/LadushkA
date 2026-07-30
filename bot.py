import os
import base64
import asyncio
import sqlite3
import requests
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties

# ==========================
# НАСТРОЙКИ (Переменные окружения)
# ==========================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7837011810"))

# GitHub настройки
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # Пример: Ladushka008/LadushkA
DB_FILE = "database.db"
GITHUB_FILE_PATH = "database.db"
BRANCH = "main"

if not TOKEN:
    print("CRITICAL ERROR: BOT_TOKEN is missing!")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()
db = None
cursor = None


# ==========================
# GITHUB СИНХРОНИЗАЦИЯ
# ==========================

def _sync_download():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GitHub sync failed: GITHUB_TOKEN or GITHUB_REPO missing")
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        response = requests.get(url, headers=headers, params={"ref": BRANCH}, timeout=10)
        if response.status_code == 200:
            content_b64 = response.json().get("content", "")
            file_data = base64.b64decode(content_b64)
            with open(DB_FILE, "wb") as f:
                f.write(file_data)
            print("Database downloaded from GitHub")
            return True
        else:
            print(f"GitHub sync failed: Status {response.status_code}")
            return False
    except Exception as e:
        print(f"GitHub sync failed: {e}")
        return False


def _sync_upload():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GitHub sync failed: GITHUB_TOKEN or GITHUB_REPO missing")
        return False

    if not os.path.exists(DB_FILE):
        print("GitHub sync failed: File DB not found")
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        with open(DB_FILE, "rb") as f:
            content_bytes = f.read()
        content_b64 = base64.b64encode(content_bytes).decode("utf-8")

        sha = None
        get_resp = requests.get(url, headers=headers, params={"ref": BRANCH}, timeout=10)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")

        data = {
            "message": "Auto-update database.db",
            "content": content_b64,
            "branch": BRANCH
        }
        if sha:
            data["sha"] = sha

        put_resp = requests.put(url, headers=headers, json=data, timeout=10)
        if put_resp.status_code in [200, 201]:
            print("Database uploaded to GitHub")
            return True
        else:
            print(f"GitHub sync failed: Status {put_resp.status_code}")
            return False
    except Exception as e:
        print(f"GitHub sync failed: {e}")
        return False


def trigger_github_upload():
    asyncio.create_task(asyncio.to_thread(_sync_upload))


# ==========================
# БАЗА ДАННЫХ
# ==========================

def init_db():
    global db, cursor
    if not os.path.exists(DB_FILE):
        _sync_download()

    db = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = db.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        balance INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender INTEGER,
        receiver INTEGER,
        amount INTEGER,
        action TEXT,
        reason TEXT,
        date TEXT
    )
    """)

    db.commit()


init_db()


# ==========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================

def register_user(user):
    cursor.execute(
        """
        INSERT OR IGNORE INTO users(user_id, username, full_name, balance)
        VALUES(?,?,?,0)
        """,
        (user.id, user.username, user.full_name)
    )

    cursor.execute(
        """
        UPDATE users
        SET username=?, full_name=?
        WHERE user_id=?
        """,
        (user.username, user.full_name, user.id)
    )
    db.commit()
    trigger_github_upload()


def get_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


def profile_link(user):
    if user.username:
        return f"https://t.me/{user.username}"
    return f"tg://user?id={user.id}"


def add_history(sender, receiver, amount, action, reason=""):
    cursor.execute(
        """
        INSERT INTO history(sender, receiver, amount, action, reason, date)
        VALUES(?,?,?,?,?,?)
        """,
        (
            sender,
            receiver,
            amount,
            action,
            reason,
            datetime.now().strftime("%d.%m.%Y %H:%M")
        )
    )
    db.commit()
    trigger_github_upload()


def is_admin(user_id: int):
    return user_id == ADMIN_ID


# ==========================
# ХЕНДЛЕРЫ
# ==========================

@dp.message(Command("start"))
async def start(message: Message):
    register_user(message.from_user)
    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Это бот группы <b>Ладушки</b>.\n\n"
        "Напиши <b>баланс</b>, чтобы посмотреть свои ладушки."
    )


@dp.message(F.text.lower() == "баланс")
async def balance(message: Message):
    register_user(message.from_user)
    target = message.from_user

    if message.reply_to_message:
        target = message.reply_to_message.from_user
        register_user(target)

    user_balance = get_balance(target.id)

    text = (
        f"👤 <b>{target.full_name}</b>\n\n"
        f"🔗 {profile_link(target)}\n\n"
        f"🪙 Ладушки: <b>{user_balance}</b>"
    )

    await message.answer(text)


@dp.message(F.text.lower() == "ладушка")
async def transfer_ladushka(message: Message):
    if not message.reply_to_message:
        return

    sender = message.from_user
    receiver = message.reply_to_message.from_user

    if sender.id == receiver.id:
        await message.reply("❌ Нельзя передавать ладушки самому себе.")
        return

    register_user(sender)
    register_user(receiver)

    sender_balance = get_balance(sender.id)

    if sender_balance < 1:
        await message.reply("❌ У вас нет ладушек.")
        return

    cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id=?", (sender.id,))
    cursor.execute("UPDATE users SET balance = balance + 1 WHERE user_id=?", (receiver.id,))
    db.commit()

    add_history(sender.id, receiver.id, 1, "transfer")

    await message.reply(
        "🪙 <b>Ладушка передана!</b>\n\n"
        f"От: {sender.full_name}\n"
        f"Кому: {receiver.full_name}\n\n"
        f"Теперь у вас {get_balance(sender.id)} ладушек.\n"
        f"У получателя {get_balance(receiver.id)} ладушек."
    )


@dp.message(Command("top"))
async def top_players(message: Message):
    cursor.execute("""
        SELECT full_name, balance
        FROM users
        ORDER BY balance DESC
        LIMIT 10
    """)
    rows = cursor.fetchall()

    if not rows:
        await message.answer("Пока нет игроков.")
        return

    text = "🏆 <b>ТОП игроков</b>\n\n"
    for i, row in enumerate(rows, start=1):
        text += f"{i}. {row[0]} — 🪙 {row[1]}\n"

    await message.answer(text)


@dp.message(Command("history"))
async def history(message: Message):
    cursor.execute("""
        SELECT action, amount, date
        FROM history
        WHERE sender=? OR receiver=?
        ORDER BY id DESC
        LIMIT 10
    """, (message.from_user.id, message.from_user.id))

    rows = cursor.fetchall()
    if not rows:
        await message.answer("📜 История пуста.")
        return

    text = "📜 <b>Последние операции</b>\n\n"
    for action, amount, date in rows:
        text += f"• {action} | {amount} 🪙 | {date}\n"

    await message.answer(text)


# --- Админские команды ---

@dp.message(Command("add"))
async def add_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /add 10")
        return
    try:
        amount = int(args[1])
    except ValueError:
        await message.answer("Введите число.")
        return

    user = message.reply_to_message.from_user
    register_user(user)
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user.id))
    db.commit()

    add_history(ADMIN_ID, user.id, amount, "add")
    await message.answer(f"✅ {user.full_name} получил {amount} ладушек.")


@dp.message(Command("remove"))
async def remove_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message:
        return
    args = message.text.split()
    if len(args) != 2:
        return
    try:
        amount = int(args[1])
    except ValueError:
        return

    user = message.reply_to_message.from_user
    cursor.execute("UPDATE users SET balance = MAX(balance-?,0) WHERE user_id=?", (amount, user.id))
    db.commit()

    add_history(ADMIN_ID, user.id, amount, "remove")
    await message.answer("✅ Ладушки сняты.")


@dp.message(Command("set"))
async def set_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message:
        return
    args = message.text.split()
    if len(args) != 2:
        return
    try:
        amount = int(args[1])
    except ValueError:
        return

    user = message.reply_to_message.from_user
    cursor.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user.id))
    db.commit()
    trigger_github_upload()

    await message.answer("✅ Баланс изменён.")


@dp.message(Command("fine"))
async def fine(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message:
        return
    args = message.text.split()
    if len(args) < 2:
        return
    try:
        amount = int(args[1])
    except ValueError:
        return

    user = message.reply_to_message.from_user
    cursor.execute("UPDATE users SET balance = MAX(balance-?,0) WHERE user_id=?", (amount, user.id))
    db.commit()
    trigger_github_upload()

    await message.answer(f"⚠️ Игрок {user.full_name} получил штраф {amount} ладушек.")


@dp.message(Command("bonus"))
async def bonus(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message:
        return
    args = message.text.split()
    if len(args) < 2:
        return
    try:
        amount = int(args[1])
    except ValueError:
        return

    user = message.reply_to_message.from_user
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user.id))
    db.commit()
    trigger_github_upload()

    await message.answer(f"🎁 Игрок {user.full_name} получил бонус {amount} ладушек.")


@dp.message(Command("reset"))
async def reset(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message:
        return
    user = message.reply_to_message.from_user
    cursor.execute("UPDATE users SET balance=0 WHERE user_id=?", (user.id,))
    db.commit()
    trigger_github_upload()

    await message.answer("♻️ Баланс игрока сброшен.")


# ==========================
# ЗАПУСК ЧЕРЕЗ POLLING
# ==========================

async def main():
    print("Удаляем старый Webhook...")
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот успешно запущен и ожидает сообщений!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
