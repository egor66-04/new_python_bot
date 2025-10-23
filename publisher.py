import asyncio
import logging
import httpx
import json
from aiogram import Bot
from aiogram.types import InputMediaPhoto
from dotenv import load_dotenv
load_dotenv()
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
        
        # Получаем URL для загрузки фото на стену пользователя
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=10.0)) as client:
            # Используем метод для стены группы - передаем group_id как параметр
            response = await client.get(
                "https://api.vk.com/method/photos.getWallUploadServer",
                params={
                    'group_id': config.VK_GROUP_ID,
                    'access_token': config.VK_USER_TOKEN,
                    'v': '5.131'
                },
                timeout=httpx.Timeout(timeout=10.0),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            try:
                upload_data = response.json()
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON response from VK API: {response.text}")
                return None
            
            # Проверяем на ошибки
            if 'error' in upload_data:
                error = upload_data['error']
                error_code = error.get('error_code', 'Unknown')
                error_msg = error.get('error_msg', 'Unknown error')
                
                # Специальная обработка ошибки 214 (Access to adding post denied)
                if error_code == 214:
                    logger.error(f"VK API error 214 getting wall upload server: Access to adding post denied. "
                               f"Это означает, что у токена нет прав на публикацию от имени группы или на стене запрещены публикации для данного пользователя. "
                               f"Причины ошибки и способы устранения:\n"
                               f"1. ❌ Неправильный токен: Убедитесь, что используете токен пользователя-администратора группы, а не токен самой группы\n"
                               f"2. ⚙️ Настройки группы: Перейдите в настройки группы → 'Управление сообществом' и убедитесь, что разрешена публикация от имени сообщества\n"
                               f"3. 👤 Права токена: Токен должен быть получен от пользователя с правами администратора группы\n"
                               f"4. 🌐 Параметры API: Убедитесь, что передаете правильные параметры, включая owner_id группы с префиксом '-' (например, -ID группы)\n"
                               f"5. ⏳ Лимиты публикаций: Если ошибка связана с лимитом публикаций, подождите некоторое время перед повторной попыткой\n\n"
                               f"✅ Решение: Используйте токен ПОЛЬЗОВАТЕЛЯ с правами администратора группы, а не токен самой группы. "
                               f"Только токен пользователя с правами администратора группы может публиковать посты от имени группы. "
                               f"Error details: {error_msg}")
                else:
                    logger.error(f"VK API error getting wall upload server: {error_code} - {error_msg}")
                return None
            
            if 'response' not in upload_data or 'upload_url' not in upload_data['response']:
                logger.error(f"Missing upload_url in response: {upload_data}")
                return None
            
            upload_url = upload_data['response']['upload_url']
            
            # Загружаем фото с Telegram
            file_response = await client.get(file_url, timeout=httpx.Timeout(timeout=30.0), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            files = {'photo': ('photo.jpg', file_response.content)}
            upload_response = await client.post(upload_url, files=files, timeout=httpx.Timeout(timeout=30.0), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            try:
                photo_data = upload_response.json()
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON response from VK upload: {upload_response.text}")
                return None
            
            # Проверяем, что все необходимые параметры присутствуют
            if 'photo' not in photo_data or 'server' not in photo_data or 'hash' not in photo_data:
                logger.error(f"Missing required fields in photo upload response: {photo_data}")
                return None
            
            # Проверяем, что значение photo не пустое
            if not photo_data.get('photo'):
                logger.error(f"Photo parameter is empty in upload response: {photo_data}")
                return None
            
            # Сохраняем фото на стене группы - добавляем group_id в параметры
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
            try:
                saved_data = save_response.json()
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON response from VK save: {save_response.text}")
                return None
            
            # Проверяем на ошибки
            if 'error' in saved_data:
                logger.error(f"VK API error saving wall photo: {saved_data['error']}")
                return None
            
            # Проверяем, что в ответе есть response и он не пустой
            if 'response' not in saved_data or not saved_data['response']:
                logger.error(f"Empty response when saving wall photo: {saved_data}")
                return None
            
            # Возвращаем идентификатор фото в формате "photo{owner_id}_{id}"
            photo = saved_data['response'][0]
            owner_id = photo['owner_id']
            media_id = photo['id']
            
            return f"photo{int(owner_id)}_{int(media_id)}"
            
    except Exception as e:
        logger.error(f"Failed to upload photo to VK wall: {e}", exc_info=False)
        return None

async def check_vk_token_permissions(access_token: str, group_id: str) -> bool:
    """Проверка прав токена на публикацию от имени группы"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=15.0)) as client:
            # Проверяем права токена
            response = await client.get(
                "https://api.vk.com/method/account.getProfileInfo",
                params={
                    'access_token': access_token,
                    'v': '5.131'
                },
                timeout=httpx.Timeout(timeout=15.0),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            try:
                result = response.json()
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON response from VK token check: {response.text}")
                return False
            
            # Если есть ошибка, значит токен может быть токеном группы
            if 'error' in result:
                error_code = result['error'].get('error_code', 0)
                # Ошибка 5 (Authorization failed) или 15 (Access denied) может означать токен группы
                if error_code in [5, 15]:
                    # Пробуем метод для токена группы
                    group_response = await client.get(
                        "https://api.vk.com/method/groups.getById",
                        params={
                            'group_id': group_id,
                            'access_token': access_token,
                            'v': '5.131'
                        },
                        timeout=httpx.Timeout(timeout=15.0),
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                    )
                    try:
                        group_result = group_response.json()
                    except json.JSONDecodeError:
                        logger.error(f"Failed to decode JSON response from VK group check: {group_response.text}")
                        return False
                    
                    # Если группа получена, токен может быть правильным
                    if 'response' in group_result:
                        return True
            
            return 'response' in result
    except Exception as e:
        logger.error(f"Failed to check VK token permissions: {e}")
        return False

async def check_vk_user_token_permissions(access_token: str, group_id: str) -> bool:
    """Проверка прав токена пользователя на публикацию от имени группы (новая версия)"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=15.0)) as client:
            # Проверяем права токена пользователя на публикацию от имени группы
            response = await client.get(
                "https://api.vk.com/method/groups.getById",
                params={
                    'group_id': group_id,
                    'access_token': access_token,
                    'v': '5.131',
                    'fields': 'is_admin'
                },
                timeout=httpx.Timeout(timeout=15.0),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            try:
                result = response.json()
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON response from VK user token check: {response.text}")
                return False
            
            # Если есть ошибка, значит токен может быть неправильным
            if 'error' in result:
                error_code = result['error'].get('error_code', 0)
                error_msg = result['error'].get('error_msg', 'Unknown error')
                logger.error(f"VK API error checking user token permissions: {error_code} - {error_msg}")
                return False
            
            # Если группа получена, токен может быть правильным
            if 'response' in result and result['response']:
                group_info = result['response'][0]
                # Проверяем, является ли пользователь администратором группы
                if group_info.get('is_admin', 0) == 1:
                    return True
                else:
                    logger.warning(f"User is not admin of the group: {group_info}")
                    return False
            
            return False
    except Exception as e:
        logger.error(f"Failed to check VK user token permissions: {e}")
        return False

async def publish_vk_post(bot: Bot, text: str, photo_ids: list[str] | None = None):
    """Публикация поста на стене группы ВКонтакте (текст + фото одним постом)"""
    if not config.VK_USER_TOKEN:
        logger.warning("VK user token is not configured. Skipping VK publication.")
        return False

    # Если VK_GROUP_ID не задан числом, пробуем получить его по screen_name
    group_id = config.VK_GROUP_ID
    if not group_id or int(group_id) == 0:  # Проверяем, что group_id не пустой и не равен 0
        # Попробуем получить ID группы по screen_name из переменной окружения
        import os
        vk_group_screen_name = os.getenv('VK_GROUP_SCREEN_NAME')
        if vk_group_screen_name:
            # Импортируем функцию из vk_publisher
            try:
                from vk_publisher import get_group_id_by_screen_name
                group_id = await get_group_id_by_screen_name(vk_group_screen_name, config.VK_USER_TOKEN)
                if not group_id:
                    logger.error("Could not resolve group ID by screen name. Skipping VK publication.")
                    return False
            except ImportError:
                logger.error("vk_publisher module not found. Skipping VK publication.")
                return False
        else:
            logger.error("Neither VK_GROUP_ID nor VK_GROUP_SCREEN_NAME is configured. Skipping VK publication.")
            return False

    # Проверяем права токена пользователя перед публикацией
    if not await check_vk_user_token_permissions(config.VK_USER_TOKEN, str(group_id)):
        logger.error("VK user token does not have permission to post as the group. "
                   "This may indicate that the user token doesn't have admin rights for the group. "
                   "Consider using a user token with admin rights for the group. "
                   "Error details: User token permissions check failed")
        return False

    if photo_ids is None:
        photo_ids = []
    
    # Загружаем фото на стену группы
    attachments = []
    if photo_ids:
        for file_id in photo_ids:
            # Используем функцию из vk_publisher для загрузки фото в группу
            try:
                from vk_publisher import upload_photo_to_vk_wall
                vk_photo_id = await upload_photo_to_vk_wall(bot, file_id)
                if vk_photo_id:
                    attachments.append(vk_photo_id)
            except ImportError:
                logger.error("vk_publisher module not found. Cannot upload photos to VK.")
                return False
            # Небольшая задержка между загрузками
            await asyncio.sleep(0.5)
    
    # Публикуем пост на стене группы
    post_params = {
        'owner_id': -int(group_id),  # Отрицательный ID для группы
        'from_group': 1,  # Публикуем от имени группы
        'message': text,
        'attachments': ",".join(attachments) if attachments else "",  # Фото в формате "photo{owner_id}_{id}"
        'access_token': config.VK_USER_TOKEN,
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
            try:
                result = response.json()
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON response from VK wall.post: {response.text}")
                return False
            
            # Проверяем на ошибки
            if 'error' in result:
                error = result['error']
                error_code = error.get('error_code', 'Unknown')
                error_msg = error.get('error_msg', 'Unknown error')
                
                # Специальная обработка ошибки 214 (Access to adding post denied)
                if error_code == 214:
                    logger.error(f"VK API error 214: Access to adding post denied. "
                               f"Это означает, что у токена нет прав на публикацию от имени группы или на стене запрещены публикации для данного пользователя. "
                               f"Причины ошибки и способы устранения:\n"
                               f"1. ❌ Неправильный токен: Убедитесь, что используете токен пользователя-администратора группы, а не токен самой группы\n"
                               f"2. ⚙️ Настройки группы: Перейдите в настройки группы → 'Управление сообществом' и убедитесь, что разрешена публикация от имени сообщества\n"
                               f"3. 👤 Права токена: Токен должен быть получен от пользователя с правами администратора группы\n"
                               f"4. 🌐 Параметры API: Убедитесь, что передаете правильные параметры, включая owner_id группы с префиксом '-' (например, -ID группы)\n"
                               f"5. ⏳ Лимиты публикаций: Если ошибка связана с лимитом публикаций, подождите некоторое время перед повторной попыткой\n\n"
                               f"✅ Решение: Используйте токен ПОЛЬЗОВАТЕЛЯ с правами администратора группы, а не токен самой группы. "
                               f"Только токен пользователя с правами администратора группы может публиковать посты от имени группы. "
                               f"Error details: {error_msg}")
                elif error_code == 27:  # Group authorization failed
                    logger.error(f"VK API error 27: Group authorization failed. "
                               f"Это означает, что используется токен группы вместо токена пользователя-администратора. "
                               f"Error details: {error_msg}")
                else:
                    logger.error(f"VK API error posting to wall: {error_code} - {error_msg}")
                
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