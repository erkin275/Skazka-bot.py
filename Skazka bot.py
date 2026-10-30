#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SKAZKA — Pishiriqlar tarqatish (distribyutorlik) uchun maxsus Telegram Bot.
Kutubxona: aiogram 3.x (pip install aiogram tzdata aiohttp)
Vaqt mintaqasi: Asia/Tashkent (UTC+5)
"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    FSInputFile,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ==============================================================================
# 1. SOZLAMALAR (CONFIG) VA TOSHKENT VAQTI
# ==============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "9175930111:AAHlb7yC45nsjQE0aGmvS65ZrSiiFfnhwz4")

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "6766950408").split(",")
    if x.strip().isdigit()
]

DB_PATH = os.getenv("SKAZKA_DB_PATH", "skazka_bot.db")
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

# Render uchun webhook sozlamalari
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = f"/bot/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

PORT = int(os.environ.get("PORT", 10000))


def now_tashkent_str() -> str:
    return datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M:%S")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ==============================================================================
# 2. MA'LUMOTLAR BAZASI (SQLite)
# ==============================================================================


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT NOT NULL,
                custom_name TEXT,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                registered_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                photo_file_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                photo_file_id TEXT,
                caption TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_client(user_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM clients WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def save_client(
    user_id: int,
    username: str | None,
    full_name: str,
    phone: str,
    address: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> None:
    existing = get_client(user_id)
    custom_name = existing["custom_name"] if existing else None
    registered_at = existing["registered_at"] if existing else now_tashkent_str()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO clients
            (user_id, username, full_name, custom_name, phone, address, latitude, longitude, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                full_name,
                custom_name,
                phone,
                address,
                latitude,
                longitude,
                registered_at,
            ),
        )
        conn.commit()


def update_client_custom_name(user_id: int, custom_name: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "UPDATE clients SET custom_name = ? WHERE user_id = ?",
            (custom_name.strip(), user_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_client(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM clients WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_all_clients() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM clients ORDER BY COALESCE(custom_name, full_name) ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def add_catalog_item(name: str, photo_file_id: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO catalog (name, photo_file_id, created_at) VALUES (?, ?, ?)",
            (name.strip(), photo_file_id, now_tashkent_str()),
        )
        conn.commit()
        return cursor.lastrowid


def get_catalog_items() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM catalog ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_catalog_item(item_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM catalog WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None


def update_catalog_item_name(item_id: int, new_name: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "UPDATE catalog SET name = ? WHERE id = ?",
            (new_name.strip(), item_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_catalog_item(item_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM catalog WHERE id = ?", (item_id,))
        conn.commit()
        return cursor.rowcount > 0


def log_order(client_id: int, photo_file_id: str | None, caption: str | None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO orders_log (client_id, photo_file_id, caption, created_at) VALUES (?, ?, ?, ?)",
            (client_id, photo_file_id, caption, now_tashkent_str()),
        )
        conn.commit()


def create_catalog_order(client_id: int, item_id: int, item_name: str, quantity: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO catalog_orders (client_id, item_id, item_name, quantity, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (client_id, item_id, item_name, quantity.strip(), now_tashkent_str()),
        )
        conn.commit()
        return cursor.lastrowid


def get_catalog_order(order_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM catalog_orders WHERE id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None


def update_catalog_order_status(order_id: int, status: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE catalog_orders SET status = ? WHERE id = ?",
            (status, order_id),
        )
        conn.commit()


def format_client_display_name(client: dict) -> str:
    if client.get("custom_name"):
        return f"{client['custom_name']} ({client['full_name']})"
    return client["full_name"]


# ==============================================================================
# 3. HOLATLAR (FSM STATES)
# ==============================================================================


class RegistrationStates(StatesGroup):
    waiting_for_full_name = State()
    waiting_for_phone = State()
    waiting_for_address = State()


class ClientOrderStates(StatesGroup):
    waiting_for_order_photo = State()
    waiting_for_catalog_quantity = State()


class AdminStates(StatesGroup):
    waiting_for_new_client_name = State()
    waiting_for_catalog_photo = State()
    waiting_for_catalog_name = State()
    waiting_for_catalog_rename = State()
    waiting_for_custom_broadcast = State()


# ==============================================================================
# 4. KLAVIATURALAR (KEYBOARDS)
# ==============================================================================


def client_main_kb(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📸 Zakaz / Foto-otchet yuborish")],
        [
            KeyboardButton(text="📖 Katalog (Mahsulotlar)"),
            KeyboardButton(text="👤 Mening ma'lumotlarim"),
        ],
    ]
    if is_admin(user_id):
        buttons.append([KeyboardButton(text="🔐 Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def admin_main_kb() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📢 Zakaz / Foto-otchet so'rash (Hammaga)")],
        [
            KeyboardButton(text="👥 Mijozlar ro'yxati"),
            KeyboardButton(text="✍️ Maxsus xabar yuborish"),
        ],
        [
            KeyboardButton(text="➕ Katalogga mahsulot qo'shish"),
            KeyboardButton(text="🗂 Katalogni boshqarish"),
        ],
        [KeyboardButton(text="⬅️ Mijoz menyusiga qaytish")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
    )


def phone_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Lokatsiya yuborish", request_location=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def build_clients_inline_kb(clients: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for c in clients:
        label = c["custom_name"] if c.get("custom_name") else c["full_name"]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🏪 {label} | {c['phone']}",
                    callback_data=f"client_view:{c['user_id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_detail_inline_kb(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Nomini o'zgartirish (masalan: Yugnakiy Alisher)",
                    callback_data=f"client_rename:{client_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📩 Shu mijozga zakaz eslatmasi yuborish",
                    callback_data=f"client_remind:{client_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Mijozni bazadan o'chirish",
                    callback_data=f"client_delete_ask:{client_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Mijozlar ro'yxatiga qaytish",
                    callback_data="clients_list_back",
                )
            ],
        ]
    )


def order_admin_actions_kb(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Zakaz qabul qilindi (Mijozga tasdiqlash)",
                    callback_data=f"order_confirm:{client_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Mijoz nomini o'zgartirish",
                    callback_data=f"client_rename:{client_id}",
                )
            ],
        ]
    )


def catalog_order_admin_kb(order_id: int, client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Tasdiqlash (Yetkazib beramiz)",
                    callback_data=f"cat_ord_ok:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Skladda qolmagan (Rad etish)",
                    callback_data=f"cat_ord_no:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Mijoz nomini o'zgartirish",
                    callback_data=f"client_rename:{client_id}",
                )
            ],
        ]
    )


# ==============================================================================
# 5. ROUTER VA HANDLERLAR
# ==============================================================================

router = Router()


@router.message(F.text == "❌ Bekor qilish")
async def cancel_action_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer(
            "❌ Amal bekor qilindi.",
            reply_markup=admin_main_kb(),
        )
    else:
        await message.answer(
            "❌ Amal bekor qilindi.",
            reply_markup=client_main_kb(user_id),
        )


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(
        f"Sizning Telegram ID raqamingiz: <code>{message.from_user.id}</code>\n"
        f"Toshkent vaqti: <b>{now_tashkent_str()}</b>"
    )


@router.message(Command("backup"))
async def cmd_backup(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda bu buyruqni ishlatish uchun huquq yo'q.")
        return

    if os.path.exists(DB_PATH):
        file_to_send = FSInputFile(DB_PATH)
        await message.answer_document(
            document=file_to_send,
            caption=f"📁 <b>Skazka Bot Bazasi (Backup)</b>\n🕒 Vaqt: {now_tashkent_str()}",
        )
    else:
        await message.answer("⚠️ Hozircha ma'lumotlar bazasi fayli topilmadi.")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    client = get_client(user_id)

    if client:
        display_name = format_client_display_name(client)
        await message.answer(
            f"Assalomu alaykum, <b>{display_name}</b>!\n"
            f"🥐 <b>Skazka</b> pishiriqlar botiga xush kelibsiz.\n\n"
            f"• Katalogdan pishiriq tanlab zakaz berish uchun <b>«📖 Katalog (Mahsulotlar)»</b> tugmasini bosing.\n"
            f"• Foto-otchet yuborish uchun esa <b>«📸 Zakaz / Foto-otchet yuborish»</b> tugmasini bosing.",
            reply_markup=client_main_kb(user_id),
        )
        return

    welcome_text = (
        "Assalomu alaykum! 🥐 <b>Skazka</b> rasmiy botiga xush kelibsiz!\n\n"
        "Botdan foydalanish va zakaz berish uchun qisqa ro'yxatdan o'ting.\n"
        "✍️ <b>Iltimos, Ism va Familiyangizni kiriting:</b>\n"
        "<i>(Masalan: Alisherov Alisher)</i>"
    )
    if is_admin(user_id):
        welcome_text += "\n\n💡 <i>Siz adminsiz! To'g'ridan-to'g'ri /admin buyrug'i orqali Admin panelga o'tishingiz ham mumkin.</i>"

    await state.set_state(RegistrationStates.waiting_for_full_name)
    await message.answer(welcome_text, reply_markup=ReplyKeyboardRemove())


@router.message(RegistrationStates.waiting_for_full_name, F.text)
async def reg_full_name(message: Message, state: FSMContext) -> None:
    full_name = message.text.strip()
    if len(full_name) < 3:
        await message.answer("Iltimos, ism va familiyangizni to'liqroq yozing:")
        return

    await state.update_data(full_name=full_name)
    await state.set_state(RegistrationStates.waiting_for_phone)
    await message.answer(
        "📞 Endi <b>telefon raqamingizni</b> pastdagi tugmani bosib yuboring yoki yozib qoldiring "
        "<i>(masalan: +998901234567)</i>:",
        reply_markup=phone_request_kb(),
    )


@router.message(RegistrationStates.waiting_for_phone)
async def reg_phone(message: Message, state: FSMContext) -> None:
    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        phone = message.text.strip()
    else:
        await message.answer(
            "Iltimos, telefon raqamingizni tugma orqali yoki yozuv ko'rinishida yuboring:"
        )
        return

    await state.update_data(phone=phone)
    await state.set_state(RegistrationStates.waiting_for_address)
    await message.answer(
        "🏪 Endi <b>magaziningiz joylashgan manzilni</b> (mo'ljal va magazin nomini) yozib yuboring "
        "yoki pastdagi tugma orqali <b>Lokatsiya</b> tashlang:",
        reply_markup=location_request_kb(),
    )


@router.message(RegistrationStates.waiting_for_address)
async def reg_address(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    full_name = data["full_name"]
    phone = data["phone"]
    latitude = None
    longitude = None

    if message.location:
        latitude = message.location.latitude
        longitude = message.location.longitude
        address = f"GPS Lokatsiya ({latitude:.5f}, {longitude:.5f})"
    elif message.text:
        address = message.text.strip()
    else:
        await message.answer(
            "Iltimos, magazin manzilini matn ko'rinishida yozing yoki lokatsiya yuboring:"
        )
        return

    user = message.from_user
    save_client(
        user_id=user.id,
        username=user.username,
        full_name=full_name,
        phone=phone,
        address=address,
        latitude=latitude,
        longitude=longitude,
    )
    await state.clear()

    await message.answer(
        "✅ <b>Tabriklaymiz, siz muvaffaqiyatli ro'yxatdan o'tdingiz!</b>\n\n"
        f"👤 <b>F.I.Sh:</b> {full_name}\n"
        f"📞 <b>Telefon:</b> {phone}\n"
        f"🏪 <b>Manzil:</b> {address}\n"
        f"🕒 <b>Vaqt:</b> {now_tashkent_str()}\n\n"
        "Endi <b>«📖 Katalog (Mahsulotlar)»</b> bo'limidan pishiriqlarni tanlab zakaz berishingiz "
        "yoki <b>«📸 Zakaz / Foto-otchet yuborish»</b> tugmasi orqali rasm yuborishingiz mumkin!",
        reply_markup=client_main_kb(user.id),
    )

    client_record = get_client(user.id)
    admin_notify_text = (
        "🆕 <b>Yangi mijoz ro'yxatdan o'tdi!</b>\n\n"
        f"👤 <b>Asl F.I.Sh:</b> {full_name}\n"
        f"📞 <b>Telefon:</b> {phone}\n"
        f"🏪 <b>Magazin manzili:</b> {address}\n"
        f"🔗 <b>Username:</b> @{user.username if user.username else 'mavjud emas'}\n"
        f"🕒 <b>Toshkent vaqti:</b> {client_record['registered_at']}\n\n"
        "💡 <i>Pastdagi tugmani bosib, ushbu mijozga o'zingiz uchun qulay nom "
        "(masalan: «Yugnakiy Alisher») qo'yib olishingiz mumkin:</i>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=admin_notify_text,
                reply_markup=client_detail_inline_kb(user.id),
            )
            if latitude and longitude:
                await bot.send_location(
                    chat_id=admin_id, latitude=latitude, longitude=longitude
                )
        except Exception as e:
            logging.warning(f"Adminga ({admin_id}) xabar yuborishda xatolik: {e}")


@router.message(F.text == "👤 Mening ma'lumotlarim")
async def show_my_profile(message: Message, state: FSMContext) -> None:
    client = get_client(message.from_user.id)
    if not client:
        await cmd_start(message, state)
        return

    text = (
        "👤 <b>Sizning ma'lumotlaringiz:</b>\n\n"
        f"• <b>F.I.Sh:</b> {client['full_name']}\n"
        f"• <b>Telefon:</b> {client['phone']}\n"
        f"• <b>Magazin manzili:</b> {client['address']}\n"
        f"• <b>Ro'yxatdan o'tilgan:</b> {client['registered_at']} (Toshkent vaqti)\n\n"
        "<i>Ma'lumotlarni yangilash uchun /re_register buyrug'ini bosing.</i>"
    )
    await message.answer(text, reply_markup=client_main_kb(message.from_user.id))


@router.message(Command("re_register"))
async def cmd_re_register(message: Message, state: FSMContext) -> None:
    await state.set_state(RegistrationStates.waiting_for_full_name)
    await message.answer(
        "🔄 Ma'lumotlarni qaytadan kiritamiz.\n"
        "✍️ <b>Ism va Familiyangizni kiriting:</b>",
        reply_markup=ReplyKeyboardRemove(),
    )


# ==============================================================================
# 6. KATALOG VA KATALOGDAN ZAKAZ QILISH (MIJOZ UCHUN)
# ==============================================================================


@router.message(F.text == "📖 Katalog (Mahsulotlar)")
async def client_view_catalog(message: Message, state: FSMContext) -> None:
    client = get_client(message.from_user.id)
    if not client and not is_admin(message.from_user.id):
        await cmd_start(message, state)
        return

    items = get_catalog_items()
    if not items:
        await message.answer(
            "📭 Hozircha katalogga mahsulotlar joylanmagan. Tez orada qo'shiladi!"
        )
        return

    await message.answer(
        f"🥐 <b>Skazka pishiriqlar katalogi ({len(items)} ta mahsulot):</b>\n"
        "Zakaz berish uchun xohlagan mahsulotingiz tagidagi <b>«🛒 Zakaz qilish»</b> tugmasini bosing:"
    )
    for idx, item in enumerate(items, start=1):
        order_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🛒 Zakaz qilish ({item['name'][:25]})",
                        callback_data=f"cat_buy:{item['id']}",
                    )
                ]
            ]
        )
        await message.answer_photo(
            photo=item["photo_file_id"],
            caption=f"🥐 <b>{idx}. {item['name']}</b>",
            reply_markup=order_kb,
        )


@router.callback_query(F.data.startswith("cat_buy:"))
async def cb_client_buy_from_catalog(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    client = get_client(user_id)
    if not client:
        await callback.answer("Avval ro'yxatdan o'ting!", show_alert=True)
        await cmd_start(callback.message, state)
        return

    item_id = int(callback.data.split(":")[1])
    item = get_catalog_item(item_id)
    if not item:
        await callback.answer("⚠️ Bu mahsulot katalogdan o'chirilgan!", show_alert=True)
        return

    await state.set_state(ClientOrderStates.waiting_for_catalog_quantity)
    await state.update_data(
        order_item_id=item["id"],
        order_item_name=item["name"],
        order_item_photo=item["photo_file_id"],
    )

    await callback.message.answer(
        f"🥐 Tanlangan mahsulot: <b>{item['name']}</b>\n\n"
        "🔢 <b>Nechta zakaz qilmoqchisiz?</b>\n"
        "<i>(Miqdorini yozib yuboring, masalan: 2 ta, 5 karobka, 3 kg va hokazo)</i>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(ClientOrderStates.waiting_for_catalog_quantity, F.text)
async def client_send_catalog_quantity(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    user_id = message.from_user.id
    client = get_client(user_id)
    if not client:
        await state.clear()
        await cmd_start(message, state)
        return

    quantity = message.text.strip()
    data = await state.get_data()
    item_id = data["order_item_id"]
    item_name = data["order_item_name"]
    item_photo = data["order_item_photo"]

    await state.clear()

    order_id = create_catalog_order(user_id, item_id, item_name, quantity)
    tashkent_time = now_tashkent_str()

    custom_title = (
        f"🏷 <b>Mijoz (Admin nomi):</b> <b>{client['custom_name']}</b>\n"
        if client.get("custom_name")
        else "🏷 <b>Mijoz (Admin nomi):</b> <i>(Hali nom berilmagan)</i>\n"
    )
    map_link = ""
    if client.get("latitude") and client.get("longitude"):
        map_link = (
            f" (<a href='https://maps.google.com/?q={client['latitude']},{client['longitude']}'>"
            "Xarita</a>)"
        )

    admin_text = (
        f"🛒 <b>KATALOGDAN YANGI ZAKAZ! (#{order_id})</b>\n\n"
        f"{custom_title}"
        f"👤 <b>Asl F.I.Sh:</b> {client['full_name']}\n"
        f"📞 <b>Telefon:</b> {client['phone']}\n"
        f"🏪 <b>Magazin manzili:</b> {client['address']}{map_link}\n"
        f"🕒 <b>Vaqt:</b> {tashkent_time}\n\n"
        f"🥐 <b>Pishiriq nomi:</b> <b>{item_name}</b>\n"
        f"🔢 <b>Zakaz miqdori:</b> <b>{quantity}</b>\n\n"
        "👇 <i>Ushbu zakazni tasdiqlaysizmi yoki skladda qolmaganmi?</i>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=item_photo,
                caption=admin_text,
                reply_markup=catalog_order_admin_kb(order_id, user_id),
            )
        except Exception as e:
            logging.warning(f"Adminga ({admin_id}) katalog zakazini yuborishda xatolik: {e}")

    await message.answer(
        f"✅ <b>Zakazingiz adminga yuborildi!</b>\n\n"
        f"🥐 <b>Mahsulot:</b> {item_name}\n"
        f"🔢 <b>Miqdori:</b> {quantity}\n"
        f"🕒 <b>Vaqt:</b> {tashkent_time}\n\n"
        "⏳ <i>Admin zakazni ko'rib chiqib, hozir sizga tasdiqlash xabarini yuboradi!</i>",
        reply_markup=client_main_kb(user_id),
    )


# --- ADMIN KATALOG ZAKAZINI TASDIQLASHI YOKI RAD ETISHI ---
@router.callback_query(F.data.startswith("cat_ord_ok:"))
async def cb_catalog_order_approve(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])
    order = get_catalog_order(order_id)
    if not order:
        await callback.answer("Zakaz topilmadi!", show_alert=True)
        return

    update_catalog_order_status(order_id, "approved")
    client = get_client(order["client_id"])
    client_name = format_client_display_name(client) if client else str(order["client_id"])

    try:
        await bot.send_message(
            chat_id=order["client_id"],
            text=(
                "🥐 <b>Skazka — Zakazingiz tasdiqlandi!</b> ✅\n\n"
                f"📦 <b>Mahsulot:</b> {order['item_name']}\n"
                f"🔢 <b>Miqdori:</b> {order['quantity']}\n"
                f"🕒 <b>Tasdiqlangan vaqt:</b> {now_tashkent_str()}\n\n"
                "🚚 <i>Zakazingiz ro'yxatga qo'shildi, tez orada yetkazib beramiz. Xaridingiz uchun rahmat!</i>"
            ),
            reply_markup=client_main_kb(order["client_id"]),
        )
    except Exception as e:
        await callback.answer(f"Mijozga xabar bormadi: {e}", show_alert=True)
        return

    old_caption = callback.message.caption or ""
    new_caption = (
        old_caption.split("👇")[0].strip()
        + f"\n\n✅ <b>TASDIQLANDI (Yetkazib beriladi)</b> — {now_tashkent_str()}"
    )
    await callback.message.edit_caption(caption=new_caption, reply_markup=None)
    await callback.answer(f"✅ {client_name} zakazi tasdiqlandi!", show_alert=True)


@router.callback_query(F.data.startswith("cat_ord_no:"))
async def cb_catalog_order_reject(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])
    order = get_catalog_order(order_id)
    if not order:
        await callback.answer("Zakaz topilmadi!", show_alert=True)
        return

    update_catalog_order_status(order_id, "rejected")
    client = get_client(order["client_id"])
    client_name = format_client_display_name(client) if client else str(order["client_id"])

    try:
        await bot.send_message(
            chat_id=order["client_id"],
            text=(
                "🥐 <b>Skazka — Zakaz bo'yicha ma'lumot:</b>\n\n"
                f"📦 <b>Mahsulot:</b> {order['item_name']}\n"
                f"🔢 <b>So'ralgan miqdor:</b> {order['quantity']}\n\n"
                "❌ <b>Afsuski, hozirda bu mahsulotimiz skladimizda qolmabdi.</b>\n"
                "📖 <i>Iltimos, katalogimizdan boshqa pishiriqlarimizni ko'rib chiqing!</i>"
            ),
            reply_markup=client_main_kb(order["client_id"]),
        )
    except Exception as e:
        await callback.answer(f"Mijozga xabar bormadi: {e}", show_alert=True)
        return

    old_caption = callback.message.caption or ""
    new_caption = (
        old_caption.split("👇")[0].strip()
        + f"\n\n❌ <b>RAD ETILDI (Skladda qolmagan)</b> — {now_tashkent_str()}"
    )
    await callback.message.edit_caption(caption=new_caption, reply_markup=None)
    await callback.answer(
        f"❌ {client_name} ga skladda qolmagani haqida xabar yuborildi!",
        show_alert=True,
    )


# ==============================================================================
# 7. ADMIN PANEL VA MIJOZLARNI BOSHQARISH (O'CHIRISH BILAN)
# ==============================================================================


@router.message(F.text == "📸 Zakaz / Foto-otchet yuborish")
async def prompt_for_order_photo(message: Message, state: FSMContext) -> None:
    client = get_client(message.from_user.id)
    if not client:
        await cmd_start(message, state)
        return

    await state.set_state(ClientOrderStates.waiting_for_order_photo)
    await message.answer(
        "📸 <b>Marhamat, zakaz yoki vitrina foto-otchet rasmini yuboring!</b>\n\n"
        "• Rasm ostiga (izohga) kerakli pishiriqlar soni yoki qo'shimcha zakazingizni yozishingiz mumkin.\n"
        "• Yuborgan rasmingiz darhol <b>Skazka</b> adminiga yetib boradi.\n"
        "• Agar pishiriqlarimizni katalogdan tanlab zakaz qilmoqchi bo'lsangiz, <b>«📖 Katalog (Mahsulotlar)»</b> tugmasini bosing.",
        reply_markup=client_main_kb(message.from_user.id),
    )


@router.message(Command("admin"))
@router.message(F.text == "🔐 Admin Panel")
async def open_admin_panel(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda admin huquqi yo'q.")
        return

    await state.clear()
    clients_count = len(get_all_clients())
    catalog_count = len(get_catalog_items())
    await message.answer(
        "🔐 <b>Skazka — Admin Paneli</b>\n\n"
        f"👥 Ro'yxatdan o'tgan mijozlar: <b>{clients_count} ta</b>\n"
        f"🥐 Katalogdagi mahsulotlar: <b>{catalog_count} ta</b>\n"
        f"🕒 Toshkent vaqti: <b>{now_tashkent_str()}</b>\n\n"
        "Kerakli bo'limni tanlang:",
        reply_markup=admin_main_kb(),
    )


@router.message(F.text == "⬅️ Mijoz menyusiga qaytish")
async def back_to_client_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Mijoz menyusiga qaytdingiz.",
        reply_markup=client_main_kb(message.from_user.id),
    )


@router.message(F.text == "👥 Mijozlar ro'yxati")
async def admin_list_clients(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    clients = get_all_clients()
    if not clients:
        await message.answer("📭 Hozircha ro'yxatdan o'tgan mijozlar yo'q.")
        return

    await message.answer(
        f"👥 <b>Barcha mijozlar ro'yxati ({len(clients)} ta):</b>\n"
        "Mijozning to'liq ma'lumotini ko'rish, unga <b>maxsus nom berish</b> yoki <b>o'chirib tashlash</b> uchun ustiga bosing:",
        reply_markup=build_clients_inline_kb(clients),
    )


@router.callback_query(F.data == "clients_list_back")
async def cb_clients_list_back(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    clients = get_all_clients()
    if not clients:
        await callback.message.edit_text("📭 Hozircha ro'yxatdan o'tgan mijozlar yo'q.")
        await callback.answer()
        return

    await callback.message.edit_text(
        f"👥 <b>Barcha mijozlar ro'yxati ({len(clients)} ta):</b>\n"
        "Mijozning to'liq ma'lumotini ko'rish, nomini o'zgartirish yoki o'chirish uchun tanlang:",
        reply_markup=build_clients_inline_kb(clients),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("client_view:"))
async def cb_client_view(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    client_id = int(callback.data.split(":")[1])
    client = get_client(client_id)
    if not client:
        await callback.answer("Mijoz topilmadi!", show_alert=True)
        return

    custom_label = (
        client["custom_name"]
        if client.get("custom_name")
        else "<i>(Hali maxsus nom berilmagan)</i>"
    )
    map_link = ""
    if client.get("latitude") and client.get("longitude"):
        map_link = (
            f"\n🗺 <a href='https://maps.google.com/?q={client['latitude']},{client['longitude']}'>"
            "Google Xaritada ochish</a>"
        )

    text = (
        "🏪 <b>Mijoz kartochkasi:</b>\n\n"
        f"🏷 <b>Admin qo'ygan nom:</b> {custom_label}\n"
        f"👤 <b>Asl F.I.Sh:</b> {client['full_name']}\n"
        f"📞 <b>Telefon:</b> {client['phone']}\n"
        f"📍 <b>Magazin manzili:</b> {client['address']}{map_link}\n"
        f"🔗 <b>Username:</b> @{client['username'] if client.get('username') else 'yo`q'}\n"
        f"🕒 <b>Ro'yxatdan o'tgan:</b> {client['registered_at']}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=client_detail_inline_kb(client_id),
        disable_web_page_preview=True,
    )
    await callback.answer()


# --- MIJOZNI O'CHIRISH HANDLERLARI ---
@router.callback_query(F.data.startswith("client_delete_ask:"))
async def cb_client_delete_ask(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    client_id = int(callback.data.split(":")[1])
    client = get_client(client_id)
    if not client:
        await callback.answer("Mijoz topilmadi!", show_alert=True)
        return

    confirm_del_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Ha, butunlay o'chirilsin!",
                    callback_data=f"client_delete_confirm:{client_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Yo'q, orqaga qaytish",
                    callback_data=f"client_view:{client_id}",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        f"⚠️ <b>DIQQAT!</b>\n\n"
        f"Siz haqiqatan ham <b>{format_client_display_name(client)}</b> ({client['phone']}) ni "
        f"mijozlar bazasidan o'chirib tashlamoqchimisiz?",
        reply_markup=confirm_del_kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("client_delete_confirm:"))
async def cb_client_delete_confirm(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    client_id = int(callback.data.split(":")[1])
    client = get_client(client_id)
    client_name = format_client_display_name(client) if client else str(client_id)

    delete_client(client_id)
    await callback.answer(f"🗑 {client_name} bazadan o'chirildi!", show_alert=True)

    clients = get_all_clients()
    if not clients:
        await callback.message.edit_text("📭 Barcha mijozlar o'chirildi. Ro'yxat bo'sh.")
        return

    await callback.message.edit_text(
        f"✅ <b>{client_name}</b> bazadan o'chirildi.\n\n"
        f"👥 <b>Qolgan mijozlar ro'yxati ({len(clients)} ta):</b>",
        reply_markup=build_clients_inline_kb(clients),
    )


@router.callback_query(F.data.startswith("client_rename:"))
async def cb_client_rename_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    client_id = int(callback.data.split(":")[1])
    client = get_client(client_id)
    if not client:
        await callback.answer("Mijoz topilmadi!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_new_client_name)
    await state.update_data(target_client_id=client_id)

    await callback.message.answer(
        f"✏️ <b>{client['full_name']}</b> ({client['address']}) uchun yangi nom kiriting:\n"
        "<i>(Masalan: Yugnakiy Alisher)</i>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_new_client_name, F.text)
async def admin_save_client_new_name(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    new_name = message.text.strip()
    data = await state.get_data()
    client_id = data.get("target_client_id")
    update_client_custom_name(client_id, new_name)
    client = get_client(client_id)
    await state.clear()

    await message.answer(
        f"✅ Mijoz nomi muvaffaqiyatli o'zgartirildi!\n\n"
        f"🏷 <b>Yangi nom:</b> {client['custom_name']}\n"
        f"👤 <b>Asl F.I.Sh:</b> {client['full_name']}\n"
        f"📞 <b>Telefon:</b> {client['phone']}\n"
        f"🏪 <b>Manzil:</b> {client['address']}",
        reply_markup=admin_main_kb(),
    )


@router.message(F.text == "📢 Zakaz / Foto-otchet so'rash (Hammaga)")
async def admin_broadcast_order_request_prompt(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    clients = get_all_clients()
    if not clients:
        await message.answer("📭 Xabar yuborish uchun hali mijozlar ro'yxatdan o'tmagan.")
        return

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Ha, barcha {len(clients)} ta mijozga yuborilsin!",
                    callback_data="broadcast_foto_otchet_now",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="broadcast_cancel",
                )
            ],
        ]
    )

    preview_msg = (
        "🥐 <b>Skazka</b>\n"
        "Assalomu alaykum, hurmatli mijoz!\n"
        "📸 <b>Iltimos, bugungi zakazingizni va foto-otchetni tashlab yuboring!</b>\n\n"
        "<i>Mahsulotlarimiz turlarini ko'rish va zakaz berish uchun «📖 Katalog (Mahsulotlar)» tugmasidan foydalanishingiz mumkin.</i>"
    )

    await message.answer(
        f"📢 <b>Barcha ({len(clients)} ta) mijozga quyidagi so'rov yuboriladi:</b>\n\n"
        f"{preview_msg}\n\n"
        "Tasdiqlaysizmi?",
        reply_markup=confirm_kb,
    )


@router.callback_query(F.data == "broadcast_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("❌ Xabar yuborish bekor qilindi.")
    await callback.answer()


@router.callback_query(F.data == "broadcast_foto_otchet_now")
async def cb_broadcast_foto_otchet_now(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    clients = get_all_clients()
    await callback.message.edit_text(
        f"⏳ Xabarlar {len(clients)} ta mijozga yuborilmoqda..."
    )

    broadcast_text = (
        "🥐 <b>Skazka</b>\n"
        "Assalomu alaykum, hurmatli hamkor!\n\n"
        "📸 <b>Zakaz tashlab yuboring (foto-otchet)!</b>\n"
        "Vitrina yoki qoldiq rasmini shu yerga tashlang, yoki <b>«📖 Katalog (Mahsulotlar)»</b> bo'limiga kirib pishiriqlarni tanlab zakaz bering."
    )

    sent_count = 0
    failed_count = 0
    for c in clients:
        try:
            await bot.send_message(
                chat_id=c["user_id"],
                text=broadcast_text,
                reply_markup=client_main_kb(c["user_id"]),
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed_count += 1
            logging.warning(f"Mijozga ({c['user_id']}) xabar bormadi: {e}")

    await callback.message.edit_text(
        "✅ <b>«Zakaz / Foto-otchet» so'rovi barcha mijozlarga yuborildi!</b>\n\n"
        f"📨 Yetkazildi: <b>{sent_count} ta</b> mijozga\n"
        f"⚠️ Yetib bormadi (bloklagan): <b>{failed_count} ta</b>\n"
        f"🕒 Yuborilgan vaqt (Toshkent): <b>{now_tashkent_str()}</b>"
    )
    await callback.answer("Hammaga yuborildi!")


@router.callback_query(F.data.startswith("client_remind:"))
async def cb_single_client_remind(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        return

    client_id = int(callback.data.split(":")[1])
    client = get_client(client_id)
    if not client:
        await callback.answer("Mijoz topilmadi!", show_alert=True)
        return

    try:
        await bot.send_message(
            chat_id=client_id,
            text=(
                "🥐 <b>Skazka</b>\n"
                "Assalomu alaykum! 📸 <b>Iltimos, zakaz va foto-otchetni tashlab yuboring yoki Katalog orqali zakaz bering!</b>"
            ),
            reply_markup=client_main_kb(client_id),
        )
        await callback.answer(
            f"✅ {format_client_display_name(client)} ga eslatma yuborildi!",
            show_alert=True,
        )
    except Exception as e:
        await callback.answer(f"Xatolik: {e}", show_alert=True)


@router.message(F.text == "✍️ Maxsus xabar yuborish")
async def admin_custom_broadcast_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.set_state(AdminStates.waiting_for_custom_broadcast)
    await message.answer(
        "✍️ Barcha mijozlarga yubormoqchi bo'lgan xabaringizni (matn yoki rasm+izoh) yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(AdminStates.waiting_for_custom_broadcast)
async def admin_custom_broadcast_send(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if not is_admin(message.from_user.id):
        return

    clients = get_all_clients()
    await state.clear()

    sent_count = 0
    for c in clients:
        try:
            await message.copy_to(
                chat_id=c["user_id"],
                reply_markup=client_main_kb(c["user_id"]),
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(
        f"✅ Maxsus xabaringiz <b>{sent_count} ta</b> mijozga yetkazildi!\n"
        f"🕒 Toshkent vaqti: <b>{now_tashkent_str()}</b>",
        reply_markup=admin_main_kb(),
    )


# ==============================================================================
# 8. ADMIN KATALOG BOSHQARUVI (NOM KIRITISH VA O'ZGARTIRISH BILAN)
# ==============================================================================


@router.message(F.text == "➕ Katalogga mahsulot qo'shish")
async def admin_add_catalog_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.set_state(AdminStates.waiting_for_catalog_photo)
    await message.answer(
        "📸 <b>1-qadam: Yangi mahsulotning (pishiriqning) rasmini yuboring:</b>",
        reply_markup=cancel_kb(),
    )


@router.message(AdminStates.waiting_for_catalog_photo, F.photo)
async def admin_add_catalog_photo(message: Message, state: FSMContext) -> None:
    photo_file_id = message.photo[-1].file_id
    await state.update_data(catalog_photo_id=photo_file_id)

    await state.set_state(AdminStates.waiting_for_catalog_name)
    await message.answer(
        "✍️ <b>2-qadam: Endi ushbu pishiriqning nomini (xohlasangiz narxi bilan) yozib yuboring:</b>\n"
        "<i>(Masalan: Praga torti — 85 000 so'm)</i>",
        reply_markup=cancel_kb(),
    )


@router.message(AdminStates.waiting_for_catalog_photo)
async def admin_add_catalog_photo_invalid(message: Message) -> None:
    await message.answer("⚠️ Iltimos, mahsulotning rasmini (Photo) yuboring:")


@router.message(AdminStates.waiting_for_catalog_name, F.text)
async def admin_add_catalog_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo_file_id = data["catalog_photo_id"]
    prod_name = message.text.strip()

    add_catalog_item(prod_name, photo_file_id)
    await state.clear()

    await message.answer(
        f"✅ <b>«{prod_name}»</b> katalogga muvaffaqiyatli qo'shildi!\n"
        "Mijozlar endi katalog orqali bu mahsulotga zakaz bera oladilar.",
        reply_markup=admin_main_kb(),
    )


@router.message(F.text == "🗂 Katalogni boshqarish")
async def admin_manage_catalog(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    items = get_catalog_items()
    if not items:
        await message.answer(
            "📭 Katalog bo'sh. <b>«➕ Katalogga mahsulot qo'shish»</b> tugmasi orqali mahsulot kiriting."
        )
        return

    await message.answer(f"🗂 <b>Katalogdagi mahsulotlar ({len(items)} ta):</b>")
    for item in items:
        manage_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Nomini o'zgartirish",
                        callback_data=f"catalog_edit:{item['id']}",
                    ),
                    InlineKeyboardButton(
                        text="🗑 O'chirish",
                        callback_data=f"catalog_del:{item['id']}",
                    ),
                ]
            ]
        )
        await message.answer_photo(
            photo=item["photo_file_id"],
            caption=f"🥐 <b>{item['name']}</b>\n🕒 Qo'shilgan: {item['created_at']}",
            reply_markup=manage_kb,
        )


@router.callback_query(F.data.startswith("catalog_edit:"))
async def cb_catalog_edit_name_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    item_id = int(callback.data.split(":")[1])
    item = get_catalog_item(item_id)
    if not item:
        await callback.answer("Mahsulot topilmadi!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_catalog_rename)
    await state.update_data(edit_catalog_id=item_id)

    await callback.message.answer(
        f"✏️ <b>«{item['name']}»</b> uchun yangi nom (va narx) yozib yuboring:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_catalog_rename, F.text)
async def admin_save_catalog_new_name(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    new_name = message.text.strip()
    data = await state.get_data()
    item_id = data.get("edit_catalog_id")
    update_catalog_item_name(item_id, new_name)
    await state.clear()

    await message.answer(
        f"✅ Mahsulot nomi <b>«{new_name}»</b> ga o'zgartirildi!",
        reply_markup=admin_main_kb(),
    )


@router.callback_query(F.data.startswith("catalog_del:"))
async def cb_catalog_delete(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    item_id = int(callback.data.split(":")[1])
    delete_catalog_item(item_id)
    await callback.message.delete()
    await callback.answer("🗑 Mahsulot katalogdan o'chirildi!", show_alert=True)


# ==============================================================================
# 9. FOTO-OTCHET VA ODDIY YOZMA ZAKAZLAR
# ==============================================================================


@router.message(F.photo)
async def handle_client_photo_order(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    user_id = message.from_user.id
    client = get_client(user_id)
    if not client:
        await message.answer(
            "⚠️ Zakaz yuborishdan oldin iltimos ro'yxatdan o'ting!"
        )
        await cmd_start(message, state)
        return

    await state.clear()
    photo_file_id = message.photo[-1].file_id
    caption_text = message.caption if message.caption else "(Izoh yozilmagan)"
    tashkent_time = now_tashkent_str()

    log_order(user_id, photo_file_id, message.caption)

    custom_title = (
        f"🏷 <b>Mijoz (Admin nomi):</b> <b>{client['custom_name']}</b>\n"
        if client.get("custom_name")
        else "🏷 <b>Mijoz (Admin nomi):</b> <i>(Hali nom berilmagan)</i>\n"
    )
    map_link = ""
    if client.get("latitude") and client.get("longitude"):
        map_link = (
            f" (<a href='https://maps.google.com/?q={client['latitude']},{client['longitude']}'>"
            "Xarita</a>)"
        )

    admin_caption = (
        "📥 <b>YANGI ZAKAZ / FOTO-OTCHET KELDI!</b>\n\n"
        f"{custom_title}"
        f"👤 <b>Asl F.I.Sh:</b> {client['full_name']}\n"
        f"📞 <b>Telefon:</b> {client['phone']}\n"
        f"🏪 <b>Magazin manzili:</b> {client['address']}{map_link}\n"
        f"🕒 <b>Toshkent vaqti:</b> {tashkent_time}\n"
        f"📝 <b>Mijoz izohi:</b> {caption_text}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=photo_file_id,
                caption=admin_caption,
                reply_markup=order_admin_actions_kb(user_id),
            )
        except Exception as e:
            logging.warning(f"Adminga ({admin_id}) foto-otchet yuborishda xatolik: {e}")

    await message.answer(
        "✅ <b>Rasmingiz (foto-otchet / zakaz) qabul qilindi va adminga yuborildi!</b>\n"
        f"🕒 Qabul qilingan vaqt: <b>{tashkent_time}</b>\n\n"
        "Rasmga qarab zakazingizni yozib olamiz. Rahmat!",
        reply_markup=client_main_kb(user_id),
    )


@router.callback_query(F.data.startswith("order_confirm:"))
async def cb_order_confirm_to_client(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    client_id = int(callback.data.split(":")[1])
    client = get_client(client_id)
    if not client:
        await callback.answer("Mijoz topilmadi!", show_alert=True)
        return

    try:
        await bot.send_message(
            chat_id=client_id,
            text=(
                "🥐 <b>Skazka:</b>\n"
                "✅ <b>Sizning foto-otchet va zakazingiz ko'rib chiqildi hamda ro'yxatga yozib olindi!</b>\n"
                f"🕒 Tasdiqlangan vaqt: <b>{now_tashkent_str()}</b>"
            ),
        )
        await callback.answer(
            f"✅ {format_client_display_name(client)} ga tasdiqnoma yuborildi!",
            show_alert=True,
        )
    except Exception as e:
        await callback.answer(f"Xatolik: {e}", show_alert=True)


@router.message(F.text)
async def handle_client_text_order(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    user_id = message.from_user.id
    client = get_client(user_id)
    if not client:
        await cmd_start(message, state)
        return

    tashkent_time = now_tashkent_str()
    log_order(user_id, None, message.text)

    custom_title = (
        client["custom_name"] if client.get("custom_name") else client["full_name"]
    )
    admin_msg = (
        "💬 <b>MIJOZDAN XABAR / YOZMA ZAKAZ:</b>\n\n"
        f"🏷 <b>Mijoz:</b> <b>{custom_title}</b> ({client['full_name']})\n"
        f"📞 <b>Telefon:</b> {client['phone']}\n"
        f"🏪 <b>Manzil:</b> {client['address']}\n"
        f"🕒 <b>Toshkent vaqti:</b> {tashkent_time}\n\n"
        f"📝 <b>Xabar:</b>\n{message.text}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=admin_msg,
                reply_markup=order_admin_actions_kb(user_id),
            )
        except Exception as e:
            logging.warning(f"Adminga matn yuborishda xatolik: {e}")

    await message.answer(
        "✅ Xabaringiz adminga yetkazildi! Agar katalogdan zakaz bermoqchi bo'lsangiz «📖 Katalog (Mahsulotlar)» bo'limiga o'ting.",
        reply_markup=client_main_kb(user_id),
    )


# ==============================================================================
# 10. WEBHOOK VA ASOSIY ISHGA TUSHIRISH (MAIN)
# ==============================================================================


async def on_startup(bot: Bot) -> None:
    if WEBHOOK_HOST:
        await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
        logging.info(f"Webhook o'rnatildi: {WEBHOOK_URL}")
    else:
        logging.warning("RENDER_EXTERNAL_URL topilmadi!")


async def handle_ping(request):
    return web.Response(text="Skazka Bot is alive and running!")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    dp.startup.register(on_startup)

    app = web.Application()
    app.router.add_get("/", handle_ping)

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logging.info(
        f"Skazka Webhook Bot port {PORT} da ishga tushdi! Toshkent vaqti: {now_tashkent_str()}"
    )

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
