import asyncio
import logging
import os
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

BOT_TOKEN = "8675621032:AAHKU2EeS0GMw5eWCG8T-zMYYVv6vLvUiN0"
DB_PATH = "bot_database.db"
BOT_USERNAME = None

CLICKER_COOLDOWN = 180
CLICKER_REWARD = 0.20

REFERRAL_BONUS = 3.0
OLD_FRIENDS_BONUS = 2.0
DAILY_BIO_BONUS = 1.0

TOP_PRIZES = {
    1: 200, 2: 100, 3: 50, 4: 40, 5: 35,
    6: 30, 7: 25, 8: 20, 9: 15, 10: 10,
}

RESET_HOUR = 0
RESET_MINUTE = 0

ADMIN_ID = 123456789

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
        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE daily_invites > ?", (user_invites,))
        count = await cursor.fetchone()
        return count[0] + 1

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

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["✨ Кликер звёзд", "👤 Профиль"],
            ["💰 Заработать звёзды", "💸 Вывод звёзд"],
            ["📖 Инструкция", "🏆 Топ", "⭐ Отзывы"]
        ],
        resize_keyboard=True
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        welcome = (
            "🌟 Привет! Я бот для заработка звёзд Telegram.\n"
            "Используй кнопки меню, чтобы начать.\n\n"
            "Кратко:\n"
            "• Кликер – +0.20 ⭐ каждые 3 минуты\n"
            "• Приглашай друзей – +3 ⭐ за каждого\n"
            "• Топ 10 – призы до 200 ⭐ в конце дня\n"
            "• Выводи звёзды на подарки\n"
            "• Ежедневный бонус +1 ⭐ за ссылку в профиле\n\n"
            "Удачи!"
        )
    else:
        welcome = "👋 С возвращением! Используй кнопки меню."

    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def clicker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await update.message.reply_text(f"⏳ Подожди ещё {m} мин {s} сек до следующего клика.")
            return
    await update_balance(user_id, CLICKER_REWARD)
    await set_field(user_id, "last_click_time", now.isoformat())
    await update.message.reply_text(
        f"✨ Клик успешен!\n\nВы получили *{CLICKER_REWARD:.2f} ⭐* за специальное задание.\n"
        f"Твой баланс пополнен. Возвращайся через 3 минуты за новой наградой! 🚀",
        parse_mode=ParseMode.MARKDOWN
    )

async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала /start")
        return
    _, username, first_name, balance, _, total_inv, activated, daily_inv, *_ = user[:9]
    display_name = first_name or username or "Пользователь"
    text = (
        f"👤 *Профиль*\n"
        f"──────────────\n"
        f"💬 Имя: {display_name}\n"
        f"🆔 ID: `{user_id}`\n"
        f"👤 Username: @{username if username else 'нет'}\n"
        f"──────────────\n"
        f"👥 Всего друзей: *{total_inv}*\n"
        f"✅ Активировали бота: *{activated}*\n"
        f"💰 Баланс: *{balance:.2f} ⭐*\n\n"
        f"📌 Как получить ежедневный бонус?\n"
        f"Добавь свою реферальную ссылку в описание профиля Telegram "
        f"и получай *+{DAILY_BIO_BONUS:.0f} ⭐* каждый день.\n\n"
        f"⬇️ Доступные действия:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Ввести промокод", callback_data="promo")],
        [InlineKeyboardButton("☀️ Ежедневный бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton("💸 Отправить звёзды другу", callback_data="send_stars")],
        [InlineKeyboardButton("➕ +2⭐ за старых друзей", callback_data="old_friends")],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def earn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала /start")
        return
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    text = (
        "🚀 *Приглашай друзей и зарабатывай звёзды!*\n\n"
        f"За каждого нового пользователя, который перешёл по твоей ссылке и активировал бота, "
        f"ты получаешь *{REFERRAL_BONUS:.0f} ⭐*!\n\n"
        "🔗 *Твоя личная ссылка (нажми, чтобы скопировать):*\n"
        f"`{ref_link}`\n\n"
        "📢 *Где и как распространять ссылку:*\n"
        "• Отправь друзьям в личные сообщения\n"
        "• Поделись в историях и каналах\n"
        "• Оставь в комментариях и чатах\n"
        "• Размести в социальных сетях (TikTok, Instagram, WhatsApp и др.)\n\n"
        "💡 *Совет:* чем активнее ты приглашаешь, тем выше твой рейтинг в топе, "
        "а значит, ты можешь получить ещё больше звёзд в конце дня!\n\n"
        "❗️ Если у тебя уже есть друзья, которые давно пользуются ботом, "
        "нажми кнопку ниже и получи бонус *+2⭐* за каждого старого друга."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ +2⭐ за старых друзей", callback_data="old_friends")]
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала /start")
        return
    balance = user[3]
    text = (
        f"🎁 *Вывод звёзд на подарки*\n\n"
        f"💰 Твой баланс: *{balance:.2f} ⭐*\n\n"
        "📋 *Условие вывода:*\n"
        "• Минимум 5 приглашённых друзей (активировавших бота)\n\n"
        "✅ Вывод происходит мгновенно! Выбери желаемый подарок ниже:"
    )
    gifts = [
        ("🧸 15⭐ – Мишка-сердце", "gift_15"),
        ("🌹 25⭐ – Роза", "gift_25"),
        ("🍾 50⭐ – Шампанское / Букет / Ракета / Торт", "gift_50"),
        ("🏆 100⭐ – Кубок / Кольцо / Алмаз", "gift_100"),
    ]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(name, callback_data=code)] for name, code in gifts
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def instruction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Инструкция по использованию бота*\n\n"
        "1️⃣ *Кликер звёзд* – нажимай каждые 3 минуты и получай +0.20 ⭐.\n"
        "2️⃣ *Заработок* – приглашай друзей по своей ссылке, получай +3 ⭐ за каждого.\n"
        "3️⃣ *Топ* – чем больше друзей ты приведешь за день, тем выше в топе. "
        "В конце дня лучшие 10 получают призы от 10 до 200 ⭐.\n"
        "4️⃣ *Вывод* – обменивай звёзды на реальные подарки Telegram.\n"
        "5️⃣ *Бонусы* – добавляй ссылку в описание профиля и забирай +1 ⭐ ежедневно, "
        "а также +2 ⭐ за каждого уже приглашённого друга (однократно).\n\n"
        "🔥 *Совет:* активные участники зарабатывают больше! Приглашай как можно больше людей."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def top_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    top = await get_top10_daily()
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    if not top:
        text = "🏆 *Топ 10 за сегодня*\n\nПока пусто. Будь первым, кто приведёт друзей!"
    else:
        lines = []
        for i, (uid, uname, fname, invites) in enumerate(top):
            name = fname or uname or f"ID{uid}"
            lines.append(f"{medals[i]} {name} – {invites} друзей")
        text = "🏆 *Топ 10 за сегодня*\n\n" + "\n".join(lines)

    rank = await get_user_rank_daily(user_id)
    if rank:
        text += f"\n\n✨ *Твой рейтинг:* {rank}-е место!"
    else:
        text += "\n\n✨ Ты пока не в топе за сегодня. Приглашай больше друзей!"

    prize_lines = []
    for pos, stars in TOP_PRIZES.items():
        medal = medals[pos-1] if pos <= 10 else f"{pos}."
        prize_lines.append(f"{medal} – {stars} ⭐")
    text += "\n\n🎁 *Награды за 1–10 места в конце дня:*\n" + "\n".join(prize_lines)

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def reviews_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⭐ *Отзывы о боте*\n\n"
        "Читай отзывы наших пользователей и оставляй свой в канале:\n"
        "👉 https://t.me/repa_yherova",
        parse_mode=ParseMode.MARKDOWN
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "promo":
        context.user_data["expecting_promo"] = True
        await query.edit_message_text("🎟 Введи промокод (отправь текстовым сообщением):")
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
                await query.edit_message_text("☀️ Ты уже получал сегодняшний бонус. Возвращайся завтра!")
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
            await query.edit_message_text(f"✅ *Бонус зачислен!* +{DAILY_BIO_BONUS:.0f} ⭐")
        else:
            await query.edit_message_text(
                "❌ Не удалось найти твою реферальную ссылку в описании профиля.\n"
                "Добавь её и попробуй завтра.\n\n"
                f"Твоя ссылка: `{ref_link}`",
                parse_mode=ParseMode.MARKDOWN
            )
        return
    elif data == "send_stars":
        context.user_data["expecting_transfer"] = True
        await query.edit_message_text(
            "💸 *Отправка звёзд другу*\n\n"
            "Введи ID друга и сумму звёзд в формате:\n"
            "`ID_друга КОЛИЧЕСТВО`\n\n"
            "Пример: `12345678 10`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    elif data == "old_friends":
        user = await get_user(user_id)
        if not user:
            await query.edit_message_text("Сначала /start")
            return
        if user[10]:
            await query.edit_message_text("✅ Ты уже получал бонус за старых друзей.")
            return
        if user[5] == 0:
            await query.edit_message_text("У тебя пока нет приглашённых друзей. Пригласи их и приходи снова!")
            return
        await update_balance(user_id, OLD_FRIENDS_BONUS)
        await set_field(user_id, "claimed_old_friends_bonus", 1)
        await query.edit_message_text(f"✅ *Бонус за старых друзей зачислен!* +{OLD_FRIENDS_BONUS:.0f} ⭐")
        return
    elif data.startswith("gift_"):
        prices = {"gift_15": 15, "gift_25": 25, "gift_50": 50, "gift_100": 100}
        price = prices.get(data)
        if not price:
            await query.edit_message_text("❌ Ошибка: неизвестный подарок.")
            return
        user = await get_user(user_id)
        if not user:
            await query.edit_message_text("Сначала /start")
            return
        if user[6] < 5:
            await query.edit_message_text(
                "❌ Условие не выполнено: необходимо минимум 5 активировавших бота друзей.\n"
                f"У тебя пока {user[6]}."
            )
            return
        if user[3] < price:
            await query.edit_message_text(f"❌ Недостаточно звёзд. Нужно {price} ⭐, у тебя {user[3]:.2f}.")
            return
        try:
            available = await context.bot.get_available_gifts()
        except Exception as e:
            await query.edit_message_text(f"Ошибка при получении списка подарков: {e}")
            return
        matching = [g for g in available if g.total_amount == price]
        if not matching:
            await query.edit_message_text(f"❌ К сожалению, подарков стоимостью {price} ⭐ сейчас нет в наличии.")
            return
        gift = matching[0]
        await update_balance(user_id, -price)
        try:
            await context.bot.send_gift(chat_id=user_id, gift_id=gift.id, text="🎁 Поздравляем! Это ваш подарок от бота!")
            await query.edit_message_text(f"✅ *Подарок успешно отправлен!* Списано {price} ⭐.\nНаслаждайся! 🎉")
        except Exception as e:
            await update_balance(user_id, price)
            await query.edit_message_text(f"❌ Не удалось отправить подарок: {e}")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if context.user_data.get("expecting_promo"):
        context.user_data.pop("expecting_promo")
        code = text.upper()
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT stars, used_by FROM promos WHERE code=?", (code,))
            promo = await cursor.fetchone()
            if not promo:
                await update.message.reply_text("❌ Неверный промокод. Попробуйте ещё раз.")
                return
            stars, used_str = promo
            used = used_str.split(",") if used_str else []
            if str(user_id) in used:
                await update.message.reply_text("❌ Вы уже использовали этот промокод.")
                return
            used.append(str(user_id))
            await db.execute("UPDATE promos SET used_by=? WHERE code=?", (",".join(used), code))
            await db.commit()
        await update_balance(user_id, stars)
        await update.message.reply_text(f"✅ *Промокод активирован!* +{stars:.0f} ⭐ зачислено на счёт.", parse_mode=ParseMode.MARKDOWN)
        return

    if context.user_data.get("expecting_transfer"):
        context.user_data.pop("expecting_transfer")
        parts = text.split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].replace('.','',1).isdigit():
            await update.message.reply_text(
                "❌ Неверный формат. Укажите ID друга и сумму через пробел.\n"
                "Пример: `12345678 10`",
                parse_mode=ParseMode.MARKDOWN
            )
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
            await update.message.reply_text("❌ Недостаточно звёзд на счету.")
            return
        target = await get_user(target_id)
        if not target:
            await update.message.reply_text("❌ Получатель с таким ID не найден в боте.")
            return
        await update_balance(user_id, -amount)
        await update_balance(target_id, amount)
        await update.message.reply_text(
            f"✅ *Перевод выполнен!*\n"
            f"Переведено {amount:.2f} ⭐ пользователю ID `{target_id}`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if text == "✨ Кликер звёзд":
        await clicker_handler(update, context)
    elif text == "👤 Профиль":
        await profile_handler(update, context)
    elif text == "💰 Заработать звёзды":
        await earn_handler(update, context)
    elif text == "💸 Вывод звёзд":
        await withdraw_handler(update, context)
    elif text == "📖 Инструкция":
        await instruction_handler(update, context)
    elif text == "🏆 Топ":
        await top_handler(update, context)
    elif text == "⭐ Отзывы":
        await reviews_handler(update, context)
    else:
        await update.message.reply_text(
            "🤔 Неизвестная команда. Используй кнопки меню для навигации.",
            reply_markup=main_keyboard()
        )

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
    await update.message.reply_text(f"✅ Промокод *{code}* на *{stars} ⭐* создан!", parse_mode=ParseMode.MARKDOWN)

async def daily_reset(context: ContextTypes.DEFAULT_TYPE):
    prizes = await reset_daily_and_reward()
    logging.info(f"Топ сброшен, награды: {prizes}")

logging.basicConfig(level=logging.INFO)

async def main():
    await init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    global BOT_USERNAME
    me = await app.bot.get_me()
    BOT_USERNAME = me.username
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("create_promo", create_promo))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.job_queue.run_daily(daily_reset, time=time(hour=RESET_HOUR, minute=RESET_MINUTE))
    logging.info("Бот инициализирован и готов к работе")

    webhook_url = os.getenv("WEBHOOK_URL")
    if webhook_url:
        port = int(os.getenv("PORT", 8080))
        await app.bot.set_webhook(url=webhook_url)
        await app.initialize()
        await app.start()
        await app.updater.start_webhook(listen="0.0.0.0", port=port)
        await asyncio.Event().wait()
    else:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
