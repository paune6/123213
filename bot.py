import asyncio
import logging
import os
from datetime import datetime, time
from typing import Optional

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

REQUIRED_CHANNELS = ["@mpvpavlo", "@pavelgifsts"]  # каналы для обязательной подписки

# Настройки кликера
CLICKER_COOLDOWN = 180  # 3 минуты
CLICKER_REWARD = 0.20

# Реферальные бонусы
REFERRAL_BONUS = 3.0
OLD_FRIENDS_BONUS = 2.0
DAILY_BIO_BONUS = 1.0

# Призы топа
TOP_PRIZES = {
    1: 200, 2: 100, 3: 50, 4: 40, 5: 35,
    6: 30, 7: 25, 8: 20, 9: 15, 10: 10,
}

# Время сброса топа (UTC)
RESET_HOUR = 0
RESET_MINUTE = 0

# ID администратора (для create_promo) – измените на свой
ADMIN_ID = 123456789  # замените на ваш Telegram ID

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
        row = await db.execute_fetchall("SELECT daily_invites FROM users WHERE user_id=?", (user_id,))
        if not row or row[0][0] == 0:
            return 0
        user_invites = row[0][0]
        count = await db.execute_fetchall(
            "SELECT COUNT(*) FROM users WHERE daily_invites > ?", (user_invites,)
        )
        return count[0][0] + 1

async def reset_daily_and_reward():
    async with aiosqlite.connect(DB_PATH) as db:
        top = await db.execute_fetchall(
            "SELECT user_id, daily_invites FROM users WHERE daily_invites > 0 ORDER BY daily_invites DESC LIMIT 10"
        )
        prizes = {}
        for idx, (uid, count) in enumerate(top, start=1):
            if idx in TOP_PRIZES:
                prizes[uid] = TOP_PRIZES[idx]
        for uid, stars in prizes.items():
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (stars, uid))
        await db.execute("UPDATE users SET daily_invites = 0")
        await db.commit()
        return prizes

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
            [InlineKeyboardButton(f"Подписаться на {ch}", url=f"https://t.me/{ch[1:]}")]
            for ch in not_subscribed
        ])
        text = f"❌ Для использования бота необходимо подписаться на каналы:\n{channels_text}\n\nПосле подписки вернитесь и нажмите /start или любую кнопку меню."
        if update.message:
            await update.message.reply_text(text, reply_markup=keyboard)
        elif update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        return False
    return True

# ---------- ГЛАВНОЕ МЕНЮ ----------
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["Кликер звезд", "Профиль"],
            ["Заработать звезды", "Вывод звезд"],
            ["Инструкция", "Топ", "Отзывы"]
        ],
        resize_keyboard=True
    )

# ---------- ОБРАБОТЧИКИ КОМАНД ----------
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
    if not existing:
        await add_user(user_id, user.username or "", user.first_name or "", referrer_id)
        if referrer_id and referrer_id != user_id:
            referrer = await get_user(referrer_id)
            if referrer:
                await increment_referral(referrer_id)
                await update_balance(referrer_id, REFERRAL_BONUS)
        welcome = "✅ Добро пожаловать! Используй кнопки меню."
    else:
        welcome = "С возвращением! Ты уже зарегистрирован."

    await update.message.reply_text(welcome, reply_markup=main_keyboard())

# ---------- КЛИКЕР ----------
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

# ---------- ПРОФИЛЬ ----------
async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала /start")
        return
    _, username, first_name, balance, _, total_inv, activated, daily_inv, *_ = user[:9]
    display_name = first_name or username or "Пользователь"
    text = (
        f"✨ Профиль\n"
        f"──────────────\n"
        f"💬 Имя: {display_name}\n"
        f"🆔 ID: {user_id}\n"
        f"👤 Username: @{username if username else 'нет'}\n"
        f"──────────────\n"
        f"👥 Всего друзей: {total_inv}\n"
        f"✅ Активировали бота: {activated}\n"
        f"💰 Баланс: {balance:.2f} ⭐️\n\n"
        f"⁉️ Как получить ежедневный бонус?\n"
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
    await update.message.reply_text(text, reply_markup=keyboard)

# ---------- ЗАРАБОТАТЬ ЗВЕЗДЫ ----------
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
        f"😎 Приглашай друзей и получай по {REFERRAL_BONUS:.0f} ⭐️ от Патрика "
        f"за каждого, кто активирует бота по твоей ссылке!\n\n"
        f"🔗 Твоя личная ссылка (нажми чтобы скопировать):\n"
        f"`{ref_link}`\n\n"
        f"🚀 Как набрать много переходов?\n"
        f"• Отправь её друзьям в личные сообщения 👥\n"
        f"• Поделись ссылкой в истории/канале 📱\n"
        f"• Оставь в комментариях или чатах 🗨\n"
        f"• Распространяй в других соцсетях 🌍\n\n"
        f"❗️ Чтобы повторно получить награду за уже приглашённых друзей, "
        f"жми кнопку «+2⭐️ за старых друзей»"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ +2⭐️ за старых друзей", callback_data="old_friends")]
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

# ---------- ВЫВОД ЗВЕЗД ----------
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
        f"💰 Баланс: {balance:.2f} ⭐️\n\n"
        f"‼️ Для вывода требуется:\n"
        f"— Минимум 5 приглашённых друзей, активировавших бота\n"
        f"— Быть подписанным на @mpvpavlo и @pavelgifsts\n\n"
        f"✅ Моментальный автоматический вывод!\n"
        f"Выбери подарок:"
    )
    gifts = [
        ("15⭐️ Мишка-сердце", "gift_15"),
        ("25⭐️ Роза", "gift_25"),
        ("50⭐️ Шампанское / Букет / Ракета / Торт", "gift_50"),
        ("100⭐️ Кубок / Кольцо / Алмаз", "gift_100"),
    ]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(name, callback_data=code)] for name, code in gifts
    ])
    await update.message.reply_text(text, reply_markup=keyboard)

# ---------- ИНСТРУКЦИЯ ----------
async def instruction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    text = (
        "📘 Как набрать много переходов по ссылке?\n"
        "• Отправь её друзьям в личные сообщения\n"
        "• Поделись в истории/канале\n"
        "• Оставь в комментариях или чатах\n"
        "• Распространяй в TikTok, Instagram, WhatsApp и других"
    )
    await update.message.reply_text(text)

# ---------- ТОП ----------
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
        text = "🏆 Топ 10 за день:\n" + "\n".join(lines)
    rank = await get_user_rank_daily(user_id)
    if rank:
        text += f"\n\n✨ Ты на {rank}-м месте!"
    else:
        text += "\n\n✨ Ты пока не в топе за этот день..."
    text += (
        "\n\nПопади в топ и получи приз в конце дня:\n"
        "1-е +200⭐️, 2-е +100⭐️, 3-е +50⭐️, 4-е +40⭐️,\n"
        "5-е +35⭐️, 6-е +30⭐️, 7-е +25⭐️, 8-е +20⭐️,\n"
        "9-е +15⭐️, 10-е +10⭐️"
    )
    await update.message.reply_text(text)

# ---------- ОТЗЫВЫ ----------
async def reviews_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return
    await update.message.reply_text("Наш канал отзывов: https://t.me/repa_yherova")

# ---------- ОБРАБОТКА CALLBACK-ЗАПРОСОВ ----------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if not await check_subscription(update, context):
        return

    if data == "promo":
        context.user_data["expecting_promo"] = True
        await query.edit_message_text("Введи промокод (отправь текстом):")
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
                await query.edit_message_text("Ты уже получал дневной бонус сегодня.")
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
            "Отправь ID друга и количество звёзд в формате:\n"
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
        prices = {"gift_15": 15, "gift_25": 25, "gift_50": 50, "gift_100": 100}
        price = prices.get(data)
        if not price:
            await query.edit_message_text("Ошибка: неизвестный подарок.")
            return
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
        try:
            available = await context.bot.get_available_gifts()
        except Exception as e:
            await query.edit_message_text(f"Ошибка получения списка подарков: {e}")
            return
        matching = [g for g in available if g.total_amount == price]
        if not matching:
            await query.edit_message_text(f"Нет подарков стоимостью {price}⭐️.")
            return
        gift = matching[0]
        await update_balance(user_id, -price)
        try:
            await context.bot.send_gift(chat_id=user_id, gift_id=gift.id, text="Поздравляем! Ваш подарок от бота.")
            await query.edit_message_text(f"✅ Подарок отправлен! Списано {price}⭐️.")
        except Exception as e:
            await update_balance(user_id, price)
            await query.edit_message_text(f"❌ Ошибка при отправке подарка: {e}")

# ---------- ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ----------
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
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].replace('.','',1).isdigit():
            await update.message.reply_text("❌ Неверный формат. Пример: 12345678 10")
            return
        target_id = int(parts[0])
        amount = float(parts[1])
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

    if text == "Кликер звезд":
        await clicker_handler(update, context)
    elif text == "Профиль":
        await profile_handler(update, context)
    elif text == "Заработать звезды":
        await earn_handler(update, context)
    elif text == "Вывод звезд":
        await withdraw_handler(update, context)
    elif text == "Инструкция":
        await instruction_handler(update, context)
    elif text == "Топ":
        await top_handler(update, context)
    elif text == "Отзывы":
        await reviews_handler(update, context)
    else:
        await update.message.reply_text("Используй кнопки меню.")

# ---------- АДМИН-КОМАНДА: СОЗДАНИЕ ПРОМОКОДА ----------
async def create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Защита: только администратор
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
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
    await update.message.reply_text(f"Промокод {code} на {stars}⭐️ создан.")

# ---------- ЕЖЕДНЕВНЫЙ СБРОС ТОПА ----------
async def daily_reset(context: ContextTypes.DEFAULT_TYPE):
    prizes = await reset_daily_and_reward()
    logging.info(f"Топ сброшен, награды: {prizes}")

# ---------- ЗАПУСК ----------
logging.basicConfig(level=logging.INFO)

async def setup():
    """Инициализация БД, создание приложения и настройка обработчиков."""
    await init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    global BOT_USERNAME
    me = await app.bot.get_me()
    BOT_USERNAME = me.username

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("create_promo", create_promo))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    job_queue = app.job_queue
    job_queue.run_daily(daily_reset, time=time(hour=RESET_HOUR, minute=RESET_MINUTE))

    logging.info("Бот инициализирован")
    return app

if __name__ == "__main__":
    # Сначала инициализация
    app = asyncio.run(setup())
    # Затем запуск поллинга в отдельном цикле
    asyncio.run(app.run_polling())