# get_all_chats.py
import asyncio
import json
import logging
from pymax import MaxClient

# ==============================================================================
# 🛠️ НАСТРОЙКИ
# ==============================================================================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

CONFIG_FILE = "config.json"


# ==============================================================================
# ⚙️ ЗАГРУЗКА КОНФИГУРАЦИИ
# ==============================================================================

def load_config():
    """Загружает конфигурацию из JSON-файла."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            required_keys = ['MAX_PHONE']
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
# 🔄 MAIN
# ==============================================================================

async def main():
    logger.info("🚀 Запуск скрипта для получения ID чатов Max")

    config = load_config()
    MAX_PHONE = config['MAX_PHONE']


    logger.info("⏳ Инициализация MaxClient...")

    client = MaxClient(phone=MAX_PHONE, work_dir="cache_get_chats")
    logger.info("✅ MaxClient создан.")

    logger.info("🔌 Подключение MaxClient через async with...")
    try:
        async with client:

            logger.info("✅ MaxClient подключен. Ожидание полной синхронизации...")

            @client.on_start
            async def on_client_start():
                logger.info("✅ MaxClient полностью синхронизирован.")

                dialogs = client.dialogs
                channels = client.channels
                chats = client.chats

                all_entities = []
                if dialogs:
                    logger.info(f"📊 Найдено {len(dialogs)} личных диалогов.")
                    for dialog in dialogs:

                        owner_id = dialog.owner
                        try:
                            owner_user = await client.get_user(owner_id)
                            if owner_user and owner_user.names:
                                name_obj = owner_user.names[0]
                                owner_name = name_obj.first_name or name_obj.name or f"ID_{owner_id}"
                            else:
                                owner_name = f"ID_{owner_id}"
                        except Exception as e:
                            logger.warning(f"Не удалось получить имя для владельца диалога {owner_id}: {e}")
                            owner_name = f"ID_{owner_id}"

                        all_entities.append({
                            "id": dialog.id,
                            "name": owner_name,
                            "type": "Dialog (Личный)",
                            "owner_id": owner_id
                        })
                else:
                    logger.info("ℹ️ Личных диалогов не найдено.")

                # Обрабатываем каналы
                if channels:
                    logger.info(f"📊 Найдено {len(channels)} каналов.")
                    for channel in channels:
                        all_entities.append({
                            "id": channel.id,
                            "name": getattr(channel, 'title', 'Без названия'),
                            "type": "Channel (Канал)",
                            "owner_id": None
                        })
                else:
                    logger.info("ℹ️ Каналов не найдено.")

                if chats:
                    logger.info(f"📊 Найдено {len(chats)} групповых чатов.")
                    for chat in chats:
                        all_entities.append({
                            "id": chat.id,
                            "name": getattr(chat, 'title', 'Без названия'),
                            "type": "Chat (Группа)",
                            "owner_id": None
                        })
                else:
                    logger.info("ℹ️ Групповых чатов не найдено.")

                if all_entities:
                    logger.info(f"✅ Всего найдено сущностей: {len(all_entities)}")
                    print("\n--- Список всех чатов, каналов и диалогов ---")
                    for entity in all_entities:
                        print(f"ID: {entity['id']}, Название: {entity['name']}, Тип: {entity['type']}")
                        # Если это диалог, покажем ID собеседника
                        if entity['type'] == 'Dialog (Личный)':
                            print(f"     (ID собеседника: {entity['owner_id']})")
                    print("---------------------------------------------\n")
                else:
                    logger.warning("⚠️ Не найдено ни одного диалога/чата/канала.")
                    print("Не найдено ни одного диалога/чата/канала.")


            start_event = asyncio.Event()
            @client.on_start
            async def on_client_start_and_signal():
                await on_client_start()
                start_event.set()

            await start_event.wait()
            logger.info("✅ Список чатов получен. Завершение работы скрипта.")

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("👋 Получен сигнал остановки (KeyboardInterrupt/CancelledError)...")
    except Exception as e:
        logger.critical(f"❌ Непредвиденная ошибка: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())