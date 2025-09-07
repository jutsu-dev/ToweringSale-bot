# app.py
# Python 3.11+ , aiogram 3.7+ (3.22.0 ок)
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

# ===== helpers (простая обфускация строк) =====
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

# ===== CONFIG =====
PROJECT_NAME = os.getenv("PROJECT_NAME", "@toweringsale")
BOT_TOKEN   = _de(os.getenv("BOT_TOKEN", "8052075709:AAGD7-tH2Yq7Ipixmw21y3D1B-oWWGrq03I"))
OWNER_ID    = int(_de(os.getenv("OWNER_ID", "6089346880")))
CHANNEL_ID_ENV = _de(os.getenv("CHANNEL_ID", "@toweringsale"))
RENEW_CONTACT = "@Andrew_Allen2810"

# ===== PRICES (показываются в «Подписка → 💰 Цены») =====
PRICES_TEXT = (
    "<b>💰 Тарифы и цены</b>\n\n"
    "<b>🌟 VIP</b>\n"
    "• 7 дней — <b>50 ₽ / 25⭐</b>\n"
    "• 30 дней — <b>120 ₽ / 60⭐</b>\n"
    "• 90 дней — <b>300 ₽ / 150⭐</b>\n"
    "• 365 дней — <b>800 ₽ / 400⭐</b>\n"
    "• навсегда — <b>1500 ₽ / 750⭐</b>\n\n"
    "<b>💎 Platinum</b>\n"
    "• 30 дней — <b>250 ₽ / 125⭐</b>\n"
    "• 90 дней — <b>600 ₽ / 300⭐</b>\n"
    "• 365 дней — <b>1500 ₽ / 750⭐</b>\n"
    "• навсегда — <b>3000 ₽ / 1500⭐</b>\n\n"
    "<b>🚀 Extra</b>\n"
    "• 30 дней — <b>500 ₽ / 250⭐</b>\n"
    "• 90 дней — <b>1200 ₽ / 600⭐</b>\n"
    "• 365 дней — <b>3000 ₽ / 1500⭐</b>\n"
    "• навсегда — <b>5000 ₽ / 2500⭐</b>\n\n"
    f"Оплата и подключение — пишите: {RENEW_CONTACT}"
)

# Хранилище БД вне проекта (переживает переезды/обновления)
HOME_DIR = os.path.expanduser("~")
DB_DIR = os.getenv("DB_DIR", os.path.join(HOME_DIR, ".toweringsale"))
PRIMARY_DB_PATH = os.path.join(DB_DIR, "bot.db")
BACKUP_DIR = os.path.join(DB_DIR, "backups")
LEGACY_DB_PATH = os.path.join("data", "bot.db")
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

# перенос старой базы, если есть
if not os.path.exists(PRIMARY_DB_PATH) and os.path.exists(LEGACY_DB_PATH):
    shutil.copy2(LEGACY_DB_PATH, PRIMARY_DB_PATH)

# ===== DB =====
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
    try: conn.execute(sql)
    except Exception: pass

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
            subscription_expires TEXT,
            profile_emoji TEXT,
            profile_status TEXT,
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
            user_reminded INTEGER DEFAULT 0,
            admin_reminded INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS config(
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS admin_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_tg INTEGER,
            action TEXT,
            target_tg INTEGER,
            extra TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

        # миграции
        ensure_column(c, "users", "profile_emoji", "profile_emoji TEXT")
        ensure_column(c, "users", "profile_status", "profile_status TEXT")
        ensure_column(c, "users", "day_posts_date", "day_posts_date TEXT")
        ensure_column(c, "users", "day_posts_count", "day_posts_count INTEGER DEFAULT 0")
        ensure_column(c, "users", "subscription_type", "subscription_type TEXT DEFAULT 'free'")
        ensure_column(c, "users", "subscription_expires", "subscription_expires TEXT")
        ensure_column(c, "users", "last_seen", "last_seen TEXT")
        ensure_column(c, "users", "created_at", "created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        ensure_column(c, "posts", "reject_reason", "reject_reason TEXT")
        ensure_column(c, "posts", "moderator_id", "moderator_id INTEGER")
        ensure_column(c, "posts", "user_reminded", "user_reminded INTEGER DEFAULT 0")
        ensure_column(c, "posts", "admin_reminded", "admin_reminded INTEGER DEFAULT 0")
        ensure_column(c, "posts", "created_at", "created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        ensure_index(c, "idx_users_tg_id", "CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id)")
        ensure_index(c, "idx_posts_status", "CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status)")
        ensure_index(c, "idx_posts_user", "CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id)")
        ensure_index(c, "idx_users_username", "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")

        c.execute("UPDATE users SET is_owner=1, is_admin=1 WHERE tg_id=?", (OWNER_ID,))
        row = c.execute("SELECT value FROM config WHERE key='channel_id'").fetchone()
        if not row and CHANNEL_ID_ENV:
            c.execute("INSERT INTO config(key,value) VALUES('channel_id', ?)", (CHANNEL_ID_ENV,))

# ===== DB utils =====
def get_cfg(key: str) -> Optional[str]:
    with db() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def set_cfg(key: str, value: str):
    with db() as c:
        c.execute("INSERT INTO config(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

def normalize_channel_id(raw: str | int | None) -> str | int | None:
    if raw is None: return None
    if isinstance(raw, int): return raw
    s = str(raw).strip()
    m = re.search(r"t\.me/([A-Za-z0-9_]+)", s)
    if m: return f"@{m.group(1)}"
    if re.fullmatch(r"-100\d{5,}", s): return int(s)
    if s.startswith("@"): return s
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", s): return f"@{s}"
    return s

def utcnow() -> datetime: return datetime.now(timezone.utc)
def today_str_local() -> str: return date.today().isoformat()
def parse_iso(dt: str | None) -> Optional[datetime]:
    if not dt: return None
    try: return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except Exception: return None

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
        cur = c.execute("INSERT INTO posts(user_id, content_type, text, file_id) VALUES(?,?,?,?)",
                        (user_id, content_type, text, file_id))
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

def log_action(admin_tg: int, action: str, target_tg: Optional[int] = None, extra: Optional[str] = None):
    with db() as c:
        c.execute("INSERT INTO admin_logs(admin_tg, action, target_tg, extra) VALUES(?,?,?,?)",
                  (admin_tg, action, target_tg, extra))

# ===== подписки/лимиты/кастом =====
SUB_DEFAULT_EMOJI = {"vip": "🌟", "platinum": "💎", "extra": "🚀"}

def has_active_sub(u: sqlite3.Row, types: Tuple[str, ...]) -> bool:
    if not u or (u["subscription_type"] or "free") not in types:
        return False
    exp = parse_iso(u["subscription_expires"])
    return True if exp is None else utcnow() <= exp

def ensure_daily_counter(u: sqlite3.Row) -> sqlite3.Row:
    today = today_str_local()
    if u["day_posts_date"] != today:
        with db() as c:
            c.execute("UPDATE users SET day_posts_date=?, day_posts_count=0 WHERE tg_id=?", (today, u["tg_id"]))
        return get_user(u["tg_id"])
    return u

def can_post_now(u: sqlite3.Row) -> Tuple[bool, str]:
    if has_active_sub(u, ("vip", "platinum", "extra")):
        return True, ""
    u = ensure_daily_counter(u)
    if (u["day_posts_count"] or 0) >= 30:
        return False, "Достигнут дневной лимит 30 публикаций. Оформите подписку для безлимита."
    return True, ""

def count_post_for_today(u: sqlite3.Row):
    today = today_str_local()
    with db() as c:
        c.execute("UPDATE users SET day_posts_count=COALESCE(day_posts_count,0)+1, day_posts_date=? WHERE tg_id=?",
                  (today, u["tg_id"]))

def check_subscription_expiry_and_notify(u: sqlite3.Row) -> sqlite3.Row:
    if not u: return u
    t = (u["subscription_type"] or "free").lower()
    exp = parse_iso(u["subscription_expires"])
    if t != "free" and exp is not None and utcnow() > exp:
        with db() as c:
            c.execute("UPDATE users SET subscription_type='free', subscription_expires=NULL WHERE tg_id=?", (u["tg_id"],))
        try:
            asyncio.create_task(bot.send_message(
                u["tg_id"],
                f"❌ Ваша подписка закончилась.\nДля продления напишите: {RENEW_CONTACT}"
            ))
        except Exception: pass
        return get_user(u["tg_id"])
    return u

def sub_label(u: sqlite3.Row) -> str:
    t = (u["subscription_type"] or "free").lower()
    exp = parse_iso(u["subscription_expires"])
    if t == "free": return "—"
    base = {"vip": "🌟 VIP", "platinum": "💎 Platinum", "extra": "🚀 Extra"}.get(t, t)
    if exp is None: return f"{base} (навсегда)"
    try: d = exp.date().isoformat()
    except Exception: d = u["subscription_expires"]
    return f"{base} (до {d})"

def status_badge(status: str) -> Tuple[str, str]:
    mapping = {"verified": ("Проверенный", "✅"),
               "neutral": ("Нейтральный", "⚪️"),
               "scammer": ("Возможен скам", "🚫")}
    return mapping.get(status, ("Нейтральный", "⚪️"))

def user_emoji(u: sqlite3.Row) -> str:
    if u["profile_emoji"]:
        return u["profile_emoji"]
    t = (u["subscription_type"] or "free").lower()
    return SUB_DEFAULT_EMOJI.get(t, "")

def profile_text(u: sqlite3.Row) -> str:
    st_title, st_emoji = status_badge(u["status"])
    uname = f'@{u["username"]}' if u["username"] else "(без username)"
    badge = user_emoji(u)
    extra_status = ""
    if (u["subscription_type"] or "free").lower() in ("platinum", "extra"):
        if u["profile_status"]:
            extra_status = f"\nСтатус профиля: {u['profile_status']}"
    return (
        f"👤 Профиль — {PROJECT_NAME}\n"
        f"ID: {u['tg_id']}\n"
        f"Юзернейм: {uname}\n"
        f"Статус: {st_emoji} {st_title}\n"
        f"Подписка: {sub_label(u)}\n"
        f"Эмодзи профиля: {badge or '—'}{extra_status}\n"
        f"Посты: всего {u['posts_total']}, ✅ {u['posts_approved']}, ❌ {u['posts_rejected']}\n"
        f"Регистрация: {u['created_at']}\n"
        f"Последний визит: {u['last_seen'] or '—'}"
    )

# ===== форматирование постов =====
def chunk_text(text: str, limit: int) -> Iterable[str]:
    if not text: return []
    out, buf = [], text
    while len(buf) > limit:
        cut = buf.rfind("\n", 0, limit)
        if cut == -1: cut = limit
        out.append(buf[:cut]); buf = buf[cut:].lstrip("\n")
    if buf: out.append(buf)
    return out

def split_caption(text: str, limit: int = 1024) -> Tuple[str, Optional[str]]:
    if text is None: return "", None
    if len(text) <= limit: return text, None
    return text[:limit], text[limit:]

def subscription_footer(u: sqlite3.Row) -> str:
    t = (u["subscription_type"] or "free").lower()
    if t == "vip": return "🌟 Публикация VIP"
    if t == "platinum": return "💎 Публикация Platinum"
    if t == "extra":
        trust = "✅ Trust-бейдж: Проверенный продавец"
        return f"🚀 Публикация Extra\n{trust}"
    return ""

def post_caption(author: sqlite3.Row, text: Optional[str]) -> str:
    st_title, st_emoji = status_badge(author["status"])
    uname = f'@{author["username"]}' if author["username"] else f"id:{author['tg_id']}"
    badge = user_emoji(author)
    header = f"{badge+' ' if badge else ''}Автор: {uname}\nСтатус автора: {st_emoji} {st_title}\n— — —"
    footer = subscription_footer(author)
    body = (text or "").strip()
    return "\n".join(x for x in [header, body, footer] if x)

# ===== BOT =====
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
r = Router()
dp.include_router(r)

# ===== Middlewares =====
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
            fu = event.from_user
            if fu:
                touch_user(fu.id, fu.username)
                u = get_user(fu.id)
                u = check_subscription_expiry_and_notify(u)
                data["current_user"] = u
        return await handler(event, data)

class ChannelGateMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: types.TelegramObject, data: Dict[str, Any]):
        if isinstance(event, types.CallbackQuery) and event.data == "check_sub":
            return await handler(event, data)
        user: sqlite3.Row = data.get("current_user")
        if user and user["tg_id"] == OWNER_ID:
            return await handler(event, data)
        if isinstance(event, (types.Message, types.CallbackQuery)):
            uid = event.from_user.id
            if not await is_subscribed(uid):
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Открыть канал",
                                          url=f"https://t.me/{str((get_cfg('channel_id') or CHANNEL_ID_ENV)).lstrip('@')}")],
                    [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]
                ])
                if isinstance(event, types.CallbackQuery):
                    await event.message.answer(
                        f"🚫 Для использования бота подпишитесь на канал {PROJECT_NAME}\nЗатем нажмите «Проверить подписку».",
                        reply_markup=kb
                    )
                    await event.answer()
                else:
                    await event.answer(
                        f"🚫 Для использования бота подпишитесь на канал {PROJECT_NAME}\nЗатем нажмите «Проверить подписку».",
                        reply_markup=kb
                    )
                return
        return await handler(event, data)

dp.message.middleware(UserLifecycleMiddleware())
dp.callback_query.middleware(UserLifecycleMiddleware())
dp.message.middleware(ChannelGateMiddleware())
dp.callback_query.middleware(ChannelGateMiddleware())

# ===== FSM =====
class SubmitStates(StatesGroup):
    waiting_post = State()

class RejectStates(StatesGroup):
    waiting_reason = State()

class AdminStates(StatesGroup):
    waiting_channel = State()
    waiting_add_admin = State()
    waiting_status_target = State()
    waiting_broadcast = State()
    waiting_subs_target = State()
    waiting_import_db = State()
    waiting_user_search = State()

class CustomStates(StatesGroup):
    waiting_set_emoji = State()
    waiting_set_status = State()

class PublicCheck(StatesGroup):
    waiting_query = State()

# ===== Keyboards =====
def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Отправить объявление")],
            [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="ℹ️ Инфо")],
            [KeyboardButton(text="🏂 Подписка"), KeyboardButton(text="🔎 Проверка пользователя")],
        ],
        resize_keyboard=True
    )

def admin_menu_kb(owner: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📥 Новые объявления", callback_data="admin_pending")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
    ]
    if owner:
        rows += [
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="owner_users"),
             InlineKeyboardButton(text="🔎 Поиск", callback_data="owner_search")],
            [InlineKeyboardButton(text="👑 Список админов", callback_data="owner_admins")],
            [InlineKeyboardButton(text="🌟 Подписка (выдать/снять)", callback_data="owner_subs")],
            [InlineKeyboardButton(text="👤 Статусы", callback_data="admin_status")],
            [InlineKeyboardButton(text="📡 Канал: установить", callback_data="admin_setchannel"),
             InlineKeyboardButton(text="🔎 Канал", callback_data="admin_getchannel")],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="owner_broadcast")],
            [InlineKeyboardButton(text="📤 Экспорт БД", callback_data="owner_export_db"),
             InlineKeyboardButton(text="📥 Импорт БД", callback_data="owner_import_db")],
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

def subs_action_select_type_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌟 Выдать VIP", callback_data=f"grant:vip:{tg_id}")],
        [InlineKeyboardButton(text="💎 Выдать Platinum", callback_data=f"grant:platinum:{tg_id}")],
        [InlineKeyboardButton(text="🚀 Выдать Extra", callback_data=f"grant:extra:{tg_id}")],
        [InlineKeyboardButton(text="❌ Снять подписку", callback_data=f"sub_remove:{tg_id}")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")],
    ])

def subs_action_durations_kb(sub_type: str, tg_id: int) -> InlineKeyboardMarkup:
    rows = []
    if sub_type == "vip":
        rows += [[InlineKeyboardButton(text="7 дней", callback_data=f"grant_dur:vip:{tg_id}:7")]]
    # общие периоды
    rows += [
        [InlineKeyboardButton(text="30 дней", callback_data=f"grant_dur:{sub_type}:{tg_id}:30")],
        [InlineKeyboardButton(text="90 дней", callback_data=f"grant_dur:{sub_type}:{tg_id}:90")],
        [InlineKeyboardButton(text="365 дней", callback_data=f"grant_dur:{sub_type}:{tg_id}:365")],
        [InlineKeyboardButton(text="Навсегда", callback_data=f"grant_dur:{sub_type}:{tg_id}:forever")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"owner_subs_for:{tg_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def preview_subs_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌟 Превью VIP", callback_data="pv_vip")],
        [InlineKeyboardButton(text="💎 Превью Platinum", callback_data="pv_platinum")],
        [InlineKeyboardButton(text="🚀 Превью Extra", callback_data="pv_extra")],
        [InlineKeyboardButton(text="💰 Цены", callback_data="pv_prices")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="pv_back")],
    ])

def buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Купить / Продлить", url=f"https://t.me/{RENEW_CONTACT.lstrip('@')}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="pv_back")]
    ])

def broadcast_audience_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Всем", callback_data="bc_aud:all")],
        [InlineKeyboardButton(text="🌟 Только VIP+", callback_data="bc_aud:paid")],
        [InlineKeyboardButton(text="🆓 Только Free", callback_data="bc_aud:free")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="back_admin")],
    ])

# ===== Handlers =====
@r.callback_query(F.data == "noop")
async def noop(cq: types.CallbackQuery):
    await cq.answer()

@r.callback_query(F.data == "check_sub")
async def cb_check_sub(cq: types.CallbackQuery):
    ok = await is_subscribed(cq.from_user.id)
    if ok:
        await cq.message.answer("✅ Подписка на канал найдена. Можете пользоваться ботом.", reply_markup=main_menu_kb())
    else:
        await cq.message.answer(f"❌ Вы всё ещё не подписаны на {PROJECT_NAME}.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Открыть канал", url=f"https://t.me/{str((get_cfg('channel_id') or CHANNEL_ID_ENV)).lstrip('@')}")],
            [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]
        ]))
    await cq.answer()

@r.message(CommandStart())
async def start(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    await state.clear()
    await m.answer(
        f"Привет! Это бот проекта «{PROJECT_NAME}». Выберите действие:",
        reply_markup=main_menu_kb()
    )

@r.message(F.text == "ℹ️ Инфо")
async def faq(m: types.Message):
    await m.answer(
        f"<b>{PROJECT_NAME} — FAQ</b>\n"
        f"• 📤 Отправьте объявление через кнопку — админы проверят и опубликуют.\n"
        f"• 🌟 VIP/Platinum/Extra — мгновенная публикация и привилегии.\n"
        f"• 🆓 Free — до 30 постов/день.\n"
        f"• Подписка закончилась? Пишите: {RENEW_CONTACT}\n"
        f"• Использовать бот могут только подписчики канала {PROJECT_NAME}."
    )

@r.message(F.text == "👤 Мой профиль")
async def my_profile(m: types.Message, current_user: sqlite3.Row):
    await m.answer(profile_text(current_user), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Сменить эмодзи", callback_data="me_set_emoji")],
        *([[InlineKeyboardButton(text="✏️ Сменить статус профиля", callback_data="me_set_status")]] if (current_user["subscription_type"] or "free") in ("platinum","extra") else []),
        [InlineKeyboardButton(text="🔙 Назад", callback_data="me_back")]
    ]))

@r.callback_query(F.data == "me_back")
async def me_back(cq: types.CallbackQuery, current_user: sqlite3.Row):
    await cq.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await cq.answer()

@r.callback_query(F.data == "me_set_emoji")
async def me_set_emoji(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    await state.set_state(CustomStates.waiting_set_emoji)
    t = (current_user["subscription_type"] or "free").lower()
    base = ["🌟","🔥","⚡","🎯"] if t=="vip" else (["💎","👑","🏆","🎨"] if t=="platinum" else ["🚀","🔥","🛡️","✨"])
    await cq.message.answer("Выберите одно эмодзи (или пришлите своё одним символом):\n" + " ".join(base), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="me_back")]]))
    await cq.answer()

@r.message(CustomStates.waiting_set_emoji, F.text)
async def handle_set_emoji(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    val = (m.text or "").strip()
    if len(val) > 2:
        return await m.answer("Пришлите ровно 1 эмодзи.", reply_markup=main_menu_kb())
    set_user_flag(current_user["tg_id"], "profile_emoji", val)
    await state.clear()
    await m.answer("Эмодзи обновлено.\n\n"+profile_text(get_user(current_user["tg_id"])), reply_markup=main_menu_kb())

@r.callback_query(F.data == "me_set_status")
async def me_set_status(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    t = (current_user["subscription_type"] or "free").lower()
    if t == "platinum":
        await state.set_state(CustomStates.waiting_set_status)
        await cq.message.answer("Выберите статус:\n• 💎 Премиум\n• 💎 Профи продавец\n• 💎 Надёжный\n\nИли пришлите свой (до 20 символов).", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="me_back")]]))
    elif t == "extra":
        await state.set_state(CustomStates.waiting_set_status)
        await cq.message.answer("Пришлите свой статус (до 20 символов).", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="me_back")]]))
    else:
        await cq.message.answer("Статус доступен с подписки Platinum и выше.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="me_back")]]))
    await cq.answer()

@r.message(CustomStates.waiting_set_status, F.text)
async def handle_set_status(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    txt = (m.text or "").strip()
    if len(txt) > 20:
        return await m.answer("Максимум 20 символов.", reply_markup=main_menu_kb())
    set_user_flag(current_user["tg_id"], "profile_status", txt)
    await state.clear()
    await m.answer("Статус обновлён.\n\n"+profile_text(get_user(current_user["tg_id"])), reply_markup=main_menu_kb())

# отправка объявления
@r.message(F.text == "📤 Отправить объявление")
async def submit_start(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    await state.set_state(SubmitStates.waiting_post)
    await m.answer("Пришлите текст/фото/видео/документ одним сообщением.\nОтмена — /cancel")

@r.message(Command("cancel"))
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Действие отменено.", reply_markup=main_menu_kb())

async def notify_admins(post_id: int, author: sqlite3.Row, ctype: str, text: str | None, file_id: str | None, vip_published: bool = False):
    cap = ("📥 Новое объявление" if not vip_published else "🌟 Платная публикация (уже в канале)") + f" #{post_id}\n{profile_text(author)}"
    kb = None if vip_published else InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod_approve:{post_id}")],
        [InlineKeyboardButton(text="❌ Отклонить (с причиной)", callback_data=f"mod_reject:{post_id}")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")],
    ])
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
    ok, reason = can_post_now(u)
    if not ok:
        await m.answer(f"🚫 {reason}")
        return

    if has_active_sub(u, ("vip","platinum","extra")):
        try:
            await publish(u, ctype, text, file_id)
            update_user_stats(u["id"], "posts_total", 1)
            update_user_stats(u["id"], "posts_approved", 1)
            await m.answer("✅ Ваш пост опубликован.")
            await notify_admins(0, u, ctype, text, file_id, vip_published=True)
        except Exception as e:
            await m.answer(f"⚠️ Ошибка публикации: {e}")
        return

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

# публикация в канал
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

# ===== /admin =====
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

# модерация
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
async def mod_approve(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_admin(current_user):
        return await cq.answer("Нет доступа", show_alert=True)
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
        log_action(cq.from_user.id, "approve_post", author["tg_id"], f"post_id={pid}")
        try:
            await bot.send_message(author["tg_id"], f"✅ Ваш пост #{pid} опубликован в канале.")
        except Exception: pass
        await cq.message.answer("✅ Опубликовано.", reply_markup=back_admin_kb())
    except Exception as e:
        await cq.message.answer(f"❌ Ошибка публикации: {e}", reply_markup=back_admin_kb())

@r.callback_query(F.data.startswith("mod_reject:"))
async def mod_reject(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_admin(current_user):
        return await cq.answer("Нет доступа", show_alert=True)
    pid = int(cq.data.split(":")[1])
    post = post_by_id(pid)
    if not post: return await cq.answer("Уже обработано")
    await state.set_state(RejectStates.waiting_reason)
    await state.update_data(pid=pid)
    await cq.message.answer("Введите причину отклонения:", reply_markup=back_admin_kb())
    await cq.answer()

@r.message(StateFilter(RejectStates.waiting_reason))
async def reject_reason(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    data = await state.get_data()
    pid = int(data.get("pid"))
    reason = (m.text or "").strip()
    post = post_by_id(pid)
    if post:
        author = user_by_id(post["user_id"])
        set_post_status(pid, "rejected", m.from_user.id, reason)
        update_user_stats(author["id"], "posts_rejected", 1)
        log_action(m.from_user.id, "reject_post", author["tg_id"], f"post_id={pid};reason={reason}")
        try:
            await bot.send_message(author["tg_id"], f"❌ Ваш пост #{pid} отклонён.\nПричина: {reason}")
        except Exception as e:
            print(f"[notify author reject] fail: {e}")
    await state.clear()
    await m.answer("Отклонение зафиксировано.", reply_markup=back_admin_kb())

# пользователи: список и поиск
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
            f"  Рег: {u['created_at']} | Визит: {u['last_seen'] or '—'}"
        )
    if not items: lines.append("Пока пусто.")
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

@r.callback_query(F.data == "owner_search")
async def owner_search(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_user_search)
    await cq.message.answer("Пришлите TG ID или @username для поиска.", reply_markup=back_admin_kb())
    await cq.answer()

@r.message(AdminStates.waiting_user_search, F.text)
async def owner_search_query(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await m.answer("Только владелец", reply_markup=back_admin_kb())
    raw = m.text.strip()
    target = get_user(int(raw)) if raw.isdigit() else user_by_username(raw)
    await state.clear()
    if not target:
        return await m.answer("Пользователь не найден.", reply_markup=back_admin_kb())
    await m.answer(profile_text(target), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Выдать админа", callback_data=f"owner_make_admin:{target['tg_id']}")],
        [InlineKeyboardButton(text="🌟 Подписка (выдать/снять)", callback_data=f"owner_subs_for:{target['tg_id']}")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")]
    ]))

@r.callback_query(F.data.startswith("owner_make_admin:"))
async def owner_make_admin(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    tg_id = int(cq.data.split(":")[1])
    set_user_flag(tg_id, "is_admin", 1)
    log_action(cq.from_user.id, "grant_admin", tg_id)
    await cq.message.answer(f"Готово. {tg_id} теперь админ.", reply_markup=back_admin_kb())
    try: await bot.send_message(tg_id, "👑 Вам выданы права администратора.")
    except Exception: pass
    await cq.answer()

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
    log_action(cq.from_user.id, "revoke_admin", tg_id)
    try: await bot.send_message(tg_id, "⚠️ С вас сняты права администратора.")
    except Exception: pass
    await cq.message.answer(f"С пользователя {tg_id} сняты права админа.", reply_markup=back_admin_kb())
    await cq.answer()

# статусы пользователей
@r.callback_query(F.data == "admin_status")
async def admin_status(cq: types.CallbackQuery, current_user: sqlite3.Row, state: FSMContext):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_user_search)
    await cq.message.answer("Пришлите TG ID или @username пользователя для управления статусом.", reply_markup=back_admin_kb())
    await cq.answer()

def status_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Проверенный", callback_data=f"setstatus:{tg_id}:verified")],
        [InlineKeyboardButton(text="⚪️ Нейтральный", callback_data=f"setstatus:{tg_id}:neutral")],
        [InlineKeyboardButton(text="🚫 Скаммер", callback_data=f"setstatus:{tg_id}:scammer")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="back_admin")],
    ])

@r.callback_query(F.data.startswith("setstatus:"))
async def setstatus(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    _, tg_id, status = cq.data.split(":")
    set_user_flag(int(tg_id), "status", status)
    u = get_user(int(tg_id))
    log_action(cq.from_user.id, "set_status", int(tg_id), status)
    await cq.message.edit_text(profile_text(u), reply_markup=status_kb(int(tg_id)))
    await cq.answer("Статус обновлён")

# ======= ПОДПИСКИ (выдать/снять — все тарифы) =======
@r.callback_query(F.data == "owner_subs")
async def owner_subs(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_subs_target)
    await cq.message.answer("Пришлите TG ID или @username пользователя для управления подпиской.", reply_markup=back_admin_kb())
    await cq.answer()

@r.callback_query(F.data.startswith("owner_subs_for:"))
async def owner_subs_for(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user): return await cq.answer("Только владелец", show_alert=True)
    tg_id = int(cq.data.split(":")[1])
    u = get_user(tg_id)
    await cq.message.answer(profile_text(u), reply_markup=subs_action_select_type_kb(tg_id))
    await cq.answer()

@r.message(AdminStates.waiting_subs_target, F.text)
async def owner_subs_target(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await m.answer("Только владелец", reply_markup=back_admin_kb())
    raw = m.text.strip()
    target = get_user(int(raw)) if raw.isdigit() else user_by_username(raw)
    await state.clear()
    if not target:
        return await m.answer("Пользователь не найден.", reply_markup=back_admin_kb())
    await m.answer(profile_text(target), reply_markup=subs_action_select_type_kb(target["tg_id"]))

def expiry_from_days(days: str) -> Optional[str]:
    if days == "forever": return None
    try: d = int(days)
    except Exception: d = 30
    exp = utcnow() + timedelta(days=d)
    return exp.isoformat(timespec="seconds")

@r.callback_query(F.data.startswith("grant:"))
async def grant_select_duration(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user): return await cq.answer("Только владелец", show_alert=True)
    _, sub_type, tg_id = cq.data.split(":")
    tg_id = int(tg_id)
    await cq.message.answer(f"Выдача подписки: {sub_type.upper()} — выберите срок:", reply_markup=subs_action_durations_kb(sub_type, tg_id))
    await cq.answer()

async def grant_subscription_and_notify(sub_type: str, tg_id: int, exp_iso: Optional[str]):
    with db() as c:
        c.execute("UPDATE users SET subscription_type=?, subscription_expires=? WHERE tg_id=?", (sub_type, exp_iso, tg_id))
    u = get_user(tg_id)
    kind = {"vip":"🌟 VIP","platinum":"💎 Platinum","extra":"🚀 Extra"}.get(sub_type, sub_type)
    if exp_iso is None:
        msg = f"{kind} выдана (навсегда)."
        user_msg = f"{kind} активирована (навсегда)."
    else:
        d = parse_iso(exp_iso).date().isoformat()
        msg = f"{kind} выдана до {d}."
        user_msg = f"{kind} активирована до {d}."
    return u, msg, user_msg

@r.callback_query(F.data.startswith("grant_dur:"))
async def grant_duration_apply(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user): return await cq.answer("Только владелец", show_alert=True)
    _, sub_type, tg_id, days = cq.data.split(":")
    tg_id = int(tg_id)
    exp = expiry_from_days(days)
    u, msg, user_msg = await grant_subscription_and_notify(sub_type, tg_id, exp)
    log_action(cq.from_user.id, f"grant_{sub_type}", tg_id, f"expires={exp or 'forever'}")
    await cq.message.answer(profile_text(u), reply_markup=subs_action_select_type_kb(tg_id))
    await cq.answer(f"{msg}")
    try:
        await bot.send_message(tg_id, f"{user_msg}")
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
    log_action(cq.from_user.id, "remove_sub", tg_id)
    await cq.message.answer(profile_text(u), reply_markup=subs_action_select_type_kb(tg_id))
    await cq.answer("Подписка снята")
    try: await bot.send_message(tg_id, "❌ Ваша подписка снята.")
    except Exception: pass

# канал
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
    log_action(m.from_user.id, "set_channel", extra=str(normalized))
    await state.clear()
    await m.answer(f"Канал сохранён: {normalized}", reply_markup=back_admin_kb())

@r.callback_query(F.data == "admin_getchannel")
async def admin_getchannel(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_admin(current_user):
        return await cq.answer("Нет доступа", show_alert=True)
    ch = normalize_channel_id(get_cfg("channel_id") or CHANNEL_ID_ENV) or "(не задан)"
    await cq.message.answer(f"Текущий канал: {ch}", reply_markup=back_admin_kb())
    await cq.answer()

# рассылка
@r.callback_query(F.data == "owner_broadcast")
async def owner_broadcast(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_broadcast)
    await cq.message.answer("Пришлите контент рассылки (текст/фото/видео/документ). Отмена — /cancel", reply_markup=broadcast_audience_kb())
    await cq.answer()

_broadcast_cache: Dict[int, Dict[str, Any]] = {}

@r.message(AdminStates.waiting_broadcast)
async def handle_broadcast_content(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user): return await m.answer("Только владелец", reply_markup=back_admin_kb())
    ctype, text, file_id = None, None, None
    if m.text: ctype, text = "text", m.text
    elif m.photo: ctype, text, file_id = "photo", m.caption, m.photo[-1].file_id
    elif m.video: ctype, text, file_id = "video", m.caption, m.video.file_id
    elif m.document: ctype, text, file_id = "document", m.caption, m.document.file_id
    else:
        return await m.answer("Неподдерживаемый тип. Пришлите текст/фото/видео/документ.", reply_markup=broadcast_audience_kb())
    _broadcast_cache[m.from_user.id] = {"ctype": ctype, "text": text or "", "file_id": file_id}
    await m.answer("Выберите аудиторию:", reply_markup=broadcast_audience_kb())

@r.callback_query(F.data.startswith("bc_aud:"))
async def bc_audience(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user): return await cq.answer("Только владелец", show_alert=True)
    data = _broadcast_cache.get(cq.from_user.id)
    if not data:
        return await cq.answer("Сначала пришлите контент.", show_alert=True)
    audience = cq.data.split(":")[1]
    await state.clear()
    await cq.message.answer("🚀 Начинаю рассылку…", reply_markup=back_admin_kb())
    ok = 0; total = 0
    with db() as c:
        if audience == "all":
            users = c.execute("SELECT tg_id, subscription_type, subscription_expires FROM users").fetchall()
        elif audience == "paid":
            users = c.execute("SELECT tg_id, subscription_type, subscription_expires FROM users WHERE subscription_type IN ('vip','platinum','extra')").fetchall()
        else:
            users = c.execute("SELECT tg_id, subscription_type, subscription_expires FROM users WHERE subscription_type='free'").fetchall()
    for row in users:
        total += 1
        tg = row["tg_id"]
        try:
            if data["ctype"] == "text":
                for chunk in chunk_text(data["text"], 4096):
                    await bot.send_message(tg, chunk)
            else:
                cap_short, rest = split_caption(data["text"], 1024)
                if data["ctype"] == "photo":
                    await bot.send_photo(tg, data["file_id"], caption=cap_short)
                elif data["ctype"] == "video":
                    await bot.send_video(tg, data["file_id"], caption=cap_short)
                elif data["ctype"] == "document":
                    await bot.send_document(tg, data["file_id"], caption=cap_short)
                for chunk in chunk_text(rest or "", 4096):
                    await bot.send_message(tg, chunk)
            ok += 1
        except Exception as e:
            print(f"[broadcast] to {tg} failed: {e}")
        await asyncio.sleep(0.04)
    await cq.message.answer(f"Готово. Успешно: {ok}/{total}", reply_markup=back_admin_kb())
    await cq.answer()

# экспорт/импорт БД
@r.callback_query(F.data == "owner_export_db")
async def owner_export_db(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_owner(current_user): return await cq.answer("Только владелец", show_alert=True)
    try:
        await bot.send_document(OWNER_ID, types.FSInputFile(PRIMARY_DB_PATH), caption="Текущая база (bot.db)")
        await cq.answer("Отправил базу в личку владельцу.")
    except Exception as e:
        await cq.answer(f"Ошибка отправки: {e}", show_alert=True)

@r.callback_query(F.data == "owner_import_db")
async def owner_import_db(cq: types.CallbackQuery, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user): return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_import_db)
    await cq.message.answer("Пришлите файл базы <b>bot.db</b>. Текущая будет сохранена в backups/.", reply_markup=back_admin_kb())
    await cq.answer()

@r.message(AdminStates.waiting_import_db, F.document)
async def handle_import_db(m: types.Message, state: FSMContext, current_user: sqlite3.Row):
    if not is_owner(current_user): return await m.answer("Только владелец", reply_markup=back_admin_kb())
    doc = m.document
    if not doc.file_name.lower().endswith("bot.db"):
        return await m.answer("Нужно прислать файл с именем bot.db", reply_markup=back_admin_kb())
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if os.path.exists(PRIMARY_DB_PATH):
            shutil.copy2(PRIMARY_DB_PATH, os.path.join(BACKUP_DIR, f"bot_before_import_{ts}.db"))
        tmp_path = os.path.join(DB_DIR, f"upload_{ts}.db")
        await bot.download(doc, destination=tmp_path)
        shutil.move(tmp_path, PRIMARY_DB_PATH)
        await state.clear()
        await m.answer("✅ База обновлена. Перезапустите бота.", reply_markup=back_admin_kb())
    except Exception as e:
        await m.answer(f"Ошибка импорта: {e}", reply_markup=back_admin_kb())

# статистика
@r.callback_query(F.data == "admin_stats")
async def admin_stats(cq: types.CallbackQuery, current_user: sqlite3.Row):
    if not is_admin(current_user): return await cq.answer("Нет доступа", show_alert=True)
    with db() as c:
        total_users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        vip = c.execute("SELECT COUNT(*) AS n FROM users WHERE subscription_type='vip'").fetchone()["n"]
        plat = c.execute("SELECT COUNT(*) AS n FROM users WHERE subscription_type='platinum'").fetchone()["n"]
        extra = c.execute("SELECT COUNT(*) AS n FROM users WHERE subscription_type='extra'").fetchone()["n"]
        def cnt(days: int) -> int:
            dt = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
            return c.execute("SELECT COUNT(*) AS n FROM posts WHERE status='approved' AND created_at>=?", (dt,)).fetchone()["n"]
        day = cnt(1); week = cnt(7); month = cnt(30)
        rej = c.execute("SELECT COUNT(*) AS n FROM posts WHERE status='rejected'").fetchone()["n"]
    txt = (
        f"📊 Статистика — {PROJECT_NAME}\n"
        f"👥 Пользователей: {total_users}\n"
        f"🌟 VIP: {vip} | 💎 Platinum: {plat} | 🚀 Extra: {extra}\n"
        f"📝 Опубликовано: за сутки {day}, за неделю {week}, за месяц {month}\n"
        f"❌ Отклонённых всего: {rej}"
    )
    await cq.message.answer(txt, reply_markup=back_admin_kb())
    await cq.answer()

# Подписки — меню/превью/цены
@r.message(F.text.in_({"💳 Подписки", "Подписки", "🏂 Подписка", "Подписка"}))
async def subs_menu(m: types.Message):
    await m.answer(
        "Выберите тариф, чтобы посмотреть превью оформления поста и детали:",
        reply_markup=preview_subs_kb()
    )

@r.callback_query(F.data.in_(("pv_vip","pv_platinum","pv_extra")))
async def subs_preview(cq: types.CallbackQuery):
    kind = cq.data.split("_")[1]
    if kind == "vip":
        text = ("⚡ Продам аккаунт Standoff 2\nЦена: 300₽\nПисать: @user\n\n🌟 Публикация VIP")
    elif kind == "platinum":
        text = ("💎 [Пост в рамке 💎]\n\n👑 Обменяю скин Karambit Gold\nХочу: M9 Scratch или Stars\nСвязь: @platinum_user\n\n💎 Публикация Platinum")
    else:
        text = ("🦅 [Пост в золотой рамке 🚀]\n\n🔥 Продам редкий ник #DarkPrince\nЦена: 2500⭐ или TON\nПисать: @extra_legend\n\n🚀 Публикация Extra\n✅ Trust-бейдж: Проверенный продавец")
    await cq.message.answer(f"📌 Вот как будет выглядеть ваш пост:\n\n{text}", reply_markup=buy_kb())
    await cq.answer()

@r.callback_query(F.data == "pv_prices")
async def pv_prices(cq: types.CallbackQuery):
    await cq.message.answer(PRICES_TEXT, reply_markup=buy_kb())
    await cq.answer()

@r.callback_query(F.data == "pv_back")
async def pv_back(cq: types.CallbackQuery):
    await cq.message.answer("Выберите тариф, чтобы посмотреть превью оформления поста и детали:", reply_markup=preview_subs_kb())
    await cq.answer()

# Публичная проверка пользователя
@r.message(F.text == "🔎 Проверка пользователя")
async def user_check_start(m: types.Message, state: FSMContext):
    await state.set_state(PublicCheck.waiting_query)
    await m.answer("Пришлите @username или TG ID для проверки.\nОтмена — /cancel")

@r.message(PublicCheck.waiting_query, F.text)
async def user_check_run(m: types.Message, state: FSMContext):
    q = (m.text or "").strip()
    await state.clear()
    target = get_user(int(q)) if q.isdigit() else user_by_username(q)
    if not target:
        return await m.answer("Пользователь не найден в базе. Возможно, он ещё не пользовался ботом.", reply_markup=main_menu_kb())
    st_title, st_emoji = status_badge(target["status"])
    uname = f'@{target["username"]}' if target["username"] else "—"
    txt = (
        "🕵️ Проверка пользователя\n"
        f"ID: {target['tg_id']}\n"
        f"Юзернейм: {uname}\n"
        f"Статус: {st_emoji} {st_title}\n"
        f"Подписка: {sub_label(target)}\n"
        f"Посты: всего {target['posts_total']}, ✅ {target['posts_approved']}, ❌ {target['posts_rejected']}\n"
    )
    if target["status"] == "scammer":
        txt += "\n⚠️ Рекомендация: работайте только через гаранта."
    elif target["status"] == "neutral":
        txt += "\nℹ️ Рекомендация: проверяйте сделки, при необходимости — через гаранта."
    else:
        txt += "\n✅ Пользователь проверен."
    await m.answer(txt, reply_markup=main_menu_kb())

# ===== фоновая задача напоминаний =====
async def reminders_loop():
    await asyncio.sleep(5)
    while True:
        try:
            with db() as c:
                # юзеру — >24ч
                since_u = (utcnow() - timedelta(hours=24)).isoformat(timespec="seconds")
                pend_u = c.execute("SELECT id, user_id FROM posts WHERE status='pending' AND user_reminded=0 AND created_at<=?",
                                   (since_u,)).fetchall()
                for p in pend_u:
                    u = user_by_id(p["user_id"])
                    if u:
                        try:
                            await bot.send_message(u["tg_id"], "⚠️ Ваш пост всё ещё ждёт проверки администрацией.")
                            c.execute("UPDATE posts SET user_reminded=1 WHERE id=?", (p["id"],))
                        except Exception as e:
                            print(f"[remind user] {u['tg_id']}: {e}")
                # админам — >12ч
                since_a = (utcnow() - timedelta(hours=12)).isoformat(timespec="seconds")
                need_admin = c.execute("SELECT COUNT(*) AS n FROM posts WHERE status='pending' AND admin_reminded=0 AND created_at<=?",
                                       (since_a,)).fetchone()["n"]
                if need_admin:
                    admins = c.execute("SELECT tg_id FROM users WHERE is_admin=1 OR is_owner=1").fetchall()
                    for a in admins:
                        try:
                            await bot.send_message(a["tg_id"], f"⏰ Есть объявления, ожидающие проверки более 12 часов.")
                        except Exception: pass
                    c.execute("UPDATE posts SET admin_reminded=1 WHERE status='pending' AND created_at<=?", (since_a,))
        except Exception as e:
            print(f"[reminders] error: {e}")
        await asyncio.sleep(900)

# ===== RUN =====
async def main():
    init_db()
    backup_db()
    asyncio.create_task(reminders_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())















