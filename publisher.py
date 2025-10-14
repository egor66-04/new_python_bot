import asyncio
import logging
import httpx
from aiogram import Bot
from aiogram.types import InputMediaPhoto
import config

logger = logging.getLogger(__name__)

# Убираем старые комментарии

# Функции для публикации в ВКонтакте
async def upload_photo_to_vk(bot: Bot, file_id: str):
    """Загрузка фото на стену группы ВКонтакте"""
    try:
        # Получаем информацию о файле из Telegram
        file_info = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{config.TELEGRAM_TOKEN}/{file_info.file_path}"
        
        # Получаем URL для загрузки фото на стену группы
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=10.0)) as client:
            # Используем метод для стены группы
            response = await client.get(
                f"https://api.vk.com/method/photos.getWallUploadServer?group_id={config.VK_GROUP_ID}&access_token={config.VK_USER_TOKEN}&v=5.131",
                timeout=httpx.Timeout(timeout=10.0),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            upload_data = response.json()
            
            # Проверяем на ошибки
            if 'error' in upload_data:
                logger.error(f"VK API error getting wall upload server: {upload_data['error']}")
                return None
            
            if 'response' not in upload_data or 'upload_url' not in upload_data['response']:
                logger.error(f"Missing upload_url in response: {upload_data}")
                return None
            
            upload_url = upload_data['response']['upload_url']
            
            # Загружаем фото с Telegram
            file_response = await client.get(file_url, timeout=httpx.Timeout(timeout=30.0), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            files = {'photo': ('photo.jpg', file_response.content)}
            upload_response = await client.post(upload_url, files=files, timeout=httpx.Timeout(timeout=30.0), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            photo_data = upload_response.json()
            
            # Проверяем, что все необходимые параметры присутствуют
            if 'photo' not in photo_data or 'server' not in photo_data or 'hash' not in photo_data:
                logger.error(f"Missing required fields in photo upload response: {photo_data}")
                return None
            
            # Сохраняем фото на стене группы
            save_response = await client.post(
                "https://api.vk.com/method/photos.saveWallPhoto",
                data={
                    'group_id': config.VK_GROUP_ID,
                    'photo': photo_data['photo'],
                    'server': photo_data['server'],
                    'hash': photo_data['hash'],
                    'access_token': config.VK_USER_TOKEN,
                    'v': '5.131'
                },
                timeout=httpx.Timeout(timeout=30.0),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            saved_data = save_response.json()
            
            # Проверяем на ошибки
            if 'error' in saved_data:
                logger.error(f"VK API error saving wall photo: {saved_data['error']}")
                return None
            
            # Возвращаем идентификатор фото в формате "photo{owner_id}_{id}"
            photo = saved_data['response'][0]
            owner_id = photo['owner_id']
            media_id = photo['id']
            
            return f"photo{int(owner_id)}_{int(media_id)}"
            
    except Exception as e:
        logger.error(f"Failed to upload photo to VK wall: {e}", exc_info=True)
        return None

async def publish_vk_post(bot: Bot, text: str, photo_ids: list[str] | None = None):
    """Публикация поста на стене группы ВКонтакте (текст + фото одним постом)"""
    if not config.VK_ACCESS_TOKEN or not config.VK_GROUP_ID:
        logger.warning("VK is not configured. Skipping VK publication.")
        return False

    if photo_ids is None:
        photo_ids = []
    
    # Загружаем фото на стену группы
    attachments = []
    if photo_ids:
        for file_id in photo_ids:
            vk_photo_id = await upload_photo_to_vk(bot, file_id)
            if vk_photo_id:
                attachments.append(vk_photo_id)
            # Небольшая задержка между загрузками
            await asyncio.sleep(0.5)
    
    # Публикуем пост на стене группы
    post_params = {
        'owner_id': -int(config.VK_GROUP_ID),  # Отрицательный ID для группы
        'from_group': 1,  # Публикуем от имени группы
        'message': text,
        'attachments': ",".join(attachments) if attachments else "",  # Фото в формате "photo{owner_id}_{id}"
        'access_token': config.VK_ACCESS_TOKEN,
        'v': '5.131'
    }
    
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=15.0)) as client:
            response = await client.post(
                "https://api.vk.com/method/wall.post",
                data=post_params,
                timeout=httpx.Timeout(timeout=15.0),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            result = response.json()
            
            # Проверяем на ошибки
            if 'error' in result:
                logger.error(f"VK API error posting to wall: {result['error']}")
                return False
            
            logger.info("Successfully posted to VK wall.")
            return True
            
    except Exception as e:
        logger.error(f"Failed to post to VK wall: {e}", exc_info=False)
        return False

async def publish_telegram_post(bot: Bot, text: str, media_group: list[InputMediaPhoto] | None = None):
    """Публикация поста в Telegram канал"""
    if not config.TELEGRAM_CHANNEL_ID:
        logger.error("TELEGRAM_CHANNEL_ID is not configured")
        return
        
    logger.info(f"Executing Telegram post to channel {config.TELEGRAM_CHANNEL_ID}")
    try:
        if not media_group:
            # Проверяем, что канал задан и токен действителен
            if config.TELEGRAM_CHANNEL_ID and config.TELEGRAM_CHANNEL_ID != "":
                await asyncio.wait_for(bot.send_message(config.TELEGRAM_CHANNEL_ID, text), timeout=15)
            return

        # Вставляем текст в подпись первого фото
        if media_group:
            # Максимум 1024 символа в caption
            media_group[0].caption = text[:1024]
            # Проверяем, что канал задан и токен действителен
            if config.TELEGRAM_CHANNEL_ID and config.TELEGRAM_CHANNEL_ID != "":
                # Используем asyncio.wait_for для таймаута
                await asyncio.wait_for(bot.send_media_group(config.TELEGRAM_CHANNEL_ID, list(media_group)), timeout=15)
        logger.info("Successfully sent post to Telegram.")
        # Очищаем временные объекты
        if media_group:
            media_group.clear()
    except asyncio.TimeoutError:
        logger.error("Timeout during sending post to Telegram")
    except Exception as e:
        logger.error(f"Failed to send post to Telegram: {e}", exc_info=False)  # Убираем подробное логгирование

# Восстанавливаем функцию publish_vk_post, так как публикация в ВК нужна