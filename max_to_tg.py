import asyncio
import logging
import json
import os
from pymax import MaxClient
from pymax.filters import Filter
from pymax.types import Message
from pymax.static.enum import MessageStatus
from telegram import Bot, Update
from telegram.ext import Application as TGApplication, CommandHandler, ContextTypes

# ==============================================================================
# 🛠️ НАСТРОЙКИ
# ==============================================================================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

CONFIG_FILE = "config.json"
STATE_FILE = "bot_state.json"

ACTIVE_CHATS = set()


# ==============================================================================
# ⚙️ ЗАГРУЗКА КОНФИГУРАЦИИ
# ==============================================================================

def load_config():
    """Загружает конфигурацию из JSON-файла."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            required_keys = ['MAX_PHONE', 'MAX_CHAT_ID', 'TELEGRAM_BOT_TOKEN']
            for key in required_keys:
                if key not in config:
                    raise KeyError(f"Ключ '{key}' отсутствует в {CONFIG_FILE}")
            logger.info(f"✅ Конфигурация загружена из {CONFIG_FILE}")
            return config
    except FileNotFoundError:
        logger.critical(f"❌ Файл конфигурации {CONFIG_FILE} не найден.")
        raise
    except json.JSONDecodeError as e:
        logger.critical(f"❌ Ошибка парсинга JSON в {CONFIG_FILE}: {e}")
        raise
    except KeyError as e:
        logger.critical(f"❌ {e}")
        raise


# ==============================================================================
# 💾 РАБОТА С ФАЙЛАМИ И СОСТОЯНИЕМ
# ==============================================================================

def load_state():
    global ACTIVE_CHATS
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
                ACTIVE_CHATS = set(map(str, state.get('active_chats', [])))
                logger.info(f"✅ Состояние загружено. Чаты: {ACTIVE_CHATS}")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки: {e}")


def save_state():
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'active_chats': list(ACTIVE_CHATS)}, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")


# ==============================================================================
# 🔄 MAIN
# ==============================================================================

async def main():
    logger.info("🚀 Запуск Max -> Telegram Bridge")

    config = load_config()
    MAX_PHONE = config['MAX_PHONE']
    MAX_CHAT_ID = config['MAX_CHAT_ID']
    TELEGRAM_BOT_TOKEN = config['TELEGRAM_BOT_TOKEN']

    load_state()


    telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
    tg_app = TGApplication.builder().bot(telegram_bot).build()


    async def start_forwarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        if chat_id not in ACTIVE_CHATS:
            ACTIVE_CHATS.add(chat_id)
            save_state()
            await update.message.reply_text(f"🚀 Пересылка из Max (ID: {MAX_CHAT_ID}) включена.")
            logger.info(f"Чат {chat_id} добавлен в список активных.")
        else:
            await update.message.reply_text("✅ Уже работает.")

    async def stop_forwarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        if chat_id in ACTIVE_CHATS:
            ACTIVE_CHATS.remove(chat_id)
            save_state()
            await update.message.reply_text("🛑 Пересылка остановлена.")
            logger.info(f"Чат {chat_id} удален из списка активных.")
        else:
            await update.message.reply_text("❌ Не была включена.")

    tg_app.add_handler(CommandHandler("start", start_forwarding))
    tg_app.add_handler(CommandHandler("stop", stop_forwarding))

    await tg_app.initialize()
    await tg_app.start()
    logger.info("🤖 Telegram Application инициализирован.")


    logger.info("⏳ Инициализация MaxClient...")
    max_client = MaxClient(phone=MAX_PHONE, work_dir="cache_max")
    logger.info("✅ MaxClient создан.")


    @max_client.on_message_delete(filter=Filter(chat_id=MAX_CHAT_ID))
    async def handle_max_message_delete(message: Message):
        """
        Асинхронный обработчик для СОБЫТИЙ удаления сообщений из Max.
        """
        logger.info(f"🗑️ Событие удаления (on_message_delete) из Max (ID: {message.id}) получено и проигнорировано.")

    @max_client.on_message(filter=Filter(chat_id=MAX_CHAT_ID))
    async def handle_max_message(message: Message):
        """
        Асинхронный обработчик для сообщений из Max.
        """
        message_status = getattr(message, 'status', None)
        if message_status and MessageStatus.REMOVED in message_status:
            logger.debug(f"Пропущено удалённое сообщение (on_message) Max (ID: {message.id})")
            return

        sender_id = message.sender
        text = message.text or ""

        if not text:
            logger.debug(f"Пропущено сообщение без текста от ID {sender_id} (ID: {message.id})")
            return

        sender_name = "Неизвестный"
        if sender_id:
            try:
                sender_user = await max_client.get_user(sender_id)
                if sender_user and sender_user.names:
                    name_obj = sender_user.names[0]
                    sender_name = name_obj.first_name or name_obj.name or "Неизвестный"
                else:
                    logger.warning(f"Не удалось получить данные пользователя с ID {sender_id}")
                    sender_name = f"ID_{sender_id}"
            except Exception as e:
                logger.error(f"Ошибка при получении имени пользователя {sender_id}: {e}")
                sender_name = f"ID_{sender_id}"

        logger.info(f"📨 Новое сообщение из Max (ID: {message.id}) от {sender_name}: {text[:50]}...")

        tg_message_text = f"📩 **MAX**\n**От:** {sender_name}\n{text}"

        if ACTIVE_CHATS:
            for tg_chat_id in ACTIVE_CHATS.copy():
                try:
                    await telegram_bot.send_message(
                        chat_id=tg_chat_id,
                        text=tg_message_text,
                        parse_mode='Markdown'
                    )
                    logger.debug(f"✅ Отправлено в TG чат {tg_chat_id}")
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки в TG чат {tg_chat_id}: {e}")
                    if "not found" in str(e).lower() or "chat not found" in str(e).lower():
                        logger.info(f"Удаляю несуществующий чат {tg_chat_id} из списка активных.")
                        ACTIVE_CHATS.discard(tg_chat_id)
                        save_state()
        else:
            logger.debug("Нет активных чатов для пересылки.")

    # 3. Запуск Telegram Updater
    logger.info("🤖 Telegram Polling запущен")
    await tg_app.updater.start_polling()


    logger.info("🔌 Подключение MaxClient...")
    try:
        async with max_client:
            logger.info("✅ MaxClient запущен и слушает сообщения.")
            while True:
                await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("👋 Получен сигнал остановки (KeyboardInterrupt/CancelledError)...")
    finally:
        logger.info("🛑 Остановка Telegram...")
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        logger.info("👋 Bridge остановлен.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Основной процесс прерван пользователем.")
    except Exception as e:
        logger.critical(f"❌ Непредвиденная ошибка в main: {e}", exc_info=True)