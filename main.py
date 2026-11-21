import asyncio
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
from psycopg.rows import dict_row
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None
PORT = int(os.environ.get("PORT", "10000"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("viper-anonymous-bot")


# ---------- Database ----------
def db_connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)


def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                link_code TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS admin_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """)
        conn.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS boss_mode BOOLEAN NOT NULL DEFAULT FALSE
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS received_messages (
                id BIGSERIAL PRIMARY KEY,
                recipient_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                sender_id BIGINT NOT NULL,
                sender_username TEXT,
                recipient_message_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(recipient_id, recipient_message_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS received_messages_lookup_idx
            ON received_messages(recipient_id, recipient_message_id)
        """)
        conn.commit()


def register_user(user_id: int, username: str | None) -> str:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT link_code FROM users WHERE user_id = %s", (user_id,)
        ).fetchone()
        if row:
            conn.execute("UPDATE users SET username = %s WHERE user_id = %s", (username, user_id))
            conn.commit()
            return row["link_code"]
        code = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO users(user_id, username, link_code) VALUES (%s, %s, %s)",
            (user_id, username, code),
        )
        conn.commit()
        return code


def find_recipient_by_code(code: str):
    with db_connect() as conn:
        return conn.execute("SELECT user_id FROM users WHERE link_code = %s", (code,)).fetchone()


def save_received_message(recipient_id, sender_id, sender_username, recipient_message_id):
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO received_messages
               (recipient_id, sender_id, sender_username, recipient_message_id)
               VALUES (%s, %s, %s, %s)""",
            (recipient_id, sender_id, sender_username, recipient_message_id),
        )
        conn.commit()


def lookup_sender(recipient_id: int, recipient_message_id: int):
    with db_connect() as conn:
        return conn.execute(
            """SELECT sender_username FROM received_messages
               WHERE recipient_id = %s AND recipient_message_id = %s""",
            (recipient_id, recipient_message_id),
        ).fetchone()


def get_reply_target(recipient_id: int, record_id: int):
    with db_connect() as conn:
        return conn.execute(
            """SELECT id, sender_id, created_at,
                      (created_at + INTERVAL '24 hours' > NOW()) AS can_reply
               FROM received_messages WHERE id = %s AND recipient_id = %s""",
            (record_id, recipient_id),
        ).fetchone()


def get_user_flags(user_id: int):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT admin_enabled, boss_mode FROM users WHERE user_id = %s", (user_id,)
        ).fetchone()
        return row or {"admin_enabled": False, "boss_mode": False}


def set_admin_enabled(user_id: int):
    with db_connect() as conn:
        conn.execute("UPDATE users SET admin_enabled = TRUE WHERE user_id = %s", (user_id,))
        conn.commit()


def toggle_boss_mode(user_id: int):
    with db_connect() as conn:
        row = conn.execute(
            "UPDATE users SET boss_mode = NOT boss_mode WHERE user_id = %s RETURNING boss_mode",
            (user_id,),
        ).fetchone()
        conn.commit()
        return bool(row and row["boss_mode"])


# ---------- UI ----------
def is_admin(user_id: int) -> bool:
    return ADMIN_ID is not None and user_id == ADMIN_ID


def boss_prefix(user_id: int) -> str:
    if not is_admin(user_id):
        return ""
    try:
        return "Босс, " if get_user_flags(user_id)["boss_mode"] else ""
    except Exception:
        log.exception("Could not read admin preferences")
        return ""


def home_keyboard(user_id: int | None = None):
    rows = [[InlineKeyboardButton("🔗 Получить ссылку", callback_data="get_link")]]
    if user_id is not None and is_admin(user_id):
        try:
            if get_user_flags(user_id)["admin_enabled"]:
                rows.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin_panel")])
        except Exception:
            log.exception("Could not read admin status")
    return InlineKeyboardMarkup(rows)


def reply_keyboard(record_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ Ответить", callback_data=f"reply_{record_id}")
    ]])


def admin_keyboard(boss_mode: bool):
    label = "Имя: Босс ✓" if boss_mode else "Имя: обычное"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="toggle_boss")],
        [InlineKeyboardButton("‹ В главное меню", callback_data="home")],
    ])


async def send_home(message, user_id: int, text: str | None = None):
    body = text or f"{boss_prefix(user_id)}добро пожаловать в VIPER.\n\nТвоя анонимная почта — просто и конфиденциально."
    await message.reply_text(body, reply_markup=home_keyboard(user_id), disable_web_page_preview=True)


# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message or update.effective_chat.type != ChatType.PRIVATE:
        if message:
            await message.reply_text("Открой меня в личных сообщениях, чтобы пользоваться VIPER.")
        return

    await asyncio.to_thread(register_user, user.id, user.username)

    if context.args:
        target = await asyncio.to_thread(find_recipient_by_code, context.args[0])
        if not target:
            await message.reply_text("Эта ссылка недействительна или больше не существует.")
            return
        target_id = target["user_id"]
        if target_id == user.id:
            await message.reply_text("Это твоя собственная ссылка 🙂")
            return
        context.user_data["sending_to"] = target_id
        context.user_data.pop("reply_to_record", None)
        await message.reply_text(
            "✉️ **Новое анонимное сообщение**\n\nНапиши текст, который хочешь отправить.\nДля отмены — /cancel.",
            parse_mode="Markdown",
        )
        return

    context.user_data.pop("sending_to", None)
    context.user_data.pop("reply_to_record", None)
    await send_home(
        message,
        user.id,
        f"◈ VIPER ANONYMOUS MAIL ◈\n\n{boss_prefix(user.id)}добро пожаловать.\nЗдесь тебе могут отправлять анонимные сообщения.\n\nНажми кнопку ниже, чтобы получить личную ссылку.",
    )


async def get_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    code = await asyncio.to_thread(register_user, user.id, user.username)
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    await query.edit_message_text(
        f"◈ VIPER / ТВОЯ ССЫЛКА ◈\n\n{boss_prefix(user.id)}вот твоя анонимная ссылка:\n{link}\n\nОтправляй её друзьям или публикуй где угодно.",
        reply_markup=home_keyboard(user.id),
        disable_web_page_preview=True,
    )


async def show_my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the caller their own numeric Telegram ID for Render setup."""
    await update.effective_message.reply_text(
        f"Твой Telegram ID: `{update.effective_user.id}`", parse_mode="Markdown"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("sending_to", None)
    context.user_data.pop("reply_to_record", None)
    await update.effective_message.reply_text(
        f"{boss_prefix(update.effective_user.id)}отправка отменена.",
        reply_markup=home_keyboard(update.effective_user.id),
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    uid = user.id
    data = query.data or ""

    if data == "get_link":
        await get_link_callback(update, context)
        return

    if data == "home":
        await query.answer()
        await query.edit_message_text(
            f"◈ VIPER ANONYMOUS MAIL ◈\n\n{boss_prefix(uid)}главное меню.",
            reply_markup=home_keyboard(uid),
        )
        return

    if data == "admin_panel":
        if not is_admin(uid):
            await query.answer("Недоступно", show_alert=True)
            return
        flags = await asyncio.to_thread(get_user_flags, uid)
        if not flags["admin_enabled"]:
            await query.answer("Сначала пройди проверку доступа.", show_alert=True)
            return
        await query.answer()
        await query.edit_message_text(
            "◈ VIPER / ADMIN ◈\n\nСекретная панель управления обращением.",
            reply_markup=admin_keyboard(flags["boss_mode"]),
        )
        return

    if data == "toggle_boss":
        if not is_admin(uid):
            await query.answer("Недоступно", show_alert=True)
            return
        flags = await asyncio.to_thread(get_user_flags, uid)
        if not flags["admin_enabled"]:
            await query.answer("Нет доступа", show_alert=True)
            return
        boss_mode = await asyncio.to_thread(toggle_boss_mode, uid)
        await query.answer("Обращение изменено")
        await query.edit_message_text(
            "◈ VIPER / ADMIN ◈\n\nНастройка обращения сохранена.",
            reply_markup=admin_keyboard(boss_mode),
        )
        return

    if data.startswith("reply_"):
        try:
            record_id = int(data.split("_", 1)[1])
        except ValueError:
            await query.answer("Некорректное сообщение", show_alert=True)
            return
        record = await asyncio.to_thread(get_reply_target, uid, record_id)
        if not record:
            await query.answer("Сообщение не найдено", show_alert=True)
            return
        if not record["can_reply"]:
            await query.answer("Время для ответа (24 часа) истекло", show_alert=True)
            return
        context.user_data["reply_to_record"] = record_id
        context.user_data.pop("sending_to", None)
        await query.answer()
        await query.message.reply_text(
            "↩️ Напиши ответ на это анонимное сообщение.\nМожно отправлять сколько угодно ответов в течение 24 часов.\nДля отмены — /cancel."
        )
        return

    await query.answer()


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user or message.chat.type != ChatType.PRIVATE:
        return
    text = (message.text or "").strip()
    uid = user.id

    # Secret admin handshake is accepted only from the configured Telegram account.
    if is_admin(uid) and text.casefold() == "viper":
        context.user_data["admin_challenge"] = True
        await message.reply_text("Добрый вечер, вам что так сложно написать две р")
        return
    if is_admin(uid) and context.user_data.pop("admin_challenge", False):
        if text.casefold() == "да":
            await asyncio.to_thread(set_admin_enabled, uid)
            await message.reply_text(
                "Доступ подтверждён. Добро пожаловать, Босс.",
                reply_markup=home_keyboard(uid),
            )
        else:
            await message.reply_text("Ладно, проверка отменена.", reply_markup=home_keyboard(uid))
        return

    # Existing optional username lookup command is preserved.
    if text.casefold() == "чек" and message.reply_to_message is not None:
        record = await asyncio.to_thread(lookup_sender, uid, message.reply_to_message.message_id)
        if record is not None:
            username = record["sender_username"]
            if username:
                await message.reply_text(f"Username отправителя: @{username}")
            else:
                await message.reply_text("У отправителя нет публичного username, поэтому показать его невозможно.")
            return

    # Replies to an incoming anonymous message: unlimited until 24h after receipt.
    reply_record_id = context.user_data.get("reply_to_record")
    if reply_record_id is not None:
        if not text:
            await message.reply_text("Ответ пустой. Напиши текст или /cancel.")
            return
        if len(text) > 3800:
            await message.reply_text("Сообщение слишком длинное. Максимум 3800 символов.")
            return
        record = await asyncio.to_thread(get_reply_target, uid, int(reply_record_id))
        if not record or not record["can_reply"]:
            context.user_data.pop("reply_to_record", None)
            await message.reply_text("Время для ответа истекло. Можно отвечать в течение 24 часов после получения сообщения.")
            return
        try:
            await context.bot.send_message(
                chat_id=record["sender_id"],
                text=f"Вам пришёл ответ на анонимное сообщение:\n\n{text}",
            )
            await message.reply_text("✓ Ответ отправлен анонимно.", reply_markup=home_keyboard(uid))
        except Exception:
            log.exception("Could not deliver anonymous reply")
            await message.reply_text("Не удалось доставить ответ. Возможно, отправитель заблокировал бота.")
        return

    target_id = context.user_data.get("sending_to")
    if target_id is None:
        if is_admin(uid) and text.casefold() == "админ панель":
            flags = await asyncio.to_thread(get_user_flags, uid)
            if flags["admin_enabled"]:
                await message.reply_text("◈ VIPER / ADMIN ◈", reply_markup=admin_keyboard(flags["boss_mode"]))
                return
        await message.reply_text(
            f"{boss_prefix(uid)}сначала получи свою ссылку или перейди по ссылке человека, которому хочешь написать.",
            reply_markup=home_keyboard(uid),
        )
        return

    if not text:
        await message.reply_text("Сообщение пустое. Напиши текст или /cancel.")
        return
    if len(text) > 3800:
        await message.reply_text("Сообщение слишком длинное. Максимум 3800 символов.")
        return

    try:
        delivered = await context.bot.send_message(
            chat_id=target_id,
            text=f"{boss_prefix(target_id)}вам новое анонимное сообщение!\n\n{text}",
        )
        await asyncio.to_thread(
            save_received_message, target_id, uid, user.username, delivered.message_id
        )
        # Attach a reply button after saving the record, so callback always resolves.
        with db_connect() as conn:
            record = conn.execute(
                "SELECT id FROM received_messages WHERE recipient_id=%s AND recipient_message_id=%s",
                (target_id, delivered.message_id),
            ).fetchone()
        if record:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=target_id,
                    message_id=delivered.message_id,
                    reply_markup=reply_keyboard(record["id"]),
                )
            except Exception:
                # Delivery and persistence already succeeded; don't report a false send failure.
                log.exception("Could not attach reply button to delivered message")
    except Exception:
        log.exception("Could not deliver anonymous message")
        await message.reply_text(
            "Не получилось доставить сообщение. Возможно, получатель ещё не запускал бота или заблокировал его."
        )
        return

    context.user_data.pop("sending_to", None)
    await message.reply_text("✓ Сообщение отправлено анонимно.", reply_markup=home_keyboard(uid))


async def handle_non_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    if context.user_data.get("reply_to_record") is not None:
        await message.reply_text("Пока ответы поддерживают только текст. Напиши текст или /cancel.")
    elif context.user_data.get("sending_to") is not None:
        await message.reply_text("Пока можно отправлять только текстовые сообщения. Напиши текст или /cancel.")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"VIPER bot is running")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server listening on port %s", PORT)


async def post_init(application: Application):
    await asyncio.to_thread(init_db)
    log.info("Database initialized")


def main():
    start_health_server()
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("myid", show_my_id))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, handle_non_text))
    log.info("Starting VIPER bot with polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
