# --- АДМИН-КОМАНДА /sms ---

@router.message(F.text.startswith("/sms"))
async def cmd_sms(message: Message):
    # Проверяем, является ли пользователь администратором
    if message.from_user.id != Config.ADMIN_ID:
        return

    # Извлекаем текст после команды /sms
    parts = message.text.split(maxsplit=1)
    
    # Удаляем исходное сообщение администратора с командой
    try:
        await message.delete()
    except Exception as e:
        import logging
        logging.error(f"Не удалось удалить сообщение с командой /sms: {e}")

    # Если после команды не указан текст
    if len(parts) < 2:
        await message.answer("⚠️ Пожалуйста, укажите текст после команды /sms.")
        return

    sms_text = parts[1]

    # Отправляем сообщение в чат от имени бота
    try:
        await message.bot.send_message(chat_id=message.chat.id, text=sms_text)
    except Exception as e:
        import logging
        logging.error(f"🔴 Ошибка отправки сообщения /sms: {e}")
