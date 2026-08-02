import aiosqlite
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import random
from config import Config

TITLES = {
    "wood": {"name": "🪵 Деревянный ладушник", "clean_name": "деревянный ладушник", "price": 50},
    "bronze": {"name": "🥉 Бронзовый ладушник", "clean_name": "бронзовый ладушник", "price": 200},
    "silver": {"name": "🥈 Серебряный ладушник", "clean_name": "серебряный ладушник", "price": 600},
    "gold": {"name": "🥇 Золотой ладушник", "clean_name": "золотой ладушник", "price": 1000},
}


async def init_db() -> None:
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

        try:
            await db.execute("ALTER TABLE users ADD COLUMN active_title TEXT DEFAULT NULL;")
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER,
                item_name TEXT,
                quantity INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, item_name)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_titles (
                user_id INTEGER,
                title_key TEXT,
                PRIMARY KEY (user_id, title_key)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY DEFAULT 1,
                rules_text TEXT
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


async def ensure_user(user_id: int, username: Optional[str], full_name: str) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, balance, created_at)
            VALUES (?, ?, ?, 100, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
        """, (user_id, username, full_name, now_str))
        await db.commit()


async def get_user_data(user_id: int):
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute(
            "SELECT user_id, username, full_name, balance, last_bonus, created_at, reputation FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone()


async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def update_balance(user_id: int, amount: int) -> int:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
        return await get_balance(user_id)


async def set_balance(user_id: int, amount: int) -> None:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("UPDATE users SET balance = ? WHERE user_id = ?", (amount, user_id))
        await db.commit()


async def claim_daily_bonus(user_id: int) -> Tuple[bool, int, Optional[timedelta]]:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_bonus_str = row[0] if row else None

        now = datetime.now()
        if last_bonus_str:
            last_bonus = datetime.strptime(last_bonus_str, "%Y-%m-%d %H:%M:%S")
            if now - last_bonus < timedelta(hours=24):
                time_left = timedelta(hours=24) - (now - last_bonus)
                return False, 0, time_left

        reward = random.randint(50, 150)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?",
            (reward, now_str, user_id)
        )
        await db.commit()
        return True, reward, None


async def buy_item(user_id: int, item_name: str, price: int) -> bool:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] < price:
                return False

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))
        await db.execute("""
            INSERT INTO inventory (user_id, item_name, quantity)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, item_name) DO UPDATE SET quantity = quantity + 1
        """, (user_id, item_name))
        await db.commit()
        return True


async def use_item(user_id: int, item_name: str) -> bool:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name)) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] <= 0:
                return False

        await db.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_name = ?", (user_id, item_name))
        await db.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ? AND quantity <= 0", (user_id, item_name))
        await db.commit()
        return True


async def get_inventory(user_id: int) -> List[Tuple[str, int]]:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT item_name, quantity FROM inventory WHERE user_id = ? AND quantity > 0", (user_id,)) as cursor:
            return await cursor.fetchall()


async def get_total_items(user_id: int) -> int:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT SUM(quantity) FROM inventory WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if (row and row[0]) else 0


async def get_user_titles(user_id: int) -> List[str]:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT title_key FROM user_titles WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_active_title_key(user_id: int) -> Optional[str]:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT active_title FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_active_title(user_id: int) -> Optional[str]:
    key = await get_active_title_key(user_id)
    if key and key in TITLES:
        return TITLES[key]["name"]
    return None


async def buy_title(user_id: int, title_key: str, price: int) -> bool:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] < price:
                return False

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))
        await db.execute("INSERT OR IGNORE INTO user_titles (user_id, title_key) VALUES (?, ?)", (user_id, title_key))
        await db.commit()
        return True


async def set_active_title(user_id: int, title_key: str) -> None:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("UPDATE users SET active_title = ? WHERE user_id = ?", (title_key, user_id))
        await db.commit()


async def get_rules() -> Optional[str]:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT rules_text FROM rules WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_rules(text: str) -> None:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("INSERT INTO rules (id, rules_text) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET rules_text = excluded.rules_text", (text,))
        await db.commit()


async def change_reputation(user_id: int, delta: int) -> None:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("UPDATE users SET reputation = reputation + ? WHERE user_id = ?", (delta, user_id))
        await db.commit()


async def get_random_user(exclude_id: int) -> Optional[int]:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id != ?", (exclude_id,)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return None
            return random.choice(rows)[0]


async def get_top_reputation() -> List[Tuple[str, int]]:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        async with db.execute("SELECT full_name, reputation FROM users ORDER BY reputation DESC LIMIT 5") as cursor:
            return await cursor.fetchall()


async def add_history_entry(sender: int, receiver: int, amount: int, action: str, reason: str = "") -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute(
            "INSERT INTO history (sender, receiver, amount, action, reason, date) VALUES (?, ?, ?, ?, ?, ?)",
            (sender, receiver, amount, action, reason, now_str)
        )
        await db.commit()


async def reset_user(user_id: int) -> None:
    async with aiosqlite.connect(Config.DB_FILE) as db:
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM inventory WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_titles WHERE user_id = ?", (user_id,))
        await db.commit()
