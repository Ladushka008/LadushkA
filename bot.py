import os
import glob
import shutil
import base64
import asyncio
import sqlite3
import hashlib
import requests
import aiohttp
import zoneinfo
import random
import threading
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ==========================
# НАСТРОЙКИ
# ==========================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7837011810"))
PORT = int(os.getenv("PORT", 8080))

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "@ladushka09")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
DB_FILE = "database.db"
BACKUP_DIR = "backups"
MAX_BACKUPS = 20
GITHUB_FILE_PATH = "database.db"
BRANCH = "main"

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()
db = None
cursor = None
db_lock = threading.Lock()

# Очередь синхронизации GitHub
sync_event = asyncio.Event()
is_db_dirty = False

# Глобальное состояние дуэли
active_duel = None


# ==========================
# GITHUB СИНХРОНИЗАЦИЯ
# ==========================

def get_file_hash(filepath: str) -> str:
    """Вычисляет SHA256 хеш файла для надежной проверки файлов"""
    if not os.path.exists(filepath):
        return ""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sync_download():
    """
    Скачивает базу данных из GitHub ТОЛЬКО если локальный файл базы отсутствует.
    Если локальный файл существует и исправен, он берется за источник истины,
    чтобы исключить откат балансов к старым значениям из remote-репозитория.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GitHub sync failed: missing token or repo")
        return False

    if os.path.exists(DB_FILE):
        print("🟡 Локальная база данных уже существует. Скачивание с GitHub пропущено.")
        return True

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        response = requests.get(url, headers=headers, params={"ref": BRANCH}, timeout=15)
        if response.status_code == 200:
            res_json = response.json()
            content_b64 = res_json.get("content", "")
            file_data = base64.b64decode(content_b64)

            with db_lock:
                with open(DB_FILE, "wb") as f:
                    f.write(file_data)
            print("🟢 Database successfully downloaded from GitHub!")
            return True
        else:
            print(f"🔴 GitHub sync download failed: status {response.status_code}")
            return False
    except Exception as e:
        print(f"🔴 GitHub sync download error: {e}")
        return False


def _sync_upload_single():
    """Одиночная попытка загрузки с повторами при сбоях сети/конфликтах (Retry loop)"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False

    with db_lock:
        if not os.path.exists(DB_FILE):
            return False
        with open(DB_FILE, "rb") as f:
            content_bytes = f.read()

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            content_b64 = base64.b64encode(content_bytes).decode("utf-8")
            sha = None
            
            get_resp = requests.get(url, headers=headers, params={"ref": BRANCH}, timeout=10)
            if get_resp.status_code == 200:
                sha = get_resp.json().get("sha")

            data = {
                "message": f"Auto-update database.db [{datetime.now().strftime('%H:%M:%S')}]",
                "content": content_b64,
                "branch": BRANCH
            }
            if sha:
                data["sha"] = sha

            put_resp = requests.put(url, headers=headers, json=data, timeout=15)
            if put_resp.status_code in [200, 201]:
                print("🟢 Database uploaded to GitHub successfully!")
                return True
            else:
                print(f"⚠️ GitHub sync upload status {put_resp.status_code} (попытка {attempt}/{max_retries})")
        except Exception as e:
            print(f"⚠️ GitHub sync upload error: {e} (попытка {attempt}/{max_retries})")

        import time
        time.sleep(2 * attempt)

    return False


async def _github_sync_worker():
    """Фоновый воркер: накапливает пакеты изменений (7 сек) и выполняет строго 1 выгрузку за раз"""
    global is_db_dirty
    while True:
        await sync_event.wait()
        sync_event.clear()

        await asyncio.sleep(7)
        is_db_dirty = False

        await asyncio.to_thread(_sync_upload_single)

        if is_db_dirty:
            sync_event.set()


def save_db_changes():
    """Планирует выгрузку изменений в GitHub через воркер"""
    global is_db_dirty
    is_db_dirty = True
    sync_event.set()


# ==========================
# БЕКАПЫ И ПРОВЕРКА ЦЕЛОСТНОСТИ
# ==========================

def create_backup():
    """Создаёт резервную копию базы данных и удаляет старые (>20)"""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")

    with db_lock:
        if db:
            try:
                bck = sqlite3.connect(backup_file)
                db.backup(bck)
                bck.close()
                print(f"🟢 Резервная копия создана: {backup_file}")
            except Exception as e:
                print(f"🔴 Ошибка при создании бэкапа SQLite: {e}")
                return

    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*.db")), key=os.path.getmtime)
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        try:
            os.remove(oldest)
            print(f"🧹 Удалена старая резервная копия: {oldest}")
        except Exception as e:
            print(f"🔴 Ошибка при удалении бэкапа {oldest}: {e}")


def check_and_restore_db():
    """Проверяет целостность БД. Восстанавливает только если файл повреждён."""
    if not os.path.exists(DB_FILE):
        print("ℹ️ Файл базы данных не найден. Будет создана новая БД.")
        return

    is_corrupt = False
    try:
        conn = sqlite3.connect(DB_FILE)
        res = conn.execute("PRAGMA quick_check;").fetchone()
        conn.close()
        if not res or res[0] != "ok":
            is_corrupt = True
    except Exception as e:
        print(f"🔴 Ошибка чтения БД: {e}")
        is_corrupt = True

    if is_corrupt:
        print("⚠️ Обнаружено повреждение базы данных! Запуск автовосстановления...")
        backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*.db")), key=os.path.getmtime, reverse=True)
        restored = False
        for bck in backups:
            try:
                test_conn = sqlite3.connect(bck)
                res = test_conn.execute("PRAGMA quick_check;").fetchone()
                test_conn.close()
                if res and res[0] == "ok":
                    shutil.copy(bck, DB_FILE)
                    print(f"🟢 База данных успешно восстановлена из бэкапа: {bck}")
                    restored = True
                    break
            except Exception:
                continue
        if not restored:
            print("🔴 Не удалось найти исправный бэкап. Создаётся чистая база данных.")
            if os.path.exists(DB_FILE):
                os.remove(DB_FILE)


# ==========================
# ИНИЦИАЛИЗАЦИЯ И РАБОТА С БД
# ==========================

async def init_db():
    global db, cursor
    # 1. Проверяем целостность локальной БД
    await asyncio.to_thread(check_and_restore_db)
    # 2. Скачиваем актуальную версию из GitHub (только если локального файла нет)
    await asyncio.to_thread(_sync_download)

    # 3. Подключаемся к базе SQLite
    db = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = db.cursor()

    with db_lock:
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 0,
                last_bonus TEXT,
                created_at TEXT,
                reputation INTEGER DEFAULT 0
            )
            """)

            cursor.execute("PRAGMA table_info(users)")
            columns = [column[1] for column in cursor.fetchall()]
            if "last_bonus" not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN last_bonus TEXT")
            if "created_at" not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
            if "reputation" not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN reputation INTEGER DEFAULT 0")

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

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory(
                user_id INTEGER,
                item_name TEXT,
                quantity INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, item_name)
            )
            """)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"🔴 Ошибка при инициализации таблиц: {e}")


# ==========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================

def register_user(user):
    if not user:
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        try:
            cursor.execute(
                """
                INSERT OR IGNORE INTO users(user_id, username, full_name, balance, last_bonus, created_at, reputation)
                VALUES(?,?,?,0,NULL,?,0)
                """,
                (user.id, user.username, user.full_name, now_str)
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
        except Exception as e:
            db.rollback()
            print(f"Ошибка регистрации пользователя: {e}")
            return
    save_db_changes()


def get_balance(user_id):
    with db_lock:
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    return row[0] if row else 0


def get_reputation(user_id):
    with db_lock:
        cursor.execute("SELECT reputation FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    return row[0] if row and row[0] is not None else 0


def get_user_mention(user):
    if not user:
        return "Неизвестный"
    if user.username:
        url = f"https://t.me/{user.username}"
    else:
        url = f"tg://user?id={user.id}"
    return f'<a href="{url}">{user.full_name}</a>'


def get_total_items_count(user_id):
    with db_lock:
        cursor.execute("SELECT SUM(quantity) FROM inventory WHERE user_id=? AND quantity > 0", (user_id,))
        row = cursor.fetchone()
    return row[0] if row and row[0] is not None else 0


def add_history(sender, receiver, amount, action, reason=""):
    with db_lock:
        try:
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
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            )
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка логирования истории: {e}")
            return
    save_db_changes()


def is_admin(user_id: int):
    return user_id == ADMIN_ID


def get_item_quantity(user_id, item_name):
    with db_lock:
        cursor.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_name=?", (user_id, item_name))
        row = cursor.fetchone()
    return row[0] if row else 0


def add_item(user_id, item_name, count=1):
    with db_lock:
        try:
            cursor.execute("""
                INSERT INTO inventory (user_id, item_name, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_name) DO UPDATE SET quantity = quantity + ?
            """, (user_id, item_name, count, count))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка добавления предмета: {e}")
            return
    save_db_changes()


def remove_item(user_id, item_name, count=1):
    with db_lock:
        try:
            cursor.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_name=?", (user_id, item_name))
            row = cursor.fetchone()
            current = row[0] if row else 0

            if current <= count:
                cursor.execute("DELETE FROM inventory WHERE user_id=? AND item_name=?", (user_id, item_name))
            else:
                cursor.execute("UPDATE inventory SET quantity = quantity - ? WHERE user_id=? AND item_name=?", (count, user_id, item_name))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка удаления предмета: {e}")
            return
    save_db_changes()


# ==========================
# СИСТЕМА ДУЭЛЕЙ
# ==========================

async def duel_timeout_task(chat_id: int):
    await asyncio.sleep(600)
    global active_duel
    if active_duel:
        active_duel = None
        await bot.send_message(chat_id, "⌛ Дуэль отменена из-за отсутствия активности (10 минут).")


def cancel_duel_timer():
    global active_duel
    if active_duel and active_duel.get("timer_task"):
        active_duel["timer_task"].cancel()


def reset_duel_timer(chat_id: int):
    global active_duel
    cancel_duel_timer()
    if active_duel:
        active_duel["timer_task"] = asyncio.create_task(duel_timeout_task(chat_id))


@dp.message(F.text.lower() == "дуэль")
async def start_duel_request(message: Message):
    global active_duel

    if active_duel is not None:
        await message.reply("⚔️ Сейчас уже идёт дуэль. Дождитесь её окончания или отмените командой <code>отмена дуэли</code>.")
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    challenger = message.from_user
    opponent = message.reply_to_message.from_user

    if opponent.is_bot:
        await message.reply("🤖 Бота нельзя вызвать на дуэль.")
        return

    if challenger.id == opponent.id:
        await message.reply("❌ Нельзя вызвать самого себя на дуэль.")
        return

    register_user(challenger)
    register_user(opponent)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"duel_accept_{challenger.id}_{opponent.id}"),
                InlineKeyboardButton(text="❌ Отказаться", callback_data=f"duel_decline_{challenger.id}_{opponent.id}")
            ]
        ]
    )

    active_duel = {
        "status": "pending",
        "challenger": challenger,
        "opponent": opponent,
        "current_turn": None,
        "timer_task": asyncio.create_task(duel_timeout_task(message.chat.id))
    }

    challenger_link = get_user_mention(challenger)
    opponent_link = get_user_mention(opponent)

    await message.answer(
        f"⚔️ {challenger_link} вызывает {opponent_link} на дуэль!",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )


@dp.message(F.text.lower().in_(["отмена дуэли", "стоп дуэль", "отмена"]))
async def cancel_duel_command(message: Message):
    global active_duel
    if not active_duel:
        return

    user_id = message.from_user.id
    if user_id in [active_duel["challenger"].id, active_duel["opponent"].id] or is_admin(user_id):
        cancel_duel_timer()
        active_duel = None
        await message.reply("🛑 Дуэль была успешно отменена.")
    else:
        await message.reply("❌ Вы не участвуете в текущей дуэли.")


@dp.callback_query(F.data.startswith("duel_accept_"))
async def accept_duel_callback(callback: CallbackQuery):
    global active_duel

    if not active_duel or active_duel["status"] != "pending":
        await callback.answer("Дуэль больше недоступна.", show_alert=True)
        return

    parts = callback.data.split("_")
    opponent_id = int(parts[3])

    if callback.from_user.id != opponent_id:
        await callback.answer("Эта кнопка не для вас!", show_alert=True)
        return

    first, second = random.sample([active_duel["challenger"], active_duel["opponent"]], 2)
    active_duel["status"] = "active"
    active_duel["current_turn"] = first.id
    reset_duel_timer(callback.message.chat.id)

    first_mention = get_user_mention(first)

    await callback.message.edit_text(
        f"⚔️ <b>Дуэль началась!</b>\n\n"
        f"🎯 Первым ходит: {first_mention}\n\n"
        f"Чтобы ударить, напишите:\n<code>удар</code>",
        disable_web_page_preview=True
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("duel_decline_"))
async def decline_duel_callback(callback: CallbackQuery):
    global active_duel

    if not active_duel or active_duel["status"] != "pending":
        await callback.answer("Дуэль больше недоступна.", show_alert=True)
        return

    parts = callback.data.split("_")
    opponent_id = int(parts[3])

    if callback.from_user.id != opponent_id:
        await callback.answer("Эта кнопка не для вас!", show_alert=True)
        return

    cancel_duel_timer()
    opponent_mention = get_user_mention(active_duel["opponent"])
    active_duel = None

    await callback.message.edit_text(
        f"❌ {opponent_mention} отказался от дуэли.",
        disable_web_page_preview=True
    )
    await callback.answer()


@dp.message(F.text.lower().in_(["удар", "ударить"]))
async def make_duel_hit(message: Message):
    global active_duel

    if not active_duel or active_duel["status"] != "active":
        return

    sender = message.from_user
    p1 = active_duel["challenger"]
    p2 = active_duel["opponent"]

    if sender.id not in [p1.id, p2.id]:
        return

    if sender.id != active_duel["current_turn"]:
        await message.reply("⏳ Сейчас не ваш ход.")
        return

    reset_duel_timer(message.chat.id)
    
    attacker = sender
    defender = p2 if sender.id == p1.id else p1

    attacker_mention = get_user_mention(attacker)
    defender_mention = get_user_mention(defender)

    is_finish = random.random() < 0.30

    if is_finish:
        cancel_duel_timer()
        stolen = 0

        with db_lock:
            try:
                cursor.execute("SELECT balance FROM users WHERE user_id=?", (defender.id,))
                row = cursor.fetchone()
                def_bal = row[0] if row else 0
                stolen = min(def_bal, 3)

                if stolen > 0:
                    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (stolen, defender.id))
                    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (stolen, attacker.id))
                    db.commit()
            except Exception as e:
                db.rollback()
                print(f"Ошибка перевода денег дуэли: {e}")

        if stolen > 0:
            save_db_changes()
            add_history(defender.id, attacker.id, stolen, "duel_win")

        active_duel = None

        text = (
            f"💥 {attacker_mention} мощно врезал ладушкой по {defender_mention}!\n\n"
            f"🏆 <b>Победитель:</b> {attacker_mention}\n\n"
            f"💰 {attacker_mention} получает {stolen} ладушки.\n"
            f"💸 {defender_mention} теряет {stolen} ладушки."
        )
        await message.answer(text, disable_web_page_preview=True)
    else:
        active_duel["current_turn"] = defender.id
        text = (
            f"👏 {attacker_mention} ударил ладушкой {defender_mention}!\n\n"
            f"🎯 Теперь ходит:\n{defender_mention}\n\n"
            f"Напишите:\n<code>удар</code>"
        )
        await message.answer(text, disable_web_page_preview=True)


# ==========================
# ОСНОВНЫЕ ХЕНДЛЕРЫ
# ==========================

@dp.message(F.text.lower() == "бот")
async def bot_reply(message: Message):
    await message.reply("Тут я, тут")


@dp.message(Command("start"))
async def start(message: Message):
    register_user(message.from_user)
    await message.answer(
        "✨ <b>Добро пожаловать в бота сообщества Ладушки!</b>\n\n"
        "💬 <b>Команды:</b>\n"
        "• Напишите <b>профиль</b> — чтобы посмотреть свой профиль.\n"
        "• Напишите <b>баланс</b> — чтобы узнать счет.\n"
        "• Напишите <b>бонус</b> — чтобы получить ежедневный бонус.\n"
        "• Напишите <b>дуэль</b> — вызвать игрока на дуэль (ответом на сообщение).\n"
        "• Напишите <b>отмена</b> — отменить дуэль.\n"
        "• Напишите <b>магазин</b> — чтобы открыть магазин предметов.\n"
        "• Напишите <b>инвентарь</b> — чтобы посмотреть свои предметы.\n"
        "• Напишите <b>репутация</b> — чтобы увидеть ТОП-5 по репутации.\n"
        "• Напишите <b>крыса</b> — запустить крысу украсть ладушки у случайного игрока.\n"
        "• Ответьте на сообщение текстом <b>подарок</b> или <b>ладошка</b> — чтобы передать 1 ладушку игроку.\n"
        "• Ответьте на сообщение текстом <b>дать 50</b> — чтобы перевести ладушки.\n"
        "• Ответьте на сообщение текстом <b>ударить ладушкой</b> — применить Боевую ладушку.\n"
        "• Ответьте на сообщение текстом <b>кинуть томат</b> — бросить томат в участника."
    )


@dp.message(F.text.lower() == "профиль")
async def profile_handler(message: Message):
    register_user(message.from_user)
    target = message.reply_to_message.from_user if (message.reply_to_message and message.reply_to_message.from_user) else message.from_user
    register_user(target)

    user_balance = get_balance(target.id)
    items_count = get_total_items_count(target.id)
    user_rep = get_reputation(target.id)

    text = (
        f"👤 <b>Имя:</b> {target.full_name}\n\n"
        f"💰 <b>Баланс:</b> {user_balance} ладушек\n\n"
        f"🎒 <b>Предметов:</b> {items_count}\n\n"
        f"⭐ <b>Репутация:</b> {user_rep}/10"
    )

    await message.answer(text, disable_web_page_preview=True)


@dp.message(F.text.lower() == "баланс")
async def balance(message: Message):
    register_user(message.from_user)
    target = message.from_user

    if message.reply_to_message and message.reply_to_message.from_user:
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

    now = datetime.now()

    with db_lock:
        cursor.execute("SELECT last_bonus FROM users WHERE user_id=?", (user.id,))
        row = cursor.fetchone()
        last_bonus_str = row[0] if row else None

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

        try:
            cursor.execute(
                "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id=?",
                (reward, now.isoformat(), user.id)
            )
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка получения бонуса: {e}")
            return

    save_db_changes()
    add_history(0, user.id, reward, "daily_bonus")

    await message.reply(
        f"🎁 <b>Ежедневный бонус!</b>\n\n"
        f"💰 Ты получил: <b>+{reward}</b> ладушки"
    )


# --- МАГАЗИН И ИНВЕНТАРЬ ---

@dp.message(F.text.lower() == "магазин")
async def shop_handler(message: Message):
    text = (
        "🛒 <b>Магазин Ладушек</b>\n\n"
        "🥊 <b>Боевая ладушка</b> — 200 ладушек\n"
        "🍅 <b>Томат</b> — 100 ладушек\n"
        "🐀 <b>Крыса</b> — 250 ладушек\n\n"
        "Для покупки:\n"
        "<code>купить ладушка</code>\n"
        "<code>купить томат</code>\n"
        "<code>купить крыса</code>"
    )
    await message.answer(text)


@dp.message(F.text.lower() == "купить ладушка")
async def buy_battle_ladushka(message: Message):
    user = message.from_user
    register_user(user)
    
    price = 200
    
    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?", (price, user.id, price))
            if cursor.rowcount == 0:
                cursor.execute("SELECT balance FROM users WHERE user_id=?", (user.id,))
                row = cursor.fetchone()
                user_bal = row[0] if row else 0
                await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{user_bal}</b> ладушек")
                return
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка покупки: {e}")
            return
    
    save_db_changes()
    add_item(user.id, "battle_ladushka", 1)
    add_history(user.id, 0, price, "buy_item", "Боевая ладушка")

    await message.reply(
        "✅ <b>Покупка успешна!</b>\n\n"
        "🥊 <b>Получено:</b> Боевая ладушка ×1\n"
        f"💰 <b>Списано:</b> {price} ладушек"
    )


@dp.message(F.text.lower() == "купить томат")
async def buy_tomato(message: Message):
    user = message.from_user
    register_user(user)
    
    price = 100

    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?", (price, user.id, price))
            if cursor.rowcount == 0:
                cursor.execute("SELECT balance FROM users WHERE user_id=?", (user.id,))
                row = cursor.fetchone()
                user_bal = row[0] if row else 0
                await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{user_bal}</b> ладушек")
                return
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка покупки: {e}")
            return
    
    save_db_changes()
    add_item(user.id, "tomato", 1)
    add_history(user.id, 0, price, "buy_item", "Томат")

    await message.reply(
        "✅ <b>Покупка успешна!</b>\n\n"
        "🍅 <b>Получено:</b> Томат ×1\n"
        f"💰 <b>Списано:</b> {price} ладушек"
    )


@dp.message(F.text.lower() == "купить крыса")
async def buy_rat(message: Message):
    user = message.from_user
    register_user(user)
    
    price = 250

    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?", (price, user.id, price))
            if cursor.rowcount == 0:
                cursor.execute("SELECT balance FROM users WHERE user_id=?", (user.id,))
                row = cursor.fetchone()
                user_bal = row[0] if row else 0
                await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{user_bal}</b> ладушек")
                return
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка покупки: {e}")
            return
    
    save_db_changes()
    add_item(user.id, "rat", 1)
    add_history(user.id, 0, price, "buy_item", "Крыса")

    await message.reply(
        "✅ <b>Покупка успешна!</b>\n\n"
        "🐀 <b>Получено:</b> Крыса ×1\n"
        f"💰 <b>Списано:</b> {price} ладушек"
    )


@dp.message(F.text.lower() == "инвентарь")
async def inventory_handler(message: Message):
    user = message.from_user
    register_user(user)

    with db_lock:
        cursor.execute("SELECT item_name, quantity FROM inventory WHERE user_id=? AND quantity > 0", (user.id,))
        rows = cursor.fetchall()

    if not rows:
        await message.reply("🎒 Ваш инвентарь пуст.")
        return

    text = "🎒 <b>Ваш инвентарь</b>\n\n"
    for item_name, quantity in rows:
        if item_name == "battle_ladushka":
            text += f"🥊 <b>Боевая ладушка</b> ×{quantity}\n"
        elif item_name == "tomato":
            text += f"🍅 <b>Томат</b> ×{quantity}\n"
        elif item_name == "rat":
            text += f"🐀 <b>Крыса</b> ×{quantity}\n"
        else:
            text += f"📦 <b>{item_name}</b> ×{quantity}\n"

    await message.reply(text)


@dp.message(F.text.lower() == "ударить ладушкой")
async def hit_with_ladushka(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    sender = message.from_user
    receiver = message.reply_to_message.from_user

    register_user(sender)
    register_user(receiver)

    count = get_item_quantity(sender.id, "battle_ladushka")
    if count <= 0:
        await message.reply("❌ <b>У вас нет Боевой ладушки.</b>\n\n🛒 Купить можно в магазине за 200 ладушек.")
        return

    remove_item(sender.id, "battle_ladushka", 1)

    sender_link = get_user_mention(sender)
    receiver_link = get_user_mention(receiver)

    phrases = [
        f"🥊 {sender_link} ударил ладушкой {receiver_link}!\n\n👏 <b>ШЛЁП!</b>",
        f"💥 {sender_link} размахнулся и влепил ладушку {receiver_link}!",
        f"🏛 <b>Министр Ладушек одобрил удар.</b>\n\n🥊 {sender_link} ударил {receiver_link} ладушкой!"
    ]

    selected_phrase = random.choice(phrases)
    await message.reply(selected_phrase, disable_web_page_preview=True)


@dp.message(F.text.lower() == "кинуть томат")
async def throw_tomato(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    sender = message.from_user
    receiver = message.reply_to_message.from_user

    register_user(sender)
    register_user(receiver)

    count = get_item_quantity(sender.id, "tomato")
    if count <= 0:
        await message.reply("❌ <b>У вас нет томатов.</b>\n\n🛒 Купить можно в магазине за 100 ладушек.")
        return

    remove_item(sender.id, "tomato", 1)

    sender_link = get_user_mention(sender)
    receiver_link = get_user_mention(receiver)

    phrases = [
        f"🍅 {sender_link} кинул томат в {receiver_link}!\n\n🤭 Теперь {receiver_link} весь в томате.",
        f"🍅 {sender_link} запустил томат в {receiver_link}!\n\n💥 Прямое попадание!\n\n😂 {receiver_link} весь в кетчупе.",
        f"🎯 <b>Меткий бросок!</b>\n\n🍅 {receiver_link} теперь весь в томатном соке."
    ]

    selected_phrase = random.choice(phrases)
    await message.reply(selected_phrase, disable_web_page_preview=True)


@dp.message(F.text.lower() == "крыса")
async def use_rat(message: Message):
    sender = message.from_user
    register_user(sender)

    count = get_item_quantity(sender.id, "rat")
    if count <= 0:
        await message.reply("❌ У вас нет крысы.")
        return

    with db_lock:
        cursor.execute("SELECT user_id, full_name, balance FROM users WHERE user_id != ?", (sender.id,))
        targets = cursor.fetchall()

    if not targets:
        await message.reply("❌ Недостаточно игроков в группе.")
        return

    target_id, target_name, target_bal = random.choice(targets)
    remove_item(sender.id, "rat", 1)

    wanted_steal = random.randint(1, 3)

    if target_bal <= 0:
        await message.reply(f"🐀 Крыса обыскала карманы {target_name}...\n\n😢 Но там не оказалось ни одной ладушки.")
    elif target_bal < wanted_steal:
        stolen = target_bal
        with db_lock:
            try:
                cursor.execute("UPDATE users SET balance = 0 WHERE user_id=?", (target_id,))
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (stolen, sender.id))
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Ошибка кражи: {e}")
                return
        save_db_changes()
        add_history(target_id, sender.id, stolen, "rat_steal")

        await message.reply(
            f"🐀 Крыса пробралась к {target_name}!\n\n"
            f"💸 У {target_name} было только {target_bal} ладушки.\n"
            f"Крыса украла всё что смогла: {stolen} ладушки."
        )
    else:
        stolen = wanted_steal
        with db_lock:
            try:
                cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (stolen, target_id))
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (stolen, sender.id))
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Ошибка кражи: {e}")
                return
        save_db_changes()
        add_history(target_id, sender.id, stolen, "rat_steal")

        await message.reply(f"🐀 Крыса пробралась к {target_name}!\n\n💸 Украдено: {stolen} ладушки")


# --- ДЕНЕЖНЫЕ ПЕРЕВОДЫ И ПОДАРКИ (ЛАДОШКИ) ---

@dp.message(F.text.lower().startswith("дать "))
async def transfer_custom_amount(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
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

    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?", (amount, sender.id, amount))
            if cursor.rowcount == 0:
                cursor.execute("SELECT balance FROM users WHERE user_id=?", (sender.id,))
                row = cursor.fetchone()
                sender_balance = row[0] if row else 0
                await message.reply(f"❌ <b>Недостаточно средств!</b>\nУ вас на балансе: <b>{sender_balance}</b> ладушек.")
                return

            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, receiver.id))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка перевода: {e}")
            return

    save_db_changes()
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


@dp.message(F.text.lower().in_(["подарок", "ладошка"]))
async def transfer_one_ladushka(message: Message):
    sender = message.from_user

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Ответьте на сообщение игрока, чтобы передать ладушку!")
        return

    receiver = message.reply_to_message.from_user

    if receiver.is_bot:
        await message.reply("🤖 Нельзя передавать ладушки боту.")
        return

    if sender.id == receiver.id:
        await message.reply("❌ Нельзя дарить ладушки самому себе.")
        return

    register_user(sender)
    register_user(receiver)

    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id=? AND balance >= 1", (sender.id,))
            if cursor.rowcount == 0:
                await message.reply("❌ У вас нет ладушек.")
                return

            cursor.execute("UPDATE users SET balance = balance + 1 WHERE user_id=?", (receiver.id,))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка передачи ладушки: {e}")
            return

    save_db_changes()
    add_history(sender.id, receiver.id, 1, "transfer")

    sender_mention = get_user_mention(sender)
    receiver_mention = get_user_mention(receiver)

    await message.reply(
        f"🎁 <b>Ладушка передана!</b>\n\n"
        f"От: {sender_mention}\n"
        f"Кому: {receiver_mention}\n"
        f"Передано: <b>1 ладушка</b> 🪙\n\n"
        f"Теперь у вас {get_balance(sender.id)} ладушек.\n"
        f"У {receiver_mention} {get_balance(receiver.id)} ладушек.",
        disable_web_page_preview=True
    )


@dp.message(F.text.lower() == "топ богачей")
async def top_players(message: Message):
    with db_lock:
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

    text = "🏆 <b>Топ по ладушкам</b>\n\n"
    for i, row in enumerate(rows, start=1):
        uid, name, uname, bal = row
        url = f"https://t.me/{uname}" if uname else f"tg://user?id={uid}"
        text += f"{i}. <a href='{url}'>{name}</a> — 🪙 <b>{bal}</b>\n"

    await message.answer(text, disable_web_page_preview=True)


@dp.message(Command("history"))
async def history(message: Message):
    with db_lock:
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


# --- РЕПУТАЦИЯ ---

@dp.message(F.text.lower() == "+реп")
async def add_reputation(message: Message):
    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return

    target = message.reply_to_message.from_user

    if target.is_bot:
        await message.reply("🤖 Боту нельзя выдавать репутацию.")
        return

    register_user(target)

    with db_lock:
        cursor.execute("SELECT reputation FROM users WHERE user_id=?", (target.id,))
        row = cursor.fetchone()
        current_rep = row[0] if row and row[0] is not None else 0

        if current_rep >= 10:
            await message.reply("⭐ У этого игрока уже максимальная репутация (10).")
            return

        new_rep = current_rep + 1
        try:
            cursor.execute("UPDATE users SET reputation=? WHERE user_id=?", (new_rep, target.id))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка репутации: {e}")
            return

    save_db_changes()

    await message.reply(
        f"⭐ <b>Репутация выдана!</b>\n\n"
        f"👤 <b>Игрок:</b> {target.full_name}\n"
        f"➕ <b>Репутация:</b> +1"
    )


@dp.message(F.text.lower() == "-реп")
async def remove_reputation(message: Message):
    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return

    target = message.reply_to_message.from_user

    if target.is_bot:
        await message.reply("🤖 Боту нельзя выдавать репутацию.")
        return

    register_user(target)

    with db_lock:
        cursor.execute("SELECT reputation FROM users WHERE user_id=?", (target.id,))
        row = cursor.fetchone()
        current_rep = row[0] if row and row[0] is not None else 0

        if current_rep <= 0:
            await message.reply("⭐ У этого игрока уже минимальная репутация (0).")
            return

        new_rep = current_rep - 1
        try:
            cursor.execute("UPDATE users SET reputation=? WHERE user_id=?", (new_rep, target.id))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка репутации: {e}")
            return

    save_db_changes()

    await message.reply(
        f"⭐ <b>Репутация изменена!</b>\n\n"
        f"👤 <b>Игрок:</b> {target.full_name}\n"
        f"➖ <b>Репутация:</b> -1"
    )


@dp.message(F.text.lower() == "репутация")
async def top_reputation_handler(message: Message):
    with db_lock:
        cursor.execute("""
            SELECT full_name, reputation
            FROM users
            ORDER BY reputation DESC
            LIMIT 5
        """)
        rows = cursor.fetchall()

    if not rows:
        await message.answer("⭐ Топ по репутации пуст.")
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    text = "⭐ <b>Топ по репутации</b>\n\n"

    for idx, (full_name, rep) in enumerate(rows):
        emoji = medals[idx] if idx < len(medals) else f"{idx + 1}️⃣"
        text += f"{emoji} {full_name} — {rep} репутации\n"

    await message.answer(text)


# --- АДМИНСКИЕ КОМАНДЫ И ШТРАФЫ ---

@dp.message(F.text.lower() == "база")
async def show_users_database(message: Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Команда доступна только администраторам.")
        return

    with db_lock:
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users WHERE balance > 0")
        active_users = cursor.fetchone()[0]

        cursor.execute("""
            SELECT full_name, user_id, balance, reputation
            FROM users
            WHERE balance > 0
            ORDER BY balance DESC
        """)
        rows = cursor.fetchall()

    if not rows:
        await message.reply("📂 База данных пользователей пуста (нет участников с балансом больше 0).")
        return

    text = f"📊 <b>База пользователей (показано {active_users} из {total_users}):</b>\n\n"

    for idx, (full_name, user_id, balance, reputation) in enumerate(rows, start=1):
        text += (
            f"👤 <b>Имя:</b> {full_name}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"💰 <b>Баланс:</b> {balance} ладушек\n"
            f"⭐ <b>Репутация:</b> {reputation}\n"
            "───────────────\n"
        )

    await message.answer(text)


@dp.message(F.text.lower().startswith("штраф"))
async def fine_handler(message: Message):
    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("❌ Ответьте на сообщение игрока.")
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.reply("🤖 Бота нельзя штрафовать.")
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("⚠️ Укажите сумму штрафа числом. Пример: <code>штраф 10</code>")
        return

    fine_amount = int(parts[1])
    register_user(target)

    with db_lock:
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (target.id,))
        row = cursor.fetchone()
        current_bal = row[0] if row else 0

        if current_bal <= 0:
            await message.reply("🚔 Штраф не удалось взыскать.\n\n😅 У игрока нет ладушек для списания.")
            return

        if current_bal < fine_amount:
            deducted = current_bal
            try:
                cursor.execute("UPDATE users SET balance = 0 WHERE user_id=?", (target.id,))
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Ошибка списания штрафа: {e}")
                return
            save_db_changes()
            add_history(ADMIN_ID, target.id, deducted, "fine")

            await message.reply(
                f"🚔 Администратор выписал штраф.\n\n"
                f"👤 Игрок: {target.full_name}\n"
                f"💸 У игрока было только {current_bal} ладушек.\n\n"
                f"Списано: {deducted} ладушек\n\n"
                f"Баланс: 0 ладушек"
            )
        else:
            new_bal = current_bal - fine_amount
            try:
                cursor.execute("UPDATE users SET balance = ? WHERE user_id=?", (new_bal, target.id))
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Ошибка списания штрафа: {e}")
                return
            save_db_changes()
            add_history(ADMIN_ID, target.id, fine_amount, "fine")

            await message.reply(
                f"🚔 Администратор выписал штраф.\n\n"
                f"👤 Игрок: {target.full_name}\n"
                f"💸 Штраф: {fine_amount} ладушек\n\n"
                f"Новый баланс: {new_bal} ладушек"
            )


@dp.message(Command("add"))
async def add_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
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

    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user.id))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка изменения баланса: {e}")
            return

    save_db_changes()
    add_history(ADMIN_ID, user.id, amount, "add")
    user_link = get_user_mention(user)
    await message.answer(f"✅ {user_link} получил {amount} ладушек.", disable_web_page_preview=True)


@dp.message(Command("remove"))
async def remove_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return
    args = message.text.split()
    if len(args) != 2:
        return
    try:
        amount = int(args[1])
    except ValueError:
        return

    user = message.reply_to_message.from_user
    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance = MAX(balance-?,0) WHERE user_id=?", (amount, user.id))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка снятия баланса: {e}")
            return

    save_db_changes()
    add_history(ADMIN_ID, user.id, amount, "remove")
    await message.answer("✅ Ладушки сняты.")


@dp.message(Command("set"))
async def set_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return
    args = message.text.split()
    if len(args) != 2:
        return
    try:
        amount = int(args[1])
    except ValueError:
        return

    user = message.reply_to_message.from_user
    register_user(user)
    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user.id))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка установки баланса: {e}")
            return
    save_db_changes()

    await message.answer("✅ Баланс изменён.")


@dp.message(Command("admin_bonus"))
async def admin_bonus(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return
    args = message.text.split()
    if len(args) < 2:
        return
    try:
        amount = int(args[1])
    except ValueError:
        return

    user = message.reply_to_message.from_user
    register_user(user)
    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user.id))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка админ-бонуса: {e}")
            return
    
    save_db_changes()
    add_history(ADMIN_ID, user.id, amount, "admin_bonus")
    user_link = get_user_mention(user)
    await message.answer(f"🎁 Игрок {user_link} получил бонус {amount} ладушек.", disable_web_page_preview=True)


@dp.message(Command("reset"))
async def reset(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return
    user = message.reply_to_message.from_user
    with db_lock:
        try:
            cursor.execute("UPDATE users SET balance=0 WHERE user_id=?", (user.id,))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Ошибка сброса: {e}")
            return
    save_db_changes()

    await message.answer("♻️ Баланс игрока сброшен.")


# ==========================
# ВЕБ-СЕРВЕР И ФОНОВЫЕ ЗАДАЧИ
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


async def auto_backup_task():
    """Фоновая задача: каждые 30 минут делает резервную копию базы данных"""
    while True:
        await asyncio.sleep(1800)  # 30 минут (1800 секунд)
        try:
            await asyncio.to_thread(create_backup)
        except Exception as e:
            print(f"🔴 Ошибка во время выполнения автоматического бэкапа: {e}")


async def daily_ladushki_task():
    kyiv_tz = zoneinfo.ZoneInfo("Europe/Kyiv")
    
    while True:
        now = datetime.now(kyiv_tz)
        target_time = now.replace(hour=19, minute=0, second=0, microsecond=0)
        
        if now >= target_time:
            target_time += timedelta(days=1)
        
        wait_seconds = (target_time - now).total_seconds()
        print(f"Следующая отправка 'Ладушек' запланирована на {target_time.strftime('%d.%m.%Y %H:%M:%S')} (через {int(wait_seconds)} сек)")
        
        await asyncio.sleep(wait_seconds)
        
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


async def cleanup_db_task():
    """Очищает устаревшую временную историю операций (баланс, инвентарь и профили останутся навсегда)"""
    while True:
        try:
            with db_lock:
                cursor.execute("DELETE FROM inventory WHERE quantity <= 0")
                cutoff_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("DELETE FROM history WHERE date < ?", (cutoff_date,))
                db.commit()
            print("🧹 Очистка временных данных в базе завершена.")
            save_db_changes()
        except Exception as e:
            if db:
                db.rollback()
            print(f"Ошибка при автоматической очистке БД: {e}")

        await asyncio.sleep(86400)


async def on_shutdown():
    """Гарантирует загрузку последних изменений на GitHub при остановке/перезапуске бота"""
    global is_db_dirty
    if is_db_dirty:
        print("⏳ Загружаем последние локальные изменения в GitHub перед выключением...")
        await asyncio.to_thread(_sync_upload_single)


async def main():
    await init_db()
    await start_web_server()
    
    # Регистрируем хендлер завершения работы
    dp.shutdown.register(on_shutdown)
    
    # Первичный бэкап при старте бота
    await asyncio.to_thread(create_backup)
    
    # Запуск фоновых задач
    asyncio.create_task(_github_sync_worker())
    asyncio.create_task(auto_ping_task())
    asyncio.create_task(auto_backup_task())
    asyncio.create_task(daily_ladushki_task())
    asyncio.create_task(cleanup_db_task())
    
    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
