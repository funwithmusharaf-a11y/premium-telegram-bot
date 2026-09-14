import os
import html
import sqlite3

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CopyTextButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_NEW_BOT_TOKEN_HERE")
DB = "information.db"

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["➕ SAVE INFORMATION", "📋 GET INFORMATION"],
        ["🗑️ DELETE INFORMATION", "⚙️ SETTINGS"],
        ["📊 DASHBOARD"],
    ],
    resize_keyboard=True,
)

SERVICE_MENU = ReplyKeyboardMarkup(
    [
        ["📸 INSTAGRAM", "🔵 FACEBOOK"],
        ["💬 WHATSAPP", "🎵 TIKTOK"],
        ["📧 GMAIL", "💬 TELEGRAM"],
        ["🎮 DISCORD", "➕ OTHER"],
        ["❌ CANCEL"],
    ],
    resize_keyboard=True,
)

SERVICE_MAP = {
    "📸 INSTAGRAM": "Instagram",
    "🔵 FACEBOOK": "Facebook",
    "💬 WHATSAPP": "WhatsApp",
    "🎵 TIKTOK": "TikTok",
    "📧 GMAIL": "Gmail",
    "💬 TELEGRAM": "Telegram",
    "🎮 DISCORD": "Discord",
    "➕ OTHER": "Other",
}


def db():
    return sqlite3.connect(DB)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            email TEXT NOT NULL,
            auth_key TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def safe(value):
    return html.escape(str(value))


def field_button(label, value):
    return InlineKeyboardButton(
        text=label,
        copy_text=CopyTextButton(text=str(value)),
    )


def account_keyboard(row):
    account_id, service, username, password, email, auth_key = row

    return InlineKeyboardMarkup(
        [
            [
                field_button(f"👤 {username}", username),
                field_button(f"🔐 {password}", password),
            ],
            [
                field_button(f"📧 {email}", email),
                field_button(f"🔑 {auth_key}", auth_key),
            ],
            [
                InlineKeyboardButton(
                    "✏️ EDIT",
                    callback_data=f"edit:{account_id}",
                ),
                InlineKeyboardButton(
                    "🗑️ DELETE",
                    callback_data=f"delete:{account_id}",
                ),
            ],
        ]
    )


def account_text(row):
    account_id, service, username, password, email, auth_key = row

    return (
        f"📋 <b>{safe(service.upper())}</b>\n\n"
        f"👤 <b>USERNAME</b> : {safe(username)}\n"
        f"🔐 <b>PASSWORD</b> : {safe(password)}\n"
        f"📧 <b>EMAIL</b> : {safe(email)}\n"
        f"🔑 <b>AUTH KEY</b> : {safe(auth_key)}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "🤖 <b>MUSHU INFO</b>\n\n"
        "Your information manager is ready.",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ <b>CANCELLED</b>",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM accounts WHERE user_id = ?",
        (user_id,),
    )
    total = cur.fetchone()[0]

    cur.execute(
        """
        SELECT service, COUNT(*)
        FROM accounts
        WHERE user_id = ?
        GROUP BY service
        ORDER BY COUNT(*) DESC
        """,
        (user_id,),
    )
    services = cur.fetchall()

    conn.close()

    icons = {
        "Instagram": "📸",
        "Facebook": "🔵",
        "WhatsApp": "💬",
        "TikTok": "🎵",
        "Gmail": "📧",
        "Telegram": "💬",
        "Discord": "🎮",
        "Other": "➕",
    }

    lines = [
        "╭────────────────────────╮",
        "│  📊 <b>MUSHU DASHBOARD</b>  │",
        "╰────────────────────────╯",
        "",
        f"📦 <b>TOTAL ACCOUNTS :</b> {total}",
        "",
    ]

    for service, count in services:
        lines.append(
            f"{icons.get(service, '📁')} "
            f"<b>{safe(service.upper())}</b> : {count}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def start_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "SERVICE"

    await update.message.reply_text(
        "✨ <b>SELECT SERVICE</b>",
        parse_mode="HTML",
        reply_markup=SERVICE_MENU,
    )


async def select_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ CANCEL":
        return await cancel(update, context)

    if text not in SERVICE_MAP:
        return

    context.user_data["service"] = SERVICE_MAP[text]
    context.user_data["state"] = "USERNAME"

    await update.message.reply_text(
        f"📱 <b>{safe(SERVICE_MAP[text].upper())}</b>\n\n"
        "👤 Send username:",
        parse_mode="HTML",
        reply_markup=SERVICE_MENU,
    )


async def save_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ CANCEL":
        return await cancel(update, context)

    if text in SERVICE_MAP:
        return await select_service(update, context)

    context.user_data["username"] = text
    context.user_data["state"] = "PASSWORD"

    await update.message.reply_text(
        "🔐 Send password:",
        parse_mode="HTML",
        reply_markup=SERVICE_MENU,
    )


async def save_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ CANCEL":
        return await cancel(update, context)

    if text in SERVICE_MAP:
        return await select_service(update, context)

    context.user_data["password"] = text
    context.user_data["state"] = "EMAIL"

    await update.message.reply_text(
        "📧 Send email:",
        parse_mode="HTML",
        reply_markup=SERVICE_MENU,
    )


async def save_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ CANCEL":
        return await cancel(update, context)

    if text in SERVICE_MAP:
        return await select_service(update, context)

    context.user_data["email"] = text
    context.user_data["state"] = "AUTH_KEY"

    await update.message.reply_text(
        "🔑 Send auth key:",
        parse_mode="HTML",
        reply_markup=SERVICE_MENU,
    )


async def save_auth_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ CANCEL":
        return await cancel(update, context)

    if text in SERVICE_MAP:
        return await select_service(update, context)

    service = context.user_data.get("service")
    username = context.user_data.get("username")
    password = context.user_data.get("password")
    email = context.user_data.get("email")

    if not all([service, username, password, email]):
        return await cancel(update, context)

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO accounts
        (user_id, service, username, password, email, auth_key)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            update.effective_user.id,
            service,
            username,
            password,
            email,
            text,
        ),
    )

    conn.commit()
    conn.close()
    context.user_data.clear()

    await update.message.reply_text(
        f"✅ <b>INFORMATION SAVED</b>\n\n"
        f"📱 {safe(service.upper())}",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def get_information(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, service, username, password, email, auth_key
        FROM accounts
        WHERE user_id = ?
        ORDER BY id
        """,
        (user_id,),
    )

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "📭 <b>NO INFORMATION SAVED</b>",
            parse_mode="HTML",
            reply_markup=MAIN_MENU,
        )
        return

    for row in rows:
        await update.message.reply_text(
            account_text(row),
            parse_mode="HTML",
            reply_markup=account_keyboard(row),
        )


async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "SEARCH"

    await update.message.reply_text(
        "🔍 <b>SEARCH INFORMATION</b>\n\n"
        "Send service, username or email:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            [["❌ CANCEL"]],
            resize_keyboard=True,
        ),
    )


async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()

    if query == "❌ CANCEL":
        return await cancel(update, context)

    user_id = update.effective_user.id
    pattern = f"%{query}%"

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, service, username, password, email, auth_key
        FROM accounts
        WHERE user_id = ?
        AND (
            service LIKE ?
            OR username LIKE ?
            OR email LIKE ?
        )
        ORDER BY id
        """,
        (user_id, pattern, pattern, pattern),
    )

    rows = cur.fetchall()
    conn.close()
    context.user_data.clear()

    if not rows:
        await update.message.reply_text(
            "🔍 <b>NO RESULTS FOUND</b>",
            parse_mode="HTML",
            reply_markup=MAIN_MENU,
        )
        return

    await update.message.reply_text(
        f"🔎 <b>RESULTS FOR:</b> {safe(query)}",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

    for row in rows:
        await update.message.reply_text(
            account_text(row),
            parse_mode="HTML",
            reply_markup=account_keyboard(row),
        )


async def start_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, service, username
        FROM accounts
        WHERE user_id = ?
        ORDER BY id
        """,
        (user_id,),
    )

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "📭 <b>NO INFORMATION TO DELETE</b>",
            parse_mode="HTML",
            reply_markup=MAIN_MENU,
        )
        return

    buttons = []
    for account_id, service, username in rows:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🗑️ {service} — {username}",
                    callback_data=f"delete:{account_id}",
                )
            ]
        )

    await update.message.reply_text(
        "🗑️ <b>SELECT INFORMATION TO DELETE</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, service, username
        FROM accounts
        WHERE user_id = ?
        ORDER BY id
        """,
        (user_id,),
    )

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "📭 <b>NO INFORMATION TO EDIT</b>",
            parse_mode="HTML",
            reply_markup=MAIN_MENU,
        )
        return

    buttons = []
    for account_id, service, username in rows:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"✏️ {service} — {username}",
                    callback_data=f"chooseedit:{account_id}",
                )
            ]
        )

    await update.message.reply_text(
        "✏️ <b>SELECT INFORMATION TO EDIT</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = update.effective_user.id

    if data == "close":
        await query.message.delete()
        return

    if data.startswith("delete:"):
        account_id = int(data.split(":", 1)[1])

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ DELETE",
                        callback_data=f"confirm_delete:{account_id}",
                    ),
                    InlineKeyboardButton(
                        "❌ CANCEL",
                        callback_data="close",
                    ),
                ]
            ]
        )

        await query.message.reply_text(
            "⚠️ <b>DELETE THIS INFORMATION?</b>\n\n"
            "Are you sure?",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if data.startswith("confirm_delete:"):
        account_id = int(data.split(":", 1)[1])

        conn = db()
        cur = conn.cursor()

        cur.execute(
            "DELETE FROM accounts WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        )

        deleted = cur.rowcount
        conn.commit()
        conn.close()

        if deleted:
            await query.message.edit_text(
                "🗑️ <b>INFORMATION DELETED</b>",
                parse_mode="HTML",
            )
        else:
            await query.message.edit_text(
                "❌ <b>INFORMATION NOT FOUND</b>",
                parse_mode="HTML",
            )
        return

    if data.startswith("chooseedit:"):
        account_id = int(data.split(":", 1)[1])

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "👤 USERNAME",
                        callback_data=f"edit:{account_id}:username",
                    ),
                    InlineKeyboardButton(
                        "🔐 PASSWORD",
                        callback_data=f"edit:{account_id}:password",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "📧 EMAIL",
                        callback_data=f"edit:{account_id}:email",
                    ),
                    InlineKeyboardButton(
                        "🔑 AUTH KEY",
                        callback_data=f"edit:{account_id}:auth_key",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "❌ CANCEL",
                        callback_data="close",
                    )
                ],
            ]
        )

        await query.message.reply_text(
            "✏️ <b>SELECT FIELD TO EDIT</b>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if data.startswith("edit:"):
        parts = data.split(":")
        if len(parts) != 3:
            return

        account_id = int(parts[1])
        field = parts[2]

        allowed = {
            "username": "USERNAME",
            "password": "PASSWORD",
            "email": "EMAIL",
            "auth_key": "AUTH KEY",
        }

        if field not in allowed:
            return

        context.user_data["state"] = "EDIT_VALUE"
        context.user_data["edit_id"] = account_id
        context.user_data["edit_field"] = field

        await query.message.reply_text(
            f"✏️ <b>EDIT {allowed[field]}</b>\n\n"
            "Send the new value:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                [["❌ CANCEL"]],
                resize_keyboard=True,
            ),
        )
        return


async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ CANCEL":
        return await cancel(update, context)

    account_id = context.user_data.get("edit_id")
    field = context.user_data.get("edit_field")

    allowed = {"username", "password", "email", "auth_key"}

    if not account_id or field not in allowed:
        return await cancel(update, context)

    conn = db()
    cur = conn.cursor()

    cur.execute(
        f"UPDATE accounts SET {field} = ? WHERE id = ? AND user_id = ?",
        (text, account_id, update.effective_user.id),
    )

    changed = cur.rowcount
    conn.commit()
    conn.close()
    context.user_data.clear()

    if changed:
        message = "✅ <b>INFORMATION UPDATED</b>"
    else:
        message = "❌ <b>INFORMATION NOT FOUND</b>"

    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ <b>SETTINGS</b>\n\n"
        "• PIN protection: OFF\n"
        "• Database: SQLite\n"
        "• Click a value button to copy it\n"
        "• Delete confirmation enabled",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get("state")

    if state == "SERVICE":
        return await select_service(update, context)

    if state == "USERNAME":
        return await save_username(update, context)

    if state == "PASSWORD":
        return await save_password(update, context)

    if state == "EMAIL":
        return await save_email(update, context)

    if state == "AUTH_KEY":
        return await save_auth_key(update, context)

    if state == "SEARCH":
        return await perform_search(update, context)

    if state == "EDIT_VALUE":
        return await edit_value(update, context)

    if text == "📊 DASHBOARD":
        return await dashboard(update, context)

    if text == "➕ SAVE INFORMATION":
        return await start_save(update, context)

    if text == "📋 GET INFORMATION":
        return await get_information(update, context)

    if text == "🔍 SEARCH INFORMATION":
        return await start_search(update, context)

    if text == "✏️ EDIT INFORMATION":
        return await start_edit(update, context)

    if text == "🗑️ DELETE INFORMATION":
        return await start_delete(update, context)

    if text == "⚙️ SETTINGS":
        return await settings(update, context)

    if text == "❌ CANCEL":
        return await cancel(update, context)


def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_NEW_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    init_db()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router)
    )

    # Render Web Service: use Telegram webhook and bind to Render's PORT.
    # Local/Termux: keep normal long-polling.
    if os.getenv("RENDER") == "true":
        port = int(os.getenv("PORT", "10000"))
        base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
        if not base_url:
            raise RuntimeError("RENDER_EXTERNAL_URL is missing on Render.")

        webhook_path = "telegram-webhook"
        webhook_url = f"{base_url}/{webhook_path}"
        secret = os.getenv("WEBHOOK_SECRET")

        print(f"🤖 MUSHU INFO WEBHOOK RUNNING ON 0.0.0.0:{port}")
        print(f"🌐 Webhook: {webhook_url}")

        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=webhook_path,
            webhook_url=webhook_url,
            secret_token=secret,
            drop_pending_updates=True,
        )
    else:
        print("🤖 MUSHU INFO IS RUNNING (POLLING)...")
        application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
