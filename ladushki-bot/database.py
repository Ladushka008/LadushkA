import aiosqlite
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import random
from config import Config
from github_storage import upload_database

# Словарь доступных титулов
TITLES = {
    "wood": {"name": "🪵 Деревянный ладушник", "price": 50},
    "bronze": {"name": "🥉 Бронзовый ладушник", "price": 200},
    "silver": {"name": "🥈 Серебряный ладушник", "price": 600},
    "gold": {"name": "🥇 Золотой ладушник", "price": 1000},
}


async def init_db() -> None:
    """Инициализация таблиц базы данных."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 0,
                last_bonus TEXT,
                created_at TEXT,
                reputation INTEGER DEFAULT 0,
                active_title TEXT DEFAULT NULL
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER,
                item_name TEXT,
                quantity INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, item_name),
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS titles (
                user_id INTEGER,
                title_key TEXT,
                PRIMARY KEY (user_id, title_key),
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender INTEGER,
                receiver INTEGER,
                amount INTEGER,
                action TEXT,
                reason TEXT,
                date TEXT
            );
        """)
        
        await db.commit()
        await upload_database()


async def ensure_user(user_id: int, username: Optional[str], full_name: str) -> None:
    """Гарантирует существование пользователя в БД и обновляет его имя/username."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, balance, last_bonus, created_at, reputation)
            VALUES (?, ?, ?, 0, NULL, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name;
        """, (user_id, username, full_name, now_str))
        await db.commit()
        await upload_database()


async def get_user_data(user_id: int) -> Optional[Tuple[int, Optional[str], str, int, Optional[str], str, int]]:
    """Возвращает полную запись пользователя."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute(
            "SELECT user_id, username, full_name, balance, last_bonus, created_at, reputation FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone()


async def get_balance(user_id: int) -> int:
    """Получить актуальный баланс напрямую из БД."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def update_balance(user_id: int, delta: int) -> int:
    """Атомарно изменяет баланс на delta. Возвращает итоговый баланс."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute(
            "UPDATE users SET balance = MAX(0, balance + ?) WHERE user_id = ?",
            (delta, user_id)
        )
        await db.commit()
        await upload_database()
        
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def set_balance(user_id: int, new_balance: int) -> int:
    """Устанавливает конкретное значение баланса."""
    target_bal = max(0, new_balance)
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("UPDATE users SET balance = ? WHERE user_id = ?", (target_bal, user_id))
        await db.commit()
        await upload_database()
        return target_bal


async def claim_daily_bonus(user_id: int) -> Tuple[bool, int, Optional[timedelta]]:
    """Проверяет и выдает ежедневный бонус (1-5 ладушек)."""
    now = datetime.now()
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT last_bonus, balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False, 0, None
            
            last_bonus_str, _ = row
            if last_bonus_str:
                last_bonus_time = datetime.fromisoformat(last_bonus_str)
                next_bonus_time = last_bonus_time + timedelta(hours=24)
                if now < next_bonus_time:
                    return False, 0, (next_bonus_time - now)

            reward = random.randint(1, 5)
            await db.execute(
                "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?",
                (reward, now.isoformat(), user_id)
            )
            await db.commit()
            await upload_database()
            return True, reward, None


async def transfer_balance(sender_id: int, receiver_id: int, amount: int) -> bool:
    """Атомарный перевод ладушек от одного пользователя другому."""
    if amount <= 0 or sender_id == receiver_id:
        return False

    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("BEGIN TRANSACTION;")
        try:
            async with db.execute("SELECT balance FROM users WHERE user_id = ?", (sender_id,)) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] < amount:
                    await db.execute("ROLLBACK;")
                    return False

            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, receiver_id))
            
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(
                "INSERT INTO history (sender, receiver, amount, action, reason, date) VALUES (?, ?, ?, 'transfer', '', ?)",
                (sender_id, receiver_id, amount, now_str)
            )
            await db.commit()
            await upload_database()
            return True
        except Exception:
            await db.execute("ROLLBACK;")
            return False


async def buy_item(user_id: int, item_name: str, price: int) -> bool:
    """Покупка предмета."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("BEGIN TRANSACTION;")
        try:
            async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] < price:
                    await db.execute("ROLLBACK;")
                    return False

            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))
            await db.execute("""
                INSERT INTO inventory (user_id, item_name, quantity)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, item_name) DO UPDATE SET quantity = quantity + 1;
            """, (user_id, item_name))
            
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(
                "INSERT INTO history (sender, receiver, amount, action, reason, date) VALUES (?, 0, ?, 'buy_item', ?, ?)",
                (user_id, price, item_name, now_str)
            )
            await db.commit()
            await upload_database()
            return True
        except Exception:
            await db.execute("ROLLBACK;")
            return False


async def use_item(user_id: int, item_name: str) -> bool:
    """Списывает 1 предмет из инвентаря."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name)
        ) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] <= 0:
                return False

        await db.execute("""
            UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_name = ?
        """, (user_id, item_name))
        await db.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ? AND quantity <= 0", (user_id, item_name))
        await db.commit()
        await upload_database()
        return True


async def get_inventory(user_id: int) -> List[Tuple[str, int]]:
    """Получает список предметов пользователя."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute(
            "SELECT item_name, quantity FROM inventory WHERE user_id = ? AND quantity > 0", (user_id,)
        ) as cursor:
            return await cursor.fetchall()


async def get_total_items(user_id: int) -> int:
    """Считает общее количество предметов у пользователя."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute(
            "SELECT SUM(quantity) FROM inventory WHERE user_id = ? AND quantity > 0", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] is not None else 0


# --- Функции титулов ---

async def get_user_titles(user_id: int) -> List[str]:
    """Возвращает список ключей титулов пользователя."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT title_key FROM titles WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def get_active_title_key(user_id: int) -> Optional[str]:
    """Возвращает ключ активного титула пользователя."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT active_title FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_active_title(user_id: int) -> Optional[str]:
    """Возвращает отображаемое имя активного титула."""
    key = await get_active_title_key(user_id)
    if key and key in TITLES:
        return TITLES[key]["name"]
    return None


async def buy_title(user_id: int, title_key: str, price: int) -> bool:
    """Покупка титула."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("BEGIN TRANSACTION;")
        try:
            async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] < price:
                    await db.execute("ROLLBACK;")
                    return False

            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))
            await db.execute("INSERT OR IGNORE INTO titles (user_id, title_key) VALUES (?, ?)", (user_id, title_key))
            await db.commit()
            await upload_database()
            return True
        except Exception:
            await db.execute("ROLLBACK;")
            return False


async def set_active_title(user_id: int, title_key: str) -> None:
    """Устанавливает активный титул."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("UPDATE users SET active_title = ? WHERE user_id = ?", (title_key, user_id))
        await db.commit()
        await upload_database()


# --- Остальные функции ---

async def change_reputation(user_id: int, delta: int) -> Tuple[bool, int]:
    """Изменяет репутацию в пределе от 0 до 10."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT reputation FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False, 0
            current_rep = row[0]

        new_rep = max(0, min(10, current_rep + delta))
        if new_rep == current_rep:
            return False, current_rep

        await db.execute("UPDATE users SET reputation = ? WHERE user_id = ?", (new_rep, user_id))
        await db.commit()
        await upload_database()
        return True, new_rep


async def get_top_rich() -> List[Tuple[int, str, Optional[str], int]]:
    """Топ 10 игроков по балансу."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("""
            SELECT user_id, full_name, username, balance
            FROM users
            ORDER BY balance DESC
            LIMIT 10
        """) as cursor:
            return await cursor.fetchall()


async def get_top_reputation() -> List[Tuple[str, int]]:
    """Топ 5 игроков по репутации."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("""
            SELECT full_name, reputation
            FROM users
            ORDER BY reputation DESC
            LIMIT 5
        """) as cursor:
            return await cursor.fetchall()


async def get_random_target_for_rat(sender_id: int) -> Optional[Tuple[int, str, int]]:
    """Возвращает случайную жертву для Крысы."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute(
            "SELECT user_id, full_name, balance FROM users WHERE user_id != ?", (sender_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return None
            return random.choice(rows)


async def execute_rat_steal(thief_id: int, victim_id: int, amount: int) -> int:
    """Выполняет кражу ладушек крысой."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("BEGIN TRANSACTION;")
        try:
            async with db.execute("SELECT balance FROM users WHERE user_id = ?", (victim_id,)) as cursor:
                row = await cursor.fetchone()
                victim_bal = row[0] if row else 0

            stolen = min(victim_bal, amount)
            if stolen > 0:
                await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (stolen, victim_id))
                await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (stolen, thief_id))
                
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await db.execute(
                    "INSERT INTO history (sender, receiver, amount, action, reason, date) VALUES (?, ?, ?, 'rat_steal', '', ?)",
                    (victim_id, thief_id, stolen, now_str)
                )
            await db.commit()
            await upload_database()
            return stolen
        except Exception:
            await db.execute("ROLLBACK;")
            return 0


async def get_all_active_users() -> Tuple[int, int, List[Tuple[str, int, int, int]]]:
    """Возвращает общую статистику и список активных пользователей."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c1:
            total = (await c1.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE balance > 0") as c2:
            active = (await c2.fetchone())[0]
        async with db.execute("""
            SELECT full_name, user_id, balance, reputation
            FROM users
            WHERE balance > 0
            ORDER BY balance DESC
        """) as c3:
            rows = await c3.fetchall()
        return total, active, rows


async def add_history_entry(sender: int, receiver: int, amount: int, action: str, reason: str = "") -> None:
    """Добавляет произвольную запись в историю."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute(
            "INSERT INTO history (sender, receiver, amount, action, reason, date) VALUES (?, ?, ?, ?, ?, ?)",
            (sender, receiver, amount, action, reason, now_str)
        )
        await db.commit()
        await upload_database()


async def get_user_history(user_id: int) -> List[Tuple[str, int, str]]:
    """Возвращает последние 10 операций пользователя."""
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("""
            SELECT action, amount, date
            FROM history
            WHERE sender = ? OR receiver = ?
            ORDER BY id DESC
            LIMIT 10
        """, (user_id, user_id)) as cursor:
            return await cursor.fetchall()
