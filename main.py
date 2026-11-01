import os
import sqlite3
import secrets
import logging
from html import escape
from datetime import datetime, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

# Telegram anonymous discussion bot.
# Render: run this file as a Background Worker.
# Set BOT_TOKEN in Render environment variables.
# Set DATA_DIR=/var/data if using a Render persistent disk mounted at /var/data.

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("anonymous-chat")

TOKEN = os.getenv("BOT_TOKEN")
DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "anonymous_chat.sqlite3")

RU = {
    "welcome": "Добро пожаловать в боте для обсуждения анонимно",
    "choose_lang": "Выберите язык / Choose your language",
    "nickname": "Напишите свой никнейм. Он будет виден вашим друзьям, но не участникам групп.",
    "nickname_saved": "Ваш никнейм: <b>{}</b>\nОн отображается друзьям.",
    "menu": "Главное меню",
    "groups": "Мои группы",
    "create": "Создать группу",
    "friends": "Мои друзья",
    "settings": "Настройки",
    "no_groups": "У вас пока что нету групп.",
    "no_friends": "У вас пока что нету друзей.",
    "back": "Назад",
    "exit": "Выход",
    "enter_group": "Войти в группу",
    "group_name": "Напишите название группы.",
    "group_created": "Группа «{name}» создана!\nПригласительная ссылка:\n{link}",
    "invalid_name": "Название должно содержать от 1 до 40 символов.",
    "group_menu": "Управление группами",
    "notifications": "Уведомления",
    "mute": "Выключить уведомления",
    "unmute": "Включить уведомления",
    "delete_group": "Удалить группу (выйти)",
    "choose_group": "Выберите группу",
    "confirm_leave": "Вы точно хотите выйти из группы «{name}»?",
    "yes_leave": "Да, выйти",
    "cancel": "Отмена",
    "left": "Вы вышли из группы.",
    "muted": "Уведомления выключены.",
    "unmuted": "Уведомления включены.",
    "settings_title": "Настройки",
    "change_lang": "Поменять язык",
    "language_changed": "Язык изменён.",
    "friend_request": "Вам отправили запрос в друзья.",
    "accepted": "Запрос принят. Теперь вы друзья.",
    "declined": "Запрос отклонён.",
    "request_sent": "Запрос в друзья отправлен.",
    "already_friend": "Вы уже друзья.",
    "reply_hint": "Чтобы ответить, используйте ответ Telegram на сообщение. Чтобы отправить запрос в друзья, ответьте словом «друзья».",
    "unknown_reply": "Не удалось определить автора исходного сообщения.",
    "not_allowed": "Эта функция сейчас недоступна.",
    "group_closed": "Вы больше не состоите в этой группе.",
    "private_only": "Сначала откройте личный чат с ботом и нажмите /start.",
    "new_messages": "Пока вас не было:",
    "notifications_list": "Выберите группу, для которой хотите изменить уведомления.",
    "choose_friend": "Выберите друга для личной переписки.",
    "friend_chat": "Вы переписываетесь с <b>{}</b>. Напишите сообщение. Кнопка «Назад» вернёт в меню.",
    "friend_left": "Вы вышли из переписки.",
    "unknown": "Не понял сообщение. Используйте кнопки меню.",
    "joined": "Вы вступили в группу «{}».",
    "already_member": "Вы уже состоите в этой группе.",
    "bad_invite": "Ссылка недействительна или группа больше не существует.",
    "anonymous": "Аноним{}",
    "group_message": "<b>{}</b> написал в группу «{}»:\n{}",
    "group_reply": "<b>{}</b> ответил <b>{}</b>:\n{}",
    "friend_message": "<b>{}</b>:\n{}",
    "no_self": "Нельзя отправить запрос самому себе.",
}
EN = {
    "welcome": "Welcome to the anonymous discussion bot",
    "choose_lang": "Choose your language / Выберите язык",
    "nickname": "Send your nickname. Your friends can see it, but group members cannot.",
    "nickname_saved": "Your nickname: <b>{}</b>\nIt is visible to your friends.",
    "menu": "Main menu",
    "groups": "My groups",
    "create": "Create a group",
    "friends": "My friends",
    "settings": "Settings",
    "no_groups": "You don't have any groups yet.",
    "no_friends": "You don't have any friends yet.",
    "back": "Back",
    "exit": "Exit",
    "enter_group": "Enter group",
    "group_name": "Send the group name.",
    "group_created": "Group “{name}” created!\nInvite link:\n{link}",
    "invalid_name": "The name must be between 1 and 40 characters.",
    "group_menu": "Group settings",
    "notifications": "Notifications",
    "mute": "Mute notifications",
    "unmute": "Unmute notifications",
    "delete_group": "Leave group",
    "choose_group": "Choose a group",
    "confirm_leave": "Are you sure you want to leave “{name}”?",
    "yes_leave": "Yes, leave",
    "cancel": "Cancel",
    "left": "You left the group.",
    "muted": "Notifications muted.",
    "unmuted": "Notifications enabled.",
    "settings_title": "Settings",
    "change_lang": "Change language",
    "language_changed": "Language changed.",
    "friend_request": "You received a friend request.",
    "accepted": "Request accepted. You are now friends.",
    "declined": "Request declined.",
    "request_sent": "Friend request sent.",
    "already_friend": "You are already friends.",
    "reply_hint": "Reply to a message using Telegram's reply feature. To send a friend request, reply with the word “friends”.",
    "unknown_reply": "Couldn't identify the author of the original message.",
    "not_allowed": "This feature is unavailable right now.",
    "group_closed": "You are no longer a member of this group.",
    "private_only": "Open the bot's private chat and send /start first.",
    "new_messages": "While you were away:",
    "notifications_list": "Choose a group whose notifications you want to change.",
    "choose_friend": "Choose a friend to start a private chat.",
    "friend_chat": "You are chatting with <b>{}</b>. Send a message. Press “Back” to return.",
    "friend_left": "You left the conversation.",
    "unknown": "I didn't understand. Use the menu buttons.",
    "joined": "You joined “{}”.",
    "already_member": "You are already in this group.",
    "bad_invite": "This invite link is invalid or the group no longer exists.",
    "anonymous": "Anonymous {}",
    "group_message": "<b>{}</b> posted in “{}”:\n{}",
    "group_reply": "<b>{}</b> replied to <b>{}</b>:\n{}",
    "friend_message": "<b>{}</b>:\n{}",
    "no_self": "You can't send a friend request to yourself.",
}

# Per-user workflow states are kept in memory; core account/group data is in SQLite.
WAIT_NICK = "wait_nick"
WAIT_GROUP_NAME = "wait_group_name"
IN_GROUP = "in_group"
IN_FRIEND = "in_friend"
MAIN = "main"


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, nickname TEXT, lang TEXT DEFAULT 'ru'
        );
        CREATE TABLE IF NOT EXISTS groups_data(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            invite TEXT UNIQUE NOT NULL, owner_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS members(
            group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            anon_no INTEGER NOT NULL, muted INTEGER DEFAULT 0,
            last_seen INTEGER DEFAULT 0,
            PRIMARY KEY(group_id,user_id),
            UNIQUE(group_id,anon_no),
            FOREIGN KEY(group_id) REFERENCES groups_data(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS group_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL, anon_no INTEGER NOT NULL,
            reply_to INTEGER, body TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS friends(
            user_id INTEGER NOT NULL, friend_id INTEGER NOT NULL,
            PRIMARY KEY(user_id,friend_id)
        );
        CREATE TABLE IF NOT EXISTS friend_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER NOT NULL, to_id INTEGER NOT NULL,
            source_chat INTEGER, source_message INTEGER,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS message_map(
            chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            group_message_id INTEGER NOT NULL,
            PRIMARY KEY(chat_id,message_id)
        );
        """)
        # message_map lets replies to bot-relayed messages find the original group post.


def get_user(uid):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def lang(uid):
    u = get_user(uid)
    return (u["lang"] if u else "ru") or "ru"


def t(uid, key):
    return (EN if lang(uid) == "en" else RU)[key]


def ensure_user(uid):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))


def set_lang(uid, value):
    with db() as c:
        c.execute("UPDATE users SET lang=? WHERE user_id=?", (value, uid))


def menu_kb(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid, "groups"), callback_data="groups"),
         InlineKeyboardButton(t(uid, "create"), callback_data="create")],
        [InlineKeyboardButton(t(uid, "friends"), callback_data="friends"),
         InlineKeyboardButton(t(uid, "settings"), callback_data="settings")],
    ])


def back_kb(uid, data="menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t(uid, "back"), callback_data=data)]])


async def show_menu(target, uid, edit=False):
    text = t(uid, "menu")
    if edit:
        await target.edit_message_text(text, reply_markup=menu_kb(uid))
    else:
        await target.reply_text(text, reply_markup=menu_kb(uid))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    args = context.args
    if args and args[0].startswith("join_"):
        token = args[0][5:]
        await join_group(update, context, token)
        return
    user = get_user(uid)
    if not user["nickname"]:
        context.user_data["state"] = WAIT_NICK if user["lang"] else "choose_lang"
        await update.message.reply_text(
            t(uid, "welcome"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("RU", callback_data="lang_ru"),
                InlineKeyboardButton("ENG", callback_data="lang_en"),
            ]]),
        )
    else:
        context.user_data["state"] = MAIN
        await show_menu(update.message, uid)


async def join_group(update, context, token):
    uid = update.effective_user.id
    ensure_user(uid)
    with db() as c:
        g = c.execute("SELECT * FROM groups_data WHERE invite=?", (token,)).fetchone()
        if not g:
            await update.message.reply_text(t(uid, "bad_invite"))
            return
        existing = c.execute(
            "SELECT 1 FROM members WHERE group_id=? AND user_id=?", (g["id"], uid)
        ).fetchone()
        if existing:
            await update.message.reply_text(t(uid, "already_member"))
            return
        number = c.execute(
            "SELECT COALESCE(MAX(anon_no),0)+1 n FROM members WHERE group_id=?",
            (g["id"],),
        ).fetchone()["n"]
        c.execute("INSERT INTO members(group_id,user_id,anon_no) VALUES(?,?,?)",
                  (g["id"], uid, number))
    user = get_user(uid)
    if not user["nickname"]:
        context.user_data["pending_join"] = token
        await update.message.reply_text(
            t(uid, "welcome"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("RU", callback_data="lang_ru"),
                InlineKeyboardButton("ENG", callback_data="lang_en"),
            ]]),
        )
        return
    await update.message.reply_text(t(uid, "joined").format(g["name"]))
    await show_menu(update.message, uid)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid)
    data = q.data

    if data in ("lang_ru", "lang_en"):
        set_lang(uid, "ru" if data.endswith("ru") else "en")
        current = get_user(uid)
        if current and current["nickname"]:
            context.user_data["state"] = MAIN
            await q.edit_message_text(t(uid, "language_changed"), reply_markup=menu_kb(uid))
        else:
            context.user_data["state"] = WAIT_NICK
            await q.edit_message_text(t(uid, "nickname"))
        return

    if data.startswith("accept_") or data.startswith("decline_"):
        req_id = int(data.split("_", 1)[1])
        accept = data.startswith("accept_")
        with db() as c:
            req = c.execute("SELECT * FROM friend_requests WHERE id=? AND to_id=? AND status='pending'",
                            (req_id, uid)).fetchone()
            if not req:
                await q.edit_message_text(t(uid, "not_allowed"))
                return
            c.execute("UPDATE friend_requests SET status=? WHERE id=?",
                      ("accepted" if accept else "declined", req_id))
            if accept:
                c.execute("INSERT OR IGNORE INTO friends VALUES(?,?)", (uid, req["from_id"]))
                c.execute("INSERT OR IGNORE INTO friends VALUES(?,?)", (req["from_id"], uid))
        await q.edit_message_text(t(uid, "accepted" if accept else "declined"))
        try:
            await context.bot.send_message(req["from_id"], t(req["from_id"], "accepted" if accept else "declined"))
        except Exception:
            pass
        return

    if data == "menu":
        context.user_data["state"] = MAIN
        await show_menu(q, uid, edit=True)
    elif data == "groups":
        context.user_data["state"] = MAIN
        with db() as c:
            rows = c.execute("""SELECT g.id,g.name FROM groups_data g
                JOIN members m ON m.group_id=g.id WHERE m.user_id=? ORDER BY g.id""", (uid,)).fetchall()
        if not rows:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(t(uid, "notifications"), callback_data="notify_list")],
                [InlineKeyboardButton(t(uid, "delete_group"), callback_data="leave_list")],
                [InlineKeyboardButton(t(uid, "back"), callback_data="menu")],
            ])
            await q.edit_message_text(t(uid, "no_groups"), reply_markup=kb)
        else:
            buttons = [[InlineKeyboardButton(r["name"], callback_data=f"open_group_{r['id']}")] for r in rows]
            buttons += [
                [InlineKeyboardButton(t(uid, "notifications"), callback_data="notify_list")],
                [InlineKeyboardButton(t(uid, "delete_group"), callback_data="leave_list")],
                [InlineKeyboardButton(t(uid, "back"), callback_data="menu")],
            ]
            await q.edit_message_text(t(uid, "groups"), reply_markup=InlineKeyboardMarkup(buttons))
    elif data == "create":
        context.user_data["state"] = WAIT_GROUP_NAME
        await q.edit_message_text(t(uid, "group_name"), reply_markup=back_kb(uid))
    elif data.startswith("open_group_"):
        gid = int(data.rsplit("_", 1)[1])
        with db() as c:
            row = c.execute("""SELECT g.name,m.anon_no,m.last_seen FROM groups_data g
                JOIN members m ON m.group_id=g.id WHERE g.id=? AND m.user_id=?""", (gid, uid)).fetchone()
            if not row:
                await q.edit_message_text(t(uid, "group_closed"), reply_markup=back_kb(uid, "groups"))
                return
            # Digest of missed messages when notifications were muted.
            msgs = c.execute("""SELECT * FROM group_messages WHERE group_id=? AND id>? ORDER BY id LIMIT 100""",
                             (gid, row["last_seen"])).fetchall()
            latest = c.execute("SELECT COALESCE(MAX(id),0) n FROM group_messages WHERE group_id=?",
                               (gid,)).fetchone()["n"]
            c.execute("UPDATE members SET last_seen=? WHERE group_id=? AND user_id=?",
                      (latest, gid, uid))
        context.user_data.update(state=IN_GROUP, group_id=gid)
        digest = ""
        if msgs:
            lines = []
            for m in msgs:
                author = t(uid, "anonymous").format(m["anon_no"])
                lines.append(f"{author}: {m['body']}")
            digest = "\n\n" + t(uid, "new_messages") + "\n" + "\n".join(lines)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid, "back"), callback_data="groups")],
            [InlineKeyboardButton(t(uid, "mute"), callback_data=f"mute_{gid}")],
        ])
        await q.edit_message_text(f"«{row['name']}»{digest}\n\n{t(uid, 'reply_hint')}", reply_markup=kb)
    elif data == "friends":
        with db() as c:
            rows = c.execute("""SELECT u.user_id,u.nickname FROM friends f
                JOIN users u ON u.user_id=f.friend_id WHERE f.user_id=? ORDER BY u.nickname""",
                             (uid,)).fetchall()
        if not rows:
            await q.edit_message_text(t(uid, "no_friends"), reply_markup=back_kb(uid))
        else:
            buttons = [[InlineKeyboardButton(r["nickname"] or "Friend", callback_data=f"friend_{r['user_id']}")] for r in rows]
            buttons.append([InlineKeyboardButton(t(uid, "back"), callback_data="menu")])
            await q.edit_message_text(t(uid, "choose_friend"), reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("friend_"):
        fid = int(data.split("_", 1)[1])
        with db() as c:
            ok = c.execute("SELECT 1 FROM friends WHERE user_id=? AND friend_id=?", (uid, fid)).fetchone()
            friend = c.execute("SELECT nickname FROM users WHERE user_id=?", (fid,)).fetchone()
        if not ok or not friend:
            await q.edit_message_text(t(uid, "not_allowed"), reply_markup=back_kb(uid))
            return
        context.user_data.update(state=IN_FRIEND, friend_id=fid)
        await q.edit_message_text(t(uid, "friend_chat").format(friend["nickname"] or "Friend"),
                                  reply_markup=back_kb(uid, "friends"))
    elif data == "settings":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid, "change_lang"), callback_data="change_lang")],
            [InlineKeyboardButton(t(uid, "back"), callback_data="menu")],
        ])
        await q.edit_message_text(t(uid, "settings_title"), reply_markup=kb)
    elif data == "change_lang":
        await q.edit_message_text(t(uid, "choose_lang"), reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("RU", callback_data="lang_ru"),
            InlineKeyboardButton("ENG", callback_data="lang_en"),
        ], [InlineKeyboardButton(t(uid, "back"), callback_data="settings")]]))
    elif data == "notify_list":
        with db() as c:
            rows = c.execute("""SELECT g.id,g.name,m.muted FROM groups_data g
                JOIN members m ON m.group_id=g.id WHERE m.user_id=?""", (uid,)).fetchall()
        buttons = [[InlineKeyboardButton(
            f"{'🔕' if r['muted'] else '🔔'} {r['name']}",
            callback_data=f"mute_{r['id']}"
        )] for r in rows]
        buttons.append([InlineKeyboardButton(t(uid, "back"), callback_data="groups")])
        await q.edit_message_text(t(uid, "notifications_list"), reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("mute_"):
        gid = int(data.split("_", 1)[1])
        with db() as c:
            row = c.execute("SELECT muted FROM members WHERE group_id=? AND user_id=?", (gid, uid)).fetchone()
            if not row:
                await q.edit_message_text(t(uid, "group_closed"), reply_markup=back_kb(uid, "groups"))
                return
            new_value = 0 if row["muted"] else 1
            c.execute("UPDATE members SET muted=? WHERE group_id=? AND user_id=?", (new_value, gid, uid))
        await q.edit_message_text(t(uid, "muted" if new_value else "unmuted"),
                                  reply_markup=back_kb(uid, "notify_list"))
    elif data == "leave_list":
        with db() as c:
            rows = c.execute("""SELECT g.id,g.name FROM groups_data g JOIN members m
                ON m.group_id=g.id WHERE m.user_id=?""", (uid,)).fetchall()
        buttons = [[InlineKeyboardButton(r["name"], callback_data=f"leave_confirm_{r['id']}")] for r in rows]
        buttons.append([InlineKeyboardButton(t(uid, "back"), callback_data="groups")])
        await q.edit_message_text(t(uid, "choose_group"), reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("leave_confirm_"):
        gid = int(data.rsplit("_", 1)[1])
        with db() as c:
            g = c.execute("""SELECT g.name FROM groups_data g JOIN members m ON m.group_id=g.id
                WHERE g.id=? AND m.user_id=?""", (gid, uid)).fetchone()
        if not g:
            await q.edit_message_text(t(uid, "group_closed"), reply_markup=back_kb(uid, "groups"))
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid, "yes_leave"), callback_data=f"leave_yes_{gid}")],
            [InlineKeyboardButton(t(uid, "cancel"), callback_data="leave_list")],
        ])
        await q.edit_message_text(t(uid, "confirm_leave").format(name=g["name"]), reply_markup=kb)
    elif data.startswith("leave_yes_"):
        gid = int(data.rsplit("_", 1)[1])
        with db() as c:
            c.execute("DELETE FROM members WHERE group_id=? AND user_id=?", (gid, uid))
        context.user_data["state"] = MAIN
        await q.edit_message_text(t(uid, "left"), reply_markup=back_kb(uid, "groups"))


async def request_friend(update, context, target_id):
    uid = update.effective_user.id
    if uid == target_id:
        await update.message.reply_text(t(uid, "no_self"))
        return
    with db() as c:
        exists = c.execute("SELECT 1 FROM friends WHERE user_id=? AND friend_id=?", (uid, target_id)).fetchone()
        if exists:
            await update.message.reply_text(t(uid, "already_friend"))
            return
        pending = c.execute("""SELECT id FROM friend_requests WHERE from_id=? AND to_id=? AND status='pending'""",
                            (uid, target_id)).fetchone()
        if pending:
            await update.message.reply_text(t(uid, "request_sent"))
            return
        cur = c.execute("INSERT INTO friend_requests(from_id,to_id) VALUES(?,?)", (uid, target_id))
        req_id = cur.lastrowid
    try:
        await context.bot.send_message(
            target_id, t(target_id, "friend_request"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅", callback_data=f"accept_{req_id}"),
                InlineKeyboardButton("❌", callback_data=f"decline_{req_id}"),
            ]]),
        )
        await update.message.reply_text(t(uid, "request_sent"))
    except Exception:
        await update.message.reply_text(t(uid, "not_allowed"))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    ensure_user(uid)
    text = (update.message.text or "").strip()
    state = context.user_data.get("state", MAIN)
    user = get_user(uid)

    if not user["nickname"]:
        if state == "choose_lang":
            await update.message.reply_text(t(uid, "choose_lang"), reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("RU", callback_data="lang_ru"),
                InlineKeyboardButton("ENG", callback_data="lang_en"),
            ]]))
            return
        if state == WAIT_NICK:
            if not text or len(text) > 24:
                await update.message.reply_text(t(uid, "invalid_name"))
                return
            with db() as c:
                c.execute("UPDATE users SET nickname=? WHERE user_id=?", (text, uid))
            context.user_data["state"] = MAIN
            pending = context.user_data.pop("pending_join", None)
            await update.message.reply_text(t(uid, "nickname_saved").format(escape(text)))
            if pending:
                with db() as c:
                    g = c.execute("SELECT name FROM groups_data WHERE invite=?", (pending,)).fetchone()
                if g:
                    await update.message.reply_text(t(uid, "joined").format(escape(g["name"])))
            await show_menu(update.message, uid)
            return

    if text.lower() in ("/back", "назад", "back"):
        context.user_data["state"] = MAIN
        await show_menu(update.message, uid)
        return

    if state == WAIT_GROUP_NAME:
        if not text or len(text) > 40:
            await update.message.reply_text(t(uid, "invalid_name"), reply_markup=back_kb(uid))
            return
        token = secrets.token_urlsafe(9)
        with db() as c:
            cur = c.execute("INSERT INTO groups_data(name,invite,owner_id) VALUES(?,?,?)",
                            (text, token, uid))
            gid = cur.lastrowid
            c.execute("INSERT INTO members(group_id,user_id,anon_no) VALUES(?,?,1)", (gid, uid))
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=join_{token}"
        context.user_data["state"] = MAIN
        await update.message.reply_text(t(uid, "group_created").format(name=escape(text), link=link),
                                        disable_web_page_preview=True)
        await show_menu(update.message, uid)
        return

    if state == IN_GROUP:
        gid = context.user_data.get("group_id")
        reply_to = None
        if update.message.reply_to_message:
            with db() as c:
                mapped = c.execute(
                    "SELECT group_message_id FROM message_map WHERE chat_id=? AND message_id=?",
                    (uid, update.message.reply_to_message.message_id),
                ).fetchone()
            if mapped:
                reply_to = mapped["group_message_id"]
                with db() as c:
                    original = c.execute("SELECT sender_id FROM group_messages WHERE id=?", (reply_to,)).fetchone()
                if original and text.lower() in ("друзья", "friend", "friends"):
                    await request_friend(update, context, original["sender_id"])
                    return
        await relay_group_message(update, context, gid, text, reply_to=reply_to)
        return

    if state == IN_FRIEND:
        fid = context.user_data.get("friend_id")
        with db() as c:
            ok = c.execute("SELECT 1 FROM friends WHERE user_id=? AND friend_id=?", (uid, fid)).fetchone()
            nick = c.execute("SELECT nickname FROM users WHERE user_id=?", (uid,)).fetchone()["nickname"]
        if not ok:
            context.user_data["state"] = MAIN
            await update.message.reply_text(t(uid, "not_allowed"))
            return
        try:
            await context.bot.send_message(fid, t(fid, "friend_message").format(escape(nick or "Friend"), escape(text)),
                                           parse_mode=ParseMode.HTML)
            await update.message.reply_text("✓")
        except Exception:
            await update.message.reply_text(t(uid, "not_allowed"))
        return

    # Friend request is triggered by replying to a relayed group message with the word.
    if update.message.reply_to_message:
        original = update.message.reply_to_message
        mapped = None
        with db() as c:
            mapped = c.execute("SELECT group_message_id FROM message_map WHERE chat_id=? AND message_id=?",
                               (uid, original.message_id)).fetchone()
            if mapped:
                gm = c.execute("SELECT sender_id FROM group_messages WHERE id=?",
                               (mapped["group_message_id"],)).fetchone()
        if mapped and gm:
            if text.lower() in ("друзья", "friend", "friends"):
                await request_friend(update, context, gm["sender_id"])
            else:
                # A reply made from the main menu can still be forwarded to the group.
                with db() as c:
                    membership = c.execute("""SELECT group_id FROM members WHERE user_id=? AND group_id=(
                        SELECT group_id FROM group_messages WHERE id=?)""",
                        (uid, mapped["group_message_id"])).fetchone()
                if membership:
                    await relay_group_message(update, context, membership["group_id"], text,
                                              reply_to=mapped["group_message_id"])
                    return
    await update.message.reply_text(t(uid, "unknown"), reply_markup=menu_kb(uid))


async def relay_group_message(update, context, gid, body, reply_to=None):
    uid = update.effective_user.id
    with db() as c:
        member = c.execute("SELECT anon_no,last_seen FROM members WHERE group_id=? AND user_id=?",
                           (gid, uid)).fetchone()
        group = c.execute("SELECT name FROM groups_data WHERE id=?", (gid,)).fetchone()
        if not member or not group:
            await update.message.reply_text(t(uid, "group_closed"), reply_markup=back_kb(uid))
            context.user_data["state"] = MAIN
            return
        reply = c.execute("SELECT anon_no,body FROM group_messages WHERE id=? AND group_id=?",
                          (reply_to, gid)).fetchone() if reply_to else None
        cur = c.execute("""INSERT INTO group_messages(group_id,sender_id,anon_no,reply_to,body,created_at)
            VALUES(?,?,?,?,?,?)""",
            (gid, uid, member["anon_no"], reply_to, body, datetime.now(timezone.utc).isoformat()))
        msg_id = cur.lastrowid
        recipients = c.execute("""SELECT user_id,muted FROM members WHERE group_id=? AND user_id<>?""",
                               (gid, uid)).fetchall()
    author = t(uid, "anonymous").format(member["anon_no"])
    if reply:
        reply_author = t(uid, "anonymous").format(reply["anon_no"])
        outgoing = t(uid, "group_reply").format(author, reply_author, escape(body))
    else:
        outgoing = t(uid, "group_message").format(author, escape(group["name"]), escape(body))
    for r in recipients:
        if r["muted"]:
            continue
        try:
            sent = await context.bot.send_message(r["user_id"], outgoing, parse_mode=ParseMode.HTML)
            with db() as c:
                c.execute("INSERT OR REPLACE INTO message_map(chat_id,message_id,group_message_id) VALUES(?,?,?)",
                          (r["user_id"], sent.message_id, msg_id))
                if not r["muted"]:
                    c.execute("UPDATE members SET last_seen=? WHERE group_id=? AND user_id=?",
                              (msg_id, gid, r["user_id"]))
        except Exception as e:
            log.info("Could not deliver group message to %s: %s", r["user_id"], e)
    await update.message.reply_text("✓")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(t(uid, "unknown"), reply_markup=menu_kb(uid))


def main():
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN environment variable before starting.")
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    log.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
