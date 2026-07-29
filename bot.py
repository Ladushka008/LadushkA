import asyncio
import sqlite3
import time
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

TOKEN = "ВСТАВЬ_СЮДА_ТОКЕН_ОТ_BOTFATHER"
ADMIN_ID = 7837011810
PING_URL = "https://iris-store-bot.onrender.com/"

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Инициализация БД
db = sqlite3.connect("ladushki.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 0,
    last_daily INTEGER DEFAULT 0
)
""")
db.commit()


# Автопинг каждые 5 минут
async def auto_ping_task():
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(PING_URL, timeout=10) as response:
                    print(f"Ping OK: {response.status}")
            except Exception as e:
                print("Ping error:", e)
            
            await asyncio.sleep(300)


def get_user(user_id, username):
    cursor.execute(
        "INSERT OR IGNORE INTO users(user_id, username) VALUES(?,?)",
        (user_id, username)
    )
    db.commit()


@dp.message(Command("start"))
async def start(message: Message):
    get_user(message.from_user.id, message.from_user.full_name)
    await message.answer("👏 Добро пожаловать в систему Ладушек!")


@dp.message(Command("баланс"))
async def balance(message: Message):
    get_user(message.from_user.id, message.from_user.full_name)

    cursor.execute(
        "SELECT balance FROM users WHERE user_id=?",
        (message.from_user.id,)
    )
    user_balance = cursor.fetchone()[0]

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

    cursor.execute(
        "INSERT OR IGNORE INTO users(user_id, username) VALUES(?,?)",
        (user_id, "Пользователь")
    )
    cursor.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (amount, user_id)
    )
    db.commit()

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

    cursor.execute(
        "UPDATE users SET balance = MAX(balance - ?, 0) WHERE user_id=?",
        (amount, user_id)
    )
    db.commit()

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

    cursor.execute(
        "UPDATE users SET balance = MAX(balance - ?, 0) WHERE user_id=?",
        (amount, user_id)
    )
    db.commit()

    await message.answer(
        f"🚨 Штраф\n\n"
        f"➖ {amount} ладушек\n"
        f"📝 Причина: {reason}"
    )


@dp.message(Command("ежедневка"))
async def daily(message: Message):
    get_user(message.from_user.id, message.from_user.full_name)

    cursor.execute(
        "SELECT last_daily FROM users WHERE user_id=?",
        (message.from_user.id,)
    )
    last = cursor.fetchone()[0]
    now = int(time.time())

    if now - last < 86400:
        await message.answer("⏰ Ежедневка уже получена")
        return

    cursor.execute(
        "UPDATE users SET balance = balance + 25, last_daily=? WHERE user_id=?",
        (now, message.from_user.id)
    )
    db.commit()

    await message.answer("🎁 Вы получили 25 ладушек!")


@dp.message(Command("топ"))
async def top(message: Message):
    cursor.execute(
        "SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10"
    )
    users = cursor.fetchall()

    text = "🏆 ТОП ЛАДУШЕК\n\n"
    for place, user in enumerate(users, start=1):
        text += f"{place}. {user[0]} — {user[1]} 👏\n"

    await message.answer(text)


async def main():
    asyncio.create_task(auto_ping_task())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
