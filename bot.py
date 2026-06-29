import logging
import sqlite3
import os
import threading
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncio

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не задан TELEGRAM_TOKEN в переменных окружения!")

ADMIN_ID = 6499184401  # ваш ID (можно тоже вынести в переменную)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

DB_PATH = "bot_database.db"

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            roles TEXT,
            types TEXT,
            age_group TEXT,
            preferred_age_groups TEXT,
            gender TEXT,
            preferred_gender TEXT,
            is_searching INTEGER DEFAULT 0,
            is_chatting INTEGER DEFAULT 0,
            partner_id INTEGER DEFAULT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT roles, types, age_group, preferred_age_groups, gender, preferred_gender, is_searching, is_chatting, partner_id FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "roles": row[0].split(",") if row[0] else [],
            "types": row[1].split(",") if row[1] else [],
            "age_group": row[2],
            "preferred_age_groups": row[3].split(",") if row[3] else [],
            "gender": row[4],
            "preferred_gender": row[5].split(",") if row[5] else [],
            "is_searching": bool(row[6]),
            "is_chatting": bool(row[7]),
            "partner_id": row[8]
        }
    return None

def create_or_update_user(user_id: int, roles: list, types: list, age_group: str, preferred_age_groups: list,
                          gender: str, preferred_gender: list):
    roles_str = ",".join(roles)
    types_str = ",".join(types)
    pref_age_str = ",".join(preferred_age_groups)
    pref_gender_str = ",".join(preferred_gender)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO users (user_id, roles, types, age_group, preferred_age_groups, gender, preferred_gender)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, roles_str, types_str, age_group, pref_age_str, gender, pref_gender_str))
    conn.commit()
    conn.close()

def update_user(user_id: int, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    set_clause = ", ".join([f"{k}=?" for k in kwargs.keys()])
    values = list(kwargs.values()) + [user_id]
    cur.execute(f"UPDATE users SET {set_clause} WHERE user_id=?", values)
    conn.commit()
    conn.close()

# ========== ОЧЕРЕДЬ ПОИСКА ==========
search_queue = []

# ========== FSM СОСТОЯНИЯ ==========
class ProfileForm(StatesGroup):
    role = State()
    type = State()
    age = State()
    preferred_age = State()
    gender = State()
    preferred_gender = State()

# ========== КЛАВИАТУРЫ ==========
def get_multi_choice_kb(options: dict, selected: list, prefix: str, back_callback: str = None, next_callback: str = "next_step"):
    builder = InlineKeyboardBuilder()
    for cb, text in options.items():
        is_selected = cb in selected
        display = f"✅ {text}" if is_selected else text
        callback_data = f"{prefix}_{cb}"
        builder.add(InlineKeyboardButton(text=display, callback_data=callback_data))
    builder.adjust(1)
    nav_buttons = []
    if back_callback:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback))
    nav_buttons.append(InlineKeyboardButton(text="Далее ▶️", callback_data=next_callback))
    builder.row(*nav_buttons)
    return builder.as_markup()

def get_age_kb(selected=None):
    builder = InlineKeyboardBuilder()
    ages = ["13-16", "16-20", "20+"]
    for age in ages:
        text = f"✅ {age}" if age == selected else age
        builder.add(InlineKeyboardButton(text=text, callback_data=f"age_{age}"))
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_type"),
        InlineKeyboardButton(text="Далее ▶️", callback_data="next_step")
    )
    return builder.as_markup()

def get_preferred_age_kb(selected: list):
    builder = InlineKeyboardBuilder()
    ages = ["13-16", "16-20", "20+"]
    for age in ages:
        is_selected = age in selected
        text = f"✅ {age}" if is_selected else age
        builder.add(InlineKeyboardButton(text=text, callback_data=f"pref_age_{age}"))
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_age"),
        InlineKeyboardButton(text="Далее ▶️", callback_data="next_step")
    )
    return builder.as_markup()

def get_gender_kb(selected=None):
    builder = InlineKeyboardBuilder()
    genders = [("male", "Мужской"), ("female", "Женский")]
    for val, label in genders:
        text = f"✅ {label}" if val == selected else label
        builder.add(InlineKeyboardButton(text=text, callback_data=f"gender_{val}"))
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_pref_age"),
        InlineKeyboardButton(text="Далее ▶️", callback_data="next_step")
    )
    return builder.as_markup()

def get_preferred_gender_kb(selected: list):
    builder = InlineKeyboardBuilder()
    options = {"male": "Мужской", "female": "Женский"}
    for val, label in options.items():
        is_selected = val in selected
        text = f"✅ {label}" if is_selected else label
        builder.add(InlineKeyboardButton(text=text, callback_data=f"pref_gender_{val}"))
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_gender"),
        InlineKeyboardButton(text="✅ Готово", callback_data="finish_profile")
    )
    return builder.as_markup()

def get_main_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🔍 Начать поиск", callback_data="start_search"))
    builder.add(InlineKeyboardButton(text="👤 Моя анкета", callback_data="show_profile"))
    return builder.as_markup()

def get_cancel_search_kb():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="❌ Отменить поиск", callback_data="cancel_search"))
    return builder.as_markup()

async def show_main_menu(message: types.Message):
    await message.answer("Главное меню:", reply_markup=get_main_menu_kb())

# ========== ФУНКЦИИ ПОИСКА ==========
def are_roles_compatible(roles1, roles2):
    return (("offer" in roles1 and "seek" in roles2) or
            ("seek" in roles1 and "offer" in roles2))

def are_types_compatible(types1, types2):
    return bool(set(types1) & set(types2))

def are_ages_compatible(age1, pref_ages1, age2, pref_ages2):
    return (age2 in pref_ages1) and (age1 in pref_ages2)

def are_genders_compatible(gender1, pref_gender1, gender2, pref_gender2):
    return (gender2 in pref_gender1) and (gender1 in pref_gender2)

async def try_match(user_id: int):
    user = get_user(user_id)
    if not user:
        return False
    for candidate_id in search_queue[:]:
        if candidate_id == user_id:
            continue
        cand = get_user(candidate_id)
        if not cand:
            search_queue.remove(candidate_id)
            continue
        if (are_roles_compatible(user["roles"], cand["roles"]) and
            are_types_compatible(user["types"], cand["types"]) and
            are_ages_compatible(user["age_group"], user["preferred_age_groups"],
                                cand["age_group"], cand["preferred_age_groups"]) and
            are_genders_compatible(user["gender"], user["preferred_gender"],
                                   cand["gender"], cand["preferred_gender"])):
            search_queue.remove(candidate_id)
            if user_id in search_queue:
                search_queue.remove(user_id)
            update_user(user_id, is_searching=0, is_chatting=1, partner_id=candidate_id)
            update_user(candidate_id, is_searching=0, is_chatting=1, partner_id=user_id)
            await bot.send_message(user_id, "✅ Вы соединены с анонимным собеседником! Чтобы завершить чат, нажмите /stop. Чтобы пропустить собеседника и найти нового, нажмите /next.")
            await bot.send_message(candidate_id, "✅ Вы соединены с анонимным собеседником! Чтобы завершить чат, нажмите /stop. Чтобы пропустить собеседника и найти нового, нажмите /next.")
            return True
    if user_id not in search_queue:
        search_queue.append(user_id)
        update_user(user_id, is_searching=1)
    return False

# ========== ХЕНДЛЕРЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await state.set_state(ProfileForm.role)
        await state.update_data(step=1, roles=[], types=[], age=None, preferred_age=[], gender=None, preferred_gender=[])
        await show_role_step(message, state)
    else:
        await show_main_menu(message)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = """
📌 *Доступные команды:*
/start — главное меню
/edit — заполнить или изменить анкету
/profile — посмотреть свою анкету
/stop — завершить чат или поиск
/next — пропустить собеседника и найти нового
/report — пожаловаться на собеседника

🔍 После заполнения анкеты нажмите «Начать поиск».
    """
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("❌ Вы ещё не заполнили анкету. Используйте команду /edit, чтобы создать анкету.")
        return
    roles_map = {"offer": "Предлагаю", "seek": "Ищу"}
    type_map = {"original": "Ориджинал", "fandom": "Фандом", "other": "Другое"}
    gender_map = {"male": "Мужской", "female": "Женский"}
    roles_str = ", ".join([roles_map.get(r, r) for r in user["roles"]])
    types_str = ", ".join([type_map.get(t, t) for t in user["types"]])
    pref_age_str = ", ".join(user["preferred_age_groups"])
    pref_gender_str = ", ".join([gender_map.get(g, g) for g in user["preferred_gender"]])
    text = f"""
👤 *Ваша анкета:*
Роли: {roles_str}
Типы: {types_str}
Ваш возраст: {user["age_group"]}
Ищете возраст: {pref_age_str}
Ваш пол: {gender_map.get(user["gender"], user["gender"])}
Предпочитаемый пол: {pref_gender_str}
    """
    await message.answer(text, parse_mode="Markdown")

# === ШАГ 1: РОЛИ ===
async def show_role_step(message: types.Message, state: FSMContext, edit=False):
    data = await state.get_data()
    roles = data.get("roles", [])
    options = {"offer": "Предлагаю", "seek": "Ищу"}
    kb = get_multi_choice_kb(options, roles, "role", back_callback=None, next_callback="next_step")
    text = "Выберите роли (можно несколько):"
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

@dp.callback_query(StateFilter(ProfileForm.role), lambda c: c.data.startswith("role_"))
async def process_role_toggle(callback: types.CallbackQuery, state: FSMContext):
    value = callback.data.split("_")[1]
    data = await state.get_data()
    roles = data.get("roles", [])
    old_roles = roles.copy()
    if value in roles:
        roles.remove(value)
    else:
        roles.append(value)
    if roles == old_roles:
        await callback.answer()
        return
    await state.update_data(roles=roles)
    await show_role_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.role), lambda c: c.data == "next_step")
async def role_next(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("roles"):
        await callback.answer("Выберите хотя бы одну роль!", show_alert=True)
        return
    await state.set_state(ProfileForm.type)
    await state.update_data(step=2)
    await show_type_step(callback.message, state, edit=True)
    await callback.answer()

# === ШАГ 2: ТИПЫ ===
async def show_type_step(message: types.Message, state: FSMContext, edit=False):
    data = await state.get_data()
    types = data.get("types", [])
    options = {"original": "Ориджинал", "fandom": "Фандом", "other": "Другое"}
    kb = get_multi_choice_kb(options, types, "type", back_callback="back_to_role", next_callback="next_step")
    text = "Выберите типы (можно несколько):"
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

@dp.callback_query(StateFilter(ProfileForm.type), lambda c: c.data.startswith("type_"))
async def process_type_toggle(callback: types.CallbackQuery, state: FSMContext):
    value = callback.data.split("_")[1]
    data = await state.get_data()
    types = data.get("types", [])
    old_types = types.copy()
    if value in types:
        types.remove(value)
    else:
        types.append(value)
    if types == old_types:
        await callback.answer()
        return
    await state.update_data(types=types)
    await show_type_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.type), lambda c: c.data == "next_step")
async def type_next(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("types"):
        await callback.answer("Выберите хотя бы один тип!", show_alert=True)
        return
    await state.set_state(ProfileForm.age)
    await state.update_data(step=3)
    await show_age_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.type), lambda c: c.data == "back_to_role")
async def back_to_role(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.role)
    await state.update_data(step=1)
    await show_role_step(callback.message, state, edit=True)
    await callback.answer()

# === ШАГ 3: СВОЙ ВОЗРАСТ ===
async def show_age_step(message: types.Message, state: FSMContext, edit=False):
    data = await state.get_data()
    age = data.get("age")
    kb = get_age_kb(age)
    text = "Выберите свой возраст:"
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

@dp.callback_query(StateFilter(ProfileForm.age), lambda c: c.data.startswith("age_"))
async def process_age(callback: types.CallbackQuery, state: FSMContext):
    age = callback.data.split("_")[1]
    data = await state.get_data()
    old_age = data.get("age")
    if age == old_age:
        await callback.answer()
        return
    await state.update_data(age=age)
    await show_age_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.age), lambda c: c.data == "next_step")
async def age_next(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("age"):
        await callback.answer("Выберите свой возраст!", show_alert=True)
        return
    await state.set_state(ProfileForm.preferred_age)
    await state.update_data(step=4)
    await show_preferred_age_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.age), lambda c: c.data == "back_to_type")
async def back_to_type_from_age(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.type)
    await state.update_data(step=2)
    await show_type_step(callback.message, state, edit=True)
    await callback.answer()

# === ШАГ 4: ВОЗРАСТ СОБЕСЕДНИКА ===
async def show_preferred_age_step(message: types.Message, state: FSMContext, edit=False):
    data = await state.get_data()
    pref_age = data.get("preferred_age", [])
    kb = get_preferred_age_kb(pref_age)
    text = "Выберите возраст собеседника (можно несколько):"
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

@dp.callback_query(StateFilter(ProfileForm.preferred_age), lambda c: c.data.startswith("pref_age_"))
async def process_pref_age_toggle(callback: types.CallbackQuery, state: FSMContext):
    age = callback.data.split("_")[2]
    data = await state.get_data()
    pref_age = data.get("preferred_age", [])
    old_pref_age = pref_age.copy()
    if age in pref_age:
        pref_age.remove(age)
    else:
        pref_age.append(age)
    if pref_age == old_pref_age:
        await callback.answer()
        return
    await state.update_data(preferred_age=pref_age)
    await show_preferred_age_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.preferred_age), lambda c: c.data == "next_step")
async def pref_age_next(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("preferred_age"):
        await callback.answer("Выберите хотя бы одну группу!", show_alert=True)
        return
    await state.set_state(ProfileForm.gender)
    await state.update_data(step=5)
    await show_gender_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.preferred_age), lambda c: c.data == "back_to_age")
async def back_to_age_from_pref(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.age)
    await state.update_data(step=3)
    await show_age_step(callback.message, state, edit=True)
    await callback.answer()

# === ШАГ 5: СВОЙ ПОЛ ===
async def show_gender_step(message: types.Message, state: FSMContext, edit=False):
    data = await state.get_data()
    gender = data.get("gender")
    kb = get_gender_kb(gender)
    text = "Выберите свой пол:"
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

@dp.callback_query(StateFilter(ProfileForm.gender), lambda c: c.data.startswith("gender_"))
async def process_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    data = await state.get_data()
    old_gender = data.get("gender")
    if gender == old_gender:
        await callback.answer()
        return
    await state.update_data(gender=gender)
    await show_gender_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.gender), lambda c: c.data == "next_step")
async def gender_next(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("gender"):
        await callback.answer("Выберите свой пол!", show_alert=True)
        return
    await state.set_state(ProfileForm.preferred_gender)
    await state.update_data(step=6)
    await show_preferred_gender_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.gender), lambda c: c.data == "back_to_pref_age")
async def back_to_pref_age_from_gender(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.preferred_age)
    await state.update_data(step=4)
    await show_preferred_age_step(callback.message, state, edit=True)
    await callback.answer()

# === ШАГ 6: ПРЕДПОЧИТАЕМЫЙ ПОЛ ===
async def show_preferred_gender_step(message: types.Message, state: FSMContext, edit=False):
    data = await state.get_data()
    pref = data.get("preferred_gender", [])
    kb = get_preferred_gender_kb(pref)
    text = "Выберите предпочитаемый пол (можно несколько):"
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

@dp.callback_query(StateFilter(ProfileForm.preferred_gender), lambda c: c.data.startswith("pref_gender_"))
async def process_pref_gender_toggle(callback: types.CallbackQuery, state: FSMContext):
    value = callback.data.split("_")[2]
    data = await state.get_data()
    pref = data.get("preferred_gender", [])
    old_pref = pref.copy()
    if value in pref:
        pref.remove(value)
    else:
        pref.append(value)
    if pref == old_pref:
        await callback.answer()
        return
    await state.update_data(preferred_gender=pref)
    await show_preferred_gender_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.preferred_gender), lambda c: c.data == "back_to_gender")
async def back_to_gender_from_pref(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.gender)
    await state.update_data(step=5)
    await show_gender_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(StateFilter(ProfileForm.preferred_gender), lambda c: c.data == "finish_profile")
async def finish_profile(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("roles") or not data.get("types") or not data.get("age") or not data.get("preferred_age") or not data.get("gender") or not data.get("preferred_gender"):
        await callback.answer("Заполните все поля!", show_alert=True)
        return
    user_id = callback.from_user.id
    create_or_update_user(
        user_id,
        data["roles"],
        data["types"],
        data["age"],
        data["preferred_age"],
        data["gender"],
        data["preferred_gender"]
    )
    await state.clear()
    await callback.message.edit_text("✅ Анкета сохранена!")
    await show_main_menu(callback.message)
    await callback.answer()

# ========== КОМАНДЫ ==========
@dp.message(Command("edit"))
async def cmd_edit(message: types.Message, state: FSMContext):
    await state.set_state(ProfileForm.role)
    await state.update_data(step=1, roles=[], types=[], age=None, preferred_age=[], gender=None, preferred_gender=[])
    await show_role_step(message, state)

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("Вы не в чате и не в поиске.")
        return
    if user["is_searching"]:
        if user_id in search_queue:
            search_queue.remove(user_id)
        update_user(user_id, is_searching=0)
        await message.answer("Поиск остановлен.")
        await show_main_menu(message)
        return
    if user["is_chatting"]:
        partner_id = user["partner_id"]
        update_user(user_id, is_chatting=0, partner_id=None)
        update_user(partner_id, is_chatting=0, partner_id=None)
        await bot.send_message(partner_id, "😔 Собеседник завершил чат. Возвращаю в главное меню.")
        await show_main_menu(await bot.send_message(partner_id, "Главное меню:"))
        await message.answer("Чат завершён.")
        await show_main_menu(message)
        return
    await message.answer("Вы не в чате и не в поиске.")

@dp.message(Command("next"))
async def cmd_next(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or not user["is_chatting"]:
        await message.answer("Вы не в чате.")
        return
    partner_id = user["partner_id"]
    update_user(user_id, is_chatting=0, partner_id=None)
    update_user(partner_id, is_chatting=0, partner_id=None)
    await bot.send_message(partner_id, "😔 Собеседник переключился на другого. Возвращаю в главное меню.")
    await show_main_menu(await bot.send_message(partner_id, "Главное меню:"))
    await try_match(user_id)
    user_after = get_user(user_id)
    if user_after["is_searching"]:
        await message.answer("🔎 Ищу нового собеседника...")
        await message.answer("Ожидание...", reply_markup=get_cancel_search_kb())
    else:
        await message.answer("Новый собеседник найден!")

@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or not user["is_chatting"]:
        await message.answer("Вы не в чате, на кого жаловаться?")
        return
    partner_id = user["partner_id"]
    await bot.send_message(ADMIN_ID, f"Жалоба от {user_id} на {partner_id}\nТекст: {message.text or 'без текста'}")
    await message.answer("Ваша жалоба отправлена администратору.")

# ========== КНОПКИ МЕНЮ ==========
@dp.callback_query(lambda c: c.data == "start_search")
async def start_search(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    if not user:
        await callback.answer("Сначала заполните анкету через /edit", show_alert=True)
        return
    if user["is_chatting"]:
        await callback.answer("Вы уже в чате. Используйте /next для смены собеседника или /stop для выхода.", show_alert=True)
        return
    if user["is_searching"]:
        await callback.answer("Вы уже ищете собеседника.", show_alert=True)
        return
    matched = await try_match(user_id)
    if not matched:
        await callback.message.edit_text("🔎 Идёт поиск собеседника... Нажмите «Отменить поиск», чтобы остановить.", reply_markup=get_cancel_search_kb())
    else:
        await callback.message.delete()
    await callback.answer()

@dp.callback_query(lambda c: c.data == "cancel_search")
async def cancel_search(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in search_queue:
        search_queue.remove(user_id)
    update_user(user_id, is_searching=0)
    await callback.message.edit_text("⏹ Поиск отменён.", reply_markup=None)
    await show_main_menu(callback.message)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "edit_profile")
async def edit_profile(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.role)
    await state.update_data(step=1, roles=[], types=[], age=None, preferred_age=[], gender=None, preferred_gender=[])
    await show_role_step(callback.message, state, edit=True)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "show_profile")
async def show_profile_callback(callback: types.CallbackQuery):
    await cmd_profile(callback.message)
    await callback.answer()

# ========== ПЕРЕСЫЛКА СООБЩЕНИЙ ==========
@dp.message(F.text | F.photo | F.video | F.voice | F.document | F.sticker)
async def forward_message(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or not user["is_chatting"]:
        await message.answer("Вы не в активном чате. Используйте /start или «Начать поиск».")
        return
    partner_id = user["partner_id"]
    try:
        await message.copy_to(chat_id=partner_id)
    except Exception as e:
        logging.error(f"Не удалось переслать сообщение: {e}")
        await message.answer("Произошла ошибка при отправке.")

# ========== УСТАНОВКА КОМАНД В МЕНЮ ==========
async def set_commands():
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="edit", description="Изменить анкету"),
        BotCommand(command="profile", description="Моя анкета"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="stop", description="Завершить чат или поиск"),
        BotCommand(command="next", description="Пропустить собеседника"),
        BotCommand(command="report", description="Пожаловаться"),
    ]
    await bot.set_my_commands(commands)

# ========== ЗАПУСК БОТА ==========
async def main():
    await set_commands()
    await dp.start_polling(bot)

# ========== FLASK-ЗАГЛУШКА ДЛЯ RENDER ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Бот работает!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    asyncio.run(main())
