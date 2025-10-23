# 1. Сначала — загрузка .env
from dotenv import load_dotenv
load_dotenv()

# 2. Теперь — все остальные импорты
import asyncio
import logging
import random
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InputMediaPhoto, InaccessibleMessage
from aiogram.utils.keyboard import InlineKeyboardBuilder
# from aiogram.filters import Text  # Закомментировано, так как может быть недоступен в текущей версии aiogram

import config
from content_generator import generate_post_text
from publisher import publish_telegram_post, publish_vk_post

from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)  # Увеличиваем уровень логирования для отображения информационных сообщений
logger = logging.getLogger(__name__)

def get_current_season() -> str:
    """Определяет текущее время года по месяцу"""
    month = datetime.now().month
    
    if month in [12, 1, 2]:
        return "зима"
    elif month in [3, 4, 5]:
        return "весна"
    elif month in [6, 7, 8]:
        return "лето"
    else:
        return "осень"

print("🔧 DEBUG: Загруженный токен:", config.TELEGRAM_TOKEN[:10] + "..." if config.TELEGRAM_TOKEN else "None")
print("🔧 DEBUG: ADMIN_ID:", config.ADMIN_ID)
if not config.TELEGRAM_TOKEN:
    raise ValueError("❌ ОШИБКА: TELEGRAM_TOKEN пуст! Бот не может запуститься без токена.")
else:
    print("✅ Токен загружен успешно")

# Инициализация бота и диспетчера
bot = Bot(
    token=config.TELEGRAM_TOKEN,  # Уже проверили выше, что токен не пустой
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    # request_timeout больше не поддерживается в aiogram 3.x
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Семафор для ограничения параллельных публикаций
publish_semaphore = asyncio.Semaphore(2)

# Оптимизация потребления памяти - уменьшаем размер пула соединений

# Состояния для FSM
class PostStates(StatesGroup):
    generating = State()
    waiting_for_photos = State()
    ready_to_publish = State()
    editing_post = State()
    waiting_for_topic = State()

# Удаляем неиспользуемый код
# Вместо сохранения фото на диск, будем хранить только file_id в состоянии

# Обработчик команды /start
@dp.message(CommandStart())
async def command_start_handler(message: Message):
    if message.from_user and str(message.from_user.id) != str(config.ADMIN_ID):
        return
    
    await message.answer(
        "Привет! Я SMM-помощник Валерии.\nВыбери действие:",
        reply_markup=get_start_keyboard()
    )

# Функция для безопасного редактирования сообщений
async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup=None):
    max_retries = 2
    retry_count = 0
    
    while retry_count <= max_retries:
        try:
            # Проверяем, что callback.message существует и является обычным сообщением (а не InaccessibleMessage)
            if callback.message and not isinstance(callback.message, InaccessibleMessage) and getattr(callback.message, "message_id", None):
                await callback.message.edit_text(text, reply_markup=reply_markup)
                return  # Успешно отредактировали сообщение
            else:
                await callback.answer(text[:199] if len(text) > 199 else text, show_alert=True) # Ограничение длины для show_alert
                return  # Успешно отправили ответ
        except Exception as e:
            retry_count += 1
            if retry_count > max_retries:
                await callback.answer(f"Ошибка: {str(e)[:19] if len(str(e)) > 199 else str(e)}", show_alert=True)
                # Логируем ошибку без подробного стека для экономии ресурсов
                logger.warning(f"Error in safe_edit_message after {max_retries + 1} attempts: {e}")  # Изменяем уровень логирования на warning
                return
            # Небольшая задержка перед повторной попыткой
            await asyncio.sleep(0.5)

# Обработчик callback'ов
# Возвращаем F.data == "..." так как Text фильтр может быть недоступен в текущей версии aiogram
@dp.callback_query(F.data == "generate_post")
async def generate_post_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # Проверяем, не идет ли уже генерация
    if await state.get_state() == PostStates.generating:
        await callback.answer("Подожди, идёт генерация поста...")
        return
    await state.set_state(PostStates.generating)
    
    await safe_edit_message(callback, "💭 Генерирую случайный пост...")
    
    # Случайный выбор типа поста
    template_key = random.choice(list(config.POST_TEMPLATES.keys()))
    template_text = config.POST_TEMPLATES[template_key]
    
    # Определяем тип услуги для генерации текста
    if template_key in ["pedicure_work", "seasonal_special", "client_feedback"]:
        service_type = "pedicure"
    else:
        service_type = "manicure_pedicure"
    
    # Добавляем контактный блок к шаблону
    prompt_with_contact = f"{template_text}\n\nВ конце поста **обязательно** добавь следующий блок с контактами:\n\n{config.CONTACT_BLOCK}"
    
    # Генерируем пост
    post_text = await generate_post_text(prompt_with_contact, service_type)
    
    if post_text:
        # Сохраняем сгенерированный пост и текущий шаблон в состояние
        await state.update_data(generated_post=post_text, photos=[], current_template=template_key)
        
        # Отправляем сгенерированный пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
        
        await safe_edit_message(callback, f"Сгенерированный пост:\n\n{post_text}", reply_markup=builder.as_markup())
    else:
        await safe_edit_message(callback, "Не удалось сгенерировать пост. Попробуйте снова.")


@dp.callback_query(F.data == "generate_topic_post")
async def generate_topic_post_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(PostStates.waiting_for_topic)
    await safe_edit_message(callback, "Пришли мне тему, на которую нужно написать пост. Это может быть любая тема, связанная с маникюром, педикюром или уходом за ногтями." + "\n\n" + "Например: 'Зимние дизайны ногтей', 'Педикюр для новичков', 'Уход за ногтями в домашних условиях'")


@dp.message(PostStates.waiting_for_topic, F.text)
async def process_topic_text(message: Message, state: FSMContext):
    topic = message.text.strip() if message.text else ""
    
    if not topic:
        await message.answer("Тема не может быть пустой. Пожалуйста, пришли тему поста еще раз.")
        return
    
    await state.set_state(PostStates.generating)
    await message.answer(f"Принял тему: '{topic}'. Генерирую пост...")
    
    # Используем универсальный шаблон, адаптируя его под заданную тему
    season = get_current_season() # Получаем текущее время года
    template_text = (
        f"Ты — Валерия, мастер маникюра и педикюра из Самары. Твой стиль — дружелюбный, живой и искренний. "
        f"Напиши интересный и полезный пост на тему: '{topic}'. "
        f"Учитывай время года: сейчас {season}. "
        f"Пиши простым языком, как будто общаешься с подругой. Используй 1-2 уместных эмодзи (например, 💖, ✨, 💅, 🔥). "
        f"Текст должен быть информативным и engaging. Не используй специальное форматирование (жирный шрифт, курсив). "
        f"Длина текста — около 300-500 символов."
    )
    
    # Генерируем пост
    post_text = await generate_post_text(template_text, "manicure_pedicure")
    
    if post_text:
        # Сохраняем сгенерированный пост и тему в состояние
        await state.update_data(generated_post=post_text, photos=[], current_template="topic_based", topic=topic)
        
        # Отправляем сгенерированный пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post_topic")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
        
        await message.answer(f"Сгенерированный пост на тему '{topic}':\n\n{post_text}", reply_markup=builder.as_markup())
    else:
        await message.answer("Не удалось сгенерировать пост на заданную тему. Попробуйте снова." + "\n\n" + "Пришли тему поста еще раз.")


@dp.callback_query(F.data == "generate_pedicure_post")
async def generate_pedicure_post_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # Проверяем, не идет ли уже генерация
    if await state.get_state() == PostStates.generating:
        await callback.answer("Подожди, идёт генерация поста...")
        return
    await state.set_state(PostStates.generating)
    
    await safe_edit_message(callback, "💭 Генерирую пост о педикюре...")
    
    # Выбираем шаблон для педикюра
    template_key = "pedicure_work"
    template_text = config.POST_TEMPLATES[template_key]
    service_type = "pedicure"
    
    # Добавляем контактный блок к шаблону
    prompt_with_contact = f"{template_text}\n\nВ конце поста **обязательно** добавь следующий блок с контактами:\n\n{config.CONTACT_BLOCK}"
    
    # Генерируем пост
    post_text = await generate_post_text(prompt_with_contact, service_type)
    
    if post_text:
        # Сохраняем сгенерированный пост и текущий шаблон в состояние
        await state.update_data(generated_post=post_text, photos=[], current_template=template_key)
        
        # Отправляем сгенерированный пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
        
        await safe_edit_message(callback, f"Сгенерированный пост о педикюре:\n\n{post_text}", reply_markup=builder.as_markup())
    else:
        await safe_edit_message(callback, "Не удалось сгенерировать пост о педикюре. Попробуйте снова.")

# Функция для получения клавиатуры с типами постов
def get_post_type_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Красивая работа", callback_data="template_beautiful_work")
    builder.button(text="Лайфстайл", callback_data="template_lifestyle")
    builder.button(text="Полезный пост", callback_data="template_useful_post")
    builder.button(text="Красивый педикюр", callback_data="template_pedicure_work")
    builder.button(text="Сезонная тема", callback_data="template_seasonal_special")
    builder.button(text="Отзыв клиента", callback_data="template_client_feedback")
    return builder.as_markup()

# Обработчики для выбора типа поста
@dp.callback_query(F.data.startswith("template_"))
async def handle_template_selection(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    if callback.data:
        template_key = callback.data.replace("template_", "")
    else:
        await safe_edit_message(callback, "Произошла ошибка. Пожалуйста, начните заново.")
        return
        
    # Проверяем наличие шаблона
    template_text = config.POST_TEMPLATES.get(template_key)
    if not template_text:
        await safe_edit_message(callback, "Неизвестный тип поста. Пожалуйста, выберите снова.")
        return
    
    # Определяем тип услуги для генерации текста
    if template_key in ["pedicure_work", "seasonal_special", "client_feedback"]:
        service_type = "pedicure"
    else:
        service_type = "manicure_pedicure"
    
    # Добавляем контактный блок к шаблону
    prompt_with_contact = f"{template_text}\n\nВ конце поста **обязательно** добавь следующий блок с контактами:\n\n{config.CONTACT_BLOCK}"
    
    await safe_edit_message(callback, "💭 Генерирую пост...")
    
    # Генерируем пост
    post_text = await generate_post_text(prompt_with_contact, service_type)
    
    if post_text:
        # Сохраняем сгенерированный пост и текущий шаблон в состояние
        await state.update_data(generated_post=post_text, photos=[], current_template=template_key)
        
        # Отправляем сгенерированный пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
        
        await safe_edit_message(callback, f"Сгенерированный пост:\n\n{post_text}", reply_markup=builder.as_markup())
    else:
        await safe_edit_message(callback, "Не удалось сгенерировать пост. Попробуйте снова.")

@dp.callback_query(F.data == "regenerate_post")
async def regenerate_post_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    # Получаем текущий шаблон из состояния
    data = await state.get_data()
    current_template = data.get('current_template', 'beautiful_work')
    template_text = config.POST_TEMPLATES.get(current_template)
    if not template_text:
        await safe_edit_message(callback, "Неизвестный тип поста. Пожалуйста, начните сначала.")
        return
    
    # Определяем тип услуги для генерации текста
    if current_template in ["pedicure_work", "seasonal_special", "client_feedback"]:
        service_type = "pedicure"
    else:
        service_type = "manicure_pedicure"
    
    # Добавляем контактный блок к шаблону
    prompt_with_contact = f"{template_text}\n\nВ конце поста **обязательно** добавь следующий блок с контактами:\n\n{config.CONTACT_BLOCK}"
    
    await safe_edit_message(callback, "💭 Перегенерирую пост...")
    
    # Генерируем новый пост
    post_text = await generate_post_text(prompt_with_contact, service_type)
    
    if post_text:
        # Обновляем сгенерированный пост в состоянии
        await state.update_data(generated_post=post_text)
        
        # Отправляем новый пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
        
        await safe_edit_message(callback, f"Новый пост:\n\n{post_text}", reply_markup=builder.as_markup())
    else:
        await safe_edit_message(callback, "Не удалось сгенерировать пост. Попробуйте снова.")

@dp.callback_query(F.data == "add_photo")
async def add_photo_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await safe_edit_message(callback, "Пришли фото для поста (можно несколько).")
    await state.set_state(PostStates.waiting_for_photos)

# Обработчик фото
@dp.message(PostStates.waiting_for_photos, F.photo)
async def photo_handler(message: Message, state: FSMContext):
    # Получаем текущие фото из состояния
    data = await state.get_data()
    photos = data.get('photos', [])
    
    # Ограничиваем количество фото до максимально возможного в Telegram (10) и в конфиге
    max_photos_for_telegram = min(10, config.MAX_PHOTOS_PER_POST)
    if len(photos) >= max_photos_for_telegram:
        await message.answer(f"Максимальное количество фото в посте: {max_photos_for_telegram}")
        return
    
    # Получаем ID фото (берем самое высокое качество)
    if message.photo and len(message.photo) > 0:
        photo_id = message.photo[-1].file_id
        file_info = await bot.get_file(photo_id)
        
        # Убираем проверку размера файла, чтобы позволить загружать фото любого размера
        # if file_info.file_size and file_info.file_size > config.MAX_PHOTO_SIZE_MB * 1024 * 1024:
        #     await message.answer(f"Фото слишком большое. Максимальный размер: {config.MAX_PHOTO_SIZE_MB} МБ")
        #     return
        
        photos.append(photo_id)
    else:
        await message.answer("Ошибка: фото не найдено.")
        return
    
    # Обновляем состояние с новым списком фото
    await state.update_data(photos=photos)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Готово, все фото загружены", callback_data="photos_done")
    builder.button(text="📷 Добавить еще фото", callback_data="add_more_photos")
    
    await message.answer(
        f"Фото добавлено. Всего фото: {len(photos)}\nМожешь добавить еще или завершить загрузку.",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data == "photos_done")
async def photos_done_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    # Получаем текущий пост и фото
    data = await state.get_data()
    post_text = data.get('generated_post')
    photos = data.get('photos', [])
    
    if not post_text:
        await safe_edit_message(callback, "Ошибка: нет сгенерированного поста.")
        return
    
    # Отправляем подтверждение
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать", callback_data="publish_now")
    builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
    builder.button(text="📷 Добавить фото", callback_data="add_photo")
    builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
    
    await safe_edit_message(callback, f"Все фото загружены!\n\nТекст поста:\n{post_text}\n\nФото: {len(photos)} шт.\n\nОпубликовать или отредактировать?", reply_markup=builder.as_markup())
    
    await state.set_state(PostStates.ready_to_publish)

@dp.callback_query(F.data == "add_more_photos")
async def add_more_photos_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await safe_edit_message(callback, "Продолжай отправлять фото.")

@dp.callback_query(F.data == "publish_now")
async def publish_now_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await safe_edit_message(callback, "Публикую пост...")
    
    # Получаем пост и фото из состояния
    data = await state.get_data()
    post_text = data.get('generated_post')
    photos = data.get('photos', [])
    
    if not post_text:
        await safe_edit_message(callback, "Ошибка: нет сгенерированного поста.")
        return
    
    # Проверяем длину текста поста
    if len(post_text) > 3000:
        post_text = post_text[:2997] + "..."

    # Ограничиваем количество фото до максимально возможного в Telegram (10) и в конфиге
    max_photos_for_telegram = min(10, config.MAX_PHOTOS_PER_POST)
    if photos and len(photos) > max_photos_for_telegram:
        photos = photos[:max_photos_for_telegram]
    
    # Подготовка медиагруппы для Telegram
    media_group = []
    if photos:
        media_group = [InputMediaPhoto(media=file_id) for file_id in photos]
        # Ограничиваем количество фото в медиагруппе до максимально возможного в Telegram и в конфиге
        media_group = media_group[:max_photos_for_telegram]
    
    try:
        # Ограничиваем параллельные публикации с помощью семафора
        async with publish_semaphore:
            # Публикуем в Telegram и VK параллельно
            # Публикуем в Telegram и VK параллельно с корректным указанием return_exceptions=True
            telegram_result, vk_result = await asyncio.gather(
                publish_telegram_post(bot, post_text, media_group),
                publish_vk_post(bot, post_text, photos),
                return_exceptions=True # Это именованный параметр, указываем явно
            )
        
        # Проверяем результаты публикации
        # Функции могут возвращать True, False, None или исключение
        # None означает успешное выполнение без явного возврата значения
        telegram_success = telegram_result is True or telegram_result is None or (telegram_result is not False and not isinstance(telegram_result, Exception))
        vk_success = vk_result is True or vk_result is None or (vk_result is not False and not isinstance(vk_result, Exception))
        
        # Если VK не удался, отправляем уведомление в Telegram админу
        if not vk_success:
            error_msg = "❌ ВНИМАНИЕ: Пост не опубликован в VK. "
            if isinstance(vk_result, Exception):
                error_msg += f"Ошибка: {str(vk_result)}"
                # Логируем traceback ошибки VK для отладки
                logger.error(f"VK publication error: {vk_result}", exc_info=True)
            else:
                error_msg += "Неизвестная ошибка"
            
            # Отправляем уведомление админу в Telegram
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"⚠️ Ошибка публикации в VK:\n\n{error_msg}\n\nПост был опубликован только в Telegram канале."
                )
            except Exception as notify_error:
                logger.error(f"Не удалось отправить уведомление об ошибке VK админу: {notify_error}")
        else:
            # Логируем успешную публикацию в VK
            logger.info("Пост успешно опубликован в VK")
        
        # Очищаем состояние после публикации
        await state.clear()
        
        # Убираем удаление объектов, так как Python сам управляет памятью
        # del post_text
        # del photos
        # if media_group:
        #     media_group.clear()
        # del media_group
        
        # Формируем сообщение о результате публикации
        # Уточняем логику: если функция не вернула False или исключение, считаем успехом
        if telegram_success and vk_success:
            result_message = "✅ Пост успешно опубликован в Telegram и VK!"
        elif telegram_success:
            result_message = "✅ Пост опубликован в Telegram.\n⚠️ Не удалось опубликовать в VK (админ уведомлен)."
        elif vk_success:
            result_message = "✅ Пост опубликован в VK.\n⚠️ Не удалось опубликовать в Telegram."
        else:
            # Проверяем, были ли вообще попытки публикации
            if telegram_result is None and vk_result is None:
                result_message = "✅ Пост успешно опубликован!"
            else:
                result_message = "❌ Не удалось опубликовать пост ни в Telegram, ни в VK."
        
        await safe_edit_message(callback, result_message)
        
    except Exception as e:
        logger.error(f"Error during publishing: {e}", exc_info=False) # Убираем подробное логирование
        await safe_edit_message(callback, f"❌ Ошибка при публикации: {e}")
        # Очищаем состояние даже при ошибке
        await state.clear()
        # Убираем удаление объектов даже при ошибке, так как Python сам управляет памятью
        # if 'post_text' in locals():
        #     del post_text
        # if 'photos' in locals():
        #     del photos
        # if 'media_group' in locals():
        #     if media_group:
        #         media_group.clear()
        #     del media_group

# Удаляем дублирующий хендлер publish_handler, так как он делает то же самое, что и publish_now_handler

@dp.callback_query(F.data == "edit_post_text")
async def edit_post_text_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    # Получаем текущий пост
    data = await state.get_data()
    post_text = data.get('generated_post', '')
    
    if not post_text:
        await safe_edit_message(callback, "Ошибка: нет сгенерированного поста для редактирования.")
        return
    
    # Переходим в состояние редактирования
    await state.set_state(PostStates.editing_post)
    
    # Отправляем текущий текст и предлагаем отредактировать
    message = f"Текущий текст поста:\n\n{post_text}\n\nПришли новый текст поста или нажми 'Пропустить', чтобы оставить без изменений:"
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Пропустить редактирование", callback_data="skip_editing")
    await safe_edit_message(callback, message, builder.as_markup())


@dp.message(PostStates.editing_post, F.text)
async def process_edited_text(message: Message, state: FSMContext):
    # Получаем текст, введенный пользователем
    edited_text = message.text
    
    # Обновляем текст поста в состоянии
    await state.update_data(generated_post=edited_text)
    
    # Получаем фото и другие данные
    data = await state.get_data()
    photos = data.get('photos', [])
    
    # Возвращаемся к состоянию готовности к публикации
    await state.set_state(PostStates.ready_to_publish)
    
    # Отправляем обновленный пост с кнопками
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать", callback_data="publish_now")
    builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
    builder.button(text="📷 Добавить фото", callback_data="add_photo")
    builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
    
    await message.answer(f"Текст поста обновлен!\n\nНовый текст:\n{edited_text}\n\nФото: {len(photos)} шт.\n\nОпубликовать или отредактировать еще?", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "skip_editing")
async def skip_editing_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    # Получаем фото и другие данные
    data = await state.get_data()
    post_text = data.get('generated_post', '')
    photos = data.get('photos', [])
    
    # Возвращаемся к состоянию готовности к публикации
    await state.set_state(PostStates.ready_to_publish)
    
    # Отправляем текущий пост с кнопками
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать", callback_data="publish_now")
    builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
    builder.button(text="📷 Добавить фото", callback_data="add_photo")
    builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
    
    await safe_edit_message(callback, f"Редактирование пропущено.\n\nТекст поста:\n{post_text}\n\nФото: {len(photos)} шт.\n\nОпубликовать или отредактировать?", builder.as_markup())


@dp.callback_query(F.data == "regenerate_post_topic")
async def regenerate_topic_post_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    # Получаем текущую тему из состояния
    data = await state.get_data()
    topic = data.get('topic', '')
    
    if not topic:
        await safe_edit_message(callback, "Не найдена тема для генерации поста.")
        return
    
    await safe_edit_message(callback, f"💭 Перегенерирую пост на тему '{topic}'...")
    
    # Используем универсальный шаблон, адаптируя его под заданную тему
    season = get_current_season() # Получаем текущее время года
    template_text = (
        f"Ты — Валерия, мастер маникюра и педикюра из Самары. Твой стиль — дружелюбный, живой и искренний. "
        f"Напиши интересный и полезный пост на тему: '{topic}'. "
        f"Учитывай время года: сейчас {season}. "
        f"Пиши простым языком, как будто общаешься с подругой. Используй 1-2 уместных эмодзи (например, 💖, ✨, 💅, 🔥). "
        f"Текст должен быть информативным и engaging. Не используй специальное форматирование (жирный шрифт, курсив). "
        f"Длина текста — около 300-500 символов."
    )
    
    # Генерируем пост
    post_text = await generate_post_text(template_text, "manicure_pedicure")
    
    if post_text:
        # Обновляем сгенерированный пост в состоянии
        await state.update_data(generated_post=post_text)
        
        # Отправляем новый пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post_topic")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        builder.button(text="✏️ Редактировать текст", callback_data="edit_post_text")
        
        await safe_edit_message(callback, f"Новый пост на тему '{topic}':\n\n{post_text}", reply_markup=builder.as_markup())
    else:
        await safe_edit_message(callback, "Не удалось сгенерировать пост на заданную тему. Попробуйте снова.")


@dp.callback_query(F.data == "reset")
async def reset_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await safe_edit_message(callback, "Состояние сброшено. Выбери действие:", reply_markup=get_start_keyboard())

def get_start_keyboard():
    """Возвращает унифицированную клавиатуру для команды /start и других случаев"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🪄 Сгенерировать пост", callback_data="generate_post")
    builder.button(text="📝 Написать пост на тему", callback_data="generate_topic_post")
    builder.button(text="📷 Добавить фото", callback_data="add_photo")
    builder.button(text="✅ Опубликовать", callback_data="publish_now")
    builder.button(text="🔁 Перегенерировать", callback_data="regenerate_post")
    builder.button(text="🔄 Сброс", callback_data="reset")
    # builder.button(text="💅 Педикюр", callback_data="generate_pedicure_post")  # Убираем дублирующую кнопку
    builder.adjust(2, 2, 2, 1)  # Располагаем кнопки по 2 в ряд, последняя кнопка в отдельном ряду
    return builder.as_markup()

import signal

# Глобальная переменная для управления запуском бота
# running = True  # Убираем, так как не используется

def signal_handler(signum, frame):
    # global running  # Не используется
    logger.info("Received interrupt signal. Shutting down gracefully...")
    # running = False  # Не используется

async def main():
    logger.info("Starting bot...")
    
    # Регистрируем обработчик сигнала для корректного завершения
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Используем polling с параметрами для работы в контейнере Docker
    # Важно: убедитесь, что только один экземпляр бота запущен одновременно
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            timeout=30,
            drop_pending_updates=True,  # Сбрасываем старые обновления при запуске
            handle_signals=False  # Отключаем встроенные сигналы, чтобы использовать свои
        )
    except Exception as e:
        logger.error(f"Error during polling: {e}", exc_info=False)
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")
     
     # # Запуск с webhook (раскомментируйте для использования на сервере с HTTPS)
     # from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
     # from aiohttp import web
     #
     # # Настройка webhook
     # WEBHOOK_PATH = '/webhook'
     # # На продакшене укажите реальный домен с HTTPS
     # WEBHOOK_URL = f'https://your_domain.com{WEBHOOK_PATH}'
     #
     # # Установка webhook
     # await bot.set_webhook(
     #     url=WEBHOOK_URL,
     #     drop_pending_updates=True,
     #     allowed_updates=dp.resolve_used_update_types(),
     # )
     #
     # # Настройка веб-приложения
     # app = web.Application()
     # SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
     # setup_application(app, dp, bot=bot)
     #
     # # Запуск веб-сервера
     # runner = web.AppRunner(app)
     # await runner.setup()
     # site = web.TCPSite(runner, 'localhost', 8080) # Используем порт 8080
     # await site.start()
     #
     # # Ожидание завершения
     # await asyncio.Event().wait()

if __name__ == "__main__":
    # Запуск с ограничением потребления памяти
    asyncio.run(main(), debug=False)