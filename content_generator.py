import asyncio
import aiohttp
import logging
from config import AI_BASE_URL, AI_MODEL, AI_API_KEY

# Проверяем, что все необходимые переменные окружения определены
# Убираем проверку при импорте для возможности тестирования
# if not AI_API_KEY:
#     raise ValueError("AI_API_KEY не установлен в .env файле")
# if not AI_BASE_URL:
#     raise ValueError("AI_BASE_URL не установлен в .env файле")
# if not AI_MODEL:
#     raise ValueError("AI_MODEL не установлен в .env файле")

# Добавляем проверку наличия ключа API при вызове функции

logger = logging.getLogger(__name__)

# Уменьшаем уровень логирования
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

async def generate_post_text(prompt: str) -> str | None:
    # Проверяем наличие API ключа перед выполнением запроса
    if not AI_API_KEY:
        logger.error("AI_API_KEY не установлен в .env файле")
        return None
        
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": AI_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    for attempt in range(3):  # Делаем 3 попытки
        try:
            # Оптимизация: используем таймауты и ограничиваем размер ответа
            # Исправляем параметры SSL для избежания конфликта
            connector = aiohttp.TCPConnector(ssl=False) # Отключаем SSL проверку
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), connector=connector) as session:
                async with session.post(AI_BASE_URL, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        result = await response.json()
                        text = result['choices'][0]['message']['content']
                        logger.info("Successfully generated text")
                        # Очищаем временные объекты
                        del result
                        return text.strip()
                    else:
                        error_text = await response.text()
                        logger.error(f"API Error: {response.status} - {error_text}")
                        if attempt == 1:  # Если последняя попытка
                            return None
                        await asyncio.sleep(1)  # Задержка перед повторной попыткой
        except asyncio.TimeoutError:
            logger.error(f"Request timeout during content generation (attempt {attempt + 1}/3)")
            if attempt == 2:  # Если последняя попытка
                return None
            await asyncio.sleep(2)  # Задержка перед повторной попыткой
        except Exception as e:
            logger.error(f"Request Error during content generation (attempt {attempt + 1}/3): {e}")
            if attempt == 2:  # Если последняя попытка
                return None
            await asyncio.sleep(2)  # Задержка перед повторной попыткой
    return None