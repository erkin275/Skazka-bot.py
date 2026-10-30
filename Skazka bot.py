#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SKAZKA — Pishiriqlar tarqatish (distribyutorlik) uchun maxsus Telegram Bot.
Kutubxona: aiogram 3.x (pip install aiogram tzdata)
Vaqt mintaqasi: Asia/Tashkent (UTC+5)

Asosiy imkoniyatlar:
1. Mijozlarni ro'yxatdan o'tkazish (Ism-familiya, Telefon raqam, Magazin manzili / lokatsiyasi).
2. Admin panel orqali barcha mijozlarni ko'rish va har bir mijozga maxsus nom berish
   (masalan: "Alisherov Alisher" -> "Yugnakiy Alisher").
3. Admin panelda bitta tugmani bosish orqali barcha mijozlarga "Zakaz va foto-otchet tashlab yuboring"
   xabarini yuborish (rassilka).
4. Katalog boshqaruvi: Admin mahsulot rasmi va nomini kiritib katalog yaratadi va tahrirlaydi;
   mijozlar istalgan vaqtda katalogni ko'ra oladi.
5. Mijoz yuborgan har qanday rasm (foto-otchet / zakaz) va xabar to'g'ridan-to'g'ri adminga
   mijozning admin qo'ygan nomi, telefoni, manzili va Toshkent vaqti bilan yetib boradi.
"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

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
)

# ==============================================================================
# 1. SOZLAMALAR (CONFIG) VA TOSHKENT VAQTI
# ==============================================================================

# BotFather'dan olingan tokenni shu yerga yoki BOT_TOKEN muhit o'zgaruvchisiga yozing:
BOT_TOKEN = os.getenv("BOT_TOKEN", "9175930111:AAHlb7yC45nsjQE0aGmvS65ZrSiiFfnhwz4")

# Adminlarning Telegram ID raqamlari (o'zingizning Telegram ID raqamingizni kiriting):
# Telegram ID ni bilish uchun botga /myid buyrug'ini yuborishingiz mumkin.
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "6766950408").split(",")
    if x.strip().isdigit()
]

DB_PATH = os.getenv("SKAZKA_DB_PATH", "skazka_bot.db")
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")


def now_tashkent_str() -> str:
    """Joriy Toshkent vaqtini 'KK.OO.YYYY SS:DD:SS' formatida qaytaradi."""
    return datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M:%S")


def is_admin(user_id: int) -> bool:
    """Foydalanuvchi admin ekanligini tekshiradi."""
    return user_id in ADMIN_IDS


# ==============================================================================
# 2. MA'LUMOTLAR BAZASI (SQLite)
# ==============================================================================


def init_db() -> None:
    """SQLite bazasini va jadvallarni yaratadi."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # Mijozlar jadvali
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
        # Katalog (mahsulotlar) jadvali
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
        # Yuborilgan foto-otchet / zakazlar tarixi
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
        conn.commit()


def get_client(user_id: int) -> dict | None:
    """Mijoz ma'lumotlarini user_id bo'yicha olish."""
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
    """Yangi mijozni ro'yxatdan o'tkazish yoki ma'lumotini yangilash."""
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
    """Admin tomonidan mijozga maxsus nom (masalan: 'Yugnakiy Alisher') berish."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "UPDATE clients SET custom_name = ? WHERE user_id = ?",
            (custom_name.strip(), user_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_all_clients() -> list[dict]:
    """Barcha ro'yxatdan o'tgan mijozlar ro'yxatini qaytaradi."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM clients ORDER BY COALESCE(custom_name, full_name) ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def add_catalog_item(name: str, photo_file_id: str) -> int:
    """Katalogga yangi pishiriq / mahsulot qo'shish."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO catalog (name, photo_file_id, created_at) VALUES (?, ?, ?)",
            (name.strip(), photo_file_id, now_tashkent_str()),
        )
        conn.commit()
        return cursor.lastrowid


def get_catalog_items() -> list[dict]:
    """Katalogdagi barcha mahsulotlarni olish."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM catalog ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def delete_catalog_item(item_id: int) -> bool:
    """Katalogdan mahsulotni o'chirish."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM catalog WHERE id = ?", (item_id,))
        conn.commit()
        return cursor.rowcount > 0


def log_order(client_id: int, photo_file_id: str | None, caption: str | None) -> None:
    """Mijoz yuborgan zakaz / foto-otchetni bazada qayd etish."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO orders_log (client_id, photo_file_id, caption, created_at) VALUES (?, ?, ?, ?)",
            (client_id, photo_file_id, caption, now_tashkent_str()),
        )
        conn.commit()


def format_client_display_name(client: dict) -> str:
    """Mijozning admin qo'ygan nomini yoki asl ism-familiyasini ko'rsatadi."""
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


class AdminStates(StatesGroup):
    waiting_for_new_client_name = State()
    waiting_for_catalog_photo = State()
    waiting_for_catalog_name = State()
    waiting_for_custom_broadcast = State()


# ==============================================================================
# 4. KLAVIATURALAR (KEYBOARDS)
# ==============================================================================


def client_main_kb(user_id: int) -> ReplyKeyboardMarkup:
    """Mijozning asosiy menyusi (agar admin bo'lsa, Admin panel tugmasi ham chiqadi)."""
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
    """Admin panelining asosiy tugmalari."""
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
    """Amalni bekor qilish tugmasi."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
    )


def phone_request_kb() -> ReplyKeyboardMarkup:
    """Telefon raqamni yuborish tugmasi."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_request_kb() -> ReplyKeyboardMarkup:
    """Lokatsiya yuborish yoki matn yozish uchun klaviatura."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Lokatsiya yuborish", request_location=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def build_clients_inline_kb(clients: list[dict]) -> InlineKeyboardMarkup:
    """Admin uchun mijozlar ro'yxati inline tugmalari."""
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
    """Admin mijoz profilini ochganda chiqadigan tugmalar."""
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
                    text="⬅️ Mijozlar ro'yxatiga qaytish",
                    callback_data="clients_list_back",
                )
            ],
        ]
    )


def order_admin_actions_kb(client_id: int) -> InlineKeyboardMarkup:
    """Adminga foto-otchet kelganda rasm tagida chiqadigan tezkor tugmalar."""
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


# ==============================================================================
# 5. ROUTER VA HANDLERLAR
# ==============================================================================

router = Router()


# --- Har qanday holatdan "❌ Bekor qilish" tugmasi ---
@router.message(F.text == "❌ Bekor qilish")
async def cancel_action_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    if is_admin(user_id):
        await message.answer(
            "Amal bekor qilindi.",
            reply_markup=admin_main_kb(),
        )
    else:
        await message.answer(
            "Amal bekor qilindi.",
            reply_markup=client_main_kb(user_id),
        )


# --- Telegram ID ni bilish uchun yordamchi buyruq ---
@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(
        f"Sizning Telegram ID raqamingiz: <code>{message.from_user.id}</code>\n"
        f"Toshkent vaqti: <b>{now_tashkent_str()}</b>"
    )


# --- /start va Ro'yxatdan o'tish jarayoni ---
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
            f"Zakaz berish uchun <b>«📸 Zakaz / Foto-otchet yuborish»</b> tugmasini bosing "
            f"yoki shunchaki rasm yuboring.\n"
            f"Mahsulotlarimizni ko'rish uchun <b>«📖 Katalog (Mahsulotlar)»</b> bo'limiga o'ting.",
            reply_markup=client_main_kb(user_id),
        )
        return

    # Agar yangi foydalanuvchi admin bo'lsa ham, xohlasa ro'yxatdan o'tadi yoki /admin ga kiradi
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
        "Endi istalgan vaqtda <b>«📸 Zakaz / Foto-otchet yuborish»</b> tugmasi orqali "
        "yoki botga to'g'ridan-to'g'ri rasm tashlab zakaz berishingiz mumkin!",
        reply_markup=client_main_kb(user.id),
    )

    # Adminlarga yangi mijoz haqida xabar berish va darhol nom berish imkonini taqdim etish
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


# ==============================================================================
# 6. MIJOZ MENYUSI VA KATALOG KO'RISH
# ==============================================================================


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


@router.message(F.text == "📖 Katalog (Mahsulotlar)")
async def client_view_catalog(message: Message) -> None:
    items = get_catalog_items()
    if not items:
        await message.answer(
            "📭 Hozircha katalogga mahsulotlar joylanmagan. Tez orada qo'shiladi!"
        )
        return

    await message.answer(
        f"🥐 <b>Skazka pishiriqlar katalogi ({len(items)} ta mahsulot):</b>"
    )
    for idx, item in enumerate(items, start=1):
        await message.answer_photo(
            photo=item["photo_file_id"],
            caption=f"🥐 <b>{idx}. {item['name']}</b>",
        )


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
        "• Agar pishiriqlarimiz turlarini ko'rmoqchi bo'lsangiz, <b>«📖 Katalog (Mahsulotlar)»</b> tugmasini bosing.",
        reply_markup=client_main_kb(message.from_user.id),
    )


# ==============================================================================
# 7. ADMIN PANEL VA BOSHQARUV
# ==============================================================================


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


# --- 7.1. Mijozlar ro'yxatini ko'rish va ularga maxsus nom berish ---
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
        "Mijozning to'liq ma'lumotini ko'rish yoki unga <b>maxsus nom berish</b> "
        "(masalan: <i>Yugnakiy Alisher</i>) uchun ustiga bosing:",
        reply_markup=build_clients_inline_kb(clients),
    )


@router.callback_query(F.data == "clients_list_back")
async def cb_clients_list_back(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    clients = get_all_clients()
    await callback.message.edit_text(
        f"👥 <b>Barcha mijozlar ro'yxati ({len(clients)} ta):</b>\n"
        "Mijozning to'liq ma'lumotini ko'rish yoki nomini o'zgartirish uchun tanlang:",
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


# --- 7.2. Bitta tugma orqali HAMMA mijozlarga "Zakaz / Foto-otchet tashlab yuboring" xabarini jo'natish ---
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
        "<i>Mahsulotlarimiz turlarini ko'rish uchun «📖 Katalog (Mahsulotlar)» tugmasidan foydalanishingiz mumkin.</i>"
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
        "Vitrina yoki qoldiq rasmini shu yerga tashlang, rasmga qarab zakazingizni yozib olamiz.\n\n"
        "📖 <i>Mahsulotlarimiz katalogini ko'rish uchun pastdagi «📖 Katalog (Mahsulotlar)» tugmasini bosing.</i>"
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
            await asyncio.sleep(0.05)  # Telegram flood limitdan himoya
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


# --- Bitta mijozning o'ziga eslatma yuborish ---
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
                "Assalomu alaykum! 📸 <b>Iltimos, zakaz va foto-otchetni tashlab yuboring!</b>"
            ),
            reply_markup=client_main_kb(client_id),
        )
        await callback.answer(
            f"✅ {format_client_display_name(client)} ga eslatma yuborildi!",
            show_alert=True,
        )
    except Exception as e:
        await callback.answer(f"Xatolik: {e}", show_alert=True)


# --- 7.3. Maxsus (ixtiyoriy matnli) xabar yuborish ---
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


# --- 7.4. Katalog boshqaruvi (Mahsulot rasmi + nomini qo'shish va o'chirish) ---
@router.message(F.text == "➕ Katalogga mahsulot qo'shish")
async def admin_add_catalog_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.set_state(AdminStates.waiting_for_catalog_photo)
    await message.answer(
        "📸 <b>Yangi mahsulotning (pishiriqning) rasmini yuboring:</b>",
        reply_markup=cancel_kb(),
    )


@router.message(AdminStates.waiting_for_catalog_photo, F.photo)
async def admin_add_catalog_photo(message: Message, state: FSMContext) -> None:
    photo_file_id = message.photo[-1].file_id
    await state.update_data(catalog_photo_id=photo_file_id)

    # Agar admin rasm bilan birga caption (izoh) ham yozib yuborgan bo'lsa, darhol saqlaymiz
    if message.caption and message.caption.strip():
        prod_name = message.caption.strip()
        add_catalog_item(prod_name, photo_file_id)
        await state.clear()
        await message.answer(
            f"✅ <b>«{prod_name}»</b> katalogga muvaffaqiyatli qo'shildi!",
            reply_markup=admin_main_kb(),
        )
        return

    await state.set_state(AdminStates.waiting_for_catalog_name)
    await message.answer(
        "✍️ Endi ushbu mahsulotning <b>nomini</b> (xohlasangiz narxi yoki tavsifi bilan) yozib yuboring:\n"
        "<i>(Masalan: Napoleon pishirig'i — 45 000 so'm)</i>",
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
        f"✅ <b>«{prod_name}»</b> katalogga muvaffaqiyatli qo'shildi!",
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
        del_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🗑 O'chirish ({item['name'][:20]})",
                        callback_data=f"catalog_del:{item['id']}",
                    )
                ]
            ]
        )
        await message.answer_photo(
            photo=item["photo_file_id"],
            caption=f"🥐 <b>{item['name']}</b>\n🕒 Qo'shilgan: {item['created_at']}",
            reply_markup=del_kb,
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
# 8. MIJOZDAN KELGAN RASM (FOTO-OTCHET / ZAKAZ) VA XABARLARNI ADMINGA YETKAZISH
# ==============================================================================


@router.message(F.photo)
async def handle_client_photo_order(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    """
    Mijoz botga rasm yuborganda (zakaz / foto-otchet), rasm to'g'ridan-to'g'ri
    barcha adminlarga mijozning admin qo'ygan nomi, telefoni, manzili va Toshkent vaqti bilan boradi.
    """
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


# Agar mijoz matn ko'rinishida zakaz yozsa ham adminga yetkazish:
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
        "✅ Xabaringiz adminga yetkazildi! Agar foto-otchet yubormoqchi bo'lsangiz, rasmni shu yerga tashlang.",
        reply_markup=client_main_kb(user_id),
    )


# ==============================================================================
# 9. BOTNI ISHGA TUSHIRISH (MAIN)
# ==============================================================================


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    init_db()

    if BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        logging.error(
            "DIQQAT! BOT_TOKEN kiritilmagan. Fayl ichidagi BOT_TOKEN o'zgaruvchisiga "
            "BotFather'dan olgan tokeningizni yozing va ADMIN_IDS ga Telegram ID raqamingizni kiriting."
        )
        return

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logging.info(f"Skazka Bot ishga tushdi! Toshkent vaqti: {now_tashkent_str()}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
