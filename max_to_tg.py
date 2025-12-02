import asyncio
import logging
import json
import os
from pymax import MaxClient
from pymax.filters import Filter
from pymax.types import Message
from pymax.static.enum import MessageStatus, AttachType
from telegram import Bot, InputFile, Update
from telegram.ext import Application as TGApplication, CommandHandler, ContextTypes
import aiohttp
from io import BytesIO


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
# 📥 ЗАГРУЗКА МЕДИА ИЗ MAX
# ==============================================================================

async def download_media_from_url(url: str, filename: str, max_retries: int = 3) -> BytesIO:
    """Загружает медиа-файл по URL и возвращает BytesIO объект. С повторными попытками."""
    last_exception = None
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                # Увеличиваем connect и sock_read таймауты
                timeout = aiohttp.ClientTimeout(total=60, connect=15, sock_read=45)
                async with session.get(url, timeout=timeout) as response:
                    response.raise_for_status()
                    content = await response.read()
                    logger.debug(f"📥 Успешно загружен файл {filename} (попытка {attempt + 1})")
                    return BytesIO(content)
        except asyncio.TimeoutError as e:
            logger.warning(f"⏰ Таймаут при загрузке {filename} (попытка {attempt + 1}/{max_retries}): {e}")
            last_exception = e
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt) # Экспоненциальная задержка
        except aiohttp.ClientError as e:
            logger.warning(f"🌐 Ошибка HTTP при загрузке {filename} (попытка {attempt + 1}/{max_retries}): {e}")
            last_exception = e
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"❌ Непредвиденная ошибка при загрузке {filename} (попытка {attempt + 1}/{max_retries}): {e}")
            last_exception = e
            break # Для других ошибок не пытаемся повторно

    # Если все попытки исчерпаны
    logger.error(f"❌ Не удалось загрузить {filename} после {max_retries} попыток.")
    raise last_exception # Поднимаем последнее исключение


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
        """Асинхронный обработчик для СОБЫТИЙ удаления сообщений из Max."""
        logger.info(f"🗑️ Событие удаления (on_message_delete) из Max (ID: {message.id}) получено и проигнорировано.")# ... (ваш предыдущий код до функции handle_max_message) ...

    @max_client.on_message(filter=Filter(chat_id=MAX_CHAT_ID)   ) # Убираем фильтр для отладки
    async def handle_max_message(message: Message):
        """Асинхронный обработчик для сообщений из Max (отладочная версия)."""
        # Импорты внутри функции для локальности
        from pymax.types import PhotoAttach, FileAttach, VideoAttach, StickerAttach, AudioAttach, ControlAttach
        from pymax.static.enum import AttachType

        # Выводим ID чата и сообщения
        logger.info(f"[DEBUG] Получено сообщение в чате {message.chat_id} с ID {message.id}")

        # Проверяем статус
        message_status = getattr(message, 'status', None)
        if message_status and MessageStatus.REMOVED in message_status:
            logger.debug(f"Пропущено удалённое сообщение (on_message) Max (ID: {message.id})")
            return

        # --- ОТЛАДКА: Печатаем основные атрибуты сообщения ---
        logger.debug(f"[DEBUG] Атрибуты message: {dir(message)}")
        text = getattr(message, 'text', 'No text attr')
        logger.debug(f"[DEBUG] message.text: {text}")
        # Проверяем атрибут 'attaches' как в repomix-output.txt
        attaches = getattr(message, 'attaches', 'No attaches attr')
        logger.debug(f"[DEBUG] message.attaches: {attaches}")
        if attaches != 'No attaches attr' and attaches:
            for i, att in enumerate(attaches):
                logger.debug(f"[DEBUG] Attach {i}: Type={type(att)}, Value={att}, Dir={dir(att)}")
                # Проверяем тип вложения
                attach_type = getattr(att, 'type', 'No type attr on attach')
                logger.debug(f"         Attach {i} type enum: {attach_type}")
                if isinstance(att, PhotoAttach):
                    logger.debug(f"         -> Это PhotoAttach. base_url: {getattr(att, 'base_url', 'No base_url')}")
                elif isinstance(att, VideoAttach):
                    logger.debug(f"         -> Это VideoAttach. id: {getattr(att, 'id', 'No id')}")
                elif isinstance(att, AudioAttach):
                    logger.debug(f"         -> Это AudioAttach. url: {getattr(att, 'url', 'No url')}")
                elif isinstance(att, FileAttach):
                    logger.debug(f"         -> Это FileAttach. file_id: {getattr(att, 'file_id', 'No file_id')}")
                elif isinstance(att, StickerAttach):
                    logger.debug(f"         -> Это StickerAttach. id: {getattr(att, 'id', 'No id')}")
                elif isinstance(att, ControlAttach):
                    logger.debug(f"         -> Это ControlAttach.")
                else:
                    logger.debug(f"         -> Неизвестный тип вложения.")

        # Проверяем атрибут 'attachments' (может быть использован фильтром)
        # attachments = getattr(message, 'attachments', 'No attachments attr')
        # logger.debug(f"[DEBUG] message.attachments (фильтр): {attachments}")
        # if attachments != 'No attachments attr' and attachments:
        #      for i, att in enumerate(attachments):
        #          logger.debug(f"[DEBUG] Attachment {i}: Type={type(att)}, Value={att}, Dir={dir(att)}")

        # --- КОНЕЦ ОТЛАДКИ ---

        # Теперь проверяем, совпадает ли chat_id с MAX_CHAT_ID
        if message.chat_id != MAX_CHAT_ID:
            logger.debug(f"[DEBUG] Пропускаем сообщение из другого чата: {message.chat_id}")
            return  # Возвращаемся, если чат не совпадает

        logger.info(f"[DEBUG] Обрабатываем сообщение из нужного чата {MAX_CHAT_ID}")

        # --- ОСТАЛЬНАЯ ЛОГИКА (ваша текущая логика, но с правильным атрибутом) ---
        # Проверяем 'attaches' как в repomix-output.txt
        attachments_to_process = getattr(message, 'attaches', []) or []
        text = message.text or ""

        if not text and not attachments_to_process:
            logger.debug(f"Пропущено сообщение без текста и вложений от ID {message.sender} (ID: {message.id})")
            return

        # Получение имени отправителя
        sender_id = message.sender
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

        logger.info(
            f"📨 Новое сообщение из Max (ID: {message.id}) от {sender_name}{' (с медиа)' if attachments_to_process else ''}")

        # Подготовка caption
        tg_caption = f"📩 *MAX*\n*От:* {sender_name}"
        if text:
            tg_caption += f"\n{text}"

        media_url = None
        media_type = None
        filename = None

        # Обработка вложений
        if attachments_to_process:  # Используем 'attaches'
            logger.debug(f"DEBUG: Найдено вложений (attaches): {len(attachments_to_process)}")
            first_attachment = attachments_to_process[0]
            logger.debug(f"DEBUG: Тип первого вложения (attaches): {type(first_attachment).__name__}")

            try:
                if isinstance(first_attachment, PhotoAttach):
                    media_type = 'photo'
                    media_url = getattr(first_attachment, 'base_url', None)  # Проверяем base_url
                    if media_url:
                        filename = f"photo_{message.id}.jpg"
                        logger.debug(f"📸 Обнаружено фото: {media_url}")
                    else:
                        logger.warning(
                            f"PhotoAttach (ID: {message.id}) не имеет base_url. Dir: {dir(first_attachment)}")

                elif isinstance(first_attachment, VideoAttach):
                    media_type = 'video'
                    video_id_to_use = getattr(first_attachment, 'id', getattr(first_attachment, 'video_id', None))
                    if video_id_to_use:
                        video_info = await max_client.get_video_by_id(
                            chat_id=message.chat_id,
                            message_id=message.id,
                            video_id=video_id_to_use
                        )
                        if video_info and hasattr(video_info, 'url'):
                            media_url = video_info.url
                            filename = f"video_{message.id}.mp4"
                            logger.debug(f"🎬 Обнаружено видео: {media_url}")
                        else:
                            logger.warning(f"Не удалось получить URL видео (ID: {message.id})")
                    else:
                        logger.warning(
                            f"VideoAttach (ID: {message.id}) не имеет 'id' или 'video_id'. Dir: {dir(first_attachment)}")

                elif isinstance(first_attachment, AudioAttach):
                    media_type = 'audio'
                    audio_url = getattr(first_attachment, 'url', None)  # Проверяем url
                    if audio_url:
                        filename = f"audio_{message.id}.ogg"
                        logger.debug(f"🎵 Обнаружено аудио: {audio_url}")
                        media_url = audio_url
                    else:
                        logger.warning(f"У AudioAttach (ID: {message.id}) нет URL. Dir: {dir(first_attachment)}")

                elif isinstance(first_attachment, FileAttach):
                    media_type = 'document'
                    file_id_to_use = getattr(first_attachment, 'file_id', getattr(first_attachment, 'id', None))
                    if file_id_to_use:
                        file_info = await max_client.get_file_by_id(
                            chat_id=message.chat_id,
                            message_id=message.id,
                            file_id=file_id_to_use
                        )
                        if file_info and not file_info.unsafe and hasattr(file_info, 'url'):
                            media_url = file_info.url
                            filename = getattr(first_attachment, 'name', f"file_{message.id}.bin")
                            logger.debug(f"📄 Обнаружен файл: {media_url}")
                        else:
                            logger.warning(f"Файл (ID: {message.id}) недоступен или небезопасен")
                    else:
                        logger.warning(
                            f"FileAttach (ID: {message.id}) не имеет 'file_id' или 'id'. Dir: {dir(first_attachment)}")

                elif isinstance(first_attachment, StickerAttach):
                    logger.debug(f"👻 Стикер (ID: {message.id}) пропущен")

                else:
                    logger.debug(
                        f"Неизвестный тип вложения: {type(first_attachment).__name__}. Dir: {dir(first_attachment)}")

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке вложения (ID: {message.id}): {e}", exc_info=True)

        # Отправка в Telegram
        if not ACTIVE_CHATS:
            logger.debug("Нет активных чатов для пересылки.")
            return

        for tg_chat_id in ACTIVE_CHATS.copy():
            try:
                if media_url and media_type:
                    # Загружаем медиа
                    logger.debug(f"📥 Загрузка {media_type} для TG чата {tg_chat_id}...")
                    media_bytes = await download_media_from_url(media_url, filename)

                    # Создаем InputFile
                    input_file = InputFile(media_bytes, filename=filename)

                    # Выбираем метод отправки
                    if media_type == 'photo':
                        await telegram_bot.send_photo(
                            chat_id=tg_chat_id,
                            photo=input_file,
                            caption=tg_caption,
                            parse_mode='Markdown'
                        )
                    elif media_type == 'video':
                        await telegram_bot.send_video(
                            chat_id=tg_chat_id,
                            video=input_file,
                            caption=tg_caption,
                            parse_mode='Markdown'
                        )
                    elif media_type == 'audio':
                        await telegram_bot.send_audio(
                            chat_id=tg_chat_id,
                            audio=input_file,
                            caption=tg_caption,
                            parse_mode='Markdown'
                        )
                    elif media_type == 'document':
                        await telegram_bot.send_document(
                            chat_id=tg_chat_id,
                            document=input_file,
                            caption=tg_caption,
                            parse_mode='Markdown'
                        )

                    # Закрываем BytesIO после отправки
                    media_bytes.close()
                    logger.info(f"✅ {media_type.capitalize()} отправлено в TG чат {tg_chat_id}")

                elif text:
                    # Только текст, без медиа
                    await telegram_bot.send_message(
                        chat_id=tg_chat_id,
                        text=tg_caption,
                        parse_mode='Markdown'
                    )
                    logger.info(f"✅ Текстовое сообщение отправлено в TG чат {tg_chat_id}")

                else:
                    logger.debug(f"Нечего отправлять в TG чат {tg_chat_id}")

            except Exception as e:
                logger.error(f"❌ Ошибка отправки в TG чат {tg_chat_id}: {e}", exc_info=True)
                if "not found" in str(e).lower() or "chat not found" in str(e).lower():
                    logger.info(f"Удаляю несуществующий чат {tg_chat_id} из списка активных.")
                    ACTIVE_CHATS.discard(tg_chat_id)
                    save_state()

# ... (остальной код остается без изменений) ...




    # Запуск Telegram Updater
    logger.info("🤖 Telegram Polling запущен")
    await tg_app.updater.start_polling()

    logger.info("🔌 Подключение MaxClient...")
    try:
        async with max_client:
            logger.info("✅ MaxClient запущен и слушает сообщения.")
            while True:
                await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("👋 Получен сигнал остановки...")
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