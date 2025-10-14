import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InputMediaPhoto, InaccessibleMessage
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from content_generator import generate_post_text
from publisher import publish_telegram_post, publish_vk_post

# Настройка логирования
logging.basicConfig(level=logging.WARNING)  # Уменьшаем уровень логирования
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(
    token=config.TELEGRAM_TOKEN or "",
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

# Удаляем неиспользуемый код
# Вместо сохранения фото на диск, будем хранить только file_id в состоянии

# Обработчик команды /start
@dp.message(CommandStart())
async def command_start_handler(message: Message):
    if message.from_user and str(message.from_user.id) != str(config.ADMIN_ID):
        return
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🪄 Сгенерировать пост", callback_data="generate_post")
    
    await message.answer(
        "Привет! Я SMM-помощник Валерии.\nВыбери действие:",
        reply_markup=builder.as_markup()
    )

# Функция для безопасного редактирования сообщений
async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        # Проверяем, что callback.message существует и является обычным сообщением (а не InaccessibleMessage)
        if callback.message and not isinstance(callback.message, InaccessibleMessage) and getattr(callback.message, "message_id", None):
            await callback.message.edit_text(text, reply_markup=reply_markup)
        else:
            await callback.answer(text[:199] if len(text) > 199 else text, show_alert=True) # Ограничение длины для show_alert
    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)[:199] if len(str(e)) > 199 else str(e)}", show_alert=True)
        # Логируем ошибку без подробного стека для экономии ресурсов
        logger.error(f"Error in safe_edit_message: {e}")

# Обработчик callback'ов
@dp.callback_query(F.data == "generate_post")
async def generate_post_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # Проверяем, не идет ли уже генерация
    if await state.get_state() == PostStates.generating:
        await callback.answer("Подожди, идёт генерация поста...")
        return
    await state.set_state(PostStates.generating)
    
    await safe_edit_message(callback, "Генерирую случайный пост...")
    
    # Случайный выбор типа поста
    import random
    template_key = random.choice(list(config.POST_TEMPLATES.keys()))
    template_text = config.POST_TEMPLATES[template_key]
    
    # Добавляем контактный блок к шаблону
    prompt_with_contact = f"{template_text}\n\nВ конце поста **обязательно** добавь следующий блок с контактами:\n\n{config.CONTACT_BLOCK}"
    
    # Генерируем пост
    post_text = await generate_post_text(prompt_with_contact)
    
    if post_text:
        # Сохраняем сгенерированный пост и текущий шаблон в состояние
        await state.update_data(generated_post=post_text, photos=[], current_template=template_key)
        
        # Отправляем сгенерированный пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        
        await safe_edit_message(callback, f"Сгенерированный пост:\n\n{post_text}", reply_markup=builder.as_markup())
    else:
        await safe_edit_message(callback, "Не удалось сгенерировать пост. Попробуйте снова.")

# Функция для получения клавиатуры с типами постов
def get_post_type_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Красивая работа", callback_data="template_beautiful_work")
    builder.button(text="Лайфстайл", callback_data="template_lifestyle")
    builder.button(text="Полезный пост", callback_data="template_useful_post")
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
    
    # Добавляем контактный блок к шаблону
    prompt_with_contact = f"{template_text}\n\nВ конце поста **обязательно** добавь следующий блок с контактами:\n\n{config.CONTACT_BLOCK}"
    
    await safe_edit_message(callback, "Генерирую пост...")
    
    # Генерируем пост
    post_text = await generate_post_text(prompt_with_contact)
    
    if post_text:
        # Сохраняем сгенерированный пост и текущий шаблон в состояние
        await state.update_data(generated_post=post_text, photos=[], current_template=template_key)
        
        # Отправляем сгенерированный пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        
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
    
    # Добавляем контактный блок к шаблону
    prompt_with_contact = f"{template_text}\n\nВ конце поста **обязательно** добавь следующий блок с контактами:\n\n{config.CONTACT_BLOCK}"
    
    await safe_edit_message(callback, "Перегенерирую пост...")
    
    # Генерируем новый пост
    post_text = await generate_post_text(prompt_with_contact)
    
    if post_text:
        # Обновляем сгенерированный пост в состоянии
        await state.update_data(generated_post=post_text)
        
        # Отправляем новый пост с кнопками
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Опубликовать", callback_data="publish_now")
        builder.button(text="🔁 Сгенерировать заново", callback_data="regenerate_post")
        builder.button(text="📷 Добавить фото", callback_data="add_photo")
        
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
    
    # Проверяем лимит на количество фото
    if len(photos) >= config.MAX_PHOTOS_PER_POST:
        await message.answer(f"Достигнут лимит фото: {config.MAX_PHOTOS_PER_POST}")
        return

    # Ограничиваем количество фото до 10 для Telegram (максимум в медиагруппе)
    if len(photos) > 10:
        await message.answer("Максимальное количество фото в посте: 10")
        return
    
    # Получаем ID фото (берем самое высокое качество)
    if message.photo:
        photo_id = message.photo[-1].file_id
        file_info = await bot.get_file(photo_id)
        
        # Проверяем размер файла (приблизительно, в байтах)
        if file_info.file_size and file_info.file_size > config.MAX_PHOTO_SIZE_MB * 1024 * 1024:
            await message.answer(f"Фото слишком большое. Максимальный размер: {config.MAX_PHOTO_SIZE_MB} МБ")
            return
        
        photos.append(photo_id)
        
        # Очищаем временные объекты
        del file_info
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
    
    await safe_edit_message(callback, f"Все фото загружены!\n\nТекст поста:\n{post_text}\n\nФото: {len(photos)} шт.\n\nОпубликовать?", reply_markup=builder.as_markup())
    
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

    # Ограничиваем количество фото до 10 для Telegram (максимум в медиагруппе)
    if photos and len(photos) > 10:
        photos = photos[:10]
    
    # Подготовка медиагруппы для Telegram
    media_group = []
    if photos:
        media_group = [InputMediaPhoto(media=file_id) for file_id in photos]
        # Ограничиваем количество фото в медиагруппе до 10
        media_group = media_group[:10]
    
    try:
        # Ограничиваем параллельные публикации с помощью семафора
        async with publish_semaphore:
            # Публикуем в Telegram и VK параллельно
            await asyncio.gather(
                publish_telegram_post(bot, post_text, media_group),
                publish_vk_post(bot, post_text, photos)
            )
        
        # Очищаем состояние после публикации
        await state.clear()
        
        # Очистка временных объектов
        del post_text
        del photos
        if media_group:
            media_group.clear()
        del media_group
        
        
        await safe_edit_message(callback, "✅ Пост успешно опубликован в Telegram и VK!")
        
    except Exception as e:
        logger.error(f"Error during publishing: {e}", exc_info=False) # Убираем подробное логирование
        await safe_edit_message(callback, f"❌ Ошибка при публикации: {e}")
        # Очищаем состояние даже при ошибке
        await state.clear()
        # Очистка временных объектов даже при ошибке
        if 'post_text' in locals():
            del post_text
        if 'photos' in locals():
            del photos
        if 'media_group' in locals():
            if media_group:
                media_group.clear()
            del media_group

# Удаляем дублирующий хендлер publish_handler, так как он делает то же самое, что и publish_now_handler

@dp.callback_query(F.data == "reset")
async def reset_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await safe_edit_message(callback, "Состояние сброшено. Выбери действие:", reply_markup=get_start_keyboard())

def get_start_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🪄 Сгенерировать пост", callback_data="generate_post")
    builder.button(text="📷 Добавить фото", callback_data="add_photo")
    builder.button(text="✅ Опубликовать", callback_data="publish_now")
    builder.button(text="🔁 Перегенерировать", callback_data="regenerate_post")
    builder.button(text="🔄 Сброс", callback_data="reset")
    return builder.as_markup()

async def main():
    logger.info("Starting bot...")
    # Используем polling с параметрами для работы в контейнере Docker
    # Важно: убедитесь, что только один экземпляр бота запущен одновременно
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        timeout=30,
        drop_pending_updates=True  # Сбрасываем старые обновления при запуске
    )
    
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