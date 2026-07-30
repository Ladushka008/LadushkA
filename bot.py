import asyncio
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties

# ==========================
# НАСТРОЙКИ
# ==========================

TOKEN = "8529768374:AAHUF34sL8NygJousF46asP-FU9-H1U_Oac"

ADMIN_ID = 7837011810

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

# ==========================
# БАЗА ДАННЫХ
# ==========================

db = sqlite3.connect("ladushki.db")
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


# ==========================
# ФУНКЦИИ
# ==========================

def register_user(user):
    cursor.execute(
        """
        INSERT OR IGNORE INTO users(user_id, username, full_name, balance)
        VALUES(?,?,?,0)
        """,
        (
            user.id,
            user.username,
            user.full_name
        )
    )

    cursor.execute(
        """
        UPDATE users
        SET username=?, full_name=?
        WHERE user_id=?
        """,
        (
            user.username,
            user.full_name,
            user.id
        )
    )

    db.commit()


def get_balance(user_id):
    cursor.execute(
        "SELECT balance FROM users WHERE user_id=?",
        (user_id,)
    )

    row = cursor.fetchone()

    if row:
        return row[0]

    return 0


def profile_link(user):
    if user.username:
        return f"https://t.me/{user.username}"
    return f"tg://user?id={user.id}"


# ==========================
# START
# ==========================

@dp.message(Command("start"))
async def start(message: Message):
    register_user(message.from_user)

    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Это бот группы <b>Ладушки</b>.\n\n"
        "Напиши <b>баланс</b>, чтобы посмотреть свои ладушки."
    )


# ==========================
# БАЛАНС
# ==========================

@dp.message(F.text.lower() == "баланс")
async def balance(message: Message):

    register_user(message.from_user)

    target = message.from_user

    if message.reply_to_message:
        target = message.reply_to_message.from_user
        register_user(target)

    balance = get_balance(target.id)

    text = (
        f"👤 <b>{target.full_name}</b>\n\n"
        f"🔗 {profile_link(target)}\n\n"
        f"🪙 Ладушки: <b>{balance}</b>"
    )

    await message.answer(text)

# ==========================
# ПЕРЕДАЧА ЛАДУШЕК
# ==========================

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

    cursor.execute(
        "UPDATE users SET balance = balance - 1 WHERE user_id=?",
        (sender.id,)
    )

    cursor.execute(
        "UPDATE users SET balance = balance + 1 WHERE user_id=?",
        (receiver.id,)
    )

    db.commit()

    add_history(
        sender.id,
        receiver.id,
        1,
        "transfer"
    )

    await message.reply(
        "🪙 <b>Ладушка передана!</b>\n\n"
        f"От: {sender.full_name}\n"
        f"Кому: {receiver.full_name}\n\n"
        f"Теперь у вас {get_balance(sender.id)} ладушек.\n"
        f"У получателя {get_balance(receiver.id)} ладушек."
    )


# ==========================
# ТОП
# ==========================

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


# ==========================
# ИСТОРИЯ
# ==========================

@dp.message(Command("history"))
async def history(message: Message):

    cursor.execute("""
        SELECT action, amount, date
        FROM history
        WHERE sender=? OR receiver=?
        ORDER BY id DESC
        LIMIT 10
    """, (
        message.from_user.id,
        message.from_user.id
    ))

    rows = cursor.fetchall()

    if not rows:
        await message.answer("📜 История пуста.")
        return

    text = "📜 <b>Последние операции</b>\n\n"

    for action, amount, date in rows:
        text += f"• {action} | {amount} 🪙 | {date}\n"

    await message.answer(text)

# ==========================
# ПРОВЕРКА АДМИНА
# ==========================

def is_admin(user_id: int):
    return user_id == ADMIN_ID


# ==========================
# /add
# ==========================

@dp.message(Command("add"))
async def add_balance(message: Message):

    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        await message.answer("Ответьте на сообщение игрока.")
        return

    args = message.text.split()

    if len(args) != 2:
        await message.answer("Использование:\n/add 10")
        return

    try:
        amount = int(args[1])
    except:
        await message.answer("Введите число.")
        return

    user = message.reply_to_message.from_user

    register_user(user)

    cursor.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (amount, user.id)
    )
    db.commit()

    add_history(ADMIN_ID, user.id, amount, "add")

    await message.answer(f"✅ {user.full_name} получил {amount} ладушек.")


# ==========================
# /remove
# ==========================

@dp.message(Command("remove"))
async def remove_balance(message: Message):

    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        await message.answer("Ответьте на сообщение игрока.")
        return

    args = message.text.split()

    if len(args) != 2:
        await message.answer("Использование:\n/remove 10")
        return

    amount = int(args[1])

    user = message.reply_to_message.from_user

    cursor.execute(
        "UPDATE users SET balance = MAX(balance-?,0) WHERE user_id=?",
        (amount, user.id)
    )

    db.commit()

    add_history(ADMIN_ID, user.id, amount, "remove")

    await message.answer("✅ Ладушки сняты.")


# ==========================
# /set
# ==========================

@dp.message(Command("set"))
async def set_balance(message: Message):

    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        return

    args = message.text.split()

    if len(args) != 2:
        return

    amount = int(args[1])

    user = message.reply_to_message.from_user

    cursor.execute(
        "UPDATE users SET balance=? WHERE user_id=?",
        (amount, user.id)
    )

    db.commit()

    await message.answer("✅ Баланс изменён.")


# ==========================
# /fine
# ==========================

@dp.message(Command("fine"))
async def fine(message: Message):

    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        return

    args = message.text.split()

    if len(args) < 2:
        return

    amount = int(args[1])

    user = message.reply_to_message.from_user

    cursor.execute(
        "UPDATE users SET balance = MAX(balance-?,0) WHERE user_id=?",
        (amount, user.id)
    )

    db.commit()

    await message.answer(
        f"⚠️ Игрок {user.full_name} получил штраф {amount} ладушек."
    )


# ==========================
# /bonus
# ==========================

@dp.message(Command("bonus"))
async def bonus(message: Message):

    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        return

    args = message.text.split()

    if len(args) < 2:
        return

    amount = int(args[1])

    user = message.reply_to_message.from_user

    cursor.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (amount, user.id)
    )

    db.commit()

    await message.answer(
        f"🎁 Игрок {user.full_name} получил бонус {amount} ладушек."
    )


# ==========================
# /reset
# ==========================

@dp.message(Command("reset"))
async def reset(message: Message):

    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        return

    user = message.reply_to_message.from_user

    cursor.execute(
        "UPDATE users SET balance=0 WHERE user_id=?",
        (user.id,)
    )

    db.commit()

    await message.answer("♻️ Баланс игрока сброшен.")


# ==========================
# ЗАПУСК
# ==========================

async def main():
    print("Бот запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
