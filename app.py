# app.py — @toweringsale
# aiogram 3.22+, Python 3.11+
# Важно: бот добавлен админом в канале @toweringsale

import asyncio
import logging
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message, CallbackQuery,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
 the aiogram.fsm.state import StatesGroup, State
from aiogram.enums import ParseMode, ChatType
from aiogram.utils.keyboard import InlineKeyboardBuilder

# =======================
# ---- НАСТРОЙКИ --------
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8052075709:AAGD7-tH2Yq7Ipixmw21y3D1B-oWWGrq03I")
OWNER_ID = int(os.getenv("OWNER_ID", "6089346880"))
CHANNEL = os.getenv("CHANNEL", "@toweringsale")
PROJECT_NAME = "@toweringsale"
FOLLOW_PREFIX = "follow_"
SHOP_PREFIX = "shop_"
PROFILE_PREFIX = "profile_"

FREE_DAILY_POST_LIMIT = 30

SUB_FREE = "free"
SUB_VIP = "vip"
SUB_PLAT = "platinum"
SUB_EXTRA = "extra"

BADGE = {
    SUB_FREE: "",
    SUB_VIP: "🌟 VIP публикация",
    SUB_PLAT: "💎 Platinum публикация",
    SUB_EXTRA: "🚀 Extra публикация",
}

CATEGORIES = [
    ("Продам", "sell"),
    ("Куплю", "buy"),
    ("Обмен", "trade"),
    ("Услуги", "service"),
]

PROFILE_THEMES = ["classic", "dark", "towering"]

# =======================
# ---- ЛОГИ -------------
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("toweringsale")

# =======================
# ---- БОТ --------------
# =======================
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
r_public, r_admin, r_owner = Router(), Router(), Router()
dp.include_routers(r_public, r_admin, r_owner)

# =======================
# ---- БАЗА ДАННЫХ ------
# =======================
DB_PATH = os.path.join("data", "bot.db")
os.makedirs("data", exist_ok=True)
os.makedirs("backups", exist_ok=True)

def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _pragmas(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")
    c.execute("PRAGMA busy_timeout=5000;")
    c.execute("PRAGMA cache_size=-64000;")
    c.execute("PRAGMA temp_store=MEMORY;")
    conn.commit()

def backup_db():
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(DB_PATH, "rb") as fsrc, open(os.path.join("backups", f"bot_{ts}.db"), "wb") as fdst:
            fdst.write(fsrc.read())
    except Exception as e:
        log.warning("DB backup error: %s", e)

def init_db():
    conn = _connect(); _pragmas(conn)
    c = conn.cursor()
    # users
    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        is_owner INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        trust_status TEXT DEFAULT 'neutral',         -- verified/neutral/scammer
        subscription TEXT DEFAULT 'free',            -- free/vip/platinum/extra
        sub_expires_at TEXT,
        sub_forever INTEGER DEFAULT 0,
        profile_theme TEXT DEFAULT 'classic',
        incognito INTEGER DEFAULT 0,
        joined_at TEXT,
        posts_total INTEGER DEFAULT 0,
        posts_30d INTEGER DEFAULT 0,
        views_30d INTEGER DEFAULT 0,
        -- NEW: витрина Extra
        storefront_title TEXT,
        storefront_bio TEXT,
        -- NEW: закреп профиля
        last_profile_pin_at TEXT,
        daily_pin_count INTEGER DEFAULT 0,
        daily_pin_date TEXT
    );
    """)
    # posts
    c.execute("""
    CREATE TABLE IF NOT EXISTS posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        author_tg INTEGER NOT NULL,
        category TEXT,
        text TEXT,
        media_type TEXT,        -- photo/video/voice/none
        media_file_id TEXT,
        status TEXT,            -- pending/approved/rejected
        moderator_tg INTEGER,
        reject_reason TEXT,
        published_msg_id INTEGER,
        published_at TEXT,
        views INTEGER DEFAULT 0,
        complaints INTEGER DEFAULT 0,
        price INTEGER,
        channel TEXT DEFAULT ''
    );
    """)
    # follows
    c.execute("""
    CREATE TABLE IF NOT EXISTS follows(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        follower_tg INTEGER,
        author_tg INTEGER,
        created_at TEXT,
        UNIQUE(follower_tg, author_tg)
    );
    """)
    # alerts
    c.execute("""
    CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_tg INTEGER,
        type TEXT,     -- category/keyword/max_price
        value TEXT,
        created_at TEXT
    );
    """)
    # admin logs
    c.execute("""
    CREATE TABLE IF NOT EXISTS admin_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_tg INTEGER,
        action TEXT,
        target_id INTEGER,
        extra TEXT,
        created_at TEXT
    );
    """)
    # complaints
    c.execute("""
    CREATE TABLE IF NOT EXISTS complaints(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER,
        from_tg INTEGER,
        reason TEXT,
        created_at TEXT
    );
    """)
    # совместимость со старыми базами (без падений)
    def ensure(table: str, col: str, ddl: str):
        try:
            c.execute(f"SELECT {col} FROM {table} LIMIT 1;")
        except sqlite3.OperationalError:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl};")
    ensure("posts", "reject_reason", "reject_reason TEXT")
    ensure("posts", "price", "price INTEGER")
    ensure("users", "profile_theme", "profile_theme TEXT DEFAULT 'classic'")
    ensure("users", "incognito", "incognito INTEGER DEFAULT 0")
    ensure("users", "storefront_title", "storefront_title TEXT")
    ensure("users", "storefront_bio", "storefront_bio TEXT")
    ensure("users", "last_profile_pin_at", "last_profile_pin_at TEXT")
    ensure("users", "daily_pin_count", "daily_pin_count INTEGER DEFAULT 0")
    ensure("users", "daily_pin_date", "daily_pin_date TEXT")
    conn.commit(); conn.close()

@contextmanager
def db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def upsert_user(tg_id: int, username: Optional[str]):
    with db() as conn:
        r = conn.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if r:
            conn.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
        else:
            conn.execute("""
            INSERT INTO users(tg_id, username, is_owner, is_admin, joined_at, subscription)
            VALUES(?,?,?,?,?,?)
            """, (tg_id, username, 1 if tg_id == OWNER_ID else 0, 1 if tg_id == OWNER_ID else 0,
                  datetime.now().isoformat(), SUB_FREE))

def get_user(tg_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

def is_admin(uid: int) -> bool:
    u = get_user(uid)
    return bool(u and u["is_admin"])

def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def set_admin(uid: int, v: int):
    with db() as conn:
        conn.execute("UPDATE users SET is_admin=? WHERE tg_id=?", (v, uid))
        conn.execute("INSERT INTO admin_logs(admin_tg,action,target_id,extra,created_at) VALUES(?,?,?,?,?)",
                     (OWNER_ID, "set_admin" if v else "unset_admin", uid, "", datetime.now().isoformat()))

def list_admins() -> List[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE is_admin=1 ORDER BY (tg_id=? ) DESC, username",
                            (OWNER_ID,)).fetchall()

async def is_channel_subscribed_async(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return True

def parse_price(text: str) -> Optional[int]:
    m = re.search(r"(\d[\d\s]{0,12})\s*(?:₽|руб|руб\.|RUB|stars|⭐)", text, flags=re.IGNORECASE)
    if not m:
        m2 = re.search(r"\b(\d{1,7})\b", text)
        if not m2: return None
        try: return int(m2.group(1))
        except: return None
    val = re.sub(r"\s+","", m.group(1))
    try: return int(val)
    except: return None

def user_daily_posts_count(tg_id: int) -> int:
    start = datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM posts WHERE author_tg=? AND published_at>=?",
                            (tg_id, start)).fetchone()[0]

# =======================
# ---- FSM ---------------
# =======================
class PostSG(StatesGroup):
    cat = State()
    content = State()
    confirm = State()

class RejectSG(StatesGroup):
    reason = State()

class AlertsSG(StatesGroup):
    type = State()
    value = State()

class AdminSG(StatesGroup):
    bc_target = State()
    bc_text = State()
    grant_user = State()
    grant_level = State()
    grant_term = State()

class StoreSG(StatesGroup):
    title = State()
    bio = State()

# =======================
# ---- КЛАВИАТУРЫ --------
# =======================
def main_kb(is_admin_flag: bool):
    rows = [
        [KeyboardButton(text="➕ Разместить объявление")],
        [KeyboardButton(text="🔮 Рекомендации"), KeyboardButton(text="🔔 Подписки/Фильтры")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="ℹ️ Инфо")],
    ]
    if is_admin_flag:
        rows.append([KeyboardButton(text="🛠 Админ-меню")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def categories_kb():
    kb = [[KeyboardButton(text=title)] for title,_ in CATEGORIES]
    kb.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def profile_kb(u: sqlite3.Row):
    rows = [[KeyboardButton(text="🎨 Тема профиля"), KeyboardButton(text="🕶 Инкогнито")]]
    # NEW: магазин Extra + закреп профиля
    if u["subscription"] == SUB_EXTRA:
        rows.append([KeyboardButton(text="🛒 Моя витрина")])
    if u["subscription"] in (SUB_PLAT, SUB_EXTRA) or u["sub_forever"]:
        rows.append([KeyboardButton(text="📌 Закрепить профиль")])
    rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def alerts_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Создать фильтр")],
            [KeyboardButton(text="📃 Мои фильтры")],
            [KeyboardButton(text="⬅️ Назад")],
        ], resize_keyboard=True
    )

def admin_kb(owner: bool):
    kb = [
        [KeyboardButton(text="📨 Рассылка"), KeyboardButton(text="📊 Глобальная статистика")],
        [KeyboardButton(text="🔥 Heatmap"), KeyboardButton(text="🏆 Доска почёта")],   # NEW
        [KeyboardButton(text="👥 Пользователи"), KeyboardButton(text="🧑‍💻 Админы")],
    ]
    if owner:
        kb.insert(0, [KeyboardButton(text="➕ Выдать подписку"), KeyboardButton(text="🗝 Выдать/Снять админа")])
    kb.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# =======================
# ---- ХЕЛПЕРЫ UI --------
# =======================
def sub_string(u: sqlite3.Row) -> str:
    sub = u["subscription"] or SUB_FREE
    if sub == SUB_FREE: return "Free"
    if u["sub_forever"]: return sub.capitalize()+" (навсегда)"
    if u["sub_expires_at"]:
        try:
            dt = datetime.fromisoformat(u["sub_expires_at"])
            left = (dt - datetime.now()).days
            return f"{sub.capitalize()} до {dt.strftime('%d.%m.%Y')} (осталось {max(left,0)} дн.)"
        except: pass
    return sub.capitalize()

def render_profile_card(u: sqlite3.Row) -> str:
    uname = f"@{u['username']}" if u['username'] else f"ID{u['tg_id']}"
    inc = "ON" if u["incognito"] else "OFF"
    return (
        f"🚀 {PROJECT_NAME} | Профиль автора\n"
        f"👤 {uname} | ID <code>{u['tg_id']}</code>\n"
        f"💳 Подписка: {sub_string(u)}\n"
        f"🛡 Доверие: {u['trust_status']}\n"
        f"🎭 Инкогнито: {inc}\n"
        f"📊 Постов: {u['posts_total']} (за 30д: {u['posts_30d']})"
    )

async def bot_username() -> str:
    me = await bot.get_me()
    return me.username

def follow_link_for(author_tg: int, uname: str) -> str:
    return f"https://t.me/{uname}?start={FOLLOW_PREFIX}{author_tg}"

def shop_link_for(author_tg: int, uname: str) -> str:
    return f"https://t.me/{uname}?start={SHOP_PREFIX}{author_tg}"

# =======================
# ---- СТАРТ -------------
# =======================
@r_public.message(CommandStart(deep_link=True))
async def deep_link(m: Message, command: CommandStart):
    upsert_user(m.from_user.id, m.from_user.username)
    # проверим подписку на канал
    ok = await is_channel_subscribed_async(m.from_user.id)
    if not ok:
        await m.answer(f"Подпишись на канал {PROJECT_NAME}, затем вернись и нажми /start")
        return
    payload = command.args or ""
    if payload.startswith(FOLLOW_PREFIX):
        try:
            author = int(payload[len(FOLLOW_PREFIX):])
            if author == m.from_user.id:
                await m.answer("Нельзя подписаться на самого себя.")
                return
            with db() as conn:
                conn.execute("INSERT OR IGNORE INTO follows(follower_tg,author_tg,created_at) VALUES(?,?,?)",
                             (m.from_user.id, author, datetime.now().isoformat()))
            await m.answer("✅ Подписка на автора оформлена.")
            return
        except: pass
    if payload.startswith(SHOP_PREFIX):
        try:
            author = int(payload[len(SHOP_PREFIX):])
            await show_storefront(m, author)
            return
        except: pass
    if payload.startswith(PROFILE_PREFIX):
        try:
            author = int(payload[len(PROFILE_PREFIX):])
            await show_public_profile(m, author)
            return
        except: pass
    # fallback в обычный старт
    await start(m)

@r_public.message(CommandStart())
async def start(m: Message):
    upsert_user(m.from_user.id, m.from_user.username)
    if not await is_channel_subscribed_async(m.from_user.id):
        await m.answer(f"Чтобы пользоваться ботом — подпишись на {PROJECT_NAME}\nПосле — /start")
        return
    u = get_user(m.from_user.id)
    await m.answer(
        f"Добро пожаловать в {PROJECT_NAME}!\nБиржа объявлений с модерацией и подписками (VIP/Platinum/Extra).",
        reply_markup=main_kb(is_admin(u["is_admin"]) if u else False)
    )

# =======================
# ---- ПРОФИЛЬ -----------
# =======================
@r_public.message(F.text == "👤 Профиль")
async def profile(m: Message):
    u = get_user(m.from_user.id)
    if not u: return
    await m.answer(render_profile_card(u), reply_markup=profile_kb(u))

@r_public.message(F.text == "🎨 Тема профиля")
async def theme_menu(m: Message):
    u = get_user(m.from_user.id)
    if not u: return
    kb = InlineKeyboardBuilder()
    for t in PROFILE_THEMES:
        mark = "✅ " if u["profile_theme"] == t else ""
        kb.button(text=f"{mark}{t}", callback_data=f"theme:{t}")
    kb.adjust(1)
    await m.answer("Выберите тему:", reply_markup=kb.as_markup())

@r_public.callback_query(F.data.startswith("theme:"))
async def theme_set(c: CallbackQuery):
    t = c.data.split(":",1)[1]
    if t not in PROFILE_THEMES:
        await c.answer("Нет темы", show_alert=True); return
    with db() as conn:
        conn.execute("UPDATE users SET profile_theme=? WHERE tg_id=?", (t, c.from_user.id))
    await c.answer("Готово")
    u = get_user(c.from_user.id)
    await c.message.edit_text(render_profile_card(u), reply_markup=profile_kb(u))

@r_public.message(F.text == "🕶 Инкогнито")
async def incognito_toggle(m: Message):
    u = get_user(m.from_user.id)
    if not u: return
    if u["subscription"] not in (SUB_VIP, SUB_PLAT, SUB_EXTRA) and not u["sub_forever"]:
        await m.answer("Инкогнито доступно с VIP и выше.")
        return
    new = 0 if u["incognito"] else 1
    with db() as conn:
        conn.execute("UPDATE users SET incognito=? WHERE tg_id=?", (new, m.from_user.id))
    await m.answer(f"Инкогнито: {'ON' if new else 'OFF'}")

# =======================
# ---- ВИТРИНА (EXTRA) ---
# =======================
@r_public.message(F.text == "🛒 Моя витрина")
async def my_storefront(m: Message, state: FSMContext):
    u = get_user(m.from_user.id)
    if not u: return
    if u["subscription"] != SUB_EXTRA and not u["sub_forever"]:
        await m.answer("Витрина доступна на тарифе Extra.")
        return
    uname = await bot_username()
    link = shop_link_for(m.from_user.id, uname)
    title = u["storefront_title"] or "Моя витрина"
    bio = u["storefront_bio"] or "Добавьте описание витрины (товары/услуги/условия)."
    txt = (f"🛒 Витрина Extra\n"
           f"Название: <b>{title}</b>\n"
           f"Описание: {bio}\n\n"
           f"🔗 Ссылка для клиентов: {link}\n\n"
           f"• «✏️ Название» — задать заголовок\n"
           f"• «📄 Описание» — задать описание\n"
           f"• «📤 Поделиться в канале» — опубликовать карточку витрины в @{CHANNEL[1:]}")
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data="store:title")
    kb.button(text="📄 Описание", callback_data="store:bio")
    kb.button(text="📤 Поделиться в канале", callback_data="store:post")
    kb.adjust(1)
    await m.answer(txt, reply_markup=kb.as_markup())

@r_public.callback_query(F.data == "store:title")
async def store_title_ask(c: CallbackQuery, state: FSMContext):
    await state.set_state(StoreSG.title)
    await c.message.answer("Пришлите новое <b>название</b> витрины одним сообщением.", reply_markup=ReplyKeyboardRemove())
    await c.answer()

@r_public.message(StoreSG.title)
async def store_title_set(m: Message, state: FSMContext):
    title = (m.text or "").strip()[:80]
    with db() as conn:
        conn.execute("UPDATE users SET storefront_title=? WHERE tg_id=?", (title, m.from_user.id))
    await state.clear()
    await my_storefront(m, state)

@r_public.callback_query(F.data == "store:bio")
async def store_bio_ask(c: CallbackQuery, state: FSMContext):
    await state.set_state(StoreSG.bio)
    await c.message.answer("Пришлите новое <b>описание</b> витрины (до ~500 символов).")
    await c.answer()

@r_public.message(StoreSG.bio)
async def store_bio_set(m: Message, state: FSMContext):
    bio = (m.text or "").strip()[:500]
    with db() as conn:
        conn.execute("UPDATE users SET storefront_bio=? WHERE tg_id=?", (bio, m.from_user.id))
    await state.clear()
    await my_storefront(m, state)

@r_public.callback_query(F.data == "store:post")
async def store_post_channel(c: CallbackQuery):
    u = get_user(c.from_user.id)
    if not u: return
    uname = await bot_username()
    link = shop_link_for(c.from_user.id, uname)
    title = u["storefront_title"] or "Витрина Extra"
    bio = u["storefront_bio"] or ""
    card = (
        f"🛒 {title}\n"
        f"{bio}\n\n"
        f"📂 Последние публикации автора — по ссылке витрины:\n{link}\n\n"
        f"— опубликовано через {PROJECT_NAME}"
    )
    try:
        await bot.send_message(CHANNEL, card, disable_web_page_preview=True)
        await c.answer("Витрина опубликована в канал.")
    except Exception as e:
        await c.answer("Не удалось опубликовать (бот должен быть админом в канале).", show_alert=True)

async def show_storefront(m: Message, author_tg: int):
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE tg_id=?", (author_tg,)).fetchone()
        posts = conn.execute(
            "SELECT published_msg_id FROM posts WHERE author_tg=? AND status='approved' ORDER BY id DESC LIMIT 20",
            (author_tg,)
        ).fetchall()
    if not u:
        await m.answer("Витрина не найдена."); return
    links = [f"• https://t.me/{CHANNEL[1:]}/{p['published_msg_id']}" for p in posts if p["published_msg_id"]]
    header = f"🛒 Витрина @{u['username'] or 'ID'+str(u['tg_id'])}\n"
    if u["storefront_title"]: header += f"<b>{u['storefront_title']}</b>\n"
    if u["storefront_bio"]: header += f"{u['storefront_bio']}\n"
    txt = header + ("\n".join(links) if links else "\nПока нет опубликованных постов.")
    await m.answer(txt, disable_web_page_preview=True)

async def show_public_profile(m: Message, author_tg: int):
    u = get_user(author_tg)
    if not u:
        await m.answer("Профиль не найден.")
        return
    await m.answer(render_profile_card(u))

# =======================
# ---- ЗАКРЕП ПРОФИЛЯ ----
# =======================
def can_pin_profile(u: sqlite3.Row) -> tuple[bool, str]:
    # Platinum: 1 раз в сутки
    # Extra: безлимит, КД 1 час
    now = datetime.now()
    last = None
    if u["last_profile_pin_at"]:
        try: last = datetime.fromisoformat(u["last_profile_pin_at"])
        except: last = None
    if u["subscription"] == SUB_PLAT and not u["sub_forever"]:
        day = (u["daily_pin_date"] or "")
        if day != datetime.now().date().isoformat():
            return True, ""  # сброс счётчика на новый день
        if (u["daily_pin_count"] or 0) >= 1:
            return False, "Доступно 1 закреп профиля в сутки на Platinum."
        if last and (now - last).total_seconds() < 5:  # защита от дабл-тапа
            return False, "Подождите немного."
        return True, ""
    # Extra
    if last and (now - last).total_seconds() < 3600:
        left = 3600 - int((now - last).total_seconds())
        return False, f"Кулдаун 1 час. Осталось {left//60} мин."
    return True, ""

@r_public.message(F.text == "📌 Закрепить профиль")
async def pin_profile(m: Message):
    u = get_user(m.from_user.id)
    if not u: return
    if u["subscription"] not in (SUB_PLAT, SUB_EXTRA) and not u["sub_forever"]:
        await m.answer("Закреп профиля доступен для Platinum и Extra.")
        return
    ok, reason = can_pin_profile(u)
    if not ok:
        await m.answer(f"Нельзя закрепить сейчас: {reason}")
        return
    card = render_profile_card(u) + "\n\n" + "— закреп профиля автора"
    try:
        msg = await bot.send_message(CHANNEL, card)
        # Пин в канале
        await bot.pin_chat_message(CHANNEL, msg.message_id, disable_notification=True)
        # учёт квоты
        with db() as conn:
            if u["subscription"] == SUB_PLAT and not u["sub_forever"]:
                day = datetime.now().date().isoformat()
                if u["daily_pin_date"] != day:
                    conn.execute("UPDATE users SET daily_pin_date=?, daily_pin_count=1, last_profile_pin_at=? WHERE tg_id=?",
                                 (day, datetime.now().isoformat(), m.from_user.id))
                else:
                    conn.execute("UPDATE users SET daily_pin_count=daily_pin_count+1, last_profile_pin_at=? WHERE tg_id=?",
                                 (datetime.now().isoformat(), m.from_user.id))
            else:
                conn.execute("UPDATE users SET last_profile_pin_at=? WHERE tg_id=?", (datetime.now().isoformat(), m.from_user.id))
        await m.answer("Профиль закреплён в канале ✅")
    except Exception as e:
        await m.answer("Не удалось закрепить (бот должен быть админом в канале).")

# =======================
# ---- РЕКОМЕНДАЦИИ ------
# =======================
def user_alerts(uid: int) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT type,value FROM alerts WHERE user_tg=?", (uid,)).fetchall()

def user_followed_authors(uid: int) -> list[int]:
    with db() as conn:
        rows = conn.execute("SELECT author_tg FROM follows WHERE follower_tg=?", (uid,)).fetchall()
        return [r["author_tg"] for r in rows]

@r_public.message(F.text == "🔮 Рекомендации")
async def recommendations(m: Message):
    # Соберём последние 120 одобренных постов и отранжируем
    alerts = user_alerts(m.from_user.id)
    follows = set(user_followed_authors(m.from_user.id))
    with db() as conn:
        posts = conn.execute("""
            SELECT id, author_tg, text, category, published_msg_id
            FROM posts WHERE status='approved' AND published_msg_id IS NOT NULL
            ORDER BY id DESC LIMIT 120
        """).fetchall()
    scored = []
    for p in posts:
        score = 0
        # сигнал от подписок на автора
        if p["author_tg"] in follows: score += 3
        # сигнал от фильтров
        txt = (p["text"] or "").lower()
        for a in alerts:
            if a["type"] == "category" and a["value"] == (p["category"] or ""):
                score += 2
            elif a["type"] == "keyword" and a["value"] in txt:
                score += 2
            elif a["type"] == "max_price":
                pr = parse_price(txt) or 10**9
                try:
                    if pr <= int(a["value"]): score += 2
                except: pass
        scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [p for s,p in scored if s>0][:10] or [p for _,p in scored[:10]]
    if not top:
        await m.answer("Пока нечего рекомендовать.")
        return
    links = [f"• https://t.me/{CHANNEL[1:]}/{p['published_msg_id']}" for p in top]
    await m.answer("Рекомендации для вас:\n" + "\n".join(links), disable_web_page_preview=True)

# =======================
# ---- Доска почёта -------
# =======================
def top_extra_authors(days: int = 30, limit: int = 5) -> List[sqlite3.Row]:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with db() as conn:
        return conn.execute("""
        SELECT u.tg_id, u.username, COUNT(p.id) as cnt
        FROM users u
        JOIN posts p ON p.author_tg=u.tg_id AND p.status='approved' AND p.published_at>=?
        WHERE u.subscription='extra' OR u.sub_forever=1
        GROUP BY u.tg_id, u.username
        ORDER BY cnt DESC, u.tg_id ASC
        LIMIT ?
        """, (since, limit)).fetchall()

@r_admin.message(F.text == "🏆 Доска почёта")
async def hall_of_fame(m: Message):
    if not is_admin(m.from_user.id): return
    rows = top_extra_authors()
    if not rows:
        await m.answer("Нет данных за последние 30 дней.")
        return
    lines = ["🥇 Топ Extra авторов за 30 дней:"]
    for i,r in enumerate(rows, start=1):
        lines.append(f"{i}. @{r['username'] or 'ID'+str(r['tg_id'])} — {r['cnt']} пост(ов)")
    txt = "\n".join(lines)
    kb = InlineKeyboardBuilder()
    kb.button(text="📤 Опубликовать в канал", callback_data="hof:post")
    kb.adjust(1)
    await m.answer(txt, reply_markup=kb.as_markup())

@r_admin.callback_query(F.data == "hof:post")
async def hof_post(c: CallbackQuery):
    rows = top_extra_authors()
    if not rows:
        await c.answer("Нет данных", show_alert=True); return
    lines = ["🥇 Топ Extra авторов (30 дней):"]
    for i,r in enumerate(rows, start=1):
        lines.append(f"{i}. @{r['username'] or 'ID'+str(r['tg_id'])} — {r['cnt']} пост(ов)")
    try:
        await bot.send_message(CHANNEL, "\n".join(lines))
        await c.answer("Опубликовано в канал.")
    except:
        await c.answer("Не удалось опубликовать (бот должен быть админом в канале).", show_alert=True)

# =======================
# ---- ПОДПИСКИ/ФИЛЬТРЫ ---
# =======================
@r_public.message(F.text == "🔔 Подписки/Фильтры")
async def alerts_menu(m: Message):
    await m.answer("Умные фильтры: категория / ключевое слово / макс. цена", reply_markup=alerts_menu_kb())

@r_public.message(F.text == "➕ Создать фильтр")
async def alert_add(m: Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="Категория", callback_data="al:cat")
    kb.button(text="Ключевое слово", callback_data="al:kw")
    kb.button(text="Макс. цена", callback_data="al:pr")
    kb.adjust(1)
    await m.answer("Выберите тип фильтра:", reply_markup=kb.as_markup())
    await state.set_state(AlertsSG.type)

@r_public.callback_query(AlertsSG.type, F.data.in_({"al:cat","al:kw","al:pr"}))
async def alert_type(c: CallbackQuery, state: FSMContext):
    code = c.data.split(":")[1]
    await state.update_data(kind=code)
    if code == "cat":
        cats = ", ".join([c for _,c in CATEGORIES])
        await c.message.edit_text(f"Введите код категории ({cats})")
    elif code == "kw":
        await c.message.edit_text("Введите ключевое слово:")
    else:
        await c.message.edit_text("Введите максимальную цену числом:")
    await state.set_state(AlertsSG.value)
    await c.answer()

@r_public.message(AlertsSG.value)
async def alert_save(m: Message, state: FSMContext):
    data = await state.get_data(); kind = data.get("kind")
    val = (m.text or "").strip().lower()
    if kind == "cat":
        codes = [c for _,c in CATEGORIES]
        if val not in codes:
            await m.answer("Нет такой категории."); return
        typ = "category"
    elif kind == "kw":
        typ = "keyword"
    else:
        if not val.isdigit(): await m.answer("Нужно число."); return
        typ = "max_price"
    with db() as conn:
        conn.execute("INSERT INTO alerts(user_tg,type,value,created_at) VALUES(?,?,?,?)",
                     (m.from_user.id, typ, val, datetime.now().isoformat()))
    await state.clear()
    await m.answer("Фильтр создан ✅", reply_markup=alerts_menu_kb())

@r_public.message(F.text == "📃 Мои фильтры")
async def alerts_list(m: Message):
    with db() as conn:
        rows = conn.execute("SELECT id,type,value FROM alerts WHERE user_tg=? ORDER BY id DESC", (m.from_user.id,)).fetchall()
    if not rows:
        await m.answer("Фильтров нет.")
        return
    kb = InlineKeyboardBuilder()
    lines = []
    for r in rows:
        lines.append(f"#{r['id']}: {r['type']} = <code>{r['value']}</code>")
        kb.button(text=f"Удалить #{r['id']}", callback_data=f"alrm:{r['id']}")
    kb.adjust(2)
    await m.answer("Ваши фильтры:\n" + "\n".join(lines), reply_markup=kb.as_markup())

@r_public.callback_query(F.data.startswith("alrm:"))
async def alerts_delete(c: CallbackQuery):
    _id = int(c.data.split(":",1)[1])
    with db() as conn:
        conn.execute("DELETE FROM alerts WHERE id=? AND user_tg=?", (_id, c.from_user.id))
    await c.answer("Удалено")
    await c.message.delete()

# =======================
# ---- ПУБЛИКАЦИЯ --------
# =======================
@r_public.message(F.text == "➕ Разместить объявление")
async def post_start(m: Message, state: FSMContext):
    u = get_user(m.from_user.id)
    if not u: return
    if u["subscription"] == SUB_FREE and user_daily_posts_count(m.from_user.id) >= FREE_DAILY_POST_LIMIT:
        await m.answer("Лимит 30 постов/день на Free. Оформите VIP для безлимита.")
        return
    await m.answer("Выберите категорию:", reply_markup=categories_kb())
    await state.set_state(PostSG.cat)

@r_public.message(PostSG.cat)
async def post_cat(m: Message, state: FSMContext):
    if m.text == "⬅️ Назад":
        await state.clear()
        u = get_user(m.from_user.id); await m.answer("Отменено.", reply_markup=main_kb(bool(u["is_admin"]) if u else False)); return
    code = None
    for title,c in CATEGORIES:
        if m.text == title: code = c; break
    if not code:
        await m.answer("Выберите категорию кнопкой.")
        return
    await state.update_data(cat=code)
    await m.answer("Отправьте текст объявления (можно одно фото/видео/voice с подписью).", reply_markup=ReplyKeyboardRemove())
    await state.set_state(PostSG.content)

@r_public.message(PostSG.content, F.content_type.in_({"text","photo","video","voice"}))
async def post_collect(m: Message, state: FSMContext):
    data = await state.get_data(); cat = data.get("cat")
    media_type, media_id, text = "none", None, ""
    if m.content_type == "text":
        text = m.text or ""
    elif m.content_type == "photo":
        media_type, media_id, text = "photo", m.photo[-1].file_id, m.caption or ""
    elif m.content_type == "video":
        media_type, media_id, text = "video", m.video.file_id, m.caption or ""
    elif m.content_type == "voice":
        media_type, media_id, text = "voice", m.voice.file_id, m.caption or ""
    hints = []
    if not re.search(r"@\w{5,}", text): hints.append("⚠️ Нет контакта (@username).")
    if parse_price(text) is None: hints.append("⚠️ Нет цены.")
    hint = ("\n".join(hints)+"\n\n") if hints else ""
    await state.update_data(text=text, media_type=media_type, media_id=media_id)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Отправить", callback_data="post:ok")
    kb.button(text="✏️ Изменить", callback_data="post:edit")
    kb.button(text="❌ Отмена", callback_data="post:cancel")
    kb.adjust(3)
    await m.answer(f"{hint}<b>Превью</b>\nКатегория: {cat}\n\n{text[:2000]}", reply_markup=kb.as_markup())
    await state.set_state(PostSG.confirm)

async def publish_text_for(author: sqlite3.Row, cat: str, text: str) -> str:
    badge = BADGE.get(author["subscription"] or SUB_FREE, "")
    inc = bool(author["incognito"]) and (author["subscription"] in (SUB_VIP, SUB_PLAT, SUB_EXTRA) or author["sub_forever"])
    uname = await bot_username()
    follow = follow_link_for(author["tg_id"], uname)
    shop = shop_link_for(author["tg_id"], uname)
    author_line = f"👤 Автор: {'Аноним ID'+str(author['tg_id']) if inc else ('@'+author['username'] if author['username'] else 'ID'+str(author['tg_id']))}"
    cat_title = next((t for t,c in CATEGORIES if c==cat), cat)
    lines = [
        f"🏷 Категория: {cat_title}",
        text.strip(),
        "",
    ]
    if badge: lines.append(badge)
    lines.append(author_line)
    lines.append(f"🛡 Статус: {author['trust_status']}")
    if not inc:
        lines.append(f"📩 Подписаться на автора: {follow}")
        lines.append(f"🛒 Магазин автора: {shop}")
    lines.append(f"— опубликовано через {PROJECT_NAME}")
    return "\n".join(lines)

@r_public.callback_query(PostSG.confirm, F.data == "post:edit")
async def post_edit(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await c.message.edit_text("Отправьте новый текст/медиа:")
    await state.set_state(PostSG.content)

@r_public.callback_query(PostSG.confirm, F.data == "post:cancel")
async def post_cancel(c: CallbackQuery, state: FSMContext):
    await c.answer("Отменено")
    await state.clear()
    u = get_user(c.from_user.id)
    await c.message.edit_text("Отменено.")
    await c.message.answer("Главное меню", reply_markup=main_kb(bool(u["is_admin"]) if u else False))

@r_public.callback_query(PostSG.confirm, F.data == "post:ok")
async def post_submit(c: CallbackQuery, state: FSMContext):
    await c.answer()
    data = await state.get_data(); await state.clear()
    u = get_user(c.from_user.id)
    cat = data["cat"]; text = data["text"]; mtype = data["media_type"]; mid = data["media_id"]
    price = parse_price(text) or None
    with db() as conn:
        conn.execute("""
        INSERT INTO posts(author_tg,category,text,media_type,media_file_id,status,price,channel)
        VALUES(?,?,?,?,?,'pending',?,?)
        """, (c.from_user.id, cat, text, mtype, mid, price, CHANNEL))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if u["subscription"] in (SUB_VIP, SUB_PLAT, SUB_EXTRA) or u["sub_forever"]:
        body = await publish_text_for(u, cat, text)
        try:
            if mtype == "photo" and mid:
                msg = await bot.send_photo(CHANNEL, mid, caption=body)
            elif mtype == "video" and mid:
                msg = await bot.send_video(CHANNEL, mid, caption=body)
            elif mtype == "voice" and mid:
                msg = await bot.send_voice(CHANNEL, mid, caption=body)
            else:
                msg = await bot.send_message(CHANNEL, body)
            with db() as conn:
                conn.execute("UPDATE posts SET status='approved', published_msg_id=?, published_at=? WHERE id=?",
                             (msg.message_id, datetime.now().isoformat(), pid))
                conn.execute("UPDATE users SET posts_total=posts_total+1, posts_30d=posts_30d+1 WHERE tg_id=?",
                             (c.from_user.id,))
            await c.message.edit_text("✅ Пост опубликован.")
        except Exception as e:
            log.error("publish error: %s", e)
            await c.message.edit_text("Ошибка публикации. Бот должен быть админом в канале.")
    else:
        # модерация
        await c.message.edit_text("✅ Пост отправлен на модерацию. Админы проверят.")
        await send_to_admins_for_moderation(pid)

async def send_to_admins_for_moderation(pid: int):
    with db() as conn:
        p = conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        u = conn.execute("SELECT * FROM users WHERE tg_id=?", (p["author_tg"],)).fetchone()
    text = (
        f"📝 Новое объявление #{p['id']} (категория: {p['category']})\n"
        f"Автор: @{u['username'] or 'ID'+str(u['tg_id'])}\n\n{p['text'] or ''}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Одобрить", callback_data=f"approve:{p['id']}")
    kb.button(text="❌ Отклонить", callback_data=f"reject:{p['id']}")
    kb.adjust(2)
    admins = list_admins()
    for a in admins:
        try:
            if p["media_type"] == "photo" and p["media_file_id"]:
                await bot.send_photo(a["tg_id"], p["media_file_id"], caption=text, reply_markup=kb.as_markup())
            elif p["media_type"] == "video" and p["media_file_id"]:
                await bot.send_video(a["tg_id"], p["media_file_id"], caption=text, reply_markup=kb.as_markup())
            elif p["media_type"] == "voice" and p["media_file_id"]:
                await bot.send_voice(a["tg_id"], p["media_file_id"], caption=text, reply_markup=kb.as_markup())
            else:
                await bot.send_message(a["tg_id"], text, reply_markup=kb.as_markup())
        except Exception as e:
            log.warning("send preview error: %s", e)

@r_admin.callback_query(F.data.startswith("approve:"))
async def cb_approve(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    with db() as conn:
        p = conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        if not p or p["status"] != "pending":
            await c.answer("Уже обработано.", show_alert=True); return
        u = conn.execute("SELECT * FROM users WHERE tg_id=?", (p["author_tg"],)).fetchone()
    body = await publish_text_for(u, p["category"], p["text"])
    try:
        if p["media_type"] == "photo" and p["media_file_id"]:
            msg = await bot.send_photo(CHANNEL, p["media_file_id"], caption=body)
        elif p["media_type"] == "video" and p["media_file_id"]:
            msg = await bot.send_video(CHANNEL, p["media_file_id"], caption=body)
        elif p["media_type"] == "voice" and p["media_file_id"]:
            msg = await bot.send_voice(CHANNEL, p["media_file_id"], caption=body)
        else:
            msg = await bot.send_message(CHANNEL, body)
        with db() as conn:
            conn.execute("UPDATE posts SET status='approved', moderator_tg=?, published_msg_id=?, published_at=? WHERE id=?",
                         (c.from_user.id, msg.message_id, datetime.now().isoformat(), pid))
        await c.message.edit_text(f"✅ Опубликовано (#{pid})")
        try: await bot.send_message(p["author_tg"], "✅ Ваш пост одобрен и опубликован.")
        except: pass
    except Exception as e:
        await c.answer("Публикация не удалась (права бота?).", show_alert=True)

@r_admin.callback_query(F.data.startswith("reject:"))
async def cb_reject(c: CallbackQuery, state: FSMContext):
    pid = int(c.data.split(":",1)[1])
    with db() as conn:
        p = conn.execute("SELECT status FROM posts WHERE id=?", (pid,)).fetchone()
    if not p or p["status"] != "pending":
        await c.answer("Уже обработано.", show_alert=True); return
    await state.set_state(RejectSG.reason)
    await state.update_data(pid=pid)
    await c.message.answer("Укажите причину отклонения (одним сообщением):")
    await c.answer()

@r_admin.message(RejectSG.reason)
async def reject_reason(m: Message, state: FSMContext):
    data = await state.get_data(); pid = int(data.get("pid"))
    reason = (m.text or "").strip()
    with db() as conn:
        row = conn.execute("SELECT author_tg,status FROM posts WHERE id=?", (pid,)).fetchone()
        if not row or row["status"] != "pending":
            await m.answer("Пост уже обработан."); await state.clear(); return
        conn.execute("UPDATE posts SET status='rejected', moderator_tg=?, reject_reason=? WHERE id=?",
                     (m.from_user.id, reason, pid))
    try: await bot.send_message(row["author_tg"], f"❌ Ваш пост отклонён.\nПричина: {reason}")
    except: pass
    await m.answer("Отклонено ✅"); await state.clear()

# =======================
# ---- АДМИНКА -----------
# =======================
@r_public.message(F.text == "🛠 Админ-меню")
async def admin_menu(m: Message):
    if not is_admin(m.from_user.id): return
    await m.answer("Админ-меню:", reply_markup=admin_kb(is_owner(m.from_user.id)))

@r_owner.message(F.text == "🗝 Выдать/Снять админа")
async def owner_admins(m: Message):
    rows = list_admins()
    txt = "Админы:\n" + ("\n".join([f"• @{r['username'] or 'ID'+str(r['tg_id'])}" for r in rows]) if rows else "нет")
    await m.answer(txt + "\n\nВыдать: @user или ID\nСнять: <code>remove ID</code>")

@r_owner.message(F.text.regexp(r"^remove\s+\d+$"))
async def owner_remove_admin(m: Message):
    uid = int(re.findall(r"\d+", m.text)[0])
    if uid == OWNER_ID:
        await m.answer("Нельзя снять владельца."); return
    set_admin(uid, 0); await m.answer("Снял.")

@r_owner.message()
async def owner_add_admin(m: Message):
    text = (m.text or "").strip()
    if not text.startswith("@") and not text.isdigit(): return
    if not is_owner(m.from_user.id): return
    if text.startswith("@"):
        with db() as conn:
            r = conn.execute("SELECT tg_id FROM users WHERE username=?", (text[1:],)).fetchone()
        if not r: await m.answer("Нет такого в базе."); return
        uid = r["tg_id"]
    else:
        uid = int(text)
    if uid == OWNER_ID:
        await m.answer("Владелец уже админ."); return
    set_admin(uid, 1)
    try: await bot.send_message(uid, "✅ Вам выданы права администратора в @toweringsale.")
    except: pass
    await m.answer("Выдал.")

@r_admin.message(F.text == "📨 Рассылка")
async def bc_menu(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    kb = InlineKeyboardBuilder()
    for t in ("all","vip","platinum","extra","active7"):
        kb.button(text=t, callback_data=f"bc:{t}")
    kb.adjust(3,2)
    await m.answer("Аудитория:", reply_markup=kb.as_markup())

@r_admin.callback_query(F.data.startswith("bc:"))
async def bc_pick(c: CallbackQuery, state: FSMContext):
    await state.update_data(bc_target=c.data.split(":",1)[1])
    await c.message.edit_text("Отправьте текст рассылки одним сообщением.")
    await state.set_state(AdminSG.bc_text); await c.answer()

@r_admin.message(AdminSG.bc_text)
async def bc_send(m: Message, state: FSMContext):
    data = await state.get_data(); target = data.get("bc_target","all")
    text = m.text or ""
    with db() as conn:
        if target == "all": rows = conn.execute("SELECT tg_id FROM users").fetchall()
        elif target in (SUB_VIP,SUB_PLAT,SUB_EXTRA):
            rows = conn.execute("SELECT tg_id FROM users WHERE subscription=?", (target,)).fetchall()
        else:
            since = (datetime.now()-timedelta(days=7)).isoformat()
            rows = conn.execute("SELECT DISTINCT author_tg as tg_id FROM posts WHERE published_at>=?", (since,)).fetchall()
    sent = 0
    for r in rows:
        try: await bot.send_message(r["tg_id"], text); sent += 1
        except: pass
        await asyncio.sleep(0.01)
    await m.answer(f"Отправлено: {sent}")
    await state.clear()

@r_admin.message(F.text == "📊 Глобальная статистика")
async def gstats(m: Message):
    if not is_admin(m.from_user.id): return
    with db() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        vip = conn.execute("SELECT COUNT(*) FROM users WHERE subscription='vip'").fetchone()[0]
        plat = conn.execute("SELECT COUNT(*) FROM users WHERE subscription='platinum'").fetchone()[0]
        extra = conn.execute("SELECT COUNT(*) FROM users WHERE subscription='extra'").fetchone()[0]
    await m.answer(f"Пользователей: {users}\nПостов: {posts}\nVIP: {vip} | Platinum: {plat} | Extra: {extra}")

@r_admin.message(F.text == "🔥 Heatmap")
async def heatmap(m: Message):
    if not is_admin(m.from_user.id): return
    with db() as conn:
        rows = conn.execute("SELECT published_at FROM posts WHERE published_at IS NOT NULL").fetchall()
    dow, hours = [0]*7, [0]*24
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["published_at"]); dow[dt.weekday()] += 1; hours[dt.hour] += 1
        except: pass
    days = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
    await m.answer("🗓 По дням:\n" + "\n".join(f"{days[i]}: {'█'*max(1,d//5)} {d}" for i,d in enumerate(dow)))
    await m.answer("⏰ По часам:\n" + "\n".join(f"{i:02d}: {'█'*max(1,h//3)} {h}" for i,h in enumerate(hours)))

# =======================
# ---- ИНФО/НАЗАД/ФОЛЛБЕК
# =======================
@r_public.message(F.text == "ℹ️ Инфо")
async def info(m: Message):
    uname = await bot_username()
    u = get_user(m.from_user.id)
    shop = shop_link_for(m.from_user.id, uname)
    await m.answer(
        f"{PROJECT_NAME}\n"
        f"• Free — модерация, 30 постов/день\n"
        f"• VIP/Platinum/Extra — мгновенная публикация\n"
        f"• Extra: витрина магазина ({shop}), закреп профиля (безлимит, КД 1ч)\n"
        f"• Platinum: закреп профиля (1/сутки)\n"
        f"• Рекомендации — персональная подборка\n"
        f"• Для подписки/продления: @Andrew_Allen2810"
    )

@r_public.message(F.text == "⬅️ Назад")
async def back(m: Message, state: FSMContext):
    await state.clear()
    u = get_user(m.from_user.id)
    await m.answer("Главное меню", reply_markup=main_kb(bool(u["is_admin"]) if u else False))

@r_public.message()
async def fallback(m: Message):
    u = get_user(m.from_user.id)
    await m.answer("Главное меню", reply_markup=main_kb(bool(u["is_admin"]) if u else False))

# =======================
# ---- MAIN --------------
# =======================
async def on_startup():
    init_db()
    backup_db()
    me = await bot.get_me()
    log.info("Bot started as @%s", me.username)

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped")
















