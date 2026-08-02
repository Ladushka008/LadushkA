import asyncio
import random
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
import database as db

router = Router()


class RulesState(StatesGroup):
    waiting_for_rules = State()


class TitleBuyCB(CallbackData, prefix="buy_title"):
    action: str
    key: str


class TitleSelectCB(CallbackData, prefix="select_title"):
    key: str


def get_mention(user) -> str:
    url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    return f'<a href="{url}">{user.full_name}</a>'


@router.message(F.text.lower() == "бот")
async def bot_reply(message: Message):
    await message.reply("Тут я, тут")


@router.message(F.text.lower() == "минута ладушек")
async def ladushka_minute_handler(message: Message):
    await message.answer("👏 Минута ладушек проходит каждый день в 19:00 по киевскому времени")


@router.message(Command("start"))
async def cmd_start(message: Message):
    await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "✨ <b>Добро пожаловать в бота сообщества Ладушки!</b>\n\n"
        "💬 <b>Основные команды:</b>\n"
        "• <b>профиль</b> — посмотреть свой профиль\n"
        "• <b>баланс</b> — узнать счет\n"
        "• <b>бонус</b> — ежедневный бонус\n"
        "• <b>правила</b> — прочитать правила группы\n"
        "• <b>титулы</b> — магазин титулов\n"
        "• <b>мои титулы</b> — выбор титула\n"
        "• <b>магазин</b> — магазин предметов\n"
        "• <b>инвентарь</b> — ваш инвентарь\n"
        "• <b>репутация</b> — топ участников\n"
        "• <b>крыса</b> — запустить крысу\n\n"
        "🥊 <b>Боевые команды (ответом на сообщение):</b>\n"
        "• <b>ударить ладушкой</b> — применить Боевую ладушку\n"
        "• <b>кинуть томат</b> — бросить томат"
    )


# --- ПРАВИЛА ---

@router.message(F.text.lower().in_(["правила", "права", "📜 правила"]))
async def rules_handler(message: Message):
    rules_text = await db.get_rules()
    if rules_text:
        await message.answer(f"📜 <b>Правила группы:</b>\n\n{rules_text}")
    else:
        await message.answer("📜 <b>Правила</b>\n\nПравила ещё не установлены.")


@router.message(F.text.lower() == "изменить правила")
async def edit_rules_start(message: Message, state: FSMContext):
    if message.from_user.id != Config.ADMIN_ID:
        await message.reply("❌ Только администраторы могут изменять правила.")
        return

    await state.set_state(RulesState.waiting_for_rules)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_rules_input")]]
    )
    await message.answer("📝 Отправьте новый текст правил для вашей группы:", reply_markup=kb)


@router.callback_query(F.data == "cancel_rules_input")
async def cancel_rules_input(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text("❌ Ввод правил отменён.")
    await query.answer()


@router.message(RulesState.waiting_for_rules)
async def process_rules_input(message: Message, state: FSMContext):
    if message.from_user.id != Config.ADMIN_ID:
        return

    new_rules = message.text
    await db.set_rules(new_rules)
    await state.clear()
    await message.reply("✅ Правила успешно обновлены!")


# --- ПРОФИЛЬ И СТАТИСТИКА ---

@router.message(F.text.lower() == "профиль")
async def profile_handler(message: Message):
    await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    target = message.reply_to_message.from_user if (message.reply_to_message and message.reply_to_message.from_user) else message.from_user
    if target.is_bot:
        await message.reply("🤖 Это бот.")
        return

    await db.ensure_user(target.id, target.username, target.full_name)
    user_data = await db.get_user_data(target.id)
    items_count = await db.get_total_items(target.id)
    active_title = await db.get_active_title(target.id) or "Нет"

    if user_data:
        _, _, full_name, balance, _, _, reputation = user_data
        text = (
            f"👤 <b>Имя:</b> {full_name}\n"
            f"👏 <b>Баланс:</b> {balance} ладушек\n"
            f"🏷 <b>Титул:</b> {active_title}\n"
            f"🎒 <b>Предметов:</b> {items_count}\n"
            f"⭐ <b>Репутация:</b> {reputation}/10"
        )
        await message.answer(text, disable_web_page_preview=True)


@router.message(F.text.lower() == "баланс")
async def balance_handler(message: Message):
    await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    target = message.reply_to_message.from_user if (message.reply_to_message and message.reply_to_message.from_user) else message.from_user
    if target.is_bot:
        await message.reply("🤖 У бота нет баланса.")
        return

    await db.ensure_user(target.id, target.username, target.full_name)
    user_balance = await db.get_balance(target.id)
    user_link = get_mention(target)

    text = f"┌ 👤 <b>Профиль:</b> {user_link}\n└ 🪙 <b>Баланс:</b> {user_balance} ладушек"
    await message.answer(text, disable_web_page_preview=True)


@router.message(F.text.lower() == "бонус")
async def bonus_handler(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)

    success, reward, time_left = await db.claim_daily_bonus(user.id)
    if not success and time_left:
        hours = time_left.seconds // 3600
        minutes = (time_left.seconds % 3600) // 60
        await message.reply(
            f"⏳ <b>Бонус уже получен.</b>\n"
            f"Следующий бонус будет доступен через <b>{hours} ч. {minutes} мин.</b>"
        )
        return

    if success:
        await db.add_history_entry(0, user.id, reward, "daily_bonus")
        await message.reply(
            f"🎁 <b>Ежедневный бонус!</b>\n\n💰 Ты получил: <b>+{reward}</b> ладушек"
        )


@router.message(F.text.lower() == "репутация")
async def reputation_top_handler(message: Message):
    top_list = await db.get_top_reputation()
    if not top_list:
        await message.answer("⭐ Список репутации пуст.")
        return

    text = "⭐ <b>Топ участников по репутации:</b>\n\n"
    for i, (name, rep) in enumerate(top_list, 1):
        text += f"{i}. <b>{name}</b> — {rep} ⭐\n"
    await message.answer(text)


# --- МАГАЗИН И ПРЕДМЕТЫ ---

@router.message(F.text.lower() == "магазин")
async def shop_handler(message: Message):
    text = (
        "🛒 <b>Магазин Предметов</b>\n\n"
        "🥊 <b>Боевая ладушка</b> — 200 ладушек\n"
        "🍅 <b>Томат</b> — 100 ладушек\n"
        "🐀 <b>Крыса</b> — 250 ладушек\n\n"
        "Для покупки напишите:\n"
        "• <code>купить ладушка</code>\n"
        "• <code>купить томат</code>\n"
        "• <code>купить крыса</code>"
    )
    await message.answer(text)


@router.message(F.text.lower() == "купить ладушка")
async def buy_battle_ladushka(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)
    price = 200

    if await db.buy_item(user.id, "battle_ladushka", price):
        await message.reply(f"✅ <b>Покупка успешна!</b>\n\n🥊 <b>Получено:</b> Боевая ладушка ×1\n💰 <b>Списано:</b> {price} ладушек")
    else:
        current_bal = await db.get_balance(user.id)
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_bal}</b> ладушек")


@router.message(F.text.lower() == "купить томат")
async def buy_tomato(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)
    price = 100

    if await db.buy_item(user.id, "tomato", price):
        await message.reply(f"✅ <b>Покупка успешна!</b>\n\n🍅 <b>Получено:</b> Томат ×1\n💰 <b>Списано:</b> {price} ладушек")
    else:
        current_bal = await db.get_balance(user.id)
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_bal}</b> ладушек")


@router.message(F.text.lower() == "купить крыса")
async def buy_rat(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)
    price = 250

    if await db.buy_item(user.id, "rat", price):
        await message.reply(f"✅ <b>Покупка успешна!</b>\n\n🐀 <b>Получено:</b> Крыса ×1\n💰 <b>Списано:</b> {price} ладушек")
    else:
        current_bal = await db.get_balance(user.id)
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_bal}</b> ладушек")


@router.message(F.text.lower() == "инвентарь")
async def inventory_handler(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)
    items = await db.get_inventory(user.id)
    if not items:
        await message.reply("🎒 Ваш инвентарь пуст.")
        return

    text = "🎒 <b>Ваш инвентарь:</b>\n\n"
    for item_name, quantity in items:
        if item_name == "battle_ladushka":
            text += f"🥊 <b>Боевая ладушка</b> ×{quantity}\n"
        elif item_name == "tomato":
            text += f"🍅 <b>Томат</b> ×{quantity}\n"
        elif item_name == "rat":
            text += f"🐀 <b>Крыса</b> ×{quantity}\n"
        else:
            text += f"📦 <b>{item_name}</b> ×{quantity}\n"
    await message.reply(text)


# --- ИГРОВЫЕ ДЕЙСТВИЯ ---

@router.message(F.text.lower() == "ударить ладушкой")
async def use_battle_ladushka(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("❌ Ответьте на сообщение игрока, которого хотите ударить!")
        return

    attacker = message.from_user
    target = message.reply_to_message.from_user

    if target.id == attacker.id or target.is_bot:
        await message.reply("❌ Нельзя применить это действие на себя или бота.")
        return

    await db.ensure_user(attacker.id, attacker.username, attacker.full_name)
    await db.ensure_user(target.id, target.username, target.full_name)

    if not await db.use_item(attacker.id, "battle_ladushka"):
        await message.reply("❌ У вас нет Боевой ладушки в инвентаре! Купите её в магазине.")
        return

    target_balance = await db.get_balance(target.id)
    stolen = min(target_balance, 100)
    
    if stolen > 0:
        await db.update_balance(target.id, -stolen)
        await db.update_balance(attacker.id, stolen)
        await message.reply(f"🥊 {get_mention(attacker)} ударил ладушкой {get_mention(target)} и отобрал <b>{stolen}</b> ладушек!")
    else:
        await message.reply(f"🥊 {get_mention(attacker)} ударил ладушкой {get_mention(target)}, но у него нечего забрать!")


@router.message(F.text.lower() == "кинуть томат")
async def use_tomato(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("❌ Ответьте на сообщение игрока, в которого хотите бросить томат!")
        return

    attacker = message.from_user
    target = message.reply_to_message.from_user

    if target.id == attacker.id or target.is_bot:
        await message.reply("❌ Нельзя бросить томат в себя или в бота.")
        return

    await db.ensure_user(attacker.id, attacker.username, attacker.full_name)
    await db.ensure_user(target.id, target.username, target.full_name)

    if not await db.use_item(attacker.id, "tomato"):
        await message.reply("❌ У вас нет томата! Купите его в магазине.")
        return

    await db.change_reputation(target.id, -1)
    await message.reply(f"🍅 {get_mention(attacker)} бросил томат в {get_mention(target)}! Репутация жертвы понижена.")


@router.message(F.text.lower() == "крыса")
async def use_rat(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)

    if not await db.use_item(user.id, "rat"):
        await message.reply("❌ У вас нет крысы! Купите её в магазине.")
        return

    victim_id = await db.get_random_user(exclude_id=user.id)
    if not victim_id:
        await message.reply("🐀 Крыса побегала, но не нашла жертву.")
        return

    victim_bal = await db.get_balance(victim_id)
    stolen = min(victim_bal, random.randint(30, 80))

    if stolen > 0:
        await db.update_balance(victim_id, -stolen)
        await db.update_balance(user.id, stolen)
        await message.reply(f"🐀 Ваша крыса проникла к случайному игроку и утащила <b>{stolen}</b> ладушек!")
    else:
        await message.reply("🐀 Ваша крыса прибежала пустой.")


# --- ТИТУЛЫ ---

@router.message(F.text.lower() == "титулы")
async def titles_shop_handler(message: Message):
    text = (
        "🏷 <b>Титулы ладушника</b>\n\n"
        "🪵 <b>Деревянный ладушник</b> — 50 👏\n"
        "🥉 <b>Бронзовый ладушник</b> — 200 👏\n"
        "🥈 <b>Серебряный ладушник</b> — 600 👏\n"
        "🥇 <b>Золотой ладушник</b> — 1000 👏\n\n"
        "<i>Чтобы купить титул, напишите его название.</i>"
    )
    await message.answer(text)


@router.message(F.text.lower() == "мои титулы")
async def my_titles_handler(message: Message):
    user_id = message.from_user.id
    await db.ensure_user(user_id, message.from_user.username, message.from_user.full_name)

    owned_keys = await db.get_user_titles(user_id)
    if not owned_keys:
        await message.reply("🎒 У вас пока нет купленных титулов. Напишите <b>титулы</b>, чтобы открыть магазин.")
        return

    active_key = await db.get_active_title_key(user_id)
    text = "🏷 <b>Мои титулы</b>\n\n"
    buttons = []

    for key in owned_keys:
        info = db.TITLES.get(key)
        if not info:
            continue
        is_active = (key == active_key)
        status = " ✅" if is_active else ""
        text += f"{info['name']}{status}\n"

        if not is_active:
            buttons.append([InlineKeyboardButton(text=f"Выбрать {info['name']}", callback_data=TitleSelectCB(key=key).pack())])

    text += f"\n<b>Активный титул:</b>\n{db.TITLES[active_key]['name'] if active_key in db.TITLES else 'Нет'}"
    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    await message.answer(text, reply_markup=kb)


# --- АДМИНИСТРАТИВНЫЕ КОМАНДЫ (/add и /revelo) ---

@router.message(Command("add"))
async def admin_add_balance(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    await db.ensure_user(target.id, target.username, target.full_name)

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("❌ Использование: ответьте на сообщение и напишите /add [сумма]")
        return

    amount = int(args[1])
    await db.update_balance(target.id, amount)
    await db.add_history_entry(Config.ADMIN_ID, target.id, amount, "admin_add")

    new_bal = await db.get_balance(target.id)
    await message.answer(f"✅ Баланс игрока {get_mention(target)} увеличен на {amount}. Новый баланс: {new_bal} 👏", disable_web_page_preview=True)


@router.message(Command("revelo"))
async def admin_revelo_balance(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    await db.ensure_user(target.id, target.username, target.full_name)

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("❌ Использование: ответьте на сообщение и напишите /revelo [сумма]")
        return

    amount = int(args[1])
    await db.update_balance(target.id, -amount)
    await db.add_history_entry(Config.ADMIN_ID, target.id, -amount, "admin_revelo")

    new_bal = await db.get_balance(target.id)
    await message.answer(f"✅ У игрока {get_mention(target)} забрано {amount} ладушек. Новый баланс: {new_bal} 👏", disable_web_page_preview=True)


@router.message(Command("reset"))
async def admin_reset(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    await db.reset_user(target.id)
    await message.answer(f"🔄 Данные игрока {get_mention(target)} сброшены.", disable_web_page_preview=True)


# --- УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ПОКУПКИ ТИТУЛОВ (СТРОГО В САМОМ КОНЦЕ!) ---

@router.message(F.text)
async def title_buy_request(message: Message):
    user_text = message.text.strip().lower()

    matched_key = None
    for key, info in db.TITLES.items():
        clean_name = info.get("clean_name", "").lower()
        full_name = info.get("name", "").lower()
        if user_text in (clean_name, full_name):
            matched_key = key
            break

    if not matched_key:
        return

    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)

    title_info = db.TITLES[matched_key]
    owned_titles = await db.get_user_titles(user.id)
    if matched_key in owned_titles:
        await message.reply("🏷 У вас уже куплен этот титул! Напишите <b>мои титулы</b>, чтобы выбрать его.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Купить", callback_data=TitleBuyCB(action="confirm", key=matched_key).pack()),
                InlineKeyboardButton(text="❌ Отмена", callback_data=TitleBuyCB(action="cancel", key=matched_key).pack()),
            ]
        ]
    )

    await message.reply(f"Вы хотите купить титул {title_info['name']} за {title_info['price']} 👏?", reply_markup=kb)


@router.callback_query(TitleBuyCB.filter())
async def process_title_buy_callback(query: CallbackQuery, callback_data: TitleBuyCB):
    user_id = query.from_user.id
    title_key = callback_data.key
    title_info = db.TITLES.get(title_key)

    if not title_info:
        await query.answer("Титул не найден.", show_alert=True)
        return

    if callback_data.action == "cancel":
        await query.message.edit_text("❌ Покупка титула отменена.")
        await query.answer()
        return

    if callback_data.action == "confirm":
        success = await db.buy_title(user_id, title_key, title_info["price"])
        if success:
            await db.set_active_title(user_id, title_key)
            await query.message.edit_text(f"🎉 Вы успешно купили и установили титул {title_info['name']}!")
            await query.answer("Покупка успешно завершена!")
        else:
            balance = await db.get_balance(user_id)
            await query.message.edit_text(
                f"❌ <b>Недостаточно ладушек!</b>\nСтоимость: {title_info['price']} 👏\nВаш баланс: {balance} 👏"
            )
            await query.answer()


@router.callback_query(TitleSelectCB.filter())
async def process_title_select_callback(query: CallbackQuery, callback_data: TitleSelectCB):
    user_id = query.from_user.id
    title_key = callback_data.key
    owned_keys = await db.get_user_titles(user_id)

    if title_key not in owned_keys:
        await query.answer("У вас нет этого титула!", show_alert=True)
        return

    await db.set_active_title(user_id, title_key)
    title_name = db.TITLES[title_key]["name"]
    await query.message.edit_text(f"✅ Активный титул изменён на: {title_name}")
    await query.answer("Титул установлен!")
