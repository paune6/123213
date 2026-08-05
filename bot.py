import asyncio

# ... остальной код ...

def main():
    # 1. Синхронно инициализируем БД (запускаем корутину через asyncio.run)
    asyncio.run(init_db())

    # 2. Строим приложение
    app = Application.builder().token(BOT_TOKEN).build()

    # 3. Добавляем все обработчики (как у вас)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("create_promo", create_promo))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.job_queue.run_daily(daily_reset, time=time(hour=RESET_HOUR, minute=RESET_MINUTE))

    logging.info("Бот инициализирован и готов к работе")

    webhook_url = os.getenv("WEBHOOK_URL")
    if webhook_url:
        # Установка вебхука — асинхронная операция, выполняем её отдельно
        asyncio.run(app.bot.set_webhook(url=webhook_url))
        port = int(os.getenv("PORT", 8080))
        # run_webhook — синхронный метод, вызываем без await
        app.run_webhook(listen="0.0.0.0", port=port)
    else:
        # run_polling — синхронный метод, вызываем без await
        app.run_polling()

if __name__ == "__main__":
    main()
