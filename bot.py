import asyncio
import logging
from datetime import datetime, time

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

# ---------- НАСТРОЙКИ ----------
BOT_TOKEN = "8675621032:AAHKU2EeS0GMw5eWCG8T-zMYYVv6vLvUiN0"  # ваш токен
DB_PATH = "bot_database.db"
BOT_USERNAME = None

REQUIRED_CHANNELS = ["@pavelgifsts"]  # Только этот канал

CLICKER_COOLDOWN = 180
CLICKER_REWARD = 0.20

REFERRAL_BONUS = 1.0
OLD_FRIENDS_BONUS = 2.0
DAILY_BIO_BONUS = 1.0

TOP_PRIZES = {
    1: 200, 2: 100, 3: 50, 4: 40, 5: 35,
    6: 30, 7: 25, 8: 20, 9: 15, 10: 10,
}

RESET_HOUR = 0
RESET_MINUTE = 0

ADMIN_ID = 8798104630   # ваш ID

# ---------- БАЗА ДАННЫХ ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0.0,
                invited_by INTEGER,
                total_invites INTEGER DEFAULT 0,
                activated_count INTEGER DEFAULT 0,
                daily_invites INTEGER DEFAULT 0,
                last_click_time TEXT,
                last_daily_bonus TEXT,
                claimed_old_friends_bonus INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS promos (
                code TEXT PRIMARY KEY,
                stars REAL NOT NULL,
                used_by TEXT DEFAULT ""
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS daily_task (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                text TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS withdraw_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                gift_name TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            INSERT OR IGNORE INTO daily_task (id, text) VALUES (1, 'Задание на сегодня не установлено.')
        ''')
        await db.commit()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БД ----------
async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def add_user(user_id, username, first_name, invited_by=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, username, first_name, balance, invited_by, total_invites, activated_count, daily_invites, claimed_old_friends_bonus) "
            "VALUES(?,?,?,0.0,?,0,0,0,0)",
            (user_id, username, first_name, invited_by)
        )
        await db.commit()

async def update_balance(user_id, delta):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (delta, user_id))
        await db.commit()

async def set_field(user_id, field, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))
        await db.commit()

async def increment_referral(referrer_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET total_invites = total_invites + 1, activated_count = activated_count + 1, daily_invites = daily_invites + 1 WHERE user_id=?",
            (referrer_id,)
        )
        await db.commit()

async def get_top10_daily():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, first_name, daily_invites FROM users WHERE daily_invites > 0 ORDER BY daily_invites DESC LIMIT 10"
        ) as cursor:
            return await cursor.fetchall()

async def get_user_rank_daily(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT daily_invites FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        if not row or row[0] == 0:
            return 0
        user_invites = row[0]
        cursor2 = await db.execute("SELECT COUNT(*) FROM users WHERE daily_invites > ?", (user_invites,))
        count_row = await cursor2.fetchone()
        return count_row[0] + 1

async def reset_daily_and_reward():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, daily_invites FROM users WHERE daily_invites > 0 ORDER BY daily_invites DESC LIMIT 10"
        )
        top = await cursor.fetchall()
        prizes = {}
        for idx, (uid, count) in enumerate(top, start=1):
            if idx in TOP_PRIZES:
                prizes[uid] = TOP_PRIZES[idx]
        for uid, stars in prizes.items():
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (stars, uid))
        await db.execute("UPDATE users SET daily_invites = 0")
        await db.commit()
        return prizes

async def get_daily_task_text():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT text FROM daily_task WHERE id=1") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "Задание на сегодня не установлено."

async def set_daily_task_text(text):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE daily_task SET text=? WHERE id=1", (text,))
        await db.commit()

async def get_total_users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

# ---------- ЗАЯВКИ НА ВЫВОД ----------
async def create_withdraw_request(user_id, amount, gift_name):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO withdraw_requests (user_id, amount, gift_name, status) VALUES (?, ?, ?, 'pending')",
            (user_id, amount, gift_name)
        )
        await db.commit()
        return cursor.lastrowid

async def get_pending_requests():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, amount, gift_name, created_at FROM withdraw_requests WHERE status='pending' ORDER BY created_at ASC"
        ) as cursor:
            return await cursor.fetchall()

async def approve_request(request_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, amount FROM withdraw_requests WHERE id=? AND status='pending'",
            (request_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        user_id, amount = row
        await db.execute("UPDATE withdraw_requests SET status='done' WHERE id=?", (request_id,))
        await db.commit()
        return user_id, amount

async def reject_request(request_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, amount FROM withdraw_requests WHERE id=? AND status='pending'",
            (request_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        user_id, amount = row
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        await db.execute("UPDATE withdraw_requests SET status='rejected' WHERE id=?", (request_id,))
        await db.commit()
        return user_id, amount

# ---------- ПРОВЕРКА ПОДПИСКИ ----------
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    not_subscribed = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in ("left", "kicked"):
                not_subscribed.append(channel)
        except Exception:
            not_subscribed.append(channel)

    if not_subscribed:
        channels_text = ", ".join(not_subscribed)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📢 Подписаться на {ch}", url=f"https://t.me/{ch[1:]}")]
            for ch in not_subscribed
        ])
        text = (
            f"❌ Для использования бота необходимо подписаться на канал:\n"
            f"{channels_text}\n\n"
            f"После подписки вернитесь и нажмите /start или любую кнопку меню."
        )
        if update.message:
            await update.message.reply_text(text, reply_markup=keyboard)
        elif update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        return False
    return True

# ---------- КЛАВИАТУРЫ ----------
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["✨ Кликер звезд", "👤 Профиль"],
            ["💰 Заработать звезды", "🎁 Вывод звезд"],
            ["📘 Инструкция", "🏆 Топ", "📋 Задание дня", "⭐ Отзывы"]
        ],
        resize_keyboard=True
    )

def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📊 Статистика", "📋 Заявки на вывод"],
            ["📝 Создать промокод", "📋 Установить задание дня"],
            ["🔙 Назад"]
        ],
        resize_keyboard=True
    )

# ---------- ОБРАБОТЧИКИ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return

    user = update.effective_user
    user_id = user.id
    args = context.args

    referrer_id = None
    if args and args[0].isdigit():
        referrer_id = int(args[0])

    existing = await get_user(user_id)
    is_new = False
    if not existing:
        await add_user(user_id, user.username or "", user.first_name or "", referrer_id)
        is_new = True
        if referrer_id and referrer_id != user_id:
            referrer = await get_user(referrer_id)
            if referrer:
                await increment_referral(referrer_id)
                await update_balance(referrer_id, REFERRAL_BONUS)
                try:
                    await context.bot.send_message(
                        referrer_id,
                        f"🎉 По вашей реферальной ссылке зарегистрировался новый пользователь @{user.username or user.first_name or 'без username'}!\n"
                        f"Вы получили +{REFERRAL_BONUS}⭐️ на баланс!"
                    )
                except Exception:
                    pass

    photo_url = "https://i.postimg.cc/mgPgPPCp/photo-2026-08-05-16-36-37.jpg"
    if is_new:
        caption = (
            "✨ *Добро пожаловать в бота!*\n\n"
            "🎯 Здесь ты можешь зарабатывать звёзды, приглашать друзей и получать крутые подарки!\n\n"
            "📌 Используй кнопки меню, чтобы начать."
        )
    else:
        caption = (
            "👋 *С возвращением!*\n\n"
            "Ты уже зарегистрирован. Продолжай зарабатывать звёзды и приглашать друзей! 🚀"
        )

    if referrer_id and referrer_id != user_id and is_new:
        ref_photo = "https://i.ibb.co/TqcZWRWp/photo-2026-08-05-16-36-35.jpg"
        ref_caption = (
            "🎁 *Вы пришли по реферальной ссылке!*\n\n"
            "🌟 Активируйте бота, приглашайте друзей и получайте бонусы!\n"
            "Ваш пригласивший уже получил +1⭐️ за вас.\n\n"
            "Удачи! 🍀"
        )
        await update.message.reply_photo(photo=ref_photo, caption=ref_caption, parse_mode=ParseMode.MARKDOWN)

    await update.message.reply_photo(photo=photo_url, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def clicker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала /start")
        return
    last_click = user[8]
    now = datetime.utcnow()
    if last_click:
        last_dt = datetime.fromisoformat(last_click)
        if (now - last_dt).total_seconds() < CLICKER_COOLDOWN:
            remain = CLICKER_COOLDOWN - (now - last_dt).total_seconds()
            m, s = int(remain // 60), int(remain % 60)
            await update.message.reply_text(f"⏳ Подожди {m} мин {s} сек.")
            return
    await update_balance(user_id, CLICKER_REWARD)
    await set_field(user_id, "last_click_time", now.isoformat())
    await update.message.reply_text(f"✨ Специальное задание!\nНаграда: {CLICKER_REWARD:.2f} ⭐️\nБаланс пополнен!")

async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала /start")
        return
    username = user[1] or ""
    first_name = user[2] or "Пользователь"
    balance = user[3]
    total_inv = user[5]
    activated = user[6]
    display_name = first_name or username or "Пользователь"
    text = (
        f"✨ *Профиль*\n"
        f"──────────────\n"
        f"💬 Имя: {display_name}\n"
        f"🆔 ID: {user_id}\n"
        f"👤 Username: @{username if username else 'нет'}\n"
        f"──────────────\n"
        f"👥 Всего друзей: {total_inv}\n"
        f"✅ Активировали бота: {activated}\n"
        f"💰 Баланс: {balance:.2f} ⭐️\n\n"
        f"⁉️ *Как получить ежедневный бонус?*\n"
        f"Поставь свою личную ссылку на бота в описание своего ТГ аккаунта, "
        f"и получай за это +{DAILY_BIO_BONUS:.0f}⭐️ каждый день.\n\n"
        f"⬇️ Используй кнопки ниже"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Ввести промокод", callback_data="promo")],
        [InlineKeyboardButton("☀️ Ежедневный бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton("💸 Отправить звезды другу", callback_data="send_stars")],
        [InlineKeyboardButton("➕ +2⭐️ за старых друзей", callback_data="old_friends")],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def earn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала /start")
        return
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    text = (
        f"😎 *Приглашай друзей и получай по {REFERRAL_BONUS:.0f} ⭐️ от Патрика*\n"
        f"за каждого, кто активирует бота по твоей ссылке!\n\n"
        f"🔗 Твоя личная ссылка (нажми чтобы скопировать):\n"
        f"`{ref_link}`\n\n"
        f"🚀 *Как набрать много переходов?*\n"
        f"• Отправь её друзьям в личные сообщения 👥\n"
        f"• Поделись ссылкой в истории/канале 📱\n"
        f"• Оставь в комментариях или чатах 🗨\n"
        f"• Распространяй в других соцсетях 🌍\n\n"
        f"❗️ Чтобы повторно получить награду за уже приглашённых друзей, "
        f"жми кнопку «➕ +2⭐️ за старых друзей»"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ +2⭐️ за старых друзей", callback_data="old_friends")]
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала /start")
        return
    balance = user[3]
    text = (
        f"💰 *Ваш баланс:* {balance:.2f} ⭐️\n\n"
        f"‼️ Для вывода требуется:\n"
        f"— Минимум 5 приглашённых друзей, активировавших бота\n"
        f"— Быть подписанным на @pavelgifsts\n\n"
        f"✅ *Выберите подарок, и создастся заявка на вывод:*"
    )
    gifts = [
        ("🧸 Мишка-сердце (15⭐)", "15"),
        ("🌹 Роза (25⭐)", "25"),
        ("🍾 Шампанское / Букет / Ракета / Торт (50⭐)", "50"),
        ("🏆 Кубок / Кольцо / Алмаз (100⭐)", "100"),
    ]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(name, callback_data=f"gift_{price}")] for name, price in gifts
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def instruction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    text = (
        "📘 *Как набрать много переходов по ссылке?*\n"
        "• Отправь её друзьям в личные сообщения 👥\n"
        "• Поделись в истории/канале 📱\n"
        "• Оставь в комментариях или чатах 🗨\n"
        "• Распространяй в TikTok, Instagram, WhatsApp и других 🌍"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def top_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    user_id = update.effective_user.id
    top = await get_top10_daily()
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    if not top:
        text = "🏆 Топ 10 за день пока пуст."
    else:
        lines = []
        for i, (uid, uname, fname, invites) in enumerate(top):
            name = fname or uname or f"ID{uid}"
            lines.append(f"{medals[i]} {name} | Друзей: {invites}")
        text = "🏆 *Топ 10 за день:*\n" + "\n".join(lines)
    rank = await get_user_rank_daily(user_id)
    if rank:
        text += f"\n\n✨ Ты на *{rank}*-м месте!"
    else:
        text += "\n\n✨ Ты пока не в топе за этот день..."
    text += (
        "\n\n*Попади в топ и получи приз в конце дня:*\n"
        "1-е +200⭐️, 2-е +100⭐️, 3-е +50⭐️, 4-е +40⭐️,\n"
        "5-е +35⭐️, 6-е +30⭐️, 7-е +25⭐️, 8-е +20⭐️,\n"
        "9-е +15⭐️, 10-е +10⭐️"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def reviews_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    await update.message.reply_text("⭐ Наш канал отзывов: https://t.me/repa_yherova")

async def daily_task_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    text = await get_daily_task_text()
    await update.message.reply_text(f"📋 *Задание дня:*\n{text}", parse_mode=ParseMode.MARKDOWN)

# ---------- ОБРАБОТЧИК CALLBACK ----------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if not await check_subscription(update, context):
        return

    if data == "promo":
        context.user_data["expecting_promo"] = True
        await query.edit_message_text("🎟 Введи промокод (отправь текстом):")
        return

    elif data == "daily_bonus":
        user = await get_user(user_id)
        if not user:
            await query.edit_message_text("Сначала /start")
            return
        last_str = user[9]
        now = datetime.utcnow()
        if last_str:
            last_dt = datetime.fromisoformat(last_str)
            if now.date() == last_dt.date():
                await query.edit_message_text("☀️ Ты уже получал дневной бонус сегодня.")
                return
        try:
            chat = await context.bot.get_chat(user_id)
            bio = chat.bio or ""
        except Exception:
            bio = ""
        ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
        if ref_link in bio:
            await update_balance(user_id, DAILY_BIO_BONUS)
            await set_field(user_id, "last_daily_bonus", now.isoformat())
            await query.edit_message_text(f"✅ Ссылка найдена! +{DAILY_BIO_BONUS:.0f}⭐️")
        else:
            await query.edit_message_text("❌ Твоей реф. ссылки нет в описании профиля. Добавь и попробуй завтра.")
        return

    elif data == "send_stars":
        context.user_data["expecting_transfer"] = True
        await query.edit_message_text(
            "💸 Отправь ID друга и количество звёзд в формате:\n"
            "<code>ID_друга КОЛИЧЕСТВО</code>\n"
            "Например: <code>12345678 10</code>",
            parse_mode=ParseMode.HTML
        )
        return

    elif data == "old_friends":
        user = await get_user(user_id)
        if not user:
            await query.edit_message_text("Сначала /start")
            return
        if user[10]:
            await query.edit_message_text("Ты уже получал бонус за старых друзей.")
            return
        if user[5] == 0:
            await query.edit_message_text("У тебя ещё нет приглашённых друзей.")
            return
        await update_balance(user_id, OLD_FRIENDS_BONUS)
        await set_field(user_id, "claimed_old_friends_bonus", 1)
        await query.edit_message_text(f"✅ +{OLD_FRIENDS_BONUS:.0f}⭐️ за старых друзей!")
        return

    elif data.startswith("gift_"):
        price = int(data.split("_")[1])
        gift_names = {
            15: "Мишка-сердце",
            25: "Роза",
            50: "Шампанское / Букет / Ракета / Торт",
            100: "Кубок / Кольцо / Алмаз"
        }
        gift_name = gift_names.get(price, f"Подарок на {price}⭐")

        user = await get_user(user_id)
        if not user:
            await query.edit_message_text("Сначала /start")
            return
        if user[6] < 5:
            await query.edit_message_text("❌ Нужно минимум 5 активировавших бота друзей.")
            return
        if user[3] < price:
            await query.edit_message_text(f"❌ Недостаточно звёзд. Баланс: {user[3]:.2f}")
            return

        await update_balance(user_id, -price)
        request_id = await create_withdraw_request(user_id, price, gift_name)

        await query.edit_message_text(
            f"✅ Заявка на вывод подарка «{gift_name}» создана!\n"
            f"Номер заявки: #{request_id}\n"
            f"Сумма списана: {price}⭐️\n\n"
            f"Ожидайте подтверждения администратора."
        )

        try:
            admin_text = (
                f"📦 *Новая заявка на вывод!*\n"
                f"Заявка #{request_id}\n"
                f"Пользователь: @{user[1] or user[2] or str(user_id)} (ID: {user_id})\n"
                f"Подарок: {gift_name}\n"
                f"Сумма: {price}⭐️\n"
                f"Для просмотра всех заявок используйте /pending_requests или кнопку в админ-меню."
            )
            await context.bot.send_message(ADMIN_ID, admin_text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        return

    elif data.startswith("approve_"):
        if user_id != ADMIN_ID:
            await query.edit_message_text("⛔ У вас нет прав.")
            return
        request_id = int(data.split("_")[1])
        result = await approve_request(request_id)
        if result is None:
            await query.edit_message_text(f"❌ Заявка #{request_id} не найдена или уже обработана.")
            return
        user_id_req, amount = result
        try:
            await context.bot.send_message(
                user_id_req,
                f"🎉 Ваша заявка #{request_id} на вывод подарка одобрена!\n"
                f"Подарок скоро будет отправлен."
            )
        except Exception:
            pass
        await query.edit_message_text(f"✅ Заявка #{request_id} одобрена. Пользователь уведомлён.")
        return

    elif data.startswith("reject_"):
        if user_id != ADMIN_ID:
            await query.edit_message_text("⛔ У вас нет прав.")
            return
        request_id = int(data.split("_")[1])
        result = await reject_request(request_id)
        if result is None:
            await query.edit_message_text(f"❌ Заявка #{request_id} не найдена или уже обработана.")
            return
        user_id_req, amount = result
        try:
            await context.bot.send_message(
                user_id_req,
                f"❌ Ваша заявка #{request_id} на вывод подарка отклонена.\n"
                f"Сумма {amount}⭐️ возвращена на ваш баланс."
            )
        except Exception:
            pass
        await query.edit_message_text(f"❌ Заявка #{request_id} отклонена. Звёзды возвращены, пользователь уведомлён.")
        return

    else:
        await query.edit_message_text("Неизвестная команда.")

# ---------- ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ----------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if not await check_subscription(update, context):
        return

    if context.user_data.get("expecting_promo"):
        context.user_data.pop("expecting_promo")
        code = text.upper()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT stars, used_by FROM promos WHERE code=?", (code,)) as cursor:
                promo = await cursor.fetchone()
            if not promo:
                await update.message.reply_text("❌ Неверный промокод.")
                return
            stars, used_str = promo
            used = used_str.split(",") if used_str else []
            if str(user_id) in used:
                await update.message.reply_text("❌ Ты уже использовал этот промокод.")
                return
            used.append(str(user_id))
            await db.execute("UPDATE promos SET used_by=? WHERE code=?", (",".join(used), code))
            await db.commit()
        await update_balance(user_id, stars)
        await update.message.reply_text(f"✅ Промокод активирован! +{stars:.0f}⭐️")
        return

    if context.user_data.get("expecting_transfer"):
        context.user_data.pop("expecting_transfer")
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Неверный формат. Пример: 12345678 10")
            return
        try:
            target_id = int(parts[0])
            amount = float(parts[1])
        except ValueError:
            await update.message.reply_text("❌ ID и сумма должны быть числами.")
            return
        if amount <= 0:
            await update.message.reply_text("Сумма должна быть положительной.")
            return
        sender = await get_user(user_id)
        if not sender:
            await update.message.reply_text("Сначала /start")
            return
        if sender[3] < amount:
            await update.message.reply_text("Недостаточно звёзд.")
            return
        target = await get_user(target_id)
        if not target:
            await update.message.reply_text("Получатель не найден в боте.")
            return
        await update_balance(user_id, -amount)
        await update_balance(target_id, amount)
        await update.message.reply_text(f"✅ Переведено {amount:.2f}⭐️ пользователю ID{target_id}")
        return

    if text == "✨ Кликер звезд":
        await clicker_handler(update, context)
    elif text == "👤 Профиль":
        await profile_handler(update, context)
    elif text == "💰 Заработать звезды":
        await earn_handler(update, context)
    elif text == "🎁 Вывод звезд":
        await withdraw_handler(update, context)
    elif text == "📘 Инструкция":
        await instruction_handler(update, context)
    elif text == "🏆 Топ":
        await top_handler(update, context)
    elif text == "📋 Задание дня":
        await daily_task_handler(update, context)
    elif text == "⭐ Отзывы":
        await reviews_handler(update, context)

    elif user_id == ADMIN_ID:
        if text == "📊 Статистика":
            total_users = await get_total_users_count()
            await update.message.reply_text(f"📊 *Статистика бота*\nВсего зарегистрировано: {total_users} пользователей.", parse_mode=ParseMode.MARKDOWN)
        elif text == "📋 Заявки на вывод":
            await pending_requests_command(update, context)
        elif text == "📝 Создать промокод":
            await update.message.reply_text(
                "Используйте команду /create_promo <код> <звёзды>\n"
                "Пример: /create_promo SUMMER2026 100"
            )
        elif text == "📋 Установить задание дня":
            await update.message.reply_text(
                "Используйте команду /set_daily_task <текст задания>\n"
                "Пример: /set_daily_task Пригласи 3 друзей и получи +5⭐️"
            )
        elif text == "🔙 Назад":
            await update.message.reply_text("🔙 Возврат в главное меню.", reply_markup=main_keyboard())
        else:
            await update.message.reply_text("👑 Панель администратора", reply_markup=admin_keyboard())
    else:
        await update.message.reply_text("Используй кнопки меню.")

# ---------- АДМИН-КОМАНДЫ ----------
async def create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Использование: /create_promo <код> <звёзды>")
        return
    code = context.args[0].upper()
    try:
        stars = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Звёзды должны быть числом.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO promos(code, stars, used_by) VALUES(?,?,?)", (code, stars, ""))
        await db.commit()
    await update.message.reply_text(f"✅ Промокод {code} на {stars}⭐️ создан.")

async def pending_requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас нет прав.")
        return
    requests = await get_pending_requests()
    if not requests:
        await update.message.reply_text("📭 Нет активных заявок на вывод.")
        return
    text = "📋 *Активные заявки на вывод:*\n\n"
    keyboard_buttons = []
    for req_id, uid, amount, gift, created in requests:
        user = await get_user(uid)
        name = user[2] or user[1] or f"ID{uid}" if user else f"ID{uid}"
        text += f"#{req_id} | {name} | {gift} | {amount}⭐ | {created[:16]}\n"
        keyboard_buttons.append([
            InlineKeyboardButton(f"✅ Одобрить #{req_id}", callback_data=f"approve_{req_id}"),
            InlineKeyboardButton(f"❌ Отклонить #{req_id}", callback_data=f"reject_{req_id}")
        ])
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return
    total = await get_total_users_count()
    await update.message.reply_text(f"📊 *Всего зарегистрировано:* {total} пользователей.", parse_mode=ParseMode.MARKDOWN)

async def set_daily_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /set_daily_task <текст задания>")
        return
    task_text = " ".join(context.args)
    await set_daily_task_text(task_text)
    await update.message.reply_text(f"✅ Задание дня установлено:\n{task_text}")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас нет прав.")
        return
    await update.message.reply_text("👑 Панель администратора", reply_markup=admin_keyboard())

# ---------- ЕЖЕДНЕВНЫЙ СБРОС ----------
async def daily_reset(context: ContextTypes.DEFAULT_TYPE):
    prizes = await reset_daily_and_reward()
    logging.info(f"Топ сброшен, награды: {prizes}")

# ---------- ЗАПУСК ----------
logging.basicConfig(level=logging.INFO)

async def setup():
    await init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    global BOT_USERNAME
    me = await app.bot.get_me()
    BOT_USERNAME = me.username

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("create_promo", create_promo))
    app.add_handler(CommandHandler("pending_requests", pending_requests_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("set_daily_task", set_daily_task))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Настройка JobQueue (если установлен)
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(daily_reset, time=time(hour=RESET_HOUR, minute=RESET_MINUTE))
        logging.info("✅ JobQueue настроен, ежедневный сброс активен.")
    else:
        logging.warning("⚠️ JobQueue не установлен. Ежедневный сброс топа не будет работать. Установите python-telegram-bot[job-queue].")

    logging.info("Бот инициализирован")
    return app

if __name__ == "__main__":
    app = asyncio.run(setup())
    asyncio.run(app.run_polling())
