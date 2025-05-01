import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F  # type: ignore
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, Update  # type: ignore
from aiogram.filters import Command  # type: ignore
from aiogram.fsm.storage.memory import MemoryStorage  # type: ignore
import asyncio
from datetime import datetime

from dotenv import load_dotenv  # type: ignore
import os

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Логирование
logging.basicConfig(level=logging.INFO)

# Подключение к базе данных SQLite
conn = sqlite3.connect('users.db')
cursor = conn.cursor()

# Ensure the 'level' column exists
try:
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    print(columns)
except sqlite3.OperationalError as e:
    logging.error(f"Error ensuring 'level' column exists: {e}")

# Добавляем колонку coins, rewards и level, если их нет
try:
    cursor.execute("ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE users ADD COLUMN rewards TEXT DEFAULT ''")
    cursor.execute("ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1")
    conn.commit()
except sqlite3.OperationalError:
    pass  # Колонки уже существуют

# Добавляем колонку first_name, если её нет
try:
    cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass  # Колонка уже существует

# Таблица пользователей
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        referrer_id INTEGER,
        referrals_count INTEGER DEFAULT 0,
        discount REAL DEFAULT 0.0,
        coins INTEGER DEFAULT 0,
        rewards TEXT DEFAULT '',
        level INTEGER DEFAULT 1
    )
""")
conn.commit()

# Создаём таблицу purchases, если её нет
cursor.execute("""
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        referrer_id INTEGER,
        amount INTEGER,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()

# Словарь для отслеживания времени последнего использования команды
last_command_time = {}

# Словарь продуктов
PRODUCTS = {
    "discord_nitro_1m": {"name": "Discord Nitro (1 Month)", "price": 400},
    "spotify_premium_1m": {"name": "Spotify Premium (1 Month)", "price": 200},
    "twitch_level1_1m": {"name": "Twitch Level 1 (1 Month)", "price": 200},
}

# Функция для ограничения частоты команд
async def throttle_command(user_id: int, command: str, rate: int = 2):
    now = datetime.now()
    if user_id in last_command_time:
        last_time = last_command_time[user_id].get(command)
        if last_time and (now - last_time).total_seconds() < rate:
            return False
    last_command_time.setdefault(user_id, {})[command] = now
    return True

# Функция добавления нового пользователя в БД
def add_user(user_id, username, referrer_id=None, first_name=None):
    # Проверяем, существует ли пользователь
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        logging.info(f"Пользователь {user_id} уже существует в базе данных.")
        return

    # Добавляем нового пользователя
    cursor.execute(
        "INSERT INTO users (user_id, username, first_name, referrer_id) VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, referrer_id)
    )
    conn.commit()
    logging.info(f"Добавлен новый пользователь: {user_id}, реферер: {referrer_id}")

    # Если есть реферер, обновляем его данные
    if referrer_id:
        logging.info(f"Обновляем данные реферера: {referrer_id}")
        update_referrals_count(referrer_id)
        update_discount_and_notify(referrer_id)

# Функция обновления количества рефералов
def update_referrals_count(user_id):
    cursor.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    logging.info(f"Количество рефералов обновлено для пользователя {user_id}")

# Функция обновления скидки и уведомления реферера
def update_discount_and_notify(user_id):
    cursor.execute("SELECT referrals_count FROM users WHERE user_id = ?", (user_id,))
    referrals_count = cursor.fetchone()[0]
    discount = min(referrals_count * 2, 50)  # 2% за каждого реферала, максимум 50%
    cursor.execute("UPDATE users SET discount = ? WHERE user_id = ?", (discount, user_id))
    conn.commit()
    logging.info(f"Скидка обновлена для пользователя {user_id}: {discount}%")

    # Уведомляем реферера
    asyncio.create_task(bot.send_message(
        user_id,
        f"🎉 *You have +1 new referral!*\n"
        f"*Your discount has been increased by 2%.*\n"
        f"*Current discount: {discount}%.*",
        parse_mode="Markdown"
    ))

# Главное меню (Reply-кнопки)
def main_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 My Profile"), KeyboardButton(text="🛒 Catalog")],
            [KeyboardButton(text="🎁 Gift Shop"), KeyboardButton(text="🎁 Referral System")],
            [KeyboardButton(text="ℹ️ About Us"), KeyboardButton(text="💬 Help & Support")],
            [KeyboardButton(text="❓ About Levels")]
        ],
        resize_keyboard=True,  # Уменьшает размер кнопок для компактного отображения
    )
    return keyboard

# Проверка, является ли пользователь администратором
def is_admin(user_id):
    return user_id == ADMIN_ID

# Функция для получения баланса монет пользователя
def get_user_coins(user_id):
    cursor.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result[0] if result else 0

# Функция для обновления наград пользователя
def add_reward(user_id, reward):
    cursor.execute("SELECT rewards FROM users WHERE user_id = ?", (user_id,))
    current_rewards = cursor.fetchone()[0]
    updated_rewards = current_rewards + f"{reward}, " if current_rewards else f"{reward}, "
    cursor.execute("UPDATE users SET rewards = ? WHERE user_id = ?", (updated_rewards, user_id))
    conn.commit()

# Функция добавления монет пользователю
def add_coins(user_id, coins_to_add):
    cursor.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    current_coins = cursor.fetchone()[0]
    new_coins = current_coins + coins_to_add
    cursor.execute("UPDATE users SET coins = ? WHERE user_id = ?", (new_coins, user_id))
    conn.commit()

# Функция обновления уровня пользователя
def update_user_level(user_id):
    cursor.execute("SELECT level FROM users WHERE user_id = ?", (user_id,))
    current_level = cursor.fetchone()[0]

    # Проверяем, совершал ли пользователь покупку или его реферал
    cursor.execute("SELECT COUNT(*) FROM purchases WHERE user_id = ? OR referrer_id = ?", (user_id, user_id))
    purchase_count = cursor.fetchone()[0]

    # Если есть покупки, повышаем уровень до 2
    if purchase_count > 0 and current_level < 2:
        cursor.execute("UPDATE users SET level = 2 WHERE user_id = ?", (user_id,))
        conn.commit()

        # Уведомляем пользователя о повышении уровня
        asyncio.create_task(bot.send_message(
            user_id,
            "🎉 *Congratulations!*\n"
            "Your level has been upgraded to *Level 2*!\n\n"
            "🔹 *New benefits:*\n"
            "• You can now purchase all gifts in the Gift Shop.\n"
            "• You earn *30 coins* for each referral instead of 25.\n",
            parse_mode="Markdown"
        ))

# Обработчик команды /start
@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name  # Получаем имя пользователя
    referrer_id = None

    # Ограничение частоты команды
    if not await throttle_command(user_id, "start", rate=2):
        await message.answer("⏳ Please wait before using this command again.")
        return

    # Если сообщение содержит /start и реферальный код
    if len(message.text.split()) > 1:
        referrer_id = int(message.text.split()[1])
        logging.info(f"Пользователь {user_id} пришел по реферальной ссылке от {referrer_id}")

    # Добавляем пользователя в базу данных
    add_user(user_id, username, referrer_id, first_name)

    # Приветственное сообщение с фотографией и текстом
    photo_url = "https://i.imgur.com/lnr4Z0M.jpeg" 
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=photo_url,
        caption=(
            f"Hello, *{first_name}*! \nWelcome to *Horda Shop*! 🎉\n\n"
            "*💫 Tap the menu below to snoop around.*\n"
            "*Deals don’t bite, but they do disappear🫥 — so don’t blink...*\n\n\n"
            "*🪴Our News Channel:* [@HORDAHORDA]\n"
            "*Reviews:* [@hordareviews]"
        ),
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# Обработчик кнопки "👤 My Profile"
@dp.message(F.text == "👤 My Profile")
async def handle_profile(message: Message):
    user_id = message.from_user.id

    # Проверяем, существует ли пользователь в базе данных
    cursor.execute("SELECT referrals_count, discount, coins, rewards, level FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result:
        referrals_count, discount, coins, rewards, level = result
        rewards_list = rewards if rewards else "No rewards yet."
        await message.answer(
            f"*👤 Your Profile*\n\n"
            f"*👥 Referrals:* {referrals_count}\n"
            f"*💸 Discount:* {discount:.2f}%\n"
            f"*💰 Coins:* {coins} 🏅\n"
             f"*🏆 Level:* {level} 💎\n\n"
            f"*🎁 Presents bought:* {rewards_list}\n",
            parse_mode="Markdown"
        )
    else:
        await message.answer("You are not registered in the system yet.")

# Обработчик кнопки "🎁 Gift Shop"
@dp.message(F.text == "🎁 Gift Shop")
async def handle_gift_shop(message: Message):
    user_id = message.from_user.id

    # Проверяем уровень пользователя
    cursor.execute("SELECT level FROM users WHERE user_id = ?", (user_id,))
    level = cursor.fetchone()[0]

    # Формируем клавиатуру для всех пользователей
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎮 Discord Nitro (1 Month)"), KeyboardButton(text="🎮 Discord Nitro (3 Months)")],
            [KeyboardButton(text="🎵 Spotify Premium (1 Month)"), KeyboardButton(text="🎵 Spotify Premium (3 Months)")],
            [KeyboardButton(text="🎵 Spotify Premium (6 Months)"), KeyboardButton(text="🎵 Spotify Premium (12 Months)")],
            [KeyboardButton(text="🟣 Twitch Level 1 (1 Month)"), KeyboardButton(text="🟣 Twitch Level 1 (3 Months)")],
            [KeyboardButton(text="🟣 Twitch Level 1 (6 Months)"), KeyboardButton(text="🟣 Twitch Level 2 (1 Month)")],
            [KeyboardButton(text="🟣 Twitch Level 3 (1 Month)"), KeyboardButton(text="💸 Buy 50% Discount (300 coins 🏅)")],
            [KeyboardButton(text="💸 Buy 10% Discount (50 coins 🏅)"), KeyboardButton(text="💸 Buy 25% Discount (120 coins 🏅)")],
                                            [KeyboardButton(text="⬅️ Back to Menu")]
        ],
        resize_keyboard=True,
    )

    await message.answer(
        "🎁 *Gift Shop*\n\n"
        "Here are the available gifts and discounts you can purchase with your coins.\n\n"
        "🎮 *Discord Nitro*\n"
        "▫️ *1 Month — 400 coins 🏅*\n"
        "▫️* 3 Months — 800 coins 🏅*\n\n"
        "🎵 *Spotify Premium*\n"
        "▫️ *1 Month — 200 coins 🏅*\n"
        "▫️ *3 Months — 450 coins 🏅*\n"
        "▫️ *6 Months — 600 coins 🏅*\n"
        "▫️ *12 Months — 1220 coins 🏅*\n\n"
        "🟣 *Twitch Subscriptions*\n"
        "▫️ *Level 1 (1 Month) — 200 coins 🏅*\n"
        "▫️ *Level 1 (3 Months) — 400 coins* 🏅\n"
        "▫️ *Level 1 (6 Months) — 800 coins *🏅\n"
        "▫️ *Level 2 (1 Month) — 300 coins* 🏅\n"
        "▫️ *Level 3 (1 Month) — 800 coins *🏅\n\n"
        "💸 *Discounts:*\n"
        "▫️* 10% — 50 coins *🏅\n"
        "▫️ *25% — 120 coins* 🏅\n"
        "▫️ *50% — 300 coins *🏅\n\n",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# Обработчик покупки подарков
@dp.message(F.text.in_({
    "🎮 Discord Nitro (1 Month)",
    "🎮 Discord Nitro (3 Months)",
    "🎵 Spotify Premium (1 Month)",
    "🎵 Spotify Premium (3 Months)",
    "🎵 Spotify Premium (6 Months)",
    "🎵 Spotify Premium (12 Months)",
    "🟣 Twitch Level 1 (1 Month)",
    "🟣 Twitch Level 1 (3 Months)",
    "🟣 Twitch Level 1 (6 Months)",
    "🟣 Twitch Level 2 (1 Month)",
    "🟣 Twitch Level 3 (1 Month)"
}))
async def handle_gift_purchase(message: Message):
    user_id = message.from_user.id
    gift_mapping = {
        "🎮 Discord Nitro (1 Month)": ("Discord Nitro (1 Month)", 400),
        "🎮 Discord Nitro (3 Months)": ("Discord Nitro (3 Months)", 800),
        "🎵 Spotify Premium (1 Month)": ("Spotify Premium (1 Month)", 200),
        "🎵 Spotify Premium (3 Months)": ("Spotify Premium (3 Months)", 450),
        "🎵 Spotify Premium (6 Months)": ("Spotify Premium (6 Months)", 600),
        "🎵 Spotify Premium (12 Months)": ("Spotify Premium (12 Months)", 1220),
        "🟣 Twitch Level 1 (1 Month)": ("Twitch Level 1 (1 Month)", 200),
        "🟣 Twitch Level 1 (3 Months)": ("Twitch Level 1 (3 Months)", 400),
        "🟣 Twitch Level 1 (6 Months)": ("Twitch Level 1 (6 Months)", 800),
        "🟣 Twitch Level 2 (1 Month)": ("Twitch Level 2 (1 Month)", 300),
        "🟣 Twitch Level 3 (1 Month)": ("Twitch Level 3 (1 Month)", 800),
    }
    gift_name, gift_cost = gift_mapping[message.text]

    # Проверяем уровень пользователя
    cursor.execute("SELECT level FROM users WHERE user_id = ?", (user_id,))
    level = cursor.fetchone()[0]

    # Если уровень недостаточен
    if level < 2:
        await message.answer(
            f"❌ *This gift is only available for Level 2 users.*\n"
            f"Earn Level 2 by making a purchase or if your referral makes a purchase.\n\n"
            f"*Your current balance:* {get_user_coins(user_id)} 🏅 coins\n"
            f"*Cost:* {gift_cost} 🏅 coins",
            parse_mode="Markdown"
        )
        return

    # Проверяем баланс пользователя
    coins = get_user_coins(user_id)
    if coins < gift_cost:
        await message.answer(
            f"❌ *You don't have enough coins to buy {gift_name}.*\n"
            f"*Your current balance:* {coins} 🏅 coins\n"
            f"*Cost:* {gift_cost} 🏅 coins",
            parse_mode="Markdown"
        )
        return

    # Списываем монеты и добавляем подарок
    cursor.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (gift_cost, user_id))
    conn.commit()
    add_reward(user_id, gift_name)

    await message.answer(
        f"🎉 *Congratulations!*\n"
        f"*You successfully purchased {gift_name}.*\n"
        f"*Your current balance:* {coins - gift_cost} 🏅 coins",
        parse_mode="Markdown"
    )

# Обработчик покупки скидок
@dp.message(F.text.in_({
    "💸 Buy 10% Discount (50 coins 🏅)",
    "💸 Buy 25% Discount (120 coins 🏅)",
    "💸 Buy 50% Discount (300 coins 🏅)",
    "💸 Buy 75% Discount (600 coins 🏅)",
    "💸 Buy 100% Discount (1000 coins 🏅)"
}))
async def handle_buy_discount(message: Message):
    user_id = message.from_user.id
    discount_mapping = {
        "💸 Buy 10% Discount (50 coins 🏅)": (10, 50),
        "💸 Buy 25% Discount (120 coins 🏅)": (25, 120),
        "💸 Buy 50% Discount (300 coins 🏅)": (50, 300),
        "💸 Buy 75% Discount (600 coins 🏅)": (75, 600),
        "💸 Buy 100% Discount (1000 coins 🏅)": (100, 1000),
    }
    discount_percent, discount_cost = discount_mapping[message.text]

    # Проверяем уровень пользователя
    cursor.execute("SELECT level, coins, discount FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    level, coins, current_discount = user_data

    # Проверяем, доступна ли скидка для текущего уровня
    if discount_percent > 50 and level < 2:
        await message.answer(
            "❌ *This discount is only available for Level 2 users.*\n"
            "Earn Level 2 by making a purchase or if your referral makes a purchase.",
            parse_mode="Markdown"
        )
        return

    # Проверяем баланс пользователя
    if coins < discount_cost:
        await message.answer(
            f"❌ *You don't have enough coins to buy a {discount_percent}% discount.*\n"
            f"*Your current balance:* {coins} 🏅 coins\n"
            f"*Cost:* {discount_cost} 🏅 coins",
            parse_mode="Markdown"
        )
        return

    # Списываем монеты и увеличиваем скидку
    new_discount = current_discount + discount_percent
    cursor.execute("UPDATE users SET coins = coins - ?, discount = ? WHERE user_id = ?", (discount_cost, new_discount, user_id))
    conn.commit()

    await message.answer(
        f"🎉 *Congratulations!*\n"
        f"*You successfully purchased a {discount_percent}% discount.*\n"
        f"*Your current balance:* {coins - discount_cost} 🏅 coins\n"
        f"*Your total discount:* {new_discount}%",
        parse_mode="Markdown"
    )

# Обработчик кнопки "⬅️ Back to Menu"
@dp.message(F.text == "⬅️ Back to Menu")
async def handle_back_to_menu(message: Message):
    await message.answer("⬅️ Back to the main menu.", reply_markup=main_menu())

# Обработчик кнопки "Assortiment"
@dp.message(F.text == "🛒 Catalog")
async def handle_assortiment(message: Message):                              
    await message.answer(
        "Choose a category:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🎧 Spotify Premium"), KeyboardButton(text="🔴 YouTube Premium")],
                [KeyboardButton(text="🟣 Twitch Subscription"), KeyboardButton(text="💎 Discord Nitro")],
                [KeyboardButton(text="⭐ Telegram Stars"), KeyboardButton(text="Turkish Bankcards 🇹🇷")],
                                            [KeyboardButton(text="Back")]
            ],
            resize_keyboard=True
        )
    )

# Levels
@dp.message(F.text == "❓ About Levels")
async def handle_about_levels(message: Message):
    await message.answer(
        "*📈 About Levels*\n\n"
        "🔹 *Level 1:*\n"
        "• Access to basic features.\n"
        "• Earn 25 coins per referral.\n\n"
        "🔹 *Level 2:*\n"
        "• Access to premium gifts in the Gift Shop.\n"
        "• Earn 30 coins per referral.\n"
        "• Unlock exclusive discounts.\n\n"
        "🔹 *How to level up:*\n"
        "• Make a purchase or invite a friend who makes a purchase.\n\n"
        "Start leveling up today and enjoy more benefits! 🚀",
        parse_mode="Markdown"
    )


# Обработчики для Spotify, YouTube Premium и Twitch Prime
@dp.message(F.text == "🎧 Spotify Premium")
async def handle_spotify(message: Message):
    await message.answer(
        "🎵 *Spotify Premium Individual*\n\n"
        "▫️* 1 month — $3.99*\n\n"
        "▫️* 3 months — $8.99*\n\n"
        "▫️ *6 months — $12.99*\n\n"
        "*▫️ 12 months — $22.99* \n\n"
        "*Payment methods:\n🪙Crypto\n💸PayPal*\n\n"
        "*To buy: @headphony*",
    parse_mode="Markdown")

@dp.message(F.text == "🔴 YouTube Premium")
async def handle_youtube(message: Message):
    await message.answer(
        "soon..."
    )

@dp.message(F.text == "🟣 Twitch Subscription")
async def handle_twitch(message: Message):
    await message.answer(
        "*🎮 Twitch Subscription*\n"
        "*LEVEL 1✅\n\n*"
        "*▫️ Level 1 — 1 Month — $3.99*\n\n"
        "*▫️ Level 1 — 3 Months — $8.99*\n\n"
        "*▫️ Level 1 — 6 Months — $17.99*\n\n"
        "*LEVEL 2✅\n\n*"
        "*▫️ Level 2 — 1 Month — $5.99*\n\n"
        "*LEVEL 3✅\n\n*"
        "*▫️ Level 3 — 1 Month — $14.99*\n\n"
        "🥰No account access needed — just *your* and the *streamer’s* *nicknames!*\n\n"
        "*Payment methods:\n- Crypto\n- PayPal*\n\n"
        "*To buy: @heaphony*",
        
        parse_mode="Markdown"
    )

# Обработчик кнопки "Turkish Bankcards 🇹🇷"
@dp.message(F.text == "Turkish Bankcards 🇹🇷")
async def handle_turkish_bankcards(message: Message):
    await message.answer(
        "Choose a card type:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Fups 🇹🇷"), KeyboardButton(text="Ozan 🇹🇷")],
                [KeyboardButton(text="Paycell 🇹🇷"), KeyboardButton(text="Other Stuff 🇹🇷")],
                [KeyboardButton(text="📖 Must Read"), KeyboardButton(text="Back")]
            ],
            resize_keyboard=True
        )
    )

@dp.message(F.text == "Fups 🇹🇷")
async def handle_fups(message: Message):
    photo_url = "https://imgur.com/a/Ns79AjX"  # Замените на URL вашей картинки
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=photo_url,
        caption=(
            "<b>FUPS</b> is a digital banking platform offering personal <b>IBANs</b>, <b>Visa cards</b>, and "
            "<b>instant money transfers</b> ⭐\n\n"
            "Enjoy <b>high daily limits</b>, easy bill payments, and fast top-ups — all with a user-friendly app that "
            "fits your lifestyle! 😎\n\n"
            "<b>Learn more about FUPS:</b> <a href='https://fups.com'>Visit FUPS</a>\n\n\n"
            "<b>It's yours just for 19.99$! 💸</b>\n\n"
            "<b>Payment methods:</b>\n- Crypto (TON, BTC, USDC, BNB)\n- PayPal\n\n"
            "To buy: @headphony"
        ),
        parse_mode="HTML"
    )

@dp.message(F.text == "Ozan 🇹🇷")
async def handle_ozan(message: Message):
    photo_url = "https://imgur.com/a/hGYZ9Ny"  # Замените на URL вашей картинки
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=photo_url,
        caption=(
            "<b>Your money, your rules.</b>\n\n"
            "<a href='https://ozan.com'>Ozan</a> gives you <b>instant accounts</b>, <b>powerful cards</b>, and <b>fast</b>, "
            "<b>borderless</b> transfers — all with real, <b>transparent limits</b>.\n\n"
            "Spend, send, and control your finances without delays or surprises 🌐\n\n"
            "<b>Price is only 19.99$ 💸</b>\n\n"
            "Freedom has never felt this easy 😏\n\n"
            "<b>Payment methods:</b>\n- Crypto (TON, BTC, USDC, BNB)\n- PayPal\n\n"
            "To buy: @headphony"
        ),
        parse_mode="HTML"
    )

@dp.message(F.text == "Paycell 🇹🇷")
async def handle_paycell(message: Message):
    photo_url = "https://imgur.com/a/LDGGDkG"  # Замените на URL вашей картинки
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=photo_url,
        caption=(
            "<b>Paycell</b>, powered by <a href='https://www.turkcell.com.tr'>Turkcell</a>, lets you pay <b>bills</b>, "
            "<b>shop online</b>, and <b>send money</b> with just your phone number ⭐\n\n"
            "<b>Supports both local and international payments, with flexible spending limits and fast processing!</b> 🚀\n\n\n"
            "<b>Priced at just 34.99$! 💸</b>\n\n"
            "<b>Payment methods:</b>\n- Crypto (TON, BTC, USDC, BNB)\n- PayPal\n\n"
            "To buy: @headphony\n\n"
            "⚠️CURRENTLY UNAVAILABLE⚠️"
        ),
        parse_mode="HTML"
    )

@dp.message(F.text == "Other Stuff 🇹🇷")
async def handle_back(message: Message):
    await message.answer(
        "*🇹🇷Premium methods to top up a Turkish card - 1.99$*\n\n"
        "*🇹🇷Turkish passport details - 5$*\n\n"
        "*Payment methods:\n- Crypto (TON, BTC, USDC, BNB)\n- PayPal\n\n*"
        "*To buy: @headphony*",
            parse_mode="Markdown")

@dp.message(F.text == "💎 Discord Nitro")
async def handle_discord(message: Message):
    await message.answer(
        "💎 *Discord Nitro Full*\n\n"
        "*1 month — $6.49*\n\n"
        "*3 months — $13.99*\n\n"
        "*6 months — soon...*\n\n"
        "*🎁 You'll get Nitro as a gift — no need to log in anywhere, no data required!*\n\n"
        "*⚜️ You'll only have to activate it with VPN and that's it!*\n\n"
        "*Payment methods:\n- Crypto (TON, BTC, USDC, BNB)\n- PayPal*\n\n"
        "*To buy @headphony*",
        parse_mode="Markdown"
    )

@dp.message(F.text == "⭐ Telegram Stars")
async def handle_telegram_stars(message: Message):
    await message.answer(
        "*⭐ Telegram Stars*\n\n"
        "*100⭐ — $1.79*\n\n"
        "*250⭐ — $4.59*\n\n"
        "*500⭐ — $8.99*\n\n"
        "*1000⭐ — $16.99*\n\n"
        "*📦 All stars are purchased officially and delivered via Telegram!*\n\n"
        "✅ No account info, no logins — just your *@username* to receive the gift.\n\n"
        "*Payment methods:\n- Crypto (TON, BTC, USDC, BNB)\n- PayPal*\n\n"
        "*To buy @headphony*",
        parse_mode="Markdown"
    )

# Обработчик кнопки "Назад"
@dp.message(F.text == "Back")
async def handle_back(message: Message):
    await message.answer("You are back to the main menu.", reply_markup=main_menu())

# Обработчик кнопки "Info about us"
@dp.message(F.text == "ℹ️ About Us")
async def handle_about(message: Message):
    await message.answer(
        "*Horda Shop. We don’t beg — we deliver.*\n\n"
        "*Fast deals, clean setup, zero bullshit.*\n\n"
        "You came for the *price* — you’ll stay for the service 👊\n\n"
        "*Cheap? Yeah 🤩*\n"
        "*Shady? Nah 😎*\n\n"
        "*We move different...*",
        parse_mode="Markdown"
    )

# Обработчик кнопки "Referral System"
@dp.message(F.text == "🎁 Referral System")
async def handle_referral(message: Message):
    user_id = message.from_user.id
    referral_link = f"https://t.me/hordashop_bot?start={user_id}"
    await message.answer(
        f"*🎉 Referral System*\n\n"
        f"*Invite* your *friends* and earn *rewards!*\n"
        f"For every user who joins with your link, you’ll receive:\n\n"
        f"• *🔁 25 coins automatically just for each referral*\n\n"
        f"• *💸 + 20% 🏅 of a purchase that your referral makes*\n\n"
        f"*Automatic Levelup if your referral makes a purchase 🔝*\n\n"
        f"*Your referral link: {referral_link}*",
    parse_mode="Markdown")

# Обработчик кнопки "Help"
@dp.message(F.text == "💬 Help & Support")
async def handle_help(message: Message):
    await message.answer(
        "*Got any questions?*\n\n"
        "Feel free to reach out to us anytime:\n"
        "*📩 @headphony*",
       parse_mode="Markdown" 
       )

@dp.message(F.text == "📖 Must Read")
async def handle_to_read(message: Message):
    await message.answer(
        "*Important! 🚨*\n\n"
"Please note that in rare cases, there may be a delay in the issuance of Turkish cards. We make every effort to ensure quick delivery, but depending on the volume of orders and external factors, the process may take slightly longer than usual.\n\n\n"
"*What might affect the processing time ❓*\n\n\n"
"*• Technical issues on the supplier's side ⚙️*\n\n"
"*• Temporary limitations on card availability 🚫*\n\n"
"*• Security and verification procedures 🛡️*\n\n\n"
"*We will keep you updated on the status of your order at each stage. In case of a delay, we guarantee that your card will be issued as soon as possible 😊*",
   parse_mode="Markdown"
   )

# Команда: /give_coins
@dp.message(Command(commands=["give_coins"]))
async def handle_give_coins(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: `/give_coins @username <amount>`", parse_mode="Markdown")
        return

    try:
        username = args[1].lstrip("@")
        coins_to_add = int(args[2])

        cursor.execute("SELECT user_id, coins FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if not user:
            await message.answer(f"User with username `@{username}` not found.", parse_mode="Markdown")
            return

        user_id, current_coins = user
        new_coins = current_coins + coins_to_add

        cursor.execute("UPDATE users SET coins = ? WHERE user_id = ?", (new_coins, user_id))
        conn.commit()

        await bot.send_message(
            user_id,
            f"🎉 *You have received {coins_to_add} 🏅 coins!*\n"
            f"*Your current balance: {new_coins} 🏅 coins.*",
            parse_mode="Markdown"
        )

        await message.answer(
            f"User with username `@{username}` has been credited with {coins_to_add} 🏅 coins.\n"
            f"New balance: {new_coins} 🏅 coins.",
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("Invalid input. Please provide a valid username and coin amount.")

# Команда: /remove_coins
@dp.message(Command(commands=["remove_coins"]))
async def handle_remove_coins(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: `/remove_coins @username <amount>`", parse_mode="Markdown")
        return

    try:
        username = args[1].lstrip("@")
        coins_to_remove = int(args[2])

        cursor.execute("SELECT user_id, coins FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if not user:
            await message.answer(f"User with username `@{username}` not found.", parse_mode="Markdown")
            return

        user_id, current_coins = user
        new_coins = max(current_coins - coins_to_remove, 0)

        cursor.execute("UPDATE users SET coins = ? WHERE user_id = ?", (new_coins, user_id))
        conn.commit()

        await bot.send_message(
            user_id,
            f"❌ *{coins_to_remove} 🏅 coins have been removed from your balance.*\n"
            f"*Your current balance: {new_coins} 🏅 coins.*",
            parse_mode="Markdown"
        )

        await message.answer(
            f"User with username `@{username}` has had {coins_to_remove} 🏅 coins removed.\n"
            f"New balance: {new_coins} 🏅 coins.",
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("Invalid input. Please provide a valid username and coin amount.")

# Команда: /register_purchase
@dp.message(Command(commands=["register_purchase"]))
async def handle_register_purchase(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: `/register_purchase @username <product_code>`", parse_mode="Markdown")
        return

    try:
        username = args[1].lstrip("@")
        product_code = args[2]

        # Проверяем, существует ли продукт
        if product_code not in PRODUCTS:
            await message.answer(f"Invalid product code: `{product_code}`", parse_mode="Markdown")
            return

        product = PRODUCTS[product_code]
        product_name = product["name"]
        product_price = product["price"]

        # Проверяем, существует ли пользователь
        cursor.execute("SELECT user_id, referrer_id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if not user:
            await message.answer(f"User with username `@{username}` not found.", parse_mode="Markdown")
            return

        user_id, referrer_id = user

        # Начисляем монеты рефереру, если он существует
        if referrer_id:
            coins_to_add = int(product_price * 0.2)
            add_coins(referrer_id, coins_to_add)

            await bot.send_message(
                referrer_id,
                f"🎉 *The user you invited made a purchase!*\n"
                f"*You earned {coins_to_add} 🏅 coins!*\n",
                parse_mode="Markdown"
            )

        # Обновляем уровень пользователя
        update_user_level(user_id)

        await message.answer(
            f"Purchase of `{product_name}` by user `@{username}` has been successfully registered.",
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("Invalid input. Please provide a valid username and product code.")

# Команда: /register_purchase_general
@dp.message(Command(commands=["register_purchase_general"]))
async def handle_register_purchase_general(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: `/register_purchase_general @username <amount>`", parse_mode="Markdown")
        return

    try:
        username = args[1].lstrip("@")
        purchase_amount = int(args[2])

        # Проверяем, существует ли пользователь
        cursor.execute("SELECT user_id, referrer_id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if not user:
            await message.answer(f"User with username `@{username}` not found.", parse_mode="Markdown")
            return

        user_id, referrer_id = user

        # Записываем покупку в таблицу purchases
        cursor.execute("INSERT INTO purchases (user_id, referrer_id, amount) VALUES (?, ?, ?)", (user_id, referrer_id, purchase_amount))
        conn.commit()

        # Обновляем уровень пользователя
        update_user_level(user_id)

        await message.answer(
            f"Purchase of `{purchase_amount}` coins by user `@{username}` has been successfully registered.",
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("Invalid input. Please provide a valid username and purchase amount.")

# Команда: /delete_user
@dp.message(Command(commands=["delete_user"]))
async def handle_delete_user(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Usage: `/delete_user <user_id>`", parse_mode="Markdown")
        return

    try:
        user_id = int(args[1])

        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            await message.answer(f"User with ID `{user_id}` not found.", parse_mode="Markdown")
            return

        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()

        await message.answer(f"User with ID `{user_id}` has been successfully deleted.", parse_mode="Markdown")

    except ValueError:
        await message.answer("Invalid input. Please provide a valid user ID.")

# Команда: /userstat
@dp.message(Command(commands=["userstat"]))
async def handle_userstat(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Usage: `/userstat @username`", parse_mode="Markdown")
        return

    username = args[1].lstrip("@")

    # Проверяем, существует ли пользователь
    cursor.execute("SELECT user_id, referrals_count, coins, rewards FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    if not user:
        await message.answer(f"User with username `@{username}` not found.", parse_mode="Markdown")
        return

    user_id, referrals_count, coins, rewards = user
    rewards_list = rewards if rewards else "No rewards yet."

    # Получаем список рефералов
    cursor.execute("SELECT username FROM users WHERE referrer_id = ?", (user_id,))
    referrals = cursor.fetchall()
    referrals_list = "\n".join([f"• @{referral[0]}" for referral in referrals]) if referrals else "No referrals yet."

    # Отправляем статистику
    await message.answer(
        f"*User Statistics:*\n\n"
        f"*User ID:* `{user_id}`\n"
        f"*Username:* `@{username}`\n"
        f"*Referrals:* `{referrals_count}`\n"
        f"*Coins:* `{coins} 🏅`\n"
        f"*Rewards:* `{rewards_list}`\n\n"
        f"*Referrals List:*\n{referrals_list}",
        parse_mode="Markdown"
    )

# Команда: /list_users
@dp.message(Command(commands=["list_users"]))
async def handle_list_users(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("🚫 Доступно только админам.")

    # Получаем список user_id из базы данных
    cursor.execute("SELECT user_id FROM users")
    user_ids = [row[0] for row in cursor.fetchall()]

    # Обновляем данные пользователей через get_chat
    for user_id in user_ids:
        try:
            chat = await bot.get_chat(user_id)  # Получаем актуальные данные пользователя
            await update_user_in_db(
                user_id=chat.id,
                username=chat.username,
                first_name=chat.first_name
            )
        except Exception as e:
            logging.warning(f"Не удалось обновить данные пользователя {user_id}: {e}")

    # Получаем актуальный список пользователей из базы данных
    cursor.execute("SELECT user_id, username, first_name FROM users")
    users = cursor.fetchall()

    if not users:
        return await message.answer("📭 База пользователей пуста.")

    # Формируем ответное сообщение
    response = "📂 <b>Список пользователей:</b>\n\n"
    for user_id, username, first_name in users:
        response += (
            f"▫️ <b>{first_name if first_name else '—'}</b>\n"
            f"├ @{username if username else 'нет юзернейма'}\n"
            f"└ ID: <code>{user_id}</code>\n"
            f"🔗 <a href='tg://user?id={user_id}'>Профиль</a>\n\n"
        )

    await message.answer(response, parse_mode="HTML", disable_web_page_preview=True)

# Глобальный обработчик ошибок
@dp.errors()
async def handle_errors(update: Update, exception: Exception):
    logging.error(f"An error occurred: {exception}\nUpdate: {update}")
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"An error occurred:\n\n{exception}",
        parse_mode="Markdown"
    )
    return True  # Return True to prevent the error from stopping the bot

# Обработчик для необработанных сообщений
@dp.message()
async def handle_unhandled_messages(message: Message):
    await message.answer("There is no such command. Try again!")

# Запуск бота
async def main():
    await dp.start_polling(bot)

logging.basicConfig(level=logging.DEBUG)

if __name__ == '__main__':
    asyncio.run(main())
