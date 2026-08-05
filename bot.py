import asyncio
import logging
import os
import re
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

# ===== Конфигурация =====
BOT_TOKEN = "8675621032:AAHKU2EeS0GMw5eWCG8T-zMYYVv6vLvUiN0"
DB_PATH = "bot_database.db"

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

ADMIN_IDS = [8798104630, 5078387190]

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ===== БД =====
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

# ===== Клавиатуры =====
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["✨ Кликер звёзд", "👤 Профиль"],
            ["💰 Заработать звёзды", "💸 Вывод звёзд"],
            ["📖 Инструкция", "🏆 Топ", "⭐ Отзывы"]
        ],
        resize_keyboard=True
    )

# ===== ВСЕ ОБРАБОТЧИКИ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Команда /start от {update.effective_user.id}")
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
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
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
    keyboard = InlineKeyboardMarkup([InlineKeyboardButton(name, callback_data=code) for name, code in gifts])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def instruction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Инструкция по использованию бота*\n\n"
        "1️⃣ *Кликер звёзд* – нажимай каждые 3 минуты и получай +0.20 ⭐.\n"
        "2️⃣ *Заработок* – приглашай друзей по своей ссылке, получай +3 ⭐ за каждого.\n"
        "3️⃣ *Топ* – чем больше друзей ты приведёшь за день, тем выше в топе. "
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

# ===== CALLBACK =====
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    context.user_data.pop("expecting_promo", None)
    context.user_data.pop("expecting_transfer", None)

    if data.startswith("admin_"):
        if not is_admin(user_id):
            await query.edit_message_text("⛔ Нет доступа.")
            return

        if data == "admin_create_promo":
            context.user_data["admin_action"] = "create_promo"
            await query.edit_message_text("🎟 Введите промокод и звёзды в одном сообщении:\n`CODE 150` (например: WELCOME 150)")
            return

        elif data == "admin_list_promo":
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT code, stars, used_by FROM promos")
                promos = await cursor.fetchall()
            if not promos:
                text = "📋 Промокодов пока нет."
            else:
                lines = []
                for code, stars, used_str in promos:
                    used = len(used_str.split(",")) if used_str else 0
                    lines.append(f"• `{code}` — {stars} ⭐ (использовано: {used})")
                text = "📋 Список всех промокодов:\n\n" + "\n".join(lines)
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
            return

        elif data == "admin_reset_daily":
            await query.edit_message_text("⏳ Выполняется сброс топа и раздача призов...")
            await reset_daily_and_reward()
            await query.edit_message_text("✅ Топ сброшен! Призы разосланы.")

        elif data == "admin_balance_menu":
            context.user_data["admin_action"] = "balance_menu"
            text = "💰 Выберите действие:"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить звёзды", callback_data="admin_add_balance")],
                [InlineKeyboardButton("👀 Посмотреть баланс", callback_data="admin_view_balance")],
                [InlineKeyboardButton("⬅ Назад", callback_data="admin_back")]
            ])
            await query.edit_message_text(text, reply_markup=keyboard)
            return

        elif data == "admin_myid":
            await query.edit_message_text(f"🆔 Ваш ID: `{user_id}`")

        elif data == "admin_exit":
            await query.edit_message_text("👋 До свидания!", reply_markup=main_keyboard())

        elif data == "admin_back":
            await query.edit_message_text("👑 Админ-панель", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Создать промокод", callback_data="admin_create_promo"),
                 InlineKeyboardButton("📋 Промокоды", callback_data="admin_list_promo")],
                [InlineKeyboardButton("🔄 Сбросить ТОП", callback_data="admin_reset_daily"),
                 InlineKeyboardButton("💰 Баланс", callback_data="admin_balance_menu")]
            ]))

        elif data == "admin_add_balance":
            context.user_data["admin_action"] = "add_balance"
            await query.edit_message_text("💰 Добавление звёзд\n\nВведите в формате:\n`user_id сумма`\nПример: `1234567890 100.5`")
            return

        elif data == "admin_view_balance":
            context.user_data["admin_action"] = "view_balance"
            await query.edit_message_text("👀 Введите user_id:")
            return

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
        bot_username = context.bot.username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
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
            gifts = await context.bot.get_available_gifts()
            matching = [g for g in gifts if g.total_amount == price]
            if not matching:
                await query.edit_message_text(f"❌ К сожалению, подарков стоимостью {price} ⭐ сейчас нет в наличии.")
                return
            gift = matching[0]
            await update_balance(user_id, -price)
            await context.bot.send_gift(
                chat_id=user_id,
                gift_id=gift.id,
                text="🎁 Поздравляем! Это ваш подарок от бота!"
            )
            await query.edit_message_text(f"✅ *Подарок успешно отправлен!* Списано {price} ⭐.\nНаслаждайся! 🎉")
        except Exception as e:
            await update_balance(user_id, price)
            await query.edit_message_text(f"❌ Не удалось отправить подарок: {e}")

# ===== ОБРАБОТЧИК ТЕКСТА =====
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if context.user_data.get("admin_action") == "create_promo":
        context.user_data.pop("admin_action")
        try:
            code, stars_str = text.split(maxsplit=1)
            stars = float(stars_str)
        except:
            await update.message.reply_text("❌ Неверный формат. Используй: CODE 150")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO promos(code, stars, used_by) VALUES(?,?,?)", (code.upper(), stars, ""))
            await db.commit()
        await update.message.reply_text(f"✅ Промокод *{code.upper()}* на *{stars} ⭐* создан!", parse_mode=ParseMode.MARKDOWN)
        return

    if context.user_data.get("admin_action") == "add_balance":
        context.user_data.pop("admin_action")
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit():
            await update.message.reply_text("❌ Неверный формат. Используй: user_id сумма")
            return
        target_id = int(parts[0])
        try:
            amount = float(parts[1])
        except ValueError:
            await update.message.reply_text("❌ Сумма должна быть числом.")
            return
        await update_balance(target_id, amount)
        await update.message.reply_text(f"✅ Админу добавлено {amount:.2f} ⭐ пользователю {target_id}")
        return

    if context.user_data.get("admin_action") == "view_balance":
        context.user_data.pop("admin_action")
        try:
            target_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ Введи число (user_id).")
            return
        user = await get_user(target_id)
        if not user:
            await update.message.reply_text("❌ Пользователь не найден.")
            return
        balance = user[3]
        await update.message.reply_text(f"💰 Баланс пользователя {target_id}: *{balance:.2f} ⭐*", parse_mode=ParseMode.MARKDOWN)
        return

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
        if len(parts) != 2:
            await update.message.reply_text("❌ Неверный формат. Укажите ID друга и сумму через пробел.\nПример: `12345678 10`")
            return
        if not parts[0].isdigit():
            await update.message.reply_text("❌ ID должен быть числом.")
            return
        if not re.match(r'^\d+(\.\d+)?$', parts[1]):
            await update.message.reply_text("❌ Сумма должна быть числом (целым или дробным).")
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
            f"✅ *Перевод выполнен!*\nПереведено {amount:.2f} ⭐ пользователю ID `{target_id}`.",
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

# ===== АДМИН-ПАНЕЛЬ =====
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"Команда /admin от {user_id} (is_admin={is_admin(user_id)})")

    if not is_admin(user_id):
        await update.message.reply_text(
            f"⛔ У вас нет доступа к данной команде.\n\n"
            f"Ваш ID: `{user_id}`\n"
            f"Добавьте его в список ADMIN_IDS в файле bot.py и перезапустите бота.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    text = (
        "👑 *Админ-панель*\n\n"
        "Выберите действие:"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Создать промокод", callback_data="admin_create_promo"),
            InlineKeyboardButton("📋 Промокоды", callback_data="admin_list_promo")
        ],
        [
            InlineKeyboardButton("🔄 Сбросить ТОП", callback_data="admin_reset_daily"),
            InlineKeyboardButton("💰 Баланс", callback_data="admin_balance_menu")
        ],
        [
            InlineKeyboardButton("👀 Мой ID", callback_data="admin_myid"),
            InlineKeyboardButton("🚪 Выйти", callback_data="admin_exit")
        ]
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_panel(update, context)

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_panel(update, context)

# ===== ЕЖЕДНЕВНЫЙ СБРОС =====
async def daily_reset(context: ContextTypes.DEFAULT_TYPE):
    prizes = await reset_daily_and_reward()
    logger.info(f"Топ сброшен, награды: {prizes}")

# ===== ГЛАВНАЯ ФУНКЦИЯ =====
def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(init_db())

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("create_promo", create_promo))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.job_queue.run_daily(daily_reset, time=time(hour=RESET_HOUR, minute=RESET_MINUTE))

    logger.info("Бот инициализирован и готов к работе")

    webhook_url = os.getenv("WEBHOOK_URL")
    if webhook_url:
        loop.run_until_complete(app.bot.set_webhook(url=webhook_url))
        port = int(os.getenv("PORT", 8080))
        app.run_webhook(listen="0.0.0.0", port=port)
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
