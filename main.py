import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
PORT = int(os.environ.get("PORT", "10000"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("anonymous-mail-bot")


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
            "SELECT link_code FROM users WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET username = %s WHERE user_id = %s",
                (username, user_id),
            )
            conn.commit()
            return row["link_code"]

        code = secrets.token_urlsafe(9)
        conn.execute(
            """
            INSERT INTO users(user_id, username, link_code)
            VALUES (%s, %s, %s)
            """,
            (user_id, username, code),
        )
        conn.commit()
        return code


def find_recipient_by_code(code: str):
    with db_connect() as conn:
        return conn.execute(
            "SELECT user_id FROM users WHERE link_code = %s",
            (code,),
        ).fetchone()


def save_received_message(
    recipient_id: int,
    sender_id: int,
    sender_username: str | None,
    recipient_message_id: int,
):
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO received_messages
                (recipient_id, sender_id, sender_username, recipient_message_id)
            VALUES (%s, %s, %s, %s)
            """,
            (recipient_id, sender_id, sender_username, recipient_message_id),
        )
        conn.commit()


def lookup_sender(recipient_id: int, recipient_message_id: int):
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT sender_username
            FROM received_messages
            WHERE recipient_id = %s AND recipient_message_id = %s
            """,
            (recipient_id, recipient_message_id),
        ).fetchone()


def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Получить ссылку", callback_data="get_link")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or update.effective_chat.type != ChatType.PRIVATE:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Открой меня в личных сообщениях, чтобы пользоваться ботом."
            )
        return

    register_user(user.id, user.username)

    if context.args:
        code = context.args[0]
        target = find_recipient_by_code(code)
        if not target:
            await update.effective_message.reply_text("Эта ссылка недействительна или больше не существует.")
            return
        target_id = target["user_id"]
        if target_id == user.id:
            await update.effective_message.reply_text("Это твоя собственная ссылка 🙂")
            return

        context.user_data["sending_to"] = target_id
        await update.effective_message.reply_text(
            "Напишите сообщение, которое хотите передать.\n\n"
            "Чтобы отменить отправку, напишите /cancel."
        )
        return

    context.user_data.pop("sending_to", None)
    await update.effective_message.reply_text(
        "Привет! Здесь можно получать анонимные сообщения.\n\n"
        "Нажми кнопку ниже, чтобы получить свою личную ссылку.",
        reply_markup=home_keyboard(),
    )


async def get_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    code = register_user(user.id, user.username)
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    await query.edit_message_text(
        f"Твоя личная ссылка:\n{link}\n\n"
        "Можешь отправить её в группу или друзьям. Любой, кто перейдёт по ней, "
        "сможет написать тебе сообщение.",
        reply_markup=home_keyboard(),
        disable_web_page_preview=True,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("sending_to", None)
    await update.effective_message.reply_text(
        "Отправка отменена.",
        reply_markup=home_keyboard(),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user or message.chat.type != ChatType.PRIVATE:
        return

    # "чек" works only as a reply to a message previously delivered by this bot.
    if (
        message.text.strip().casefold() == "чек"
        and message.reply_to_message is not None
    ):
        record = lookup_sender(user.id, message.reply_to_message.message_id)
        if record is not None:
            username = record["sender_username"]
            if username:
                await message.reply_text(f"Username отправителя: @{username}")
            else:
                await message.reply_text(
                    "У отправителя не установлен публичный username, "
                    "поэтому показать его невозможно."
                )
            return

    target_id = context.user_data.get("sending_to")
    if target_id is None:
        await message.reply_text(
            "Сначала перейди по личной ссылке человека, которому хочешь написать.",
            reply_markup=home_keyboard(),
        )
        return

    text = message.text.strip()
    if not text:
        await message.reply_text("Сообщение пустое. Напиши текст или /cancel.")
        return
    if len(text) > 3900:
        await message.reply_text("Сообщение слишком длинное. Отправь текст короче 3900 символов.")
        return

    try:
        delivered = await context.bot.send_message(
            chat_id=target_id,
            text=f"Вам новое сообщение!\n\n{text}",
        )
        save_received_message(
            recipient_id=target_id,
            sender_id=user.id,
            sender_username=user.username,
            recipient_message_id=delivered.message_id,
        )
    except Exception:
        log.exception("Could not deliver anonymous message")
        await message.reply_text(
            "Не получилось доставить сообщение. Возможно, получатель ещё не запускал бота "
            "или заблокировал его."
        )
        return

    context.user_data.pop("sending_to", None)
    await message.reply_text("Сообщение отправлено! ✉️", reply_markup=home_keyboard())


async def handle_non_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("sending_to") is not None:
        await update.effective_message.reply_text(
            "Пока можно отправлять только текстовые сообщения. Напиши текст или /cancel."
        )


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on port %s", PORT)


async def post_init(application: Application):
    init_db()
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
    application.add_handler(CallbackQueryHandler(get_link_callback, pattern="^get_link$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, handle_non_text))

    log.info("Starting bot with polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
