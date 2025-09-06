# app.py
# Python 3.11+, aiogram>=3.7 (например 3.22.0)
# Проект: @toweringsale

import asyncio
import os
import re
import sqlite3
import shutil
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Tuple, Iterable, List, Callable, Any, Dict

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.dispatcher.middlewares.base import BaseMiddleware

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ============== helpers: микро-обфускация ==============
def _de(s: str | None) -> str | None:
    if not s or not isinstance(s, str):
        return s
    if s.startswith("b64:"):
        import base64
        try:
            return base64.b64decode(s[4:].encode()).decode()
        except Exception:
            return s
    return s

# ============== CONFIG ==============
PROJECT_NAME = os.getenv("PROJECT_NAME", "@toweringsale")
BOT_TOKEN   = _de(os.getenv("BOT_TOKEN", "8052075709:AAGD7-tH2Yq7Ipixmw21y3D1B-oWWGrq03I"))
OWNER_ID    = int(_de(os.getenv("OWNER_ID", "6089346880")))
CHANNEL_ID_ENV = _de(os.getenv("CHANNEL_ID", "@toweringsale"))  # @toweringsale по умолчанию
RENEW_CONTACT = "@sexmvls"  # куда писать для продления VIP

# Глобальная БД (переживает переносы проекта)
HOME_DIR = os.path.expanduser("~")
DB_DIR = os.getenv("DB_DIR", os.path.join(HOME_DIR, ".toweringsale"))
PRIMARY_DB_PATH = os.path.join(DB_DIR, "bot.db")
BACKUP_DIR = os.path.join(DB_DIR, "backups")
LEGACY_DB_PATH = os.path.join("data", "bot.db")
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

if not os.path.exists(PRIMARY_DB_PATH) and os.path.exists(LEGACY_DB_PATH):
    shutil.copy2(LEGACY_DB_PATH, PRIMARY_DB_PATH)

# ============== DB + BACKUP ==============
def backup_db():
    if os.path.exists(PRIMARY_DB_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(PRIMARY_DB_PATH, os.path.join(BACKUP_DIR, f"bot_{ts}.db"))

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(PRIMARY_DB_PATH, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA cache_size=-200000")
    return conn

def ensure_column(conn: sqlite3.Connection, table: str, col: str, col_def: str):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

def ensure_index(conn: sqlite3.Connection, name: str, sql: str):
    try:
        conn.execute(sql)
    except Exception:
        pass

def init_db():
    with db() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE,
            username TEXT,
            is_owner INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            status TEXT DEFAULT 'neutral', -- verified/neutral/scammer
            posts_total INTEGER DEFAULT 0,
            posts_approved INTEGER DEFAULT 0,
            posts_rejected INTEGER DEFAULT 0,
            day_posts_date TEXT,
            day_posts_count INTEGER DEFAULT 0,
            subscription_type TEXT DEFAULT 'free',  -- free/vip/platinum/extra
            subscription_expires TEXT,             -- ISO8601 или NULL
            last_seen TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content_type TEXT,  -- text/photo/video/document
            text TEXT,
            file_id TEXT,
            status TEXT DEFAULT 'pending', -- pending/approved/rejected
            moderator_id INTEGER,
            reject_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS config(
            key TEXT PRIMARY KEY,
            value TEXT
        )""")

        # миграции (на будущее совместимость)
        ensure_column(c, "users", "day_posts_date", "day_posts_date TEXT")
        ensure_column(c, "users", "day_posts_count", "day_posts_count INTEGER DEFAULT 0")
        ensure_column(c, "users", "subscription_type", "subscription_type TEXT DEFAULT 'free'")
        ensure_column(c, "users", "subscription_expires", "subscription_expires TEXT")
        ensure_column(c, "users", "last_seen", "last_seen TEXT")
        ensure_column(c, "users", "created_at", "created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        ensure_column(c, "posts", "reject_reason", "reject_reason TEXT")
        ensure_column(c, "posts", "moderator_id", "moderator_id INTEGER")
        ensure_column(c, "posts", "created_at", "created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        ensure_index(c, "idx_users_tg_id",
                     "CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id)")
        ensure_index(c, "idx_posts_status",
                     "CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status)")
        ensure_index(c, "idx_posts_user",
                     "CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id)")
        ensure_index(c, "idx_users_username",
                     "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")

        # владельца помечаем
        c.execute("UPDATE users SET is_owner=1, is_admin=1 WHERE tg_id=?", (OWNER_ID,))

        # канал по умолчанию
        row = c.execute("SELECT value FROM config WHERE key='channel_id'").fetchone()
        if not row and CHANNEL_ID_ENV:
            c.execute("INSERT INTO config(key,value) VALUES('channel_id', ?)", (CHANNEL_ID_ENV,))

# ============== DB utils ==============
def get_cfg(key: str) -> Optional[str]:
    with db() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def set_cfg(key: str, value: str):
    with db() as c:
        c.execute("INSERT INTO config(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

def normalize_channel_id(raw: str | int | None) -> str | int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    m = re.search(r"t\.me/([A-Za-z0-9_]+)", s)
    if m:
        return f"@{m.group(1)}"
    if re.fullmatch(r"-100\d{5,}", s):
        return int(s)
    if s.startswith("@"):
        return s
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", s):
        return f"@{s}"
    return s

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def today_str_local() -> str:
    return date.today().isoformat()

def parse_iso(dt: str | None) -> Optional[datetime]:
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except Exception:
        return None

def fmt_local(dt_iso: str | None) -> str:
    """Красивый локальный вывод времени 'YYYY-MM-DD HH:MM'."""
    if not dt_iso:
        return "—"
    try:
        aware = parse_iso(dt_iso)
        if not aware:
            return dt_iso
        local_tz = datetime.now().astimezone().tzinfo
        loc = aware.astimezone(local_tz)
        return loc.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_iso

def touch_user(tg_id: int, username: Optional[str]):
    with db() as c:
        now = utcnow().isoformat(timespec="seconds")
        row = c.execute("SELECT tg_id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if row:
            c.execute("UPDATE users SET username=?, last_seen=? WHERE tg_id=?", (username, now, tg_id))
        else:
            is_owner = 1 if tg_id == OWNER_ID else 0
            c.execute("INSERT INTO users(tg_id, username, is_owner, is_admin, status, subscription_type, last_seen) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (tg_id, username, is_owner, is_owner, "neutral", "free", now))

def get_user(tg_id: int) -> sqlite3.Row:
    with db() as c:
        return c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

def get_or_create_user(tg_id: int, username: Optional[str]) -> sqlite3.Row:
    touch_user(tg_id, username)
    return get_user(tg_id)

def user_by_id(uid: int) -> Optional[sqlite3.Row]:
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def user_by_username(name: str) -> Optional[sqlite3.Row]:
    uname = name.lstrip("@").lower()
    with db() as c:
        return c.execute("SELECT * FROM users WHERE LOWER(username)=?", (uname,)).fetchone()

def set_user_flag(tg_id: int, field: str, val: Any):
    with db() as c:
        c.execute(f"UPDATE users SET {field}=? WHERE tg_id=?", (val, tg_id))

def update_user_stats(user_id: int, field: str, inc: int = 1):
    with db() as c:
        c.execute(f"UPDATE users SET {field}={field}+? WHERE id=?", (inc, user_id))

def create_post(user_id: int, content_type: str, text: str | None, file_id: str | None) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO posts(user_id, content_type, text, file_id) VALUES(?,?,?,?)",
            (user_id, content_type, text, file_id)
        )
        return cur.lastrowid

def next_pending_post() -> Optional[sqlite3.Row]:
    with db() as c:
        return c.execute("SELECT * FROM posts WHERE status='pending' ORDER BY id ASC LIMIT 1").fetchone()

def post_by_id(pid: int) -> Optional[sqlite3.Row]:
    with db() as c:
        return c.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()

def set_post_status(pid: int, status: str, moderator_tg: int, reason: Optional[str] = None):
    with db() as c:
        c.execute("UPDATE posts SET status=?, moderator_id=?, reject_reason=? WHERE id=?",
                  (status, moderator_tg, reason, pid))

# ============== подписки/лимиты ==============
def has_active_vip(u: sqlite3.Row) -> bool:
    if not u or u["subscription_type"] != "vip":
        return False
    exp = parse_iso(u["subscription_expires"])
    if exp is None:
        return True
    return utcnow() <= exp

def ensure_daily_counter(u: sqlite3.Row) -> sqlite3.Row:
    today = today_str_local()
    if u["day_posts_date"] != today:
        with db() as c:
            c.execute("UPDATE users SET day_posts_date=?, day_posts_count=0 WHERE tg_id=?", (today, u["tg_id"]))
        return get_user(u["tg_id"])
    return u

def can_post_now(u: sqlite3.Row) -> Tuple[bool, str]:
    if has_active_vip(u):
        return True, ""
    u = ensure_daily_counter(u)
    if (u["day_posts_count"] or 0) >= 30:
        return False, "Достигнут дневной лимит 30 публикаций. Оформите 🌟 VIP для безлимита."
    return True, ""

def count_post_for_today(u: sqlite3.Row):
    today = today_str_local()
    with db() as c:
        c.execute("UPDATE users SET day_posts_count=COALESCE(day_posts_count,0)+1, day_posts_date=? WHERE tg_id=?",
                  (today, u["tg_id"]))

def check_subscription_expiry_and_notify(u: sqlite3.Row) -> sqlite3.Row:
    if not u or u["subscription_type"] != "vip":
        return u
    exp = parse_iso(u["subscription_expires"])
    if exp is None:
        return u
    if utcnow() > exp:
        with db() as c:
            c.execute("UPDATE users SET subscription_type='free', subscription_expires=NULL WHERE tg_id=?", (u["tg_id"],))
        try:
            asyncio.create_task(bot.send_message(
                u["tg_id"],
                f"❌ Ваша подписка VIP закончилась.\nДля продления напишите: {RENEW_CONTACT}"
            ))
        except Exception:
            pass
        return get_user(u["tg_id"])
    return u

# ============== форматирование ==============
def status_badge(status: str) -> Tuple[str, str]:
    mapping = {"verified": ("Проверенный", "✅"),
               "neutral": ("Нейтральный", "⚪️"),
               "scammer": ("Возможен скам", "🚫")}
    return mapping.get(status, ("Нейтральный", "⚪️"))

def sub_label(u: sqlite3.Row) -> str:
    t = u["subscription_type"] or "free"
    exp = parse_iso(u["subscription_expires"])
    if t == "vip":
        if exp is None:
            return "🌟 VIP (навсегда)"
        else:
            return f"🌟 VIP (до {exp.date().isoformat()})"
    if t == "platinum":
        return "💎 Platinum"
    if t == "extra":
        return "🚀 Extra"
    return "—"

def profile_text(u: sqlite3.Row) -> str:
    st_title, st_emoji = status_badge(u["status"])
    uname = f'@{u["username"]}' if u["username"] else "(без username)"
    return (
        f"👤 Профиль — {PROJECT_NAME}\n"
        f"ID: {u['tg_id']}\n"
        f"Юзернейм: {uname}\n"
        f"Статус: {st_emoji} {st_title}\n"
        f"Подписка: {sub_label(u)}\n"
        f"Посты: всего {u['posts_total']}, ✅ {u['posts_approved']}, ❌ {u['posts_rejected']}\n"
        f"Регистрация: {fmt_local(u['created_at'])}\n"
        f"Последний визит: {fmt_local(u['last_seen'])}"
    )

def subscription_text() -> str:
    return (
        "<b>🏂 Подписки</b>\n\n"
        "<b>Free (бесплатно)</b>\n"
        "⚪️ базовый доступ для всех пользователей\n"
        "— до 30 постов в день\n"
        "— каждое объявление проходит модерацию\n"
        "— нет специальных меток\n\n"
        "<b>🌟 VIP (стартовая премиумка)</b> — <b>150 ₽ / месяц</b>\n"
        "✨ моментальная публикация без модерации\n"
        "✨ тег «🌟 VIP публикация» в канале\n"
        "✨ безлимит на количество постов\n"
        "✨ метка в профиле: «Подписка: 🌟 VIP (до …)»\n"
        "✨ админы получают уведомление о VIP-публикации\n\n"
        "<b>💎 Platinum (средний уровень)</b> — <i>скоро доступно</i>\n"
        "💠 всё, что у VIP\n"
        "💠 закрепление поста на 24 часа\n"
        "💠 автоподнятие старого поста (1 раз в день)\n"
        "💠 приоритет в рассылках\n"
        "💠 отдельный приватный канал для Platinum\n\n"
        "<b>🚀 Extra (ультра)</b> — <i>скоро доступно</i>\n"
        "🚀 всё, что у Platinum\n"
        "🚀 персональная поддержка владельца\n"
        "🚀 аналитика постов (просмотры/CTR)\n"
        "🚀 возможность ставить реакции («🔥», «💎») на посты\n"
        "🚀 розыгрыши и бонусы только для Extra"
    )

# ============== клавиатуры ==============
def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Отправить объявление")],
            [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="ℹ️ Инфо")],
            [KeyboardButton(text="🏂 Подписка")],
        ],
        resize_keyboard=True
    )

def admin_menu_kb(owner: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📥 Новые объявления", callback_data="admin_pending")]]
    if owner:
        rows += [
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data="owner_users")],
            [InlineKeyboardButton(text="👑 Список админов", callback_data="owner_admins")],
            [InlineKeyboardButton(text="🌟 Подписка (выдать/снять)", callback_data="owner_subs")],
            [InlineKeyboardButton(text="👤 Статусы", callback_data="admin_status")],
            [InlineKeyboardButton(text="📡 Канал: установить", callback_data="admin_setchannel"),
             InlineKeyboardButton(text="🔎 Канал", callback_data="admin_getchannel")],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="owner_broadcast")],
        ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")]
    ])

def mod_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod_approve:{post_id}")],
        [InlineKeyboardButton(text="❌ Отклонить (с причиной)", callback_data=f"mod_reject:{post_id}")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")],
    ])

def users_nav_kb(page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    row = []
    if has_prev:
        row.append(InlineKeyboardButton(text="⬅️", callback_data=f"users_page:{page-1}"))
    row.append(InlineKeyboardButton(text=f"Стр. {page+1}", callback_data="noop"))
    if has_next:
        row.append(InlineKeyboardButton(text="➡️", callback_data=f"users_page:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[row, [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")]])

def admins_list_kb(admins: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows = []
    for a in admins:
        tag = a["username"] and f"@{a['username']}" or str(a["tg_id"])
        label = f"{tag} {'(владелец)' if a['is_owner'] else ''}"
        if a["tg_id"] == OWNER_ID:
            rows.append([InlineKeyboardButton(text=label, callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"owner_remove_admin:{a['tg_id']}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def subs_action_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌟 VIP 7 дней", callback_data=f"sub_vip:{tg_id}:7")],
        [InlineKeyboardButton(text="🌟 VIP 30 дней", callback_data=f"sub_vip:{tg_id}:30")],
        [InlineKeyboardButton(text="🌟 VIP 90 дней", callback_data=f"sub_vip:{tg_id}:90")],
        [InlineKeyboardButton(text="🌟 VIP 365 дней", callback_data=f"sub_vip:{tg_id}:365")],
        [InlineKeyboardButton(text="🌟 VIP навсегда", callback_data=f"sub_vip:{tg_id}:forever")],
        [InlineKeyboardButton(text="❌ Снять подписку", callback_data=f"sub_remove:{tg_id}")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")],
    ])

def gate_sub_kb() -> InlineKeyboardMarkup:
    ch = normalize_channel_id(get_cfg("channel_id") or CHANNEL_ID_ENV) or "@toweringsale"
    url = f"https://t.me/{str(ch).lstrip('@')}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Открыть канал", url=url)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]
    ])

def subs_public_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌟 Оформить VIP", url=f"https://t.me/{RENEW_CONTACT.lstrip('@')}")],
        [InlineKeyboardButton(text="👤 Проверить статус", callback_data="sub_check")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="back_main")],
    ])

# ============== BOT & dispatcher ==============
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
r = Router()
dp.include_router(r)

# ============== Middlewares ==============
async def is_subscribed(user_id: int) -> bool:
    ch = normalize_channel_id(get_cfg("channel_id") or CHANNEL_ID_ENV) or "@toweringsale"
    try:
        member = await bot.get_chat_member(ch, user_id)
        status = getattr(member, "status", None)
        return status not in ("left", "kicked")
    except Exception as e:
        print(f"[check sub] error: {e}")
        return False

class UserLifecycleMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: types.TelegramObject, data: Dict[str, Any]):
        init_db()
        if isinstance(event, (types.Message, types.CallbackQuery)):
            from_user = event.from_user
            if from_user:
                touch_user(from_user.id, from_user.username)
                u = get_user(from_user.id)
                u = check_subscription_expiry_and_notify(u)
                data["current_user"] = u
        return await handler(event, data)

class ChannelGateMiddleware(BaseMiddleware):
    """Гейт по подписке: везде. Исключения: владелец и служебные check_sub/back_main для UX."""
    async def __call__(self, handler: Callable, event: types.TelegramObject, data: Dict[str, Any]):
        if isinstance(event, types.CallbackQuery) and event.data in ("check_sub", "back_main"):
            return await handler(event, data)
        user: sqlite3.Row = data.get("current_user")
        if user and user["tg_id"] == OWNER_ID:
            return await handler(event, data)
        if isinstance(event, (types.Message, types.CallbackQuery)):
            uid = event.from_user.id
            if not await is_subscribed(uid):
                text = (
                    f"🚫 Для использования бота подпишитесь на канал {PROJECT_NAME}\n"
                    f"Затем нажмите «Проверить подписку»."
                )
                if isinstance(event, types.CallbackQuery):
                    await event.message.answer(text, reply_markup=gate_sub_kb())
                    await event.answer()
                else:
                    await event.answer(text, reply_markup=gate_sub_kb())
                return
        return await handler(event, data)

dp.message.middleware(UserLifecycleMiddleware())
dp.callback_query.middleware(UserLifecycleMiddleware())
dp.message.middleware(ChannelGateMiddleware())
dp.callback_query.middleware(ChannelGateMiddleware())

# ============== FSM ==============
class SubmitStates(StatesGroup):
    waiting_post = State()

class RejectStates(StatesGroup):
    waiting_reason = State()

class AdminStates(StatesGroup):
    waiting_channel = State()
    waiting_add_admin = State()
    waiting_remove_admin = State()
    waiting_status_target = State()
    waiting_broadcast = State()
    waiting_subs_target = State()

# ============== HANDLERS ==============

# --- Проверка подписки и возврат в меню ---
@r.callback_query(F.data == "check_sub")
async def cb_check_sub(cq: types.CallbackQuery):
    ok = await is_subscribed(cq.from_user.id)
    if ok:
        await cq.message.answer("✅ Подписка найдена. Можете пользоваться ботом.", reply_markup=main_menu_kb())
    else:
        await cq.message.answer(f"❌ Вы всё ещё не подписаны на {PROJECT_NAME}.", reply_markup=gate_sub_kb())
    await cq.answer()

@r.callback_query(F.data == "back_main")
async def cb_back_main(cq: types.CallbackQuery):
    await cq.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await cq.answer()

# --- Старт/меню ---
@r.message(CommandStart())
async def start(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    await m.answer(
        f"Привет! Это бот проекта «{PROJECT_NAME}».\nВыберите действие:",
        reply_markup=main_menu_kb()
    )

@r.message(F.text == "ℹ️ Инфо")
async def info(m: types.Message):
    await m.answer(
        f"<b>{PROJECT_NAME}</b>\n"
        f"• Публикуйте объявления\n"
        f"• 🌟 VIP — моментальная публикация без модерации\n"
        f"• Free — до 30 постов в день\n"
        f"• Для VIP продления: {RENEW_CONTACT}"
    )

@r.message(F.text == "👤 Мой профиль")
async def my_profile(m: types.Message, current_user: sqlite3.Row):
    await m.answer(profile_text(current_user))

# --- Вкладка «Подписка» ---
@r.message(F.text == "🏂 Подписка")
async def open_subscription(m: types.Message):
    await m.answer(subscription_text(), reply_markup=subs_public_kb())

@r.callback_query(F.data == "sub_check")
async def cb_sub_check(cq: types.CallbackQuery, current_user: sqlite3.Row):
    await cq.message.answer(f"Ваш текущий статус подписки:\n<b>{sub_label(current_user)}</b>")
    await cq.answer()

# --- Отправка объявления ---
@r.message(F.text == "📤 Отправить объявление")
async def submit_start(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    await state.set_state(SubmitStates.waiting_post)
    await m.answer("Пришлите текст/фото/видео/документ одним сообщением.\nОтмена — /cancel")

@r.message(Command("cancel"))
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Действие отменено.", reply_markup=main_menu_kb())

async def notify_admins(post_id: int, author: sqlite3.Row, ctype: str, text: str | None, file_id: str | None, vip_published: bool = False):
    cap = ("📥 Новое объявление" if not vip_published else "🌟 VIP публикация (уже в канале)") + f" #{post_id}\n{profile_text(author)}"
    kb = None if vip_published else mod_kb(post_id)
    with db() as c:
        admins = c.execute("SELECT tg_id FROM users WHERE is_admin=1 OR is_owner=1").fetchall()
    for a in admins:
        tg = a["tg_id"]
        try:
            if ctype == "text":
                await bot.send_message(tg, f"{cap}\n\n{text or ''}", reply_markup=kb)
            elif ctype == "photo":
                await bot.send_photo(tg, file_id, caption=f"{cap}\n\n{text or ''}", reply_markup=kb)
            elif ctype == "video":
                await bot.send_video(tg, file_id, caption=f"{cap}\n\n{text or ''}", reply_markup=kb)
            elif ctype == "document":
                await bot.send_document(tg, file_id, caption=f"{cap}\n\n{text or ''}", reply_markup=kb)
        except Exception as e:
            print(f"[notify_admins] to {tg} failed: {e}")

async def handle_post(m: types.Message, ctype: str, text: str | None, file_id: str | None, state: FSMContext, current_user: sqlite3.Row):
    await state.clear()
    u = ensure_daily_counter(current_user)
    # лимиты
    ok, reason = can_post_now(u)
    if not ok:
        await m.answer(f"🚫 {reason}")
        return

    # VIP: сразу в канал
    if has_active_vip(u):
        try:
            await publish(u, ctype, text, file_id)
            update_user_stats(u["id"], "posts_total", 1)
            update_user_stats(u["id"], "posts_approved", 1)
            await m.answer("🌟 Ваш пост опубликован (VIP).")
            await notify_admins(0, u, ctype, text, file_id, vip_published=True)
        except Exception as e:
            await m.answer(f"⚠️ Ошибка публикации: {e}")
        return

    # free: сохраняем и на модерацию
    pid = create_post(u["id"], ctype, text, file_id)
    count_post_for_today(u)
    update_user_stats(u["id"], "posts_total", 1)
    await m.answer("Спасибо! Ваше объявление отправлено на модерацию.")
    await notify_admins(pid, u, ctype, text, file_id, vip_published=False)

@r.message(SubmitStates.waiting_post, F.text)
async def post_text(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    await handle_post(m, "text", m.text, None, state, current_user)

@r.message(SubmitStates.waiting_post, F.photo)
async def post_photo(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    await handle_post(m, "photo", m.caption, m.photo[-1].file_id, state, current_user)

@r.message(SubmitStates.waiting_post, F.video)
async def post_video(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    await handle_post(m, "video", m.caption, m.video.file_id, state, current_user)

@r.message(SubmitStates.waiting_post, F.document)
async def post_document(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    await handle_post(m, "document", m.caption, m.document.file_id, state, current_user)

# --- Публикация в канал ---
async def publish(author: sqlite3.Row, ctype: str, text: str | None, file_id: str | None, target: Optional[int | str] = None):
    dest_raw = target or get_cfg("channel_id") or CHANNEL_ID_ENV or OWNER_ID
    dest = normalize_channel_id(dest_raw)
    cap_full = post_caption(author, text)

    if ctype == "text":
        for chunk in chunk_text(cap_full, 4096):
            await bot.send_message(dest, chunk)
        return
    cap_short, rest = split_caption(cap_full, 1024)
    if ctype == "photo":
        await bot.send_photo(dest, file_id, caption=cap_short)
    elif ctype == "video":
        await bot.send_video(dest, file_id, caption=cap_short)
    elif ctype == "document":
        await bot.send_document(dest, file_id, caption=cap_short)
    if rest:
        for chunk in chunk_text(rest or "", 4096):
            await bot.send_message(dest, chunk)

def post_caption(author: sqlite3.Row, text: Optional[str]) -> str:
    st_title, st_emoji = status_badge(author["status"])
    uname = f'@{author["username"]}' if author["username"] else f"id:{author['tg_id']}"
    vip_tag = "🌟 VIP публикация\n" if has_active_vip(author) else ""
    header = f"{vip_tag}Автор: {uname}\nСтатус автора: {st_emoji} {st_title}\n— — —"
    return f"{header}\n{text}" if text else header

def chunk_text(text: str, limit: int) -> Iterable[str]:
    if not text:
        return []
    out = []
    buf = text
    while len(buf) > limit:
        cut = buf.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        out.append(buf[:cut])
        buf = buf[cut:].lstrip("\n")
    if buf:
        out.append(buf)
    return out

def split_caption(text: str, limit: int = 1024) -> Tuple[str, Optional[str]]:
    if text is None:
        return "", None
    if len(text) <= limit:
        return text, None
    return text[:limit], text[limit:]

# --- Админка ---
def is_admin(u: sqlite3.Row) -> bool:
    return bool(u and (u["is_admin"] or u["is_owner"]))

def is_owner(u: sqlite3.Row) -> bool:
    return bool(u and u["is_owner"])

@r.message(Command("admin"))
async def admin_panel(m: types.Message, current_user: sqlite3.Row):
    if not is_admin(current_user):
        return await m.answer("Нет доступа")
    await m.answer("Админ-панель:", reply_markup=admin_menu_kb(owner=is_owner(current_user)))

@r.callback_query(F.data == "admin_back")
async def admin_back(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    await state.clear()
    await cq.message.answer("Админ-панель:", reply_markup=admin_menu_kb(owner=is_owner(current_user)))
    await cq.answer()

@r.callback_query(F.data == "back_admin")
async def back_admin(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    await state.clear()
    await cq.message.answer("Админ-панель:", reply_markup=admin_menu_kb(owner=is_owner(current_user)))
    await cq.answer()

# --- Модерация ---
@r.callback_query(F.data == "admin_pending")
async def admin_pending(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_admin(current_user):
        return await cq.answer("Нет доступа", show_alert=True)
    post = next_pending_post()
    if not post:
        await cq.message.answer("Новых объявлений нет.", reply_markup=back_admin_kb()); return await cq.answer()
    author = user_by_id(post["user_id"])
    try:
        if post["content_type"] == "text":
            await cq.message.answer(post_caption(author, post["text"]), reply_markup=mod_kb(post["id"]))
        elif post["content_type"] == "photo":
            await cq.message.answer_photo(post["file_id"], caption=post_caption(author, post["text"]), reply_markup=mod_kb(post["id"]))
        elif post["content_type"] == "video":
            await cq.message.answer_video(post["file_id"], caption=post_caption(author, post["text"]), reply_markup=mod_kb(post["id"]))
        elif post["content_type"] == "document":
            await cq.message.answer_document(post["file_id"], caption=post_caption(author, post["text"]), reply_markup=mod_kb(post["id"]))
    finally:
        await cq.answer()

@r.callback_query(F.data.startswith("mod_approve:"))
async def mod_approve(cq: types.CallbackQuery):
    await cq.answer("Публикую…")
    pid = int(cq.data.split(":")[1])
    post = post_by_id(pid)
    if not post:
        return await cq.message.answer("⚠️ Уже обработано", reply_markup=back_admin_kb())
    author = user_by_id(post["user_id"])
    try:
        await publish(author, post["content_type"], post["text"], post["file_id"])
        set_post_status(pid, "approved", cq.from_user.id)
        update_user_stats(author["id"], "posts_approved", 1)
        try:
            await bot.send_message(author["tg_id"], f"✅ Ваш пост #{pid} опубликован в канале.")
        except Exception:
            pass
        await cq.message.answer("✅ Опубликовано.", reply_markup=back_admin_kb())
    except Exception as e:
        try:
            await publish(author, post["content_type"], post["text"], post["file_id"], target=OWNER_ID)
            await cq.message.answer(f"⚠️ В канал не удалось: {e}\nОтправил владельцу.", reply_markup=back_admin_kb())
        except Exception as e2:
            await cq.message.answer(f"❌ Ошибка публикации: {e2}", reply_markup=back_admin_kb())

@r.callback_query(F.data.startswith("mod_reject:"))
async def mod_reject(cq: types.CallbackQuery, state: FSMContext):
    pid = int(cq.data.split(":")[1])
    post = post_by_id(pid)
    if not post:
        return await cq.answer("Уже обработано")
    await state.set_state(RejectStates.waiting_reason)
    await state.update_data(pid=pid)
    await cq.message.answer("Введите причину отклонения:", reply_markup=back_admin_kb())
    await cq.answer()

@r.message(StateFilter(RejectStates.waiting_reason))
async def reject_reason(m: types.Message, state: FSMContext):
    data = await state.get_data()
    pid = data.get("pid")
    reason = (m.text or "").strip()
    post = post_by_id(pid)
    if post:
        author = user_by_id(post["user_id"])
        set_post_status(pid, "rejected", m.from_user.id, reason)
        update_user_stats(author["id"], "posts_rejected", 1)
        try:
            await bot.send_message(author["tg_id"], f"❌ Ваш пост #{pid} отклонён.\nПричина: {reason}")
        except Exception as e:
            print(f"[notify author reject] fail: {e}")
    await state.clear()
    await m.answer("Отклонение зафиксировано.", reply_markup=back_admin_kb())

# --- OWNER: список пользователей (пагинация) ---
PAGE_SIZE = 12

def fetch_users_page(page: int) -> Tuple[List[sqlite3.Row], bool]:
    offset = page * PAGE_SIZE
    with db() as c:
        items = c.execute(
            "SELECT * FROM users ORDER BY COALESCE(last_seen, created_at) DESC LIMIT ? OFFSET ?",
            (PAGE_SIZE+1, offset)
        ).fetchall()
    has_next = len(items) > PAGE_SIZE
    return (items[:PAGE_SIZE], has_next)

def render_users_list(items: List[sqlite3.Row]) -> str:
    lines = ["👥 Список пользователей (последние активные):"]
    for u in items:
        uname = f"@{u['username']}" if u["username"] else "—"
        sub = sub_label(u)
        st_title, st_emoji = status_badge(u["status"])
        lines.append(
            f"— ID:{u['tg_id']} | {uname}\n"
            f"  Подписка: {sub} | Статус: {st_emoji} {st_title}\n"
            f"  Посты: всего {u['posts_total']}, ✅ {u['posts_approved']}, ❌ {u['posts_rejected']}\n"
            f"  Рег: {fmt_local(u['created_at'])} | Визит: {fmt_local(u['last_seen'])}"
        )
    if not items:
        lines.append("Пока пусто.")
    return "\n".join(lines)

@r.callback_query(F.data == "owner_users")
async def owner_users(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    page = 0
    items, has_next = fetch_users_page(page)
    await cq.message.answer(render_users_list(items), reply_markup=users_nav_kb(page, has_prev=False, has_next=has_next))
    await cq.answer()

@r.callback_query(F.data.startswith("users_page:"))
async def users_page(cq: types.CallbackQuery):
    page = int(cq.data.split(":")[1])
    items, has_next = fetch_users_page(page)
    has_prev = page > 0
    await cq.message.edit_text(render_users_list(items), reply_markup=users_nav_kb(page, has_prev, has_next))
    await cq.answer()

# --- OWNER: список админов и снятие ---
@r.callback_query(F.data == "owner_admins")
async def owner_admins(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    with db() as c:
        admins = c.execute("SELECT * FROM users WHERE is_admin=1 OR is_owner=1 ORDER BY is_owner DESC, tg_id").fetchall()
    await cq.message.answer("👑 Администраторы:", reply_markup=admins_list_kb(admins))
    await cq.answer()

@r.callback_query(F.data.startswith("owner_remove_admin:"))
async def owner_remove_admin_btn(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    tg_id = int(cq.data.split(":")[1])
    if tg_id == OWNER_ID:
        return await cq.answer("Нельзя снять владельца", show_alert=True)
    set_user_flag(tg_id, "is_admin", 0)
    try:
        await bot.send_message(tg_id, "⚠️ С вас сняты права администратора.")
    except Exception:
        pass
    await cq.message.answer(f"С пользователя {tg_id} сняты права админа.", reply_markup=back_admin_kb())
    await cq.answer()

# --- OWNER: назначение админов по ID или @username ---
@r.callback_query(F.data == "admin_admins")
async def admins_menu_legacy(cq: types.CallbackQuery, current_user: sqlite3.Row, state: FSMContext):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await owner_admins(cq, current_user)
    await state.set_state(AdminStates.waiting_add_admin)
    await cq.message.answer("Пришлите TG ID или @username, чтобы выдать админа.", reply_markup=back_admin_kb())
    await cq.answer()

@r.message(AdminStates.waiting_add_admin, F.text)
async def owner_add_admin_id_or_username(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await m.answer("Только владелец", reply_markup=back_admin_kb())
    raw = m.text.strip()
    target = get_user(int(raw)) if raw.isdigit() else user_by_username(raw)
    if not target:
        return await m.answer("Пользователь не найден в базе (нужно, чтобы он написал боту хотя бы раз).", reply_markup=back_admin_kb())
    set_user_flag(target["tg_id"], "is_admin", 1)
    await state.clear()
    await m.answer(f"Готово. {target['tg_id']} теперь админ.", reply_markup=back_admin_kb())
    try:
        await bot.send_message(target["tg_id"], "👑 Вам выданы права администратора.")
    except Exception:
        pass

# --- OWNER: статусы (ID или @username) ---
@r.callback_query(F.data == "admin_status")
async def admin_status(cq: types.CallbackQuery, current_user: sqlite3.Row, state: FSMContext):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_status_target)
    await cq.message.answer("Пришлите TG ID или @username пользователя для управления статусом.", reply_markup=back_admin_kb())
    await cq.answer()

def status_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Проверенный", callback_data=f"setstatus:{tg_id}:verified")],
        [InlineKeyboardButton(text="⚪️ Нейтральный", callback_data=f"setstatus:{tg_id}:neutral")],
        [InlineKeyboardButton(text="🚫 Скаммер", callback_data=f"setstatus:{tg_id}:scammer")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")],
    ])

@r.message(AdminStates.waiting_status_target, F.text)
async def admin_status_user(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await m.answer("Только владелец", reply_markup=back_admin_kb())
    raw = m.text.strip()
    target = get_user(int(raw)) if raw.isdigit() else user_by_username(raw)
    if not target:
        return await m.answer("Пользователь не найден.", reply_markup=back_admin_kb())
    await state.clear()
    await m.answer(profile_text(target), reply_markup=status_kb(target["tg_id"]))

@r.callback_query(F.data.startswith("setstatus:"))
async def setstatus(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    _, tg_id, status = cq.data.split(":")
    set_user_flag(int(tg_id), "status", status)
    u = get_user(int(tg_id))
    await cq.message.edit_text(profile_text(u), reply_markup=status_kb(int(tg_id)))
    await cq.answer("Статус обновлён")

# --- OWNER: Подписки (выдать/снять) ---
@r.callback_query(F.data == "owner_subs")
async def owner_subs(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_subs_target)
    await cq.message.answer("Пришлите TG ID или @username пользователя для управления подпиской.", reply_markup=back_admin_kb())
    await cq.answer()

@r.message(AdminStates.waiting_subs_target, F.text)
async def owner_subs_target(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await m.answer("Только владелец", reply_markup=back_admin_kb())
    raw = m.text.strip()
    target = get_user(int(raw)) if raw.isdigit() else user_by_username(raw)
    if not target:
        return await m.answer("Пользователь не найден.", reply_markup=back_admin_kb())
    await state.clear()
    await m.answer(profile_text(target), reply_markup=subs_action_kb(target["tg_id"]))

def vip_expiry_from_days(days: str) -> Optional[str]:
    if days == "forever":
        return None
    try:
        d = int(days)
    except Exception:
        d = 30
    exp = utcnow() + timedelta(days=d)
    return exp.isoformat(timespec="seconds")

@r.callback_query(F.data.startswith("sub_vip:"))
async def sub_vip(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    _, tg_id, days = cq.data.split(":")
    tg_id = int(tg_id)
    exp = vip_expiry_from_days(days)
    with db() as c:
        c.execute("UPDATE users SET subscription_type='vip', subscription_expires=? WHERE tg_id=?", (exp, tg_id))
    u = get_user(tg_id)
    await cq.message.edit_text(profile_text(u), reply_markup=subs_action_kb(tg_id))
    await cq.answer("VIP выдан")
    try:
        if exp is None:
            await bot.send_message(tg_id, "🌟 Вам выдана подписка VIP (навсегда).")
        else:
            d = parse_iso(exp).date().isoformat()
            await bot.send_message(tg_id, f"🌟 Вам выдана подписка VIP (до {d}).")
    except Exception:
        pass

@r.callback_query(F.data.startswith("sub_remove:"))
async def sub_remove(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    _, tg_id = cq.data.split(":")
    tg_id = int(tg_id)
    with db() as c:
        c.execute("UPDATE users SET subscription_type='free', subscription_expires=NULL WHERE tg_id=?", (tg_id,))
    u = get_user(tg_id)
    await cq.message.edit_text(profile_text(u), reply_markup=subs_action_kb(tg_id))
    await cq.answer("Подписка снята")
    try:
        await bot.send_message(tg_id, "❌ Ваша подписка VIP снята.")
    except Exception:
        pass

# --- Канал ---
@r.callback_query(F.data == "admin_setchannel")
async def admin_setchannel(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_channel)
    await cq.message.answer("Пришлите @username, ссылку t.me/… или ID (-100…). Бот должен быть админом канала.", reply_markup=back_admin_kb())
    await cq.answer()

@r.message(AdminStates.waiting_channel, F.text)
async def save_channel(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await m.answer("Только владелец", reply_markup=back_admin_kb())
    raw = m.text.strip()
    normalized = normalize_channel_id(raw)
    set_cfg("channel_id", str(normalized))
    await state.clear()
    await m.answer(f"Канал сохранён: {normalized}", reply_markup=back_admin_kb())

@r.callback_query(F.data == "admin_getchannel")
async def admin_getchannel(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_admin(current_user):
        return await cq.answer("Нет доступа", show_alert=True)
    ch = normalize_channel_id(get_cfg("channel_id") or CHANNEL_ID_ENV) or "(не задан)"
    await cq.message.answer(f"Текущий канал: {ch}", reply_markup=back_admin_kb())
    await cq.answer()

# --- Рассылка (owner) ---
@r.callback_query(F.data == "owner_broadcast")
async def owner_broadcast(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_broadcast)
    await cq.message.answer("Пришлите контент рассылки (текст/фото/видео/документ). Отмена — /cancel", reply_markup=back_admin_kb())
    await cq.answer()

@r.message(AdminStates.waiting_broadcast)
async def handle_broadcast(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await m.answer("Только владелец", reply_markup=back_admin_kb())
    ctype, text, file_id = None, None, None
    if m.text:
        ctype, text = "text", m.text
    elif m.photo:
        ctype, text, file_id = "photo", m.caption, m.photo[-1].file_id
    elif m.video:
        ctype, text, file_id = "video", m.caption, m.video.file_id
    elif m.document:
        ctype, text, file_id = "document", m.caption, m.document.file_id
    else:
        return await m.answer("Неподдерживаемый тип. Пришлите текст/фото/видео/документ.", reply_markup=back_admin_kb())

    await state.clear()
    await m.answer("🚀 Начинаю рассылку…", reply_markup=back_admin_kb())
    ok = 0
    total = 0
    with db() as c:
        users = c.execute("SELECT tg_id FROM users").fetchall()
    for row in users:
        total += 1
        tg = row["tg_id"]
        try:
            if ctype == "text":
                for chunk in chunk_text(text, 4096):
                    await bot.send_message(tg, chunk)
            elif ctype == "photo":
                cap_short, rest = split_caption(text or "", 1024)
                await bot.send_photo(tg, file_id, caption=cap_short)
                for chunk in chunk_text(rest or "", 4096):
                    await bot.send_message(tg, chunk)
            elif ctype == "video":
                cap_short, rest = split_caption(text or "", 1024)
                await bot.send_video(tg, file_id, caption=cap_short)
                for chunk in chunk_text(rest or "", 4096):
                    await bot.send_message(tg, chunk)
            elif ctype == "document":
                cap_short, rest = split_caption(text or "", 1024)
                await bot.send_document(tg, file_id, caption=cap_short)
                for chunk in chunk_text(rest or "", 4096):
                    await bot.send_message(tg, chunk)
            ok += 1
        except Exception as e:
            print(f"[broadcast] to {tg} failed: {e}")
        await asyncio.sleep(0.04)
    await m.answer(f"Готово. Успешно: {ok}/{total}", reply_markup=back_admin_kb())

# ============== RUN ==============
async def main():
    init_db()
    backup_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())













