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

ADMIN_IDS = [8798104630, 5078387190]  # ← добавь свои ID сюда

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

# ===== Обработчики (без изменений) =====
# (все функции start, clicker_handler, profile_handler, earn_handler, withdraw_handler, 
#  instruction_handler, top_handler, reviews_handler, callback_handler, message_handler, 
#  create_promo, myid остаются точно такими же, как в предыдущей версии)

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

# ... (остальные функции без изменений — copy-paste из предыдущего ответа, если нужно)

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
        "Доступные команды:\n"
        "/create_promo <код> <звёзды> – создать промокод\n"
        "/admin – показать это меню"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ===== Команды бота =====
async def create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"Команда /create_promo от {user_id}")
    if not is_admin(user_id):
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
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

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"🆔 *Ваш ID:* `{user_id}`\n\n"
        "Используйте этот ID для добавления в список администраторов.",
        parse_mode=ParseMode.MARKDOWN
    )

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
