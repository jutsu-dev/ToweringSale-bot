# app.py
# Python 3.11+, aiogram>=3.7
import asyncio
import os
import sqlite3
import shutil
from datetime import datetime
from typing import Optional, Tuple

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

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# =========================
# 🔧 CONFIG
# =========================
BOT_TOKEN   = os.getenv("BOT_TOKEN", "8052075709:AAGD7-tH2Yq7Ipixmw21y3D1B-oWWGrq03I")
PROJECT_NAME = "@ToweringSale"
OWNER_ID = 6089346880

DATA_DIR = "data"
DB_PATH = os.path.join(DATA_DIR, "bot.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# =========================
# 💾 DB + BACKUP
# =========================
def backup_db():
    if os.path.exists(DB_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"bot_{ts}.db")
        shutil.copy2(DB_PATH, backup_file)
        print(f"[DB] backup created: {backup_file}")

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_column(conn: sqlite3.Connection, table: str, col: str, col_def: str):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        print(f"[DB] added column {table}.{col}")

def init_db():
    with db() as c:
        # базовые таблицы
        c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE,
            username TEXT,
            is_owner INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            vip INTEGER DEFAULT 0,
            status TEXT DEFAULT 'neutral',
            posts_total INTEGER DEFAULT 0,
            posts_approved INTEGER DEFAULT 0,
            posts_rejected INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content_type TEXT,
            text TEXT,
            file_id TEXT,
            status TEXT DEFAULT 'pending',
            moderator_id INTEGER,
            reject_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS config(
            key TEXT PRIMARY KEY,
            value TEXT
        )""")

        # миграции для старых баз
        ensure_column(c, "posts", "reject_reason", "reject_reason TEXT")
        ensure_column(c, "posts", "moderator_id", "moderator_id INTEGER")
        ensure_column(c, "posts", "created_at", "created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        ensure_column(c, "users", "vip", "vip INTEGER DEFAULT 0")
        ensure_column(c, "users", "status", "status TEXT DEFAULT 'neutral'")
        ensure_column(c, "users", "posts_total", "posts_total INTEGER DEFAULT 0")
        ensure_column(c, "users", "posts_approved", "posts_approved INTEGER DEFAULT 0")
        ensure_column(c, "users", "posts_rejected", "posts_rejected INTEGER DEFAULT 0")
        ensure_column(c, "users", "created_at", "created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        # гарантируем владельца
        c.execute("UPDATE users SET is_owner=1, is_admin=1 WHERE tg_id=?", (OWNER_ID,))

def get_cfg(key: str) -> Optional[str]:
    with db() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def set_cfg(key: str, value: str):
    with db() as c:
        c.execute(
            "INSERT INTO config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )

def get_or_create_user(tg_id: int, username: Optional[str]) -> sqlite3.Row:
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if row:
            if row["username"] != username:
                c.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
            return row
        is_owner = 1 if tg_id == OWNER_ID else 0
        c.execute(
            "INSERT INTO users(tg_id, username, is_owner, is_admin) VALUES(?,?,?,?)",
            (tg_id, username, is_owner, is_owner)
        )
        return c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

def user_by_tg(tg_id: int) -> Optional[sqlite3.Row]:
    with db() as c:
        return c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

def user_by_id(uid: int) -> Optional[sqlite3.Row]:
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def update_user_stats(user_id: int, field: str, inc: int = 1):
    with db() as c:
        c.execute(f"UPDATE users SET {field}={field}+? WHERE id=?", (inc, user_id))

def set_user_flag(tg_id: int, field: str, val: int):
    with db() as c:
        c.execute(f"UPDATE users SET {field}=? WHERE tg_id=?", (val, tg_id))

def set_user_status(tg_id: int, status: str):
    with db() as c:
        c.execute("UPDATE users SET status=? WHERE tg_id=?", (status, tg_id))

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
        c.execute(
            "UPDATE posts SET status=?, moderator_id=?, reject_reason=? WHERE id=?",
            (status, moderator_tg, reason, pid)
        )

# =========================
# 🧩 HELPERS
# =========================
def status_badge(status: str) -> Tuple[str, str]:
    mapping = {"verified": ("Проверенный", "✅"),
               "neutral": ("Нейтральный", "⚪️"),
               "scammer": ("Возможен скам", "🚫")}
    return mapping.get(status, ("Нейтральный", "⚪️"))

def profile_text(u: sqlite3.Row) -> str:
    st_title, st_emoji = status_badge(u["status"])
    vip = "🌟 VIP" if u["vip"] else "—"
    uname = f'@{u["username"]}' if u["username"] else "(без username)"
    return (
        f"👤 Профиль — {PROJECT_NAME}\n"
        f"ID: {u['tg_id']}\n"
        f"Юзернейм: {uname}\n"
        f"Статус: {st_emoji} {st_title}\n"
        f"Подписка: {vip}\n"
        f"Посты: всего {u['posts_total']}, ✅ {u['posts_approved']}, ❌ {u['posts_rejected']}"
    )

def post_caption(author: sqlite3.Row, text: Optional[str]) -> str:
    st_title, st_emoji = status_badge(author["status"])
    uname = f'@{author["username"]}' if author["username"] else f"id:{author['tg_id']}"
    header = f"Автор: {uname}\nСтатус автора: {st_emoji} {st_title}\n— — —"
    return f"{header}\n{text}" if text else header

def is_admin(u: sqlite3.Row) -> bool:
    return bool(u["is_admin"] or u["is_owner"])

def is_owner(u: sqlite3.Row) -> bool:
    return bool(u["is_owner"])

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Отправить объявление")],
            [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="ℹ️ Инфо")],
        ],
        resize_keyboard=True
    )

def admin_menu_kb(owner: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📥 Новые объявления", callback_data="admin_pending")]]
    if owner:
        rows += [
            [InlineKeyboardButton(text="👥 Админы", callback_data="admin_admins")],
            [InlineKeyboardButton(text="👤 Статусы / VIP", callback_data="admin_status")],
            [InlineKeyboardButton(text="📡 Канал: установить", callback_data="admin_setchannel"),
             InlineKeyboardButton(text="🔎 Канал", callback_data="admin_getchannel")],
        ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def mod_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod_approve:{post_id}")],
        [InlineKeyboardButton(text="❌ Отклонить (с причиной)", callback_data=f"mod_reject:{post_id}")]
    ])

def status_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Проверенный", callback_data=f"setstatus:{tg_id}:verified")],
        [InlineKeyboardButton(text="⚪️ Нейтральный", callback_data=f"setstatus:{tg_id}:neutral")],
        [InlineKeyboardButton(text="🚫 Скаммер", callback_data=f"setstatus:{tg_id}:scammer")],
        [InlineKeyboardButton(text="🌟 Переключить VIP", callback_data=f"togglevip:{tg_id}")]
    ])

# =========================
# 🎯 FSM
# =========================
class SubmitStates(StatesGroup):
    waiting_post = State()

class RejectStates(StatesGroup):
    waiting_reason = State()

class AdminStates(StatesGroup):
    waiting_channel = State()
    waiting_add_admin = State()
    waiting_remove_admin = State()
    waiting_status_user = State()

# =========================
# 🤖 BOT
# =========================
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
r = Router()
dp.include_router(r)

# ---------- Startup ----------
@r.message(CommandStart())
async def start(m: types.Message, state: FSMContext):
    init_db()
    get_or_create_user(m.from_user.id, m.from_user.username)
    await m.answer(
        f"Привет! Это бот проекта «{PROJECT_NAME}».\nВыберите действие:",
        reply_markup=main_menu_kb()
    )

@r.message(F.text == "ℹ️ Инфо")
async def info(m: types.Message):
    await m.answer(f"<b>{PROJECT_NAME}</b>\n• Публикуйте объявления\n• VIP — сразу, без модерации")

@r.message(F.text == "👤 Мой профиль")
async def my_profile(m: types.Message):
    u = get_or_create_user(m.from_user.id, m.from_user.username)
    await m.answer(profile_text(u))

# ---------- Submit ----------
@r.message(F.text == "📤 Отправить объявление")
async def submit_start(m: types.Message, state: FSMContext):
    await state.set_state(SubmitStates.waiting_post)
    await m.answer("Пришлите текст/фото/видео/документ одним сообщением.\nОтмена — /cancel")

@r.message(Command("cancel"))
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Действие отменено.", reply_markup=main_menu_kb())

async def notify_admins(post_id: int, author: sqlite3.Row, ctype: str, text: str | None, file_id: str | None):
    cap = f"📥 Новое объявление #{post_id}\n{profile_text(author)}"
    kb = mod_kb(post_id)
    with db() as c:
        admins = c.execute("SELECT tg_id FROM users WHERE is_admin=1 OR is_owner=1").fetchall()
    for a in admins:
        try:
            tg = a["tg_id"]
            if ctype == "text":
                await bot.send_message(tg, f"{cap}\n\n{text}", reply_markup=kb)
            elif ctype == "photo":
                await bot.send_photo(tg, file_id, caption=f"{cap}\n\n{text or ''}", reply_markup=kb)
            elif ctype == "video":
                await bot.send_video(tg, file_id, caption=f"{cap}\n\n{text or ''}", reply_markup=kb)
            elif ctype == "document":
                await bot.send_document(tg, file_id, caption=f"{cap}\n\n{text or ''}", reply_markup=kb)
        except Exception:
            pass

async def handle_post(m: types.Message, ctype: str, text: str | None, file_id: str | None, state: FSMContext):
    await state.clear()
    u = get_or_create_user(m.from_user.id, m.from_user.username)
    update_user_stats(u["id"], "posts_total", 1)
    if u["vip"]:
        await publish(u, ctype, text, file_id)
        update_user_stats(u["id"], "posts_approved", 1)
        await m.answer("🌟 Ваш пост опубликован (VIP).")
        return
    pid = create_post(u["id"], ctype, text, file_id)
    await m.answer("Спасибо! Ваше объявление отправлено на модерацию.")
    await notify_admins(pid, u, ctype, text, file_id)

@r.message(SubmitStates.waiting_post, F.text)
async def post_text(m: types.Message, state: FSMContext):
    await handle_post(m, "text", m.text, None, state)

@r.message(SubmitStates.waiting_post, F.photo)
async def post_photo(m: types.Message, state: FSMContext):
    await handle_post(m, "photo", m.caption, m.photo[-1].file_id, state)

@r.message(SubmitStates.waiting_post, F.video)
async def post_video(m: types.Message, state: FSMContext):
    await handle_post(m, "video", m.caption, m.video.file_id, state)

@r.message(SubmitStates.waiting_post, F.document)
async def post_document(m: types.Message, state: FSMContext):
    await handle_post(m, "document", m.caption, m.document.file_id, state)

# ---------- Publishing ----------
async def publish(author: sqlite3.Row, ctype: str, text: str | None, file_id: str | None):
    cap = post_caption(author, text)
    target = get_cfg("channel_id") or OWNER_ID
    if ctype == "text":
        await bot.send_message(target, cap)
    elif ctype == "photo":
        await bot.send_photo(target, file_id, caption=cap)
    elif ctype == "video":
        await bot.send_video(target, file_id, caption=cap)
    elif ctype == "document":
        await bot.send_document(target, file_id, caption=cap)

# ---------- Admin panel ----------
@r.message(Command("admin"))
async def admin_panel(m: types.Message):
    u = get_or_create_user(m.from_user.id, m.from_user.username)
    if not is_admin(u):
        return await m.answer("Нет доступа")
    await m.answer("Админ-панель:", reply_markup=admin_menu_kb(owner=is_owner(u)))

@r.callback_query(F.data == "admin_back")
async def admin_back(cq: types.CallbackQuery):
    await cq.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await cq.answer()

@r.callback_query(F.data == "admin_pending")
async def admin_pending(cq: types.CallbackQuery):
    u = user_by_tg(cq.from_user.id)
    if not is_admin(u):
        return await cq.answer("Нет доступа", show_alert=True)
    post = next_pending_post()
    if not post:
        return await cq.message.answer("Новых объявлений нет.")
    author = user_by_id(post["user_id"])
    if post["content_type"] == "text":
        await cq.message.answer(post_caption(author, post["text"]), reply_markup=mod_kb(post["id"]))
    elif post["content_type"] == "photo":
        await cq.message.answer_photo(post["file_id"], caption=post_caption(author, post["text"]), reply_markup=mod_kb(post["id"]))
    elif post["content_type"] == "video":
        await cq.message.answer_video(post["file_id"], caption=post_caption(author, post["text"]), reply_markup=mod_kb(post["id"]))
    elif post["content_type"] == "document":
        await cq.message.answer_document(post["file_id"], caption=post_caption(author, post["text"]), reply_markup=mod_kb(post["id"]))
    await cq.answer()

# ---- approve / reject ----
@r.callback_query(F.data.startswith("mod_approve:"))
async def mod_approve(cq: types.CallbackQuery):
    pid = int(cq.data.split(":")[1])
    post = post_by_id(pid)
    if not post:
        return await cq.answer("Уже обработано")
    author = user_by_id(post["user_id"])
    await publish(author, post["content_type"], post["text"], post["file_id"])
    set_post_status(pid, "approved", cq.from_user.id)
    update_user_stats(author["id"], "posts_approved", 1)
    try:
        await bot.send_message(author["tg_id"], f"✅ Ваш пост #{pid} опубликован.")
    except Exception:
        pass
    await cq.answer("Опубликовано")

@r.callback_query(F.data.startswith("mod_reject:"))
async def mod_reject(cq: types.CallbackQuery, state: FSMContext):
    pid = int(cq.data.split(":")[1])
    post = post_by_id(pid)
    if not post:
        return await cq.answer("Уже обработано")
    await state.set_state(RejectStates.waiting_reason)
    await state.update_data(pid=pid)
    await cq.message.answer("Введите причину отклонения:")
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
        except Exception:
            pass
    await state.clear()
    await m.answer("Отклонение зафиксировано.")

# ---------- Owner-only: admins / statuses / channel ----------
def owner_only(tg_id: int) -> bool:
    u = user_by_tg(tg_id)
    return bool(u and u["is_owner"])

@r.callback_query(F.data == "admin_admins")
async def admins_menu(cq: types.CallbackQuery, state: FSMContext):
    if not owner_only(cq.from_user.id):
        return await cq.answer("Только владелец", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Выдать админа", callback_data="owner_add_admin")],
        [InlineKeyboardButton(text="➖ Снять админа", callback_data="owner_remove_admin")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")],
    ])
    await cq.message.answer("Управление администраторами:", reply_markup=kb)
    await cq.answer()

@r.callback_query(F.data == "owner_add_admin")
async def owner_add_admin(cq: types.CallbackQuery, state: FSMContext):
    if not owner_only(cq.from_user.id):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_add_admin)
    await cq.message.answer("Пришлите TG ID пользователя, которому выдать админа.")
    await cq.answer()

@r.message(AdminStates.waiting_add_admin, F.text.regexp(r"^\d+$"))
async def owner_add_admin_id(m: types.Message, state: FSMContext):
    if not owner_only(m.from_user.id):
        return await m.answer("Только владелец")
    tg_id = int(m.text.strip())
    get_or_create_user(tg_id, None)
    set_user_flag(tg_id, "is_admin", 1)
    await state.clear()
    await m.answer(f"Готово. {tg_id} теперь админ.")

@r.callback_query(F.data == "owner_remove_admin")
async def owner_remove_admin(cq: types.CallbackQuery, state: FSMContext):
    if not owner_only(cq.from_user.id):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_remove_admin)
    await cq.message.answer("Пришлите TG ID пользователя, у которого снять админа.")
    await cq.answer()

@r.message(AdminStates.waiting_remove_admin, F.text.regexp(r"^\d+$"))
async def owner_remove_admin_id(m: types.Message, state: FSMContext):
    if not owner_only(m.from_user.id):
        return await m.answer("Только владелец")
    tg_id = int(m.text.strip())
    if tg_id == OWNER_ID:
        return await m.answer("Нельзя снять владельца.")
    set_user_flag(tg_id, "is_admin", 0)
    await state.clear()
    await m.answer(f"С {tg_id} сняты права админа.")

@r.callback_query(F.data == "admin_status")
async def admin_status(cq: types.CallbackQuery, state: FSMContext):
    if not owner_only(cq.from_user.id):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_status_user)
    await cq.message.answer("Пришлите TG ID пользователя для управления статусом/VIP.")
    await cq.answer()

def status_kb_for(tg_id: int) -> InlineKeyboardMarkup:
    return status_kb(tg_id)

@r.message(AdminStates.waiting_status_user, F.text.regexp(r"^\d+$"))
async def admin_status_user(m: types.Message, state: FSMContext):
    if not owner_only(m.from_user.id):
        return await m.answer("Только владелец")
    tg_id = int(m.text.strip())
    u = user_by_tg(tg_id) or get_or_create_user(tg_id, None)
    await state.clear()
    await m.answer(profile_text(u), reply_markup=status_kb_for(tg_id))

@r.callback_query(F.data.startswith("setstatus:"))
async def setstatus(cq: types.CallbackQuery):
    if not owner_only(cq.from_user.id):
        return await cq.answer("Только владелец", show_alert=True)
    _, tg_id, status = cq.data.split(":")
    set_user_status(int(tg_id), status)
    u = user_by_tg(int(tg_id))
    await cq.message.edit_text(profile_text(u), reply_markup=status_kb_for(int(tg_id)))
    await cq.answer("Статус обновлён")

@r.callback_query(F.data.startswith("togglevip:"))
async def togglevip(cq: types.CallbackQuery):
    if not owner_only(cq.from_user.id):
        return await cq.answer("Только владелец", show_alert=True)
    _, tg_id = cq.data.split(":")
    u = user_by_tg(int(tg_id))
    set_user_flag(int(tg_id), "vip", 0 if u and u["vip"] else 1)
    u2 = user_by_tg(int(tg_id))
    await cq.message.edit_text(profile_text(u2), reply_markup=status_kb_for(int(tg_id)))
    await cq.answer("VIP переключён")

@r.callback_query(F.data == "admin_setchannel")
async def admin_setchannel(cq: types.CallbackQuery, state: FSMContext):
    if not owner_only(cq.from_user.id):
        return await cq.answer("Только владелец", show_alert=True)
    await state.set_state(AdminStates.waiting_channel)
    await cq.message.answer("Пришлите @username канала или числовой ID (-100...). Бот должен быть админом там.")
    await cq.answer()

@r.message(AdminStates.waiting_channel, F.text)
async def save_channel(m: types.Message, state: FSMContext):
    if not owner_only(m.from_user.id):
        return await m.answer("Только владелец")
    set_cfg("channel_id", m.text.strip())
    await state.clear()
    await m.answer(f"Канал сохранён: {m.text.strip()}")

@r.callback_query(F.data == "admin_getchannel")
async def admin_getchannel(cq: types.CallbackQuery):
    if not is_admin(user_by_tg(cq.from_user.id)):
        return await cq.answer("Нет доступа", show_alert=True)
    ch = get_cfg("channel_id") or "(не задан)"
    await cq.message.answer(f"Текущий канал: {ch}")
    await cq.answer()

# =========================
# ▶️ RUN
# =========================
async def main():
    init_db()
    backup_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())









