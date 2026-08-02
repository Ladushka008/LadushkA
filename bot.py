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
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ==========================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# ==========================
# КОНФИГУРАЦИЯ
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

# ==========================================
# СИСТЕМА ДИАГНОСТИКИ И ТРЕКИНГА БАЛАНСОВ
# ==========================================

KNOWN_BALANCES = {}

EVENT_TRACKER = {
    "sync_download_called": False,
    "last_sync_download_time": "Никогда",
    "sync_upload_called": False,
    "last_sync_upload_time": "Никогда",
    "sqlite_connect_called": False,
    "last_sqlite_connect_time": "Никогда"
}

LAST_DB_ACTION = {
    "function": "None",
    "timestamp": "None",
    "user_id": None,
    "details": ""
}


def get_file_hash(filepath: str) -> str:
    """Вычисляет SHA256 хеш файла database.db"""
    if not os.path.exists(filepath):
        return "FILE_NOT_FOUND"
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def track_sqlite_connect():
    """Фиксирует подключение к базе через sqlite3.connect"""
    EVENT_TRACKER["sqlite_connect_called"] = True
    EVENT_TRACKER["last_sqlite_connect_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def update_db_meta():
    """Обновляет метаданные БД (время и максимальный ID истории)"""
    if not db:
        return
    try:
        now_str = datetime.now().isoformat()
        cur = db.cursor()
        cur.execute("SELECT MAX(id) FROM history;")
        row = cur.fetchone()
        max_id = row[0] if row and row[0] is not None else 0

        cur.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES ('last_modified', ?);", (now_str,))
        cur.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES ('max_op_id', ?);", (str(max_id),))
    except Exception as e:
        logging.error(f"⚠️ Ошибка обновления метаданных БД: {e}")


def log_db_commit(caller_name: str):
    """Выполняет commit() с фиксированием метаданных и SHA256"""
    global LAST_DB_ACTION
    if db:
        update_db_meta()
        db.commit()
        db_hash = get_file_hash(DB_FILE)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        LAST_DB_ACTION["function"] = caller_name
        LAST_DB_ACTION["timestamp"] = now_str
        logging.info(f"💾 [COMMIT] Вызван из: {caller_name} в {now_str} | SHA256: {db_hash[:12]}")


def change_user_balance(user_id: int, new_balance: int, caller_function: str, reason: str = "") -> int:
    """Единая точка изменения баланса."""
    global KNOWN_BALANCES
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with db_lock:
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        old_balance = row[0] if row else 0

        cursor.execute("UPDATE users SET balance = ? WHERE user_id=?", (new_balance, user_id))
        log_db_commit(caller_function)

        KNOWN_BALANCES[user_id] = new_balance

    db_hash = get_file_hash(DB_FILE)
    logging.info(
        f"\n📝 [BALANCE_UPDATE_LOG]\n"
        f"├─ Название функции: {caller_function}\n"
        f"├─ User ID: {user_id}\n"
        f"├─ Баланс ДО: {old_balance}\n"
        f"├─ Баланс ПОСЛЕ: {new_balance}\n"
        f"├─ Точное время: {now_str}\n"
        f"├─ Причина: {reason}\n"
        f"└─ SHA256 DB: {db_hash[:12]}"
    )

    save_db_changes()
    
    asyncio.create_task(verify_balance_after_delay(user_id, new_balance, caller_function, delay=600))
    return old_balance


async def verify_balance_after_delay(user_id: int, expected_bal: int, original_caller: str, delay: int = 600):
    await asyncio.sleep(delay)
    current_bal = get_balance(user_id)
    current_hash = get_file_hash(DB_FILE)

    if current_bal != expected_bal:
        logging.critical(
            f"\n🚨 [ALERT! ОБНАРУЖЕН РАССИНХРОН БАЛАНСА ЧЕРЕЗ 10 МИНУТ!] 🚨\n"
            f"├─ User ID: {user_id}\n"
            f"├─ Ожидаемый баланс: {expected_bal}\n"
            f"├─ Фактический баланс: {current_bal}\n"
            f"├─ Исходная функция изменения: {original_caller}\n"
            f"├─ Последняя функция commit(): {LAST_DB_ACTION['function']} ({LAST_DB_ACTION['timestamp']})\n"
            f"└─ SHA256 DB: {current_hash[:12]}"
        )
    else:
        logging.info(
            f"✅ [10-MIN VERIFY OK] Баланс User ID {user_id} корректен ({current_bal}). SHA256: {current_hash[:12]}"
        )


async def balance_checker_task():
    global KNOWN_BALANCES
    logging.info("🔍 [MONITOR] Автоматический монитор балансов запущен.")

    while True:
        await asyncio.sleep(30)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with db_lock:
            try:
                cursor.execute("SELECT user_id, balance FROM users")
                rows = cursor.fetchall()
            except Exception as e:
                logging.error(f"🔴 Ошибка чтения БД во время мониторинга: {e}")
                continue

        current_db_hash = get_file_hash(DB_FILE)

        for user_id, current_bal in rows:
            if user_id in KNOWN_BALANCES:
                expected_bal = KNOWN_BALANCES[user_id]
                if current_bal != expected_bal:
                    logging.critical(
                        f"\n🚨🚨🚨 [ALERT! РАССИНХРОН/ОТКАТ БАЛАНСА!] 🚨🚨🚨\n"
                        f"├─ User ID: {user_id}\n"
                        f"├─ Ожидалось (в кэше): {expected_bal}\n"
                        f"├─ Найдено (в БД): {current_bal}\n"
                        f"├─ Время: {now_str}\n"
                        f"└─ SHA256 DB: {current_db_hash}"
                    )
            KNOWN_BALANCES[user_id] = current_bal


# ==========================
# GITHUB СИНХРОНИЗАЦИЯ (ТОЛЬКО РЕЗЕРВНОЕ КОПИРОВАНИЕ)
# ==========================

def _sync_upload_single():
    """Отправка локальной БД на GitHub в качестве бэкапа"""
    EVENT_TRACKER["sync_upload_called"] = True
    EVENT_TRACKER["last_sync_upload_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False

    with db_lock:
        if not os.path.exists(DB_FILE):
            logging.error("🔴 [_sync_upload_single] Отмена: Файл database.db не существует.")
            return False

        if db:
            try:
                # ВАЖНО: Фиксируем все WAL-данные в основной файл перед отправкой
                cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                db.commit()
            except Exception as e:
                logging.warning(f"⚠️ [_sync_upload_single] Ошибка wal_checkpoint: {e}")

        file_hash = get_file_hash(DB_FILE)
        with open(DB_FILE, "rb") as f:
            content_bytes = f.read()

    logging.info(f"📤 [_sync_upload_single] Отправка бэкапа базы на GitHub... SHA256: {file_hash[:12]}")

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            content_b64 = base64.b64encode(content_bytes).decode("utf-8")
            sha = None

            get_resp = requests.get(url, headers=headers, params={"ref": BRANCH}, timeout=10)
            if get_resp.status_code == 200:
                sha = get_resp.json().get("sha")

            data = {
                "message": f"Backup database.db [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] SHA256: {file_hash[:8]}",
                "content": content_b64,
                "branch": BRANCH
            }
            if sha:
                data["sha"] = sha

            put_resp = requests.put(url, headers=headers, json=data, timeout=15)
            if put_resp.status_code in [200, 201]:
                logging.info(f"🟢 [_sync_upload_single] Бэкап отправлен на GitHub! SHA256: {file_hash[:12]}")
                return True
            else:
                logging.warning(f"⚠️ [_sync_upload_single] Ответ GitHub: {put_resp.status_code} (попытка {attempt}/{max_retries})")
        except Exception as e:
            logging.error(f"⚠️ [_sync_upload_single] Ошибка отправки: {e} (попытка {attempt}/{max_retries})")

        import time
        time.sleep(2 * attempt)

    return False


async def _github_sync_worker():
    global is_db_dirty
    while True:
        await sync_event.wait()
        sync_event.clear()

        success = await asyncio.to_thread(_sync_upload_single)
        if success:
            is_db_dirty = False

        if is_db_dirty:
            sync_event.set()


def save_db_changes():
    global is_db_dirty
    is_db_dirty = True
    sync_event.set()


# ==========================
# БЭКАПЫ И ЗАЩИЩЕННОЕ ВОССТАНОВЛЕНИЕ
# ==========================

def get_db_info(db_path: str):
    """
    Извлекает метаданные базы данных (max_op_id и last_modified) для сравнения версий.
    """
    if not os.path.exists(db_path):
        return None, None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Проверка целостности
        res = cur.execute("PRAGMA quick_check;").fetchone()
        if not res or res[0] != "ok":
            conn.close()
            return None, None

        max_op_id = 0
        try:
            cur.execute("SELECT MAX(id) FROM history;")
            r = cur.fetchone()
            if r and r[0] is not None:
                max_op_id = r[0]
        except Exception:
            pass

        last_modified = ""
        try:
            cur.execute("SELECT value FROM db_meta WHERE key='last_modified';")
            r = cur.fetchone()
            if r and r[0]:
                last_modified = r[0]
        except Exception:
            pass

        conn.close()
        return max_op_id, last_modified
    except Exception as e:
        logging.error(f"🔴 Ошибка при получении метаданных БД ({db_path}): {e}")
        return None, None


def create_backup():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")

    with db_lock:
        if db:
            try:
                # ВАЖНО: Выполняем wal_checkpoint перед бэкапом
                cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                db.commit()

                bck = sqlite3.connect(backup_file)
                db.backup(bck)
                bck.close()
                logging.info(f"🟢 Резервная копия создана: {backup_file}")
            except Exception as e:
                logging.error(f"🔴 Ошибка бэкапа SQLite: {e}")
                return

    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*.db")), key=os.path.getmtime)
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        try:
            os.remove(oldest)
            logging.info(f"🧹 Удален старый бэкап: {oldest}")
        except Exception as e:
            logging.error(f"🔴 Ошибка удаления бэкапа {oldest}: {e}")


def check_and_restore_db():
    """
    Безопасная проверка и восстановление БД только при критическом повреждении текущей базы,
    с обязательной защитой от отката (Rollback Protection).
    """
    if not os.path.exists(DB_FILE):
        logging.info("ℹ️ Файл базы данных не найден на диске. Будет создана новая БД.")
        return

    curr_max_id, curr_modified = get_db_info(DB_FILE)
    if curr_max_id is not None:
        logging.info(f"🟢 Текущая БД исправна (Max Op ID: {curr_max_id}, Modified: {curr_modified}). Восстановление не требуется.")
        return

    logging.critical("⚠️ Обнаружено повреждение основной базы данных! Поиск подходящего бэкапа...")
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*.db")), key=os.path.getmtime, reverse=True)
    
    best_backup = None
    best_max_id = -1

    for bck in backups:
        bck_max_id, bck_modified = get_db_info(bck)
        if bck_max_id is not None:
            if bck_max_id > best_max_id:
                best_max_id = bck_max_id
                best_backup = bck

    if best_backup:
        shutil.copy(best_backup, DB_FILE)
        logging.info(f"🟢 База данных успешно восстановлена из бэкапа: {best_backup} (Max Op ID: {best_max_id})")
    else:
        logging.error("🔴 Ни один исправный бэкап не найден. Файл будет создан с нуля.")
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)


async def init_db():
    global db, cursor
    # 1. Проверяем локальную целостность (без автоматического скачивания с GitHub)
    await asyncio.to_thread(check_and_restore_db)

    track_sqlite_connect()
    db = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = db.cursor()

    with db_lock:
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            
            # Мета-таблица для контроля версий
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS db_meta(
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """)

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
            log_db_commit("init_db")

            cursor.execute("SELECT user_id, balance FROM users")
            for u_id, bal in cursor.fetchall():
                KNOWN_BALANCES[u_id] = bal

        except Exception as e:
            db.rollback()
            logging.error(f"🔴 Ошибка при инициализации таблиц: {e}")


# ==========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================

def is_user_registered(user_id: int) -> bool:
    with db_lock:
        cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None


def ensure_user_registered(user):
    if not user or user.is_bot:
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        try:
            cursor.execute(
                """
                INSERT INTO users(user_id, username, full_name, balance, last_bonus, created_at, reputation)
                VALUES(?, ?, ?, 0, NULL, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name
                """,
                (user.id, user.username, user.full_name, now_str)
            )
            log_db_commit("ensure_user_registered")
            if user.id not in KNOWN_BALANCES:
                KNOWN_BALANCES[user.id] = 0
        except Exception as e:
            db.rollback()
            logging.error(f"Ошибка регистрации пользователя: {e}")


def register_user(user):
    ensure_user_registered(user)


def get_balance(user_id: int) -> int:
    with db_lock:
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    return row[0] if row else 0


def get_reputation(user_id: int) -> int:
    with db_lock:
        cursor.execute("SELECT reputation FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    return row[0] if row and row[0] is not None else 0


def get_user_mention(user):
    if not user:
        return "Неизвестный"
    url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    return f'<a href="{url}">{user.full_name}</a>'


def get_total_items_count(user_id: int) -> int:
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
                (sender, receiver, amount, action, reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            log_db_commit("add_history")
        except Exception as e:
            db.rollback()
            logging.error(f"Ошибка логирования истории: {e}")
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
            log_db_commit("add_item")
        except Exception as e:
            db.rollback()
            logging.error(f"Ошибка добавления предмета: {e}")
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
            log_db_commit("remove_item")
        except Exception as e:
            db.rollback()
            logging.error(f"Ошибка удаления предмета: {e}")
            return
    save_db_changes()


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
        "• Напишите <b>баскетбол 50</b> — сыграть в баскетбольную мини-игру 🏀\n"
        "• Напишите <b>магазин</b> — чтобы открыть магазин предметов.\n"
        "• Напишите <b>инвентарь</b> — чтобы посмотреть свои предметы.\n"
        "• Напишите <b>репутация</b> — чтобы увидеть ТОП-5 по репутации.\n"
        "• Напишите <b>крыса</b> — запустить крысу украсть ладушки у случайного игрока.\n"
        "• Ответьте на сообщение текстом <b>подарок</b> или <b>ладошка</b> — чтобы передать 1 ладушку игроку.\n"
        "• Ответьте на сообщение текстом <b>дать 50</b> — чтобы перевести ладушки.\n"
        "• Ответьте на сообщение текстом <b>ударить ладушкой</b> — применить Боевую ладушку.\n"
        "• Ответьте на сообщение текстом <b>кинуть томат</b> — бросить томат в участника."
    )


# --- МИНИ-ИГРА БАСКЕТБОЛ ---

@dp.message(Command("basketball"))
@dp.message(F.text.lower().startswith("баскетбол"))
@dp.message(F.text.lower().startswith("баскет"))
async def basketball_game(message: Message):
    user = message.from_user
    ensure_user_registered(user)

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("⚠️ Укажите сумму числом. Пример: <code>баскетбол 50</code>")
        return

    amount = int(parts[1])
    if amount <= 0:
        await message.reply("❌ Сумма должна быть больше 0.")
        return

    current_balance = get_balance(user.id)
    if current_balance < amount:
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_balance}</b> ладушек")
        return

    dice_msg = await message.answer_dice(emoji="🏀")
    score = dice_msg.dice.value

    await asyncio.sleep(3.5)

    if score >= 4:
        reward = int(amount * 0.3)
        new_bal = current_balance + reward
        change_user_balance(user.id, new_bal, "basketball_game_win", f"Выигрыш в баскетбол +{reward}")

        add_history(0, user.id, reward, "basketball_win")
        await message.reply(
            f"🏀 <b>ТОЧНЫЙ БРОСОК!</b>\n\n"
            f"🎉 Попадание в корзину!\n"
            f"💰 Вы получили: <b>+{reward}</b> ладушек 👏\n"
            f"🪙 Ваш баланс: <b>{new_bal}</b>"
        )
    else:
        loss = amount
        new_bal = max(0, current_balance - loss)
        change_user_balance(user.id, new_bal, "basketball_game_loss", f"Проигрыш в баскетбол -{loss}")

        add_history(user.id, 0, loss, "basketball_loss")
        await message.reply(
            f"🏀 Бросок…\n"
            f"😔 Промах!\n"
            f"💸 Потеряно: <b>{loss}</b> ладушек.\n"
            f"🪙 Текущий баланс: <b>{new_bal}</b> ладушек."
        )


@dp.message(F.text.lower() == "профиль")
async def profile_handler(message: Message):
    ensure_user_registered(message.from_user)

    target = message.reply_to_message.from_user if (message.reply_to_message and message.reply_to_message.from_user) else message.from_user
    ensure_user_registered(target)

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
    ensure_user_registered(message.from_user)

    target = message.from_user
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        ensure_user_registered(target)

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
    ensure_user_registered(user)

    now = datetime.now()

    with db_lock:
        cursor.execute("SELECT last_bonus, balance FROM users WHERE user_id=?", (user.id,))
        row = cursor.fetchone()
        last_bonus_str = row[0] if row else None
        current_bal = row[1] if row else 0

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
        new_bal = current_bal + reward

        try:
            cursor.execute("UPDATE users SET last_bonus = ? WHERE user_id=?", (now.isoformat(), user.id))
            log_db_commit("get_daily_bonus_time_update")
        except Exception as e:
            db.rollback()
            logging.error(f"Ошибка сохранения даты бонуса: {e}")
            return

    change_user_balance(user.id, new_bal, "get_daily_bonus", f"Ежедневный бонус +{reward}")
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
    ensure_user_registered(user)
    price = 200
    current_bal = get_balance(user.id)

    if current_bal < price:
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_bal}</b> ладушек")
        return

    new_bal = current_bal - price
    change_user_balance(user.id, new_bal, "buy_battle_ladushka", f"Покупка Боевая ладушка за {price}")
    
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
    ensure_user_registered(user)
    price = 100
    current_bal = get_balance(user.id)

    if current_bal < price:
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_bal}</b> ладушек")
        return

    new_bal = current_bal - price
    change_user_balance(user.id, new_bal, "buy_tomato", f"Покупка Томата за {price}")

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
    ensure_user_registered(user)
    price = 250
    current_bal = get_balance(user.id)

    if current_bal < price:
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_bal}</b> ладушек")
        return

    new_bal = current_bal - price
    change_user_balance(user.id, new_bal, "buy_rat", f"Покупка Крысы за {price}")

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
    ensure_user_registered(user)

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
    ensure_user_registered(message.from_user)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    sender = message.from_user
    receiver = message.reply_to_message.from_user
    ensure_user_registered(receiver)

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
    await message.reply(random.choice(phrases), disable_web_page_preview=True)


@dp.message(F.text.lower() == "кинуть томат")
async def throw_tomato(message: Message):
    ensure_user_registered(message.from_user)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    sender = message.from_user
    receiver = message.reply_to_message.from_user
    ensure_user_registered(receiver)

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
    await message.reply(random.choice(phrases), disable_web_page_preview=True)


@dp.message(F.text.lower() == "крыса")
async def use_rat(message: Message):
    sender = message.from_user
    ensure_user_registered(sender)

    count = get_item_quantity(sender.id, "rat")
    if count <= 0:
        await message.reply("❌ У вас нет крысы.")
        return

    with db_lock:
        cursor.execute("SELECT user_id, full_name, balance FROM users WHERE user_id != ?", (sender.id,))
        targets = cursor.fetchall()

    if not targets:
        await message.reply("❌ Недостаточно зарегистрированных игроков в группе.")
        return

    target_id, target_name, target_bal = random.choice(targets)
    remove_item(sender.id, "rat", 1)

    wanted_steal = random.randint(1, 3)

    if target_bal <= 0:
        await message.reply(f"🐀 Крыса обыскала карманы {target_name}...\n\n😢 Но там не оказалось ни одной ладушки.")
    else:
        stolen = min(target_bal, wanted_steal)
        
        target_new_bal = target_bal - stolen
        sender_old_bal = get_balance(sender.id)
        sender_new_bal = sender_old_bal + stolen

        change_user_balance(target_id, target_new_bal, "use_rat_victim", f"Кража крысой (-{stolen})")
        change_user_balance(sender.id, sender_new_bal, "use_rat_thief", f"Кража крысой (+{stolen})")

        add_history(target_id, sender.id, stolen, "rat_steal")

        await message.reply(f"🐀 Крыса пробралась к {target_name}!\n\n💸 Украдено: {stolen} ладушки")


# --- ДЕНЕЖНЫЕ ПЕРЕВОДЫ И ПОДАРКИ ---

@dp.message(F.text.lower().startswith("дать "))
async def transfer_custom_amount(message: Message):
    sender = message.from_user
    ensure_user_registered(sender)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    receiver = message.reply_to_message.from_user
    ensure_user_registered(receiver)

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("⚠️ Укажите сумму числом. Пример: <code>дать 50</code>")
        return

    amount = int(parts[1])
    if amount <= 0:
        await message.reply("❌ Сумма перевода должна быть больше 0.")
        return

    if sender.id == receiver.id:
        await message.reply("❌ Нельзя переводить ладушки самому себе.")
        return

    sender_bal = get_balance(sender.id)
    if sender_bal < amount:
        await message.reply(f"❌ <b>Недостаточно средств!</b>\nУ вас на балансе: <b>{sender_bal}</b> ладушек.")
        return

    receiver_bal = get_balance(receiver.id)

    sender_new_bal = sender_bal - amount
    receiver_new_bal = receiver_bal + amount

    change_user_balance(sender.id, sender_new_bal, "transfer_custom_amount_sender", f"Перевод {amount} -> {receiver.id}")
    change_user_balance(receiver.id, receiver_new_bal, "transfer_custom_amount_receiver", f"Перевод {amount} <- {sender.id}")

    add_history(sender.id, receiver.id, amount, "transfer")

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
    ensure_user_registered(sender)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Ответьте на сообщение игрока, чтобы передать ладушку!")
        return

    receiver = message.reply_to_message.from_user
    if receiver.is_bot:
        await message.reply("🤖 Нельзя передавать ладушки боту.")
        return

    ensure_user_registered(receiver)

    if sender.id == receiver.id:
        await message.reply("❌ Нельзя дарить ладушки самому себе.")
        return

    sender_bal = get_balance(sender.id)
    if sender_bal < 1:
        await message.reply("❌ У вас нет ладушек.")
        return

    receiver_bal = get_balance(receiver.id)

    change_user_balance(sender.id, sender_bal - 1, "transfer_one_ladushka_sender", "Подарок 1 ладушка")
    change_user_balance(receiver.id, receiver_bal + 1, "transfer_one_ladushka_receiver", "Подарок 1 ладушка")

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
        await message.answer("Пока нет зарегистрированных участников.")
        return

    text = "🏆 <b>Топ по ладушкам</b>\n\n"
    for i, row in enumerate(rows, start=1):
        uid, name, uname, bal = row
        url = f"https://t.me/{uname}" if uname else f"tg://user?id={uid}"
        text += f"{i}. <a href='{url}'>{name}</a> — 🪙 <b>{bal}</b>\n"

    await message.answer(text, disable_web_page_preview=True)


@dp.message(Command("history"))
async def history(message: Message):
    ensure_user_registered(message.from_user)

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
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        return

    ensure_user_registered(target)

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
            log_db_commit("add_reputation")
        except Exception as e:
            db.rollback()
            logging.error(f"Ошибка репутации: {e}")
            return

    save_db_changes()
    await message.reply(f"⭐ <b>Репутация выдана!</b>\n\n👤 <b>Игрок:</b> {target.full_name}\n➕ <b>Репутация:</b> +1")


@dp.message(F.text.lower() == "-реп")
async def remove_reputation(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        return

    ensure_user_registered(target)

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
            log_db_commit("remove_reputation")
        except Exception as e:
            db.rollback()
            logging.error(f"Ошибка репутации: {e}")
            return

    save_db_changes()
    await message.reply(f"⭐ <b>Репутация изменена!</b>\n\n👤 <b>Игрок:</b> {target.full_name}\n➖ <b>Репутация:</b> -1")


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


# --- АДМИНСКИЕ КОМАНДЫ ---

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
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        return

    ensure_user_registered(target)

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("⚠️ Укажите сумму штрафа числом.")
        return

    fine_amount = int(parts[1])
    current_bal = get_balance(target.id)

    if current_bal <= 0:
        await message.reply("🚔 Штраф не удалось взыскать: у игрока 0 ладушек.")
        return

    if current_bal < fine_amount:
        deducted = current_bal
        new_bal = 0
    else:
        deducted = fine_amount
        new_bal = current_bal - fine_amount

    change_user_balance(target.id, new_bal, "fine_handler", f"Админ выписал штраф {deducted}")
    add_history(ADMIN_ID, target.id, deducted, "fine")

    await message.reply(
        f"🚔 Администратор выписал штраф.\n\n"
        f"👤 Игрок: {target.full_name}\n"
        f"💸 Списано: {deducted} ладушек\n"
        f"Новый баланс: {new_bal} ладушек"
    )


@dp.message(Command("add"))
async def add_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    user = message.reply_to_message.from_user
    ensure_user_registered(user)

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Использование: /add 10")
        return

    amount = int(args[1])
    old_bal = get_balance(user.id)
    new_bal = old_bal + amount

    change_user_balance(user.id, new_bal, "add_balance", f"Начисление админом {amount}")
    add_history(ADMIN_ID, user.id, amount, "add")

    user_link = get_user_mention(user)
    await message.answer(f"✅ {user_link} получил {amount} ладушек.", disable_web_page_preview=True)


@dp.message(Command("remove"))
async def remove_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    user = message.reply_to_message.from_user
    ensure_user_registered(user)

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        return

    amount = int(args[1])
    old_bal = get_balance(user.id)
    new_bal = max(0, old_bal - amount)

    change_user_balance(user.id, new_bal, "remove_balance", f"Списание админом {amount}")
    add_history(ADMIN_ID, user.id, amount, "remove")

    await message.answer(f"✅ Ладушки сняты. Старый: {old_bal} -> Новый: {new_bal}")


@dp.message(Command("set"))
async def set_balance(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    user = message.reply_to_message.from_user
    ensure_user_registered(user)

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        return

    amount = int(args[1])
    change_user_balance(user.id, amount, "set_balance", f"Установка баланса админом: {amount}")
    await message.answer("✅ Баланс изменён.")


@dp.message(Command("admin_bonus"))
async def admin_bonus(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    user = message.reply_to_message.from_user
    ensure_user_registered(user)

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return

    amount = int(args[1])
    old_bal = get_balance(user.id)
    new_bal = old_bal + amount

    change_user_balance(user.id, new_bal, "admin_bonus", f"Бонус от админа {amount}")
    add_history(ADMIN_ID, user.id, amount, "admin_bonus")

    user_link = get_user_mention(user)
    await message.answer(f"🎁 Игрок {user_link} получил бонус {amount} ладушек.", disable_web_page_preview=True)


@dp.message(Command("reset"))
async def reset(message: Message):
    if not is_admin(message.from_user.id) or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    user = message.reply_to_message.from_user
    ensure_user_registered(user)

    change_user_balance(user.id, 0, "reset", "Сброс баланса админом в 0")
    await message.answer("♻️ Баланс игрока сброшен.")


# ==========================
# ВЕБ-СЕРВЕР И ФОНОВЫЕ ЗАДАЧИ
# ==========================

async def handle_ping(request):
    return web.Response(text="Bot is running with reliable local SQLite storage!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Web server started on port {PORT}")


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
                            logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] Auto-ping success: {ping_url}")
                            success = True
                            break
                except Exception as e:
                    logging.warning(f"Auto-ping attempt {attempt} error: {e}")
                
                if not success and attempt == 1:
                    await asyncio.sleep(5)

            await asyncio.sleep(240)


async def auto_backup_task():
    while True:
        await asyncio.sleep(1800)
        try:
            await asyncio.to_thread(create_backup)
        except Exception as e:
            logging.error(f"🔴 Ошибка автоматического бэкапа: {e}")


async def daily_ladushki_task():
    kyiv_tz = zoneinfo.ZoneInfo("Europe/Kyiv")
    while True:
        now = datetime.now(kyiv_tz)
        target_time = now.replace(hour=19, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)
        
        wait_seconds = (target_time - now).total_seconds()
        logging.info(f"Следующая отправка 'Ладушек' запланирована на {target_time.strftime('%d.%m.%Y %H:%M:%S')}")
        await asyncio.sleep(wait_seconds)
        
        try:
            text = (
                "👏 <b>19:00 — Начинаем ладушки!</b> 👏\n\n"
                "🎵 <i>Ладушки, ладушки, где были? У бабушки!</i> 🎵"
            )
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=text)
            logging.info("Ежедневное сообщение 'Ладушки' отправлено!")
        except Exception as e:
            logging.error(f"Ошибка ежедневного сообщения: {e}")


async def cleanup_db_task():
    while True:
        try:
            with db_lock:
                cursor.execute("DELETE FROM inventory WHERE quantity <= 0")
                cutoff_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("DELETE FROM history WHERE date < ?", (cutoff_date,))
                log_db_commit("cleanup_db_task")
            logging.info("🧹 Очистка устаревших данных завершена.")
            save_db_changes()
        except Exception as e:
            if db:
                db.rollback()
            logging.error(f"Ошибка при автоочистке БД: {e}")
        await asyncio.sleep(86400)


async def on_shutdown():
    global is_db_dirty
    if is_db_dirty:
        logging.info("⏳ Загружаем последние изменения в GitHub перед остановкой...")
        await asyncio.to_thread(_sync_upload_single)


async def main():
    abs_db_path = os.path.abspath(DB_FILE)
    logging.info(f"📍 [ENV_CHECK] Абсолютный путь к базе данных: {abs_db_path}")
    logging.info(f"📍 [ENV_CHECK] Файл database.db существует до инициализации: {os.path.exists(DB_FILE)}")
    if os.path.exists(DB_FILE):
        logging.info(f"📍 [ENV_CHECK] Начальный SHA256 DB: {get_file_hash(DB_FILE)[:12]}")

    await init_db()
    await start_web_server()
    
    dp.shutdown.register(on_shutdown)
    await asyncio.to_thread(create_backup)
    
    # Регистрация фоновых задач
    asyncio.create_task(_github_sync_worker())
    asyncio.create_task(balance_checker_task())
    asyncio.create_task(auto_ping_task())
    asyncio.create_task(auto_backup_task())
    asyncio.create_task(daily_ladushki_task())
    asyncio.create_task(cleanup_db_task())
    
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот с локальной безопасной БД запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
