import os
import base64
import asyncio
import sqlite3
import requests
import aiohttp
import zoneinfo
import random
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ==========================
# НАСТРОЙКИ (Переменные окружения)
# ==========================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7837011810"))
PORT = int(os.getenv("PORT", 8080))

# Ссылка или ID вашей группы (по умолчанию @ladushka09)
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "@ladushka09")

# GitHub настройки
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # Пример: Ladushka008/LadushkA
DB_FILE = "database.db"
GITHUB_FILE_PATH = "database.db"
BRANCH = "main"

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
        print("GitHub sync failed: missing token or repo")
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
            print(f"GitHub sync download failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"GitHub sync download error: {e}")
        return False


def _sync_upload():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GitHub sync failed: missing token or repo")
        return False

    if not os.path.exists(DB_FILE):
        print("GitHub sync upload failed: DB file not found")
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
            print(f"GitHub sync upload failed: {put_resp.status_code}")
            return False
    except Exception as e:
        print(f"GitHub sync upload error: {e}")
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
        balance INTEGER DEFAULT 0,
        last_bonus TEXT
    )
    """)

    # Миграция: добавляем колонку last_bonus, если её ещё нет в существующей базе
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    if "last_bonus" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_bonus TEXT")

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
        INSERT OR IGNORE INTO users(user_id, username, full_name, balance, last_bonus)
        VALUES(?,?,?,0,NULL)
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


def get_user_mention(user):
    """Возвращает имя пользователя со встроенной ссылкой на его профиль"""
    if user.username:
        url = f"https://t.me/{user.username}"
    else:
        url = f"tg://user?id={user.id}"
    return f'<a href="{url}">{user.full_name}</a>'


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
        "✨ <b>Добро пожаловать в бота сообщества Ладушки!</b>\n\n"
        "💬 <b>Команды:</b>\n"
        "• Напишите <b>баланс</b> — чтобы узнать счет.\n"
        "• Напишите <b>бонус</b> — чтобы получить ежедневный бонус.\n"
        "• Ответьте на сообщение текстом <b>дать 50</b> — чтобы перевести ладушки."
    )


@dp.message(F.text.lower() == "баланс")
async def balance(message: Message):
    register_user(message.from_user)
    target = message.from_user

    if message.reply_to_message:
        target = message.reply_to_message.from_user
        register_user(target)

    user_balance = get_balance(target.id)
    user_link = get_user_mention(target)

    text = (
        f"┌ 👤 <b>Профиль:</b> {user_link}\n"
        f"└ 🪙 <b>Баланс:</b> {user_balance} ладушек"
    )

    await message.answer(text, disable_web_page_preview=True)


@dp.message(F.text.lower() == "бонус")
async def get_daily_bonus(message: Message):
    user = message.from_user
    register_user(user)

    cursor.execute("SELECT last_bonus FROM users WHERE user_id=?", (user.id,))
    row = cursor.fetchone()
    last_bonus_str = row[0] if row else None

    now = datetime.now()

    if last_bonus_str:
        last_bonus_time = datetime.fromisoformat(last_bonus_str)
        next_bonus_time = last_bonus_time + timedelta(hours=24)

        if now < next_bonus_time:
            time_left = next_bonus_time - now
            hours = time_left.seconds // 3600
            minutes = (time_left.seconds % 3600) // 60
            await message.reply(
                f"⏳ <b>Бонус уже получен.</b>\n"
                f"Следующий бонус будет доступен через <b>{hours} ч. {minutes} мин.</b>"
            )
            return

    reward = random.randint(1, 5)

    cursor.execute(
        "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id=?",
        (reward, now.isoformat(), user.id)
    )
    db.commit()

    # Мгновенно выгружаем обновившуюся БД на GitHub
    trigger_github_upload()

    add_history(0, user.id, reward, "daily_bonus")

    await message.reply(
        f"🎁 <b>Ежедневный бонус!</b>\n\n"
        f"💰 Ты получил: <b>+{reward}</b> ладушки"
    )


@dp.message(F.text.lower().startswith("дать "))
async def transfer_custom_amount(message: Message):
    if not message.reply_to_message:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("⚠️ Укажите сумму числом. Пример: <code>дать 50</code>")
        return

    amount = int(parts[1])
    if amount <= 0:
        await message.reply("❌ Сумма перевода должна быть больше 0.")
        return

    sender = message.from_user
    receiver = message.reply_to_message.from_user

    if sender.id == receiver.id:
        await message.reply("❌ Нельзя переводить ладушки самому себе.")
        return

    register_user(sender)
    register_user(receiver)

    sender_balance = get_balance(sender.id)

    if sender_balance < amount:
        await message.reply(
            f"❌ <b>Недостаточно средств!</b>\n"
            f"У вас на балансе: <b>{sender_balance}</b> ладушек."
        )
        return

    # Перевод
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, sender.id))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, receiver.id))
    db.commit()

    add_history(sender.id, receiver.id, amount, "transfer")

    sender_new_bal = get_balance(sender.id)
    receiver_new_bal = get_balance(receiver.id)

    sender_mention = get_user_mention(sender)
    receiver_mention = get_user_mention(receiver)

    await message.reply(
        f"✅ <b>Перевод успешно выполнен!</b>\n\n"
        f"📤 <b>Отправитель:</b> {sender_mention}\n"
        f"📥 <b>Получатель:</b> {receiver_mention}\n"
        f"💰 <b>Сумма:</b> {amount} ладушек\n\n"
        f"📊 <b>Новый баланс {receiver_mention}:</b> {receiver_new_bal} ладушек\n"
        f"📊 <b>Ваш новый баланс:</b> {sender_new_bal} ладушек",
        disable_web_page_preview=True
    )


@dp.message(F.text.lower() == "ладушка")
async def transfer_one_ladushka(message: Message):
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

    sender_mention = get_user_mention(sender)
    receiver_mention = get_user_mention(receiver)

    await message.reply(
        f"🪙 <b>Ладушка передана!</b>\n\n"
        f"От: {sender_mention}\n"
        f"Кому: {receiver_mention}\n\n"
        f"Теперь у вас {get_balance(sender.id)} ладушек.\n"
        f"У {receiver_mention} {get_balance(receiver.id)} ладушек.",
        disable_web_page_preview=True
    )


@dp.message(Command("top"))
async def top_players(message: Message):
    cursor.execute("""
        SELECT user_id, full_name, username, balance
        FROM users
        ORDER BY balance DESC
        LIMIT 10
    """)
    rows = cursor.fetchall()

    if not rows:
        await message.answer("Пока нет участников.")
        return

    text = "🏆 <b>ТОП Участников</b>\n\n"
    for i, row in enumerate(rows, start=1):
        uid, name, uname, bal = row
        url = f"https://t.me/{uname}" if uname else f"tg://user?id={uid}"
        text += f"{i}. <a href='{url}'>{name}</a> — 🪙 <b>{bal}</b>\n"

    await message.answer(text, disable_web_page_preview=True)


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

    text = "📜 <b>Последние операции:</b>\n\n"
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
    user_link = get_user_mention(user)
    await message.answer(f"✅ {user_link} получил {amount} ладушек.", disable_web_page_preview=True)


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

    user_link = get_user_mention(user)
    await message.answer(f"⚠️ Игрок {user_link} получил штраф {amount} ладушек.", disable_web_page_preview=True)


@dp.message(Command("admin_bonus"))
async def admin_bonus(message: Message):
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

    user_link = get_user_mention(user)
    await message.answer(f"🎁 Игрок {user_link} получил бонус {amount} ладушек.", disable_web_page_preview=True)


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
# ВЕБ-СЕРВЕР, АВТОПИНГ И РАССЫЛКА
# ==========================

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Web server started on port {PORT}")


async def auto_ping_task():
    """Фоновая задача автопинга каждые 4 минуты"""
    ping_url = "https://ladushka.onrender.com/"
    await asyncio.sleep(10)
    
    async with aiohttp.ClientSession() as session:
        while True:
            success = False
            for attempt in range(1, 3):
                try:
                    async with session.get(ping_url, timeout=10) as response:
                        if response.status == 200:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] Auto-ping success: {ping_url}")
                            success = True
                            break
                        else:
                            print(f"Auto-ping attempt {attempt} status: {response.status}")
                except Exception as e:
                    print(f"Auto-ping attempt {attempt} error: {e}")
                
                if not success and attempt == 1:
                    await asyncio.sleep(5)

            await asyncio.sleep(240)


async def daily_ladushki_task():
    """Фоновая задача: рассылка ровно в 19:00 по Киевскому времени в группу @ladushka09"""
    kyiv_tz = zoneinfo.ZoneInfo("Europe/Kyiv")
    
    while True:
        now = datetime.now(kyiv_tz)
        target_time = now.replace(hour=19, minute=0, second=0, microsecond=0)
        
        # Если 19:00 уже прошло сегодня, переносим на завтра
        if now >= target_time:
            target_time += timedelta(days=1)
        
        wait_seconds = (target_time - now).total_seconds()
        print(f"Следующая отправка 'Ладушек' запланирована на {target_time.strftime('%d.%m.%Y %H:%M:%S')} (через {int(wait_seconds)} сек)")
        
        await asyncio.sleep(wait_seconds)
        
        # Отправка сообщения в группу
        try:
            text = (
                "👏 <b>19:00 — Время петь «Ладушки»!</b> 👏\n\n"
                "🎶 <i>Ладушки, ладушки, где были? У бабушки!</i> 🎶\n"
                "✨ Пора забрать свои вечерние ладушки! ✨"
            )
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=text)
            print("Ежедневное сообщение 'Ладушки' успешно отправлено в группу!")
        except Exception as e:
            print(f"Ошибка при отправке ежедневного сообщения: {e}")


async def main():
    # Запускаем веб-сервер
    await start_web_server()
    
    # Запускаем автопинг Render
    asyncio.create_task(auto_ping_task())
    
    # Запускаем ежедневную рассылку в 19:00 по Киеву
    asyncio.create_task(daily_ladushki_task())
    
    # Очищаем вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот успешно запущен и ожидает сообщений!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
