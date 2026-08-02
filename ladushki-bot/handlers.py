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


# Состояния для ввода правил администратором
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
        "💬 <b>Команды:</b>\n"
        "• Напишите <b>профиль</b> — чтобы посмотреть свой профиль.\n"
        "• Напишите <b>баланс</b> — чтобы узнать счет.\n"
        "• Напишите <b>бонус</b> — чтобы получить ежедневный бонус.\n"
        "• Напишите <b>правила</b> или <b>права</b> — чтобы прочитать правила группы.\n"
        "• Напишите <b>минута ладушек</b> — узнать время проведения минуты ладушек.\n"
        "• Напишите <b>титулы</b> — открыть магазин титулов.\n"
        "• Напишите <b>мои титулы</b> — посмотреть свои титулы и выбрать активный.\n"
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


# ------------------- РАЗДЕЛ "ПРАВИЛА" -------------------

@router.message(F.text.lower().in_(["правила", "права", "📜 правила"]))
async def rules_handler(message: Message):
    rules_text = await db.get_rules() if hasattr(db, "get_rules") else None
    is_admin = (message.from_user.id == Config.ADMIN_ID)

    buttons = []
    if is_admin:
        btn_text = "✏️ Изменить правила" if rules_text else "➕ Добавить правила"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data="add_rules_start")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    if rules_text:
        await message.answer(f"📜 <b>Правила группы:</b>\n\n{rules_text}", reply_markup=kb)
    else:
        text = (
            "📜 <b>Правила</b>\n\n"
            "Правила ещё не установлены.\n"
            "Создайте правила для вашей группы, чтобы участники могли ознакомиться с ними."
        )
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "add_rules_start")
async def add_rules_start_callback(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != Config.ADMIN_ID:
        await query.answer("⛔ Только администратор может изменять правила.", show_alert=True)
        return

    await state.set_state(RulesState.waiting_for_rules)
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_rules_input")]]
    )
    
    await query.message.answer("📝 Отправьте текст правил для вашей группы:", reply_markup=kb)
    await query.answer()


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
    if hasattr(db, "set_rules"):
        await db.set_rules(new_rules)

    await state.clear()
    await message.reply(
        "✅ <b>Правила успешно установлены!</b>\n\n"
        "Теперь участники смогут открыть раздел «📜 Правила» и ознакомиться с ними."
    )


# ---------------------------------------------------------

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


@router.message(F.text.lower() == "титулы")
async def titles_shop_handler(message: Message):
    text = (
        "🏷 <b>Титулы ладушника</b>\n\n"
        "🪵 <b>Деревянный ладушник</b>\n"
        "💰 Цена: 50 👏\n\n"
        "🥉 <b>Бронзовый ладушник</b>\n"
        "💰 Цена: 200 👏\n\n"
        "🥈 <b>Серебряный ладушник</b>\n"
        "💰 Цена: 600 👏\n\n"
        "🥇 <b>Золотой ладушник</b>\n"
        "💰 Цена: 1000 👏\n\n"
        "<i>Чтобы купить титул, напишите его название (например: <code>Деревянный ладушник</code>).</i>"
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


@router.message(F.text)
async def title_buy_request(message: Message):
    user_text = message.text.strip().lower()

    matched_key = None
    for key, info in db.TITLES.items():
        title_name = info["name"].lower()
        if user_text == title_name or user_text in title_name or title_name in user_text:
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

    await message.reply(
        f"Вы хотите купить титул {title_info['name']} за {title_info['price']} 👏?",
        reply_markup=kb
    )


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
                f"❌ <b>Недостаточно ладушек!</b>\n"
                f"Стоимость: {title_info['price']} 👏\n"
                f"Ваш баланс: {balance} 👏"
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

    text = (
        f"┌ 👤 <b>Профиль:</b> {user_link}\n"
        f"└ 🪙 <b>Баланс:</b> {user_balance} ладушек"
    )
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
            f"🎁 <b>Ежедневный бонус!</b>\n\n"
            f"💰 Ты получил: <b>+{reward}</b> ладушки"
        )


@router.message(Command("basketball"))
@router.message(F.text.lower().startswith("баскетбол"))
@router.message(F.text.lower().startswith("баскет"))
async def basketball_game(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("⚠️ Укажите сумму числом. Пример: <code>баскетбол 50</code>")
        return

    amount = int(parts[1])
    if amount <= 0:
        await message.reply("❌ Сумма должна быть больше 0.")
        return

    current_balance = await db.get_balance(user.id)
    if current_balance < amount:
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_balance}</b> ладушек")
        return

    dice_msg = await message.answer_dice(emoji="🏀")
    score = dice_msg.dice.value

    await asyncio.sleep(3.5)

    if score >= 4:
        reward = int(amount * 0.3)
        new_bal = await db.update_balance(user.id, reward)
        await db.add_history_entry(0, user.id, reward, "basketball_win")
        await message.reply(
            f"🏀 <b>ТОЧНЫЙ БРОСОК!</b>\n\n"
            f"🎉 Попадание в корзину!\n"
            f"💰 Вы получили: <b>+{reward}</b> ладушек 👏\n"
            f"🪙 Ваш баланс: <b>{new_bal}</b>"
        )
    else:
        new_bal = await db.update_balance(user.id, -amount)
        await db.add_history_entry(user.id, 0, amount, "basketball_loss")
        await message.reply(
            f"🏀 Бросок…\n"
            f"😔 Промах!\n"
            f"💸 Потеряно: <b>{amount}</b> ладушек.\n"
            f"🪙 Текущий баланс: <b>{new_bal}</b> ладушек."
        )


@router.message(F.text.lower() == "магазин")
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


@router.message(F.text.lower() == "купить ладушка")
async def buy_battle_ladushka(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)
    price = 200

    if await db.buy_item(user.id, "battle_ladushka", price):
        await message.reply(
            "✅ <b>Покупка успешна!</b>\n\n"
            "🥊 <b>Получено:</b> Боевая ладушка ×1\n"
            f"💰 <b>Списано:</b> {price} ладушек"
        )
    else:
        current_bal = await db.get_balance(user.id)
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_bal}</b> ладушек")


@router.message(F.text.lower() == "купить томат")
async def buy_tomato(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)
    price = 100

    if await db.buy_item(user.id, "tomato", price):
        await message.reply(
            "✅ <b>Покупка успешна!</b>\n\n"
            "🍅 <b>Получено:</b> Томат ×1\n"
            f"💰 <b>Списано:</b> {price} ладушек"
        )
    else:
        current_bal = await db.get_balance(user.id)
        await message.reply(f"❌ <b>Недостаточно ладушек.</b>\n\nВаш баланс: <b>{current_bal}</b> ладушек")


@router.message(F.text.lower() == "купить крыса")
async def buy_rat(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)
    price = 250

    if await db.buy_item(user.id, "rat", price):
        await message.reply(
            "✅ <b>Покупка успешна!</b>\n\n"
            "🐀 <b>Получено:</b> Крыса ×1\n"
            f"💰 <b>Списано:</b> {price} ладушек"
        )
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

    text = "🎒 <b>Ваш инвентарь</b>\n\n"
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


@router.message(F.text.lower() == "ударить ладушкой")
async def hit_with_ladushka(message: Message):
    await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    sender = message.from_user
    receiver = message.reply_to_message.from_user
    if receiver.is_bot:
        await message.reply("🤖 Нельзя бить бота.")
        return

    await db.ensure_user(receiver.id, receiver.username, receiver.full_name)

    if not await db.use_item(sender.id, "battle_ladushka"):
        await message.reply("❌ <b>У вас нет Боевой ладушки.</b>\n\n🛒 Купить можно в магазине за 200 ладушек.")
        return

    sender_link = get_mention(sender)
    receiver_link = get_mention(receiver)

    phrases = [
        f"🥊 {sender_link} ударил ладушкой {receiver_link}!\n\n👏 <b>ШЛЁП!</b>",
        f"💥 {sender_link} размахнулся и влепил ладушку {receiver_link}!",
        f"🏛 <b>Министр Ладушек одобрил удар.</b>\n\n🥊 {sender_link} ударил {receiver_link} ладушкой!"
    ]
    await message.reply(random.choice(phrases), disable_web_page_preview=True)


@router.message(F.text.lower() == "кинуть томат")
async def throw_tomato(message: Message):
    await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    sender = message.from_user
    receiver = message.reply_to_message.from_user
    if receiver.is_bot:
        await message.reply("🤖 Нельзя бросать томат в бота.")
        return

    await db.ensure_user(receiver.id, receiver.username, receiver.full_name)

    if not await db.use_item(sender.id, "tomato"):
        await message.reply("❌ <b>У вас нет томатов.</b>\n\n🛒 Купить можно в магазине за 100 ладушек.")
        return

    sender_link = get_mention(sender)
    receiver_link = get_mention(receiver)

    phrases = [
        f"🍅 {sender_link} кинул томат в {receiver_link}!\n\n🤭 Теперь {receiver_link} весь в томате.",
        f"🍅 {sender_link} запустил томат в {receiver_link}!\n\n💥 Прямое попадание!\n\n😂 {receiver_link} весь в кетчупе.",
        f"🎯 <b>Меткий бросок!</b>\n\n🍅 {receiver_link} теперь весь в томатном соке."
    ]
    await message.reply(random.choice(phrases), disable_web_page_preview=True)


@router.message(F.text.lower() == "крыса")
async def use_rat(message: Message):
    sender = message.from_user
    await db.ensure_user(sender.id, sender.username, sender.full_name)

    target = await db.get_random_target_for_rat(sender.id)
    if not target:
        await message.reply("❌ Недостаточно зарегистрированных игроков в группе.")
        return

    if not await db.use_item(sender.id, "rat"):
        await message.reply("❌ У вас нет крысы.")
        return

    target_id, target_name, _ = target
    wanted_steal = random.randint(1, 3)
    stolen = await db.execute_rat_steal(sender.id, target_id, wanted_steal)

    if stolen <= 0:
        await message.reply(f"🐀 Крыса обыскала карманы {target_name}...\n\n😢 Но там не оказалось ни одной ладушки.")
    else:
        await message.reply(f"🐀 Крыса пробралась к {target_name}!\n\n💸 Украдено: {stolen} ладушки")


@router.message(F.text.lower().startswith("дать "))
async def transfer_custom_amount(message: Message):
    sender = message.from_user
    await db.ensure_user(sender.id, sender.username, sender.full_name)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Эта команда должна быть ответом на сообщение пользователя!")
        return

    receiver = message.reply_to_message.from_user
    if receiver.is_bot:
        await message.reply("🤖 Нельзя переводить ладушки боту.")
        return

    await db.ensure_user(receiver.id, receiver.username, receiver.full_name)

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

    success = await db.transfer_balance(sender.id, receiver.id, amount)
    if success:
        sender_new = await db.get_balance(sender.id)
        receiver_new = await db.get_balance(receiver.id)
        await message.reply(
            f"✅ <b>Перевод успешно выполнен!</b>\n\n"
            f"📤 <b>Отправитель:</b> {get_mention(sender)}\n"
            f"📥 <b>Получатель:</b> {get_mention(receiver)}\n"
            f"💰 <b>Сумма:</b> {amount} ладушек\n\n"
            f"📊 <b>Новый баланс {get_mention(receiver)}:</b> {receiver_new} ладушек\n"
            f"📊 <b>Ваш новый баланс:</b> {sender_new} ладушек",
            disable_web_page_preview=True
        )
    else:
        sender_bal = await db.get_balance(sender.id)
        await message.reply(f"❌ <b>Недостаточно средств!</b>\nУ вас на балансе: <b>{sender_bal}</b> ладушек.")


@router.message(F.text.lower().in_(["подарок", "ладошка"]))
async def transfer_one_ladushka(message: Message):
    sender = message.from_user
    await db.ensure_user(sender.id, sender.username, sender.full_name)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Ответьте на сообщение игрока, чтобы передать ладушку!")
        return

    receiver = message.reply_to_message.from_user
    if receiver.is_bot:
        await message.reply("🤖 Нельзя передавать ладушки боту.")
        return

    await db.ensure_user(receiver.id, receiver.username, receiver.full_name)

    if sender.id == receiver.id:
        await message.reply("❌ Нельзя дарить ладушки самому себе.")
        return

    success = await db.transfer_balance(sender.id, receiver.id, 1)
    if success:
        sender_new = await db.get_balance(sender.id)
        receiver_new = await db.get_balance(receiver.id)
        await message.reply(
            f"🎁 <b>Ладушка передана!</b>\n\n"
            f"От: {get_mention(sender)}\n"
            f"Кому: {get_mention(receiver)}\n"
            f"Передано: <b>1 ладушка</b> 🪙\n\n"
            f"Теперь у вас {sender_new} ладушек.\n"
            f"У {get_mention(receiver)} {receiver_new} ладушек.",
            disable_web_page_preview=True
        )
    else:
        await message.reply("❌ У вас нет ладушек.")


@router.message(F.text.lower() == "топ богачей")
async def top_rich_handler(message: Message):
    rows = await db.get_top_rich()
    if not rows:
        await message.answer("Пока нет зарегистрированных участников.")
        return

    text = "🏆 <b>Топ по ладушкам</b>\n\n"
    for i, (uid, name, uname, bal) in enumerate(rows, start=1):
        url = f"https://t.me/{uname}" if uname else f"tg://user?id={uid}"
        text += f"{i}. <a href='{url}'>{name}</a> — 🪙 <b>{bal}</b>\n"

    await message.answer(text, disable_web_page_preview=True)


@router.message(Command("history"))
async def history_handler(message: Message):
    user = message.from_user
    await db.ensure_user(user.id, user.username, user.full_name)

    rows = await db.get_user_history(user.id)
    if not rows:
        await message.answer("📜 История пуста.")
        return

    text = "📜 <b>Последние операции:</b>\n\n"
    for action, amount, date in rows:
        text += f"• {action} | {amount} 🪙 | {date}\n"

    await message.answer(text)


@router.message(F.text.lower() == "+реп")
async def add_reputation_handler(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        return

    await db.ensure_user(target.id, target.username, target.full_name)
    changed, new_rep = await db.change_reputation(target.id, 1)

    if not changed and new_rep == 10:
        await message.reply("⭐ У этого игрока уже максимальная репутация (10).")
    elif changed:
        await message.reply(f"⭐ <b>Репутация выдана!</b>\n\n👤 <b>Игрок:</b> {target.full_name}\n➕ <b>Репутация:</b> +1")


@router.message(F.text.lower() == "-реп")
async def remove_reputation_handler(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        return

    await db.ensure_user(target.id, target.username, target.full_name)
    changed, new_rep = await db.change_reputation(target.id, -1)

    if not changed and new_rep == 0:
        await message.reply("⭐ У этого игрока уже минимальная репутация (0).")
    elif changed:
        await message.reply(f"⭐ <b>Репутация изменена!</b>\n\n👤 <b>Игрок:</b> {target.full_name}\n➖ <b>Репутация:</b> -1")


@router.message(F.text.lower() == "репутация")
async def top_reputation_handler(message: Message):
    rows = await db.get_top_reputation()
    if not rows:
        await message.answer("⭐ Топ по репутации пуст.")
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    text = "⭐ <b>Топ по репутации</b>\n\n"
    for idx, (full_name, rep) in enumerate(rows):
        emoji = medals[idx] if idx < len(medals) else f"{idx + 1}️⃣"
        text += f"{emoji} {full_name} — {rep} репутации\n"

    await message.answer(text)


@router.message(F.text.lower() == "база")
async def admin_database_handler(message: Message):
    if message.from_user.id != Config.ADMIN_ID:
        await message.reply("⛔ Команда доступна только администраторам.")
        return

    total, active, rows = await db.get_all_active_users()
    if not rows:
        await message.reply("📂 База данных пользователей пуста (нет участников с балансом больше 0).")
        return

    text = f"📊 <b>База пользователей (показано {active} из {total}):</b>\n\n"
    for full_name, user_id, balance, reputation in rows:
        text += (
            f"👤 <b>Имя:</b> {full_name}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"💰 <b>Баланс:</b> {balance} ладушек\n"
            f"⭐ <b>Репутация:</b> {reputation}\n"
            "───────────────\n"
        )
    await message.answer(text)


@router.message(F.text.lower().startswith("штраф"))
async def fine_handler(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        return

    await db.ensure_user(target.id, target.username, target.full_name)

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("⚠️ Укажите сумму штрафа числом.")
        return

    fine_amount = int(parts[1])
    current_bal = await db.get_balance(target.id)

    if current_bal <= 0:
        await message.reply("🚔 Штраф не удалось взыскать: у игрока 0 ладушек.")
        return

    deducted = min(current_bal, fine_amount)
    new_bal = await db.update_balance(target.id, -deducted)
    await db.add_history_entry(Config.ADMIN_ID, target.id, deducted, "fine")

    await message.reply(
        f"🚔 Администратор выписал штраф.\n\n"
        f"👤 Игрок: {target.full_name}\n"
        f"💸 Списано: {deducted} ладушек\n"
        f"Новый баланс: {new_bal} ладушек"
    )


@router.message(Command("add"))
async def admin_add_balance(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    await db.ensure_user(target.id, target.username, target.full_name)

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Использование: /add 10")
        return

    amount = int(args[1])
    await db.update_balance(target.id, amount)
    await db.add_history_entry(Config.ADMIN_ID, target.id, amount, "add")

    await message.answer(f"✅ {get_mention(target)} получил {amount} ладушек.", disable_web_page_preview=True)


@router.message(Command("remove"))
async def admin_remove_balance(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    await db.ensure_user(target.id, target.username, target.full_name)

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        return

    amount = int(args[1])
    old_bal = await db.get_balance(target.id)
    new_bal = await db.update_balance(target.id, -amount)
    await db.add_history_entry(Config.ADMIN_ID, target.id, amount, "remove")

    await message.answer(f"✅ Ладушки сняты. Старый: {old_bal} -> Новый: {new_bal}")


@router.message(Command("set"))
async def admin_set_balance(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    await db.ensure_user(target.id, target.username, target.full_name)

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        return

    amount = int(args[1])
    await db.set_balance(target.id, amount)
    await message.answer("✅ Баланс изменён.")


@router.message(Command("admin_bonus"))
async def admin_bonus(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    await db.ensure_user(target.id, target.username, target.full_name)

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return

    amount = int(args[1])
    await db.update_balance(target.id, amount)
    await db.add_history_entry(Config.ADMIN_ID, target.id, amount, "admin_bonus")

    await message.answer(f"🎁 Игрок {get_mention(target)} получил бонус {amount} ладушек.", disable_web_page_preview=True)


@router.message(Command("reset"))
async def admin_reset(message: Message):
    if message.from_user.id != Config.ADMIN_ID or not message.reply_to_message or not message.reply_to_message.from_user:
        return

    target = message.reply_to_message.from_user
    await db.ensure_user(target.id, target.username, target.full_name)

    await db.set_balance(target.id, 0)
    await message.answer("♻️ Баланс игрока сброшен.")
