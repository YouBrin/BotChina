import gspread
import asyncio
import logging
import traceback
import html
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    JobQueue
)
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from time import sleep

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# Конфигурация
SPREADSHEET_ID = "1oPXXHOzbAYlPZlgKOo8XFKkhaZl20KOP-zXDYL5r1w4"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
ADMIN_ID = 5889636341

# Состояния диалога для добавления товара
(
    NAME, TRACK, PRICE_CNY, WEIGHT,
    PRICE_SHIPPING, PACKAGE, STATUS
) = range(7)

# Состояния для настроек параметров
(
    WAITING_FOR_CNY_RATE,
    WAITING_FOR_USD_RATE,
    WAITING_FOR_RATIO,
    WAITING_FOR_DELIVERY_RATE
) = range(7, 11)

# Кеш параметров
params_cache = {}
last_updated = None
CACHE_TIMEOUT = 300  # 5 минут

# Инициализация Google Sheets
try:
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Лист1")
    logger.info("Успешное подключение к Google Sheets")
except Exception as e:
    logger.error(f"Ошибка подключения: {e}")
    raise

def refresh_parameters(force=False):
    """Обновление кеша параметров"""
    global params_cache, last_updated
    try:
        if force or (last_updated is None) or (datetime.now() - last_updated > timedelta(seconds=CACHE_TIMEOUT)):
            values = sheet.batch_get(["B2", "B3", "B4", "B5"])
            
            def parse_value(cell_value):
                if cell_value and isinstance(cell_value, str):
                    return float(cell_value.replace(',', '.'))
                return 0.0

            new_params = {
                "cny_rate": parse_value(values[0][0][0]) if values[0] and values[0][0] else 0.0,
                "usd_rate": parse_value(values[1][0][0]) if values[1] and values[1][0] else 0.0,
                "jpy_to_usd_ratio": parse_value(values[2][0][0]) if values[2] and values[2][0] else 0.0,
                "delivery_rate": parse_value(values[3][0][0]) if values[3] and values[3][0] else 0.0,
                "last_modified": datetime.now().isoformat()
            }
            
            if new_params != params_cache:
                logger.info("Обновление кеша параметров")
                params_cache = new_params
                last_updated = datetime.now()
            
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка обновления параметров: {e}")
        return False

def get_parameters():
    """Получение параметров с кешированием"""
    refresh_parameters(force=True)
    return params_cache.copy()

def save_parameters(new_params):
    """Сохранение параметров в таблицу"""
    global params_cache
    max_retries = 3
    current_params = get_parameters()
    
    if new_params != current_params:
        try:
            for attempt in range(max_retries):
                try:
                    sheet.batch_update([
                        {'range': 'B2', 'values': [[new_params['cny_rate']]]},
                        {'range': 'B3', 'values': [[new_params['usd_rate']]]},
                        {'range': 'B4', 'values': [[new_params['jpy_to_usd_ratio']]]},
                        {'range': 'B5', 'values': [[new_params['delivery_rate']]]}
                    ])
                    logger.debug("Параметры сохранены")
                    refresh_parameters(force=True)
                    return True
                except gspread.exceptions.APIError as e:
                    logger.error(f"Ошибка API (попытка {attempt + 1}): {e}")
                    sleep(5)
            return False
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
            return False
    return True

# Фоновая задача для проверки изменений
async def background_check(context: CallbackContext):
    if refresh_parameters():
        logger.info("Параметры были обновлены из таблицы")

def main_keyboard():
    """Главное меню с кнопками"""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Добавить товар 🛒"), KeyboardButton("Мои товары 📦")],
            [KeyboardButton("⚙️ Параметры")]  # Убрана кнопка "Помощь ❓"
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие:"
    )

def parameters_keyboard():
    """Клавиатура для меню параметров"""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Текущие параметры"), KeyboardButton("Изменить параметры")],
            [KeyboardButton("◀️ Назад")]
        ],
        resize_keyboard=True
    )

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "🏍 Добро пожаловать в China Moto!",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END

async def cancel(update: Update, context: CallbackContext):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Действие отменено",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END

async def add_item_start(update: Update, context: CallbackContext):
    context.user_data.clear()
    await update.message.reply_text(
        "🏍 Введите название товара:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена ❌")]], resize_keyboard=True)
    )
    return NAME

async def save_name(update: Update, context: CallbackContext):
    if update.message.text == "Отмена ❌":
        return await cancel(update, context)
    
    context.user_data['name'] = update.message.text
    await update.message.reply_text(
        "📦 Введите трек-номер:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена ❌")]], resize_keyboard=True)
    )
    return TRACK

async def save_track(update: Update, context: CallbackContext):
    if update.message.text == "Отмена ❌":
        return await cancel(update, context)
    
    context.user_data['track'] = update.message.text
    await update.message.reply_text(
        "💴 Введите цену в ¥:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена ❌")]], resize_keyboard=True)
    )
    return PRICE_CNY

async def save_price_cny(update: Update, context: CallbackContext):
    if update.message.text == "Отмена ❌":
        return await cancel(update, context)
    
    try:
        params = get_parameters()
        context.user_data['price_cny'] = float(update.message.text.replace(',', '.'))
        context.user_data['price_byn'] = context.user_data['price_cny'] * params['cny_rate']
        await update.message.reply_text(
            "⚖ Введите вес (кг):",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена ❌")]], resize_keyboard=True)
    )
        return WEIGHT
    except ValueError:
        await update.message.reply_text("❌ Введите число")
        return PRICE_CNY

async def save_weight(update: Update, context: CallbackContext):
    if update.message.text == "Отмена ❌":
        return await cancel(update, context)
    
    try:
        context.user_data['weight'] = float(update.message.text.replace(',', '.'))
        await update.message.reply_text(
            "🚚 Введите стоимость за кг доставки в $:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена ❌")]], resize_keyboard=True)
        )
        return PRICE_SHIPPING
    except ValueError:
        await update.message.reply_text("❌ Введите число")
        return WEIGHT

async def save_shipping(update: Update, context: CallbackContext):
    if update.message.text == "Отмена ❌":
        return await cancel(update, context)
    
    try:
        params = get_parameters()
        context.user_data['shipping_per_kg'] = float(update.message.text.replace(',', '.'))
        shipping_cost = (context.user_data['shipping_per_kg'] + params['delivery_rate']) * context.user_data['weight']
        context.user_data['shipping'] = shipping_cost  # Общая стоимость доставки в $
        
        await update.message.reply_text(
            "📦 Введите цену упаковки:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена ❌")]], resize_keyboard=True)
        )
        return PACKAGE
    except Exception as e:
        logger.error(f"Ошибка расчета: {e}")
        await update.message.reply_text("❌ Ошибка расчета. Введите число")
        return PRICE_SHIPPING

async def save_package(update: Update, context: CallbackContext):
    if update.message.text == "Отмена ❌":
        return await cancel(update, context)
    
    try:
        params = get_parameters()
        context.user_data['package'] = float(update.message.text.replace(',', '.'))
        
        # Расчет стоимостей
        shipping_total_usd = context.user_data['shipping']
        shipping_total_rub = shipping_total_usd * params['usd_rate']
        total_rub = (
            context.user_data['price_byn'] + 
            shipping_total_rub + 
            context.user_data['package']
        )
        
        # Сохраняем все значения в user_data
        context.user_data['shipping_total_usd'] = shipping_total_usd
        context.user_data['shipping_total_rub'] = shipping_total_rub
        context.user_data['total_rub'] = total_rub
        context.user_data['total_usd'] = total_rub / params['usd_rate']
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Получен ✅", callback_data='received'),
             InlineKeyboardButton("Не получен ❌", callback_data='not_received')],
            [InlineKeyboardButton("Главное меню", callback_data='main_menu')]
        ])
        
        await update.message.reply_text(
            "📌 Выберите статус:",
            reply_markup=keyboard
        )
        return STATUS
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка расчета")
        return await cancel(update, context)

async def save_status(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'main_menu':
        return await main_menu(update, context)
    
    status = "Получен" if query.data == 'received' else "Не получен"
    
    # Формируем строку с учетом всех новых столбцов
    row = [
        context.user_data['name'],                      # Название
        context.user_data['track'],                    # Трек номер
        context.user_data['price_cny'],                # Цена в Китае (¥)
        context.user_data['price_byn'],                # Цена в рублях
        context.user_data['weight'],                   # Вес
        context.user_data['shipping_per_kg'],          # Доставка за кг ($)
        context.user_data['shipping_total_usd'],       # Общая стоимость доставки ($)
        context.user_data['total_rub'],                # Общая стоимость (₽)
        context.user_data['total_usd'],                # Общая стоимость ($)
        context.user_data['package'],                  # Упаковка
        status                                         # Статус
    ]
    
    try:
        sheet.append_row(row)
        await query.edit_message_text("✅ Товар успешно добавлен!")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Главное меню:",
            reply_markup=main_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        await query.edit_message_text("❌ Ошибка сохранения в таблицу")
    finally:
        context.user_data.clear()
    return ConversationHandler.END

VIEW_ITEM = 11

async def show_items(update: Update, context: CallbackContext):
    try:
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Лист1")
        data = sheet.get_values("A7:B")  # Получаем название и трек начиная с A7
        
        # Фильтруем пустые строки и сохраняем номера строк
        items_list = []
        for idx, row in enumerate(data):
            if any(field.strip() for field in row):
                items_list.append({
                    'row_num': idx + 7,  # Номер строки в таблице (начинается с 7)
                    'name': row[0] if len(row) > 0 else '',
                    'track': row[1] if len(row) > 1 else ''
                })
        
        if not items_list:
            await update.message.reply_text(
                "📭 Список товаров пуст",
                reply_markup=main_keyboard()
            )
            return ConversationHandler.END

        # Разбиваем на части по 50 товаров
        chunk_size = 50
        total_items = len(items_list)
        
        for i in range(0, total_items, chunk_size):
            chunk = items_list[i:i + chunk_size]
            # Формируем сообщение с продолжением нумерации
            message_text = "📦 Список товаров:\n\n" + "\n".join(
                f"{idx+1+i}. {item['name']} - Трек: {item['track']}"
                for idx, item in enumerate(chunk)
            )
            
            await update.message.reply_text(
                message_text,
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("Главное меню")]],
                    resize_keyboard=True
                )
            )

        await update.message.reply_text(
            f"Всего товаров: {total_items}\n\n"
            "Введите номер товара для подробностей или нажмите 'Главное меню'",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("Главное меню")]],
                resize_keyboard=True
            )
        )
        
        context.user_data['items_list'] = items_list
        return VIEW_ITEM

    except Exception as e:
        logger.error(f"Ошибка загрузки: {str(e)}")
        await update.message.reply_text(
            "❌ Ошибка загрузки данных",
            reply_markup=main_keyboard()
        )
        return ConversationHandler.END


async def view_item_details(update: Update, context: CallbackContext):
    if update.message.text == "Главное меню":
        return await main_menu(update, context)
    
    try:
        item_number = int(update.message.text)
        items = context.user_data.get('items_list', [])
        
        if 1 <= item_number <= len(items):
            item = items[item_number-1]
            
            # Получаем полные данные товара из таблицы
            try:
                sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Лист1")
                # Берем данные из соответствующей строки (A7 + номер товара - 1)
                full_row = sheet.row_values(item['row_num'])
                
                # Формируем ответ с проверкой наличия данных
                response_text = f"🔹 Товар #{item_number}\n" \
                               f"🏷 Название: {item['name']}\n" \
                               f"📮 Трек: {item['track']}\n"
                
                # Добавляем остальные поля только если они есть
                if len(full_row) > 2:
                    response_text += f"🇨🇳 Цена: {float(full_row[2].replace(',', '.')):.2f} ¥\n" \
                                    f"₽ Рубли: {float(full_row[3].replace(',', '.')):.2f}\n" \
                                    f"⚖ Вес: {float(full_row[4].replace(',', '.')):.2f} кг\n" \
                                    f"🚚 Доставка: {float(full_row[5].replace(',', '.')):.2f} $\n" \
                                    f"📦 Итого доставка: {float(full_row[6].replace(',', '.')):.2f} $\n" \
                                    f"📌 Общая стоимость: {float(full_row[7].replace(',', '.')):.2f} ₽\n" \
                                    f"💵 Общая стоимость: {float(full_row[8].replace(',', '.')):.2f} $\n" \
                                    f"🎁 Упаковка: {float(full_row[9].replace(',', '.')):.2f} $\n" \
                                    f"📌 Статус: {full_row[10] if len(full_row) > 10 else 'нет данных'}"
                
                await update.message.reply_text(
                    response_text,
                    reply_markup=main_keyboard()
                )
            except Exception as e:
                logger.error(f"Ошибка получения полных данных: {e}")
                await update.message.reply_text(
                    "❌ Ошибка загрузки полных данных товара",
                    reply_markup=main_keyboard()
                )
        else:
            await update.message.reply_text(
                "❌ Неверный номер товара. Попробуйте еще раз.",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("Главное меню")]],
                    resize_keyboard=True
                )
            )
            return VIEW_ITEM
            
    except ValueError:
        await update.message.reply_text(
            "❌ Введите номер товара цифрами",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("Главное меню")]],
                resize_keyboard=True
            )
        )
        return VIEW_ITEM
    
    return ConversationHandler.END

async def main_menu(update: Update, context: CallbackContext):
    context.user_data.clear()
    
    if update.callback_query is not None:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "Главное меню:",
            reply_markup=main_keyboard()
        )
    elif update.message is not None:
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=main_keyboard()
        )
    else:
        logger.warning("Необработанный тип update: %s", update)
        
    return ConversationHandler.END

async def show_parameters(update: Update, context: CallbackContext):
    """Показать текущие параметры"""
    try:
        params = get_parameters()
        text = (
            "📌 Текущие параметры:\n\n"
            f"• Курс ¥: {params['cny_rate']} RUB\n"
            f"• Курс $: {params['usd_rate']} RUB\n"
            f"• Соотношение ¥/$: {params['jpy_to_usd_ratio']}\n"
            f"• Доставка в Минск: {params['delivery_rate']} $\n"
            f"🕒 Последнее обновление: {last_updated.strftime('%H:%M:%S') if last_updated else 'N/A'}"
        )
        await update.message.reply_text(text, reply_markup=parameters_keyboard())
    except Exception as e:
        await update.message.reply_text("❌ Ошибка загрузки параметров", reply_markup=main_keyboard())

async def parameters_menu(update: Update, context: CallbackContext):
    """Меню управления параметрами"""
    await update.message.reply_text(
        "⚙️ Меню параметры:",
        reply_markup=parameters_keyboard()
    )

async def settings_menu(update: Update, context: CallbackContext):
    """Меню изменения параметров"""
    try:
        params = get_parameters()
        inline_keyboard = [
            [InlineKeyboardButton(f"Курс ¥ ({params['cny_rate']} RUB)", callback_data="set_cny")],
            [InlineKeyboardButton(f"Курс $ ({params['usd_rate']} RUB)", callback_data="set_usd")],
            [InlineKeyboardButton(f"Соотношение ¥/$ ({params['jpy_to_usd_ratio']})", callback_data="set_ratio")],
            [InlineKeyboardButton(f"Доставка ({params['delivery_rate']} $)", callback_data="set_delivery")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        await update.message.reply_text("Выберите параметр для изменения:", reply_markup=reply_markup)
    except Exception as e:
        await update.message.reply_text("❌ Ошибка загрузки меню", reply_markup=main_keyboard())

async def settings_button_handler(update: Update, context: CallbackContext):
    """Обработчик кнопок меню параметров"""
    query = update.callback_query
    await query.answer()
    
    try:
        params = get_parameters()
        if query.data == "set_cny":
            context.user_data['state'] = WAITING_FOR_CNY_RATE
            await query.message.reply_text(f"Текущий курс ¥: {params['cny_rate']} RUB\nВведите новое значение:")
            return WAITING_FOR_CNY_RATE
        elif query.data == "set_usd":
            context.user_data['state'] = WAITING_FOR_USD_RATE
            await query.message.reply_text(f"Текущий курс $: {params['usd_rate']} RUB\nВведите новое значение:")
            return WAITING_FOR_USD_RATE
        elif query.data == "set_ratio":
            context.user_data['state'] = WAITING_FOR_RATIO
            await query.message.reply_text(f"Текущее соотношение ¥/$: {params['jpy_to_usd_ratio']}\nВведите новое значение:")
            return WAITING_FOR_RATIO
        elif query.data == "set_delivery":
            context.user_data['state'] = WAITING_FOR_DELIVERY_RATE
            await query.message.reply_text(f"Текущая стоимость доставки: {params['delivery_rate']} $\nВведите новое значение:")
            return WAITING_FOR_DELIVERY_RATE
        elif query.data == "back":
            await query.delete_message()
            await parameters_menu(update, context)
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await query.message.reply_text("❌ Ошибка", reply_markup=main_keyboard())
    return ConversationHandler.END

async def handle_parameter_input(update: Update, context: CallbackContext):
    """Обработка ввода новых значений параметров"""
    text = update.message.text
    state = context.user_data.get('state')

    try:
        value = float(text.replace(',', '.'))
        params = get_parameters()
        if state == WAITING_FOR_CNY_RATE:
            params['cny_rate'] = value
        elif state == WAITING_FOR_USD_RATE:
            params['usd_rate'] = value
        elif state == WAITING_FOR_RATIO:
            params['jpy_to_usd_ratio'] = value
        elif state == WAITING_FOR_DELIVERY_RATE:
            params['delivery_rate'] = value

        if save_parameters(params):
            await update.message.reply_text("✅ Успешно обновлено!", reply_markup=parameters_keyboard())
        else:
            await update.message.reply_text("❌ Ошибка сохранения", reply_markup=parameters_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Введите число (например: 123.45 или 123,45)", reply_markup=parameters_keyboard())
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Критическая ошибка", reply_markup=main_keyboard())

    context.user_data.clear()
    return ConversationHandler.END

async def handle_message(update: Update, context: CallbackContext):
    """Обработчик текстовых сообщений"""
    if context.user_data.get('state'):
        return

    text = update.message.text
    try:
        if text == "⚙️ Параметры":
            await parameters_menu(update, context)
        elif text == "Изменить параметры":
            await settings_menu(update, context)
        elif text == "Текущие параметры":
            await show_parameters(update, context)
        elif text == "◀️ Назад":
            await start(update, context)
        elif text == "Главное меню":
            await main_menu(update, context)
        # Удален обработчик для кнопки "Помощь ❓"
    except Exception as e:
        logger.error(f"Ошибка: {e}")

async def error_handler(update: Update, context: CallbackContext):
    logger.error("Ошибка:", exc_info=context.error)
    tb = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"⚠️ Ошибка:\n<code>{html.escape(tb)}</code>",
        parse_mode="HTML"
    )
    if update.message:
        await update.message.reply_text(
            "❌ Произошла ошибка. Администратор уведомлен.",
            reply_markup=main_keyboard()
        )

def main():
    application = Application.builder().token("7524666016:AAFTwXVNntSzV-wIRn7NZ9d8DgELsPbdgKA").build()

    # Настройка фоновых задач
    if application.job_queue:
        application.job_queue.run_repeating(
            background_check,
            interval=300,
            first=10
        )
    else:
        logger.warning("JobQueue недоступен. Фоновые задачи отключены.")

    # ConversationHandler для добавления товара
    add_item_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r'Добавить товар 🛒'), add_item_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_name)],
            TRACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_track)],
            PRICE_CNY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_price_cny)],
            WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_weight)],
            PRICE_SHIPPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_shipping)],
            PACKAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_package)],
            STATUS: [CallbackQueryHandler(save_status)]
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            MessageHandler(filters.Regex(r'Отмена ❌|Главное меню'), main_menu)
        ],
        allow_reentry=True
    )

    # ConversationHandler для просмотра товаров
    view_items_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r'Мои товары 📦'), show_items)],
        states={
            VIEW_ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, view_item_details)]
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            MessageHandler(filters.Regex(r'Главное меню'), main_menu)
        ],
        allow_reentry=True
    )

    # ConversationHandler для настроек параметров
    settings_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'Изменить параметры'), settings_menu),
            CallbackQueryHandler(settings_button_handler)
        ],
        states={
            WAITING_FOR_CNY_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_parameter_input)],
            WAITING_FOR_USD_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_parameter_input)],
            WAITING_FOR_RATIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_parameter_input)],
            WAITING_FOR_DELIVERY_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_parameter_input)],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            MessageHandler(filters.Regex(r'Отмена ❌|◀️ Назад'), parameters_menu)
        ],
        allow_reentry=True
    )

    application.add_handler(CommandHandler('start', start))
    application.add_handler(add_item_handler)
    application.add_handler(view_items_handler)  # Добавляем новый обработчик
    application.add_handler(settings_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    application.run_polling()

if __name__ == '__main__':
    main()