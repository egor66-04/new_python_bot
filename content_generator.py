import asyncio
import aiohttp
import logging
import ssl
import certifi
from datetime import datetime
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

# Удаляем дублирующуюся функцию, так как она определена в bot.py

async def generate_post_text(prompt: str, service_type: str = "manicure_pedicure", season: str | None = None) -> str | None:
    # Проверяем наличие API ключа перед выполнением запроса
    if not AI_API_KEY:
        logger.error("AI_API_KEY не установлен в .env файле")
        return None

    # Если время года не передано, определяем его
    if season is None:
        from datetime import datetime
        month = datetime.now().month
        if month in [12, 1, 2]:
            season = "зима"
        elif month in [3, 4, 5]:
            season = "весна"
        elif month in [6, 7, 8]:
            season = "лето"
        else:
            season = "осень"
    
    # Заменяем плейсхолдер {season} в промпте на актуальное время года
    prompt = prompt.format(season=season)
        
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "ValeriaBot/1.0"
    }
    
    data = {
        "model": AI_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500  # Ограничиваем длину генерации
    }
    
    # Настройка безопасного соединения
    connector = aiohttp.TCPConnector(
        ssl=ssl.create_default_context(cafile=certifi.where())
    )
    
    timeout = aiohttp.ClientTimeout(total=30)
    
    for attempt in range(3):  # Делаем 3 попытки
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.post(AI_BASE_URL, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        result = await response.json()
                        text = result['choices'][0]['message']['content']
                        logger.info("Текст успешно сгенерирован")
                        # Очищаем временные объекты
                        del result
                        return text.strip()
                    else:
                        error_text = await response.text()
                        logger.error(f"Ошибка API {response.status}: {error_text[:200]}...")
                        if attempt == 2:  # Если последняя попытка
                            return None
                        await asyncio.sleep(2)  # Задержка перед повторной попыткой
        except asyncio.TimeoutError:
            logger.error(f"Таймаут запроса (попытка {attempt + 1}/3)")
            if attempt == 2:  # Если последняя попытка
                return None
            await asyncio.sleep(2)  # Задержка перед повторной попыткой
        except Exception as e:
            logger.error(f"Ошибка сети (попытка {attempt + 1}/3): {e}")
            if attempt == 2:  # Если последняя попытка
                return None
            await asyncio.sleep(2)  # Задержка перед повторной попыткой
    return None