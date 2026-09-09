import os, io, uuid, asyncio, sqlite3, shutil
from datetime import datetime, timezone
from aiohttp import web
from dotenv import load_dotenv
import qrcode

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, InputMediaPhoto, InputMediaVideo
)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
ENV_UPI = os.getenv("UPI_ID", "").strip()
PORT = int(os.getenv("PORT", "10000") or 10000)
DB_PATH = os.getenv("DB_PATH", "bot.db")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

bot = Bot(TOKEN)
dp = Dispatcher()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA busy_timeout=5000")

db.executescript("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    language TEXT DEFAULT 'en',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS new_user_notifications(
    user_id INTEGER PRIMARY KEY,
    notified_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    button_text TEXT NOT NULL,
    price INTEGER NOT NULL,
    description_en TEXT DEFAULT '',
    description_hi TEXT DEFAULT '',
    access_link TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'plan',
    plan_id INTEGER DEFAULT NULL,
    media_type TEXT NOT NULL,
    file_id TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS user_media_messages(
    user_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY(user_id, message_id)
);
CREATE TABLE IF NOT EXISTS user_screen_messages(
    user_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY(user_id, message_id)
);
CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id TEXT UNIQUE,
    user_id INTEGER,
    plan_id INTEGER,
    amount INTEGER,
    status TEXT DEFAULT 'pending',
    proof_file_id TEXT DEFAULT '',
    created_at TEXT,
    expires_at INTEGER DEFAULT 0,
    chat_id INTEGER DEFAULT 0,
    message_id INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT
);
""")
db.commit()

# Payment message migration: lets the countdown recover after a bot restart.
for _col, _typ in (("chat_id", "INTEGER DEFAULT 0"), ("message_id", "INTEGER DEFAULT 0")):
    try:
        db.execute(f"ALTER TABLE payments ADD COLUMN {_col} {_typ}")
        db.commit()
    except sqlite3.OperationalError:
        pass

def now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def setting(key, default=""):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r:
        value = r["value"]
        if key == "upi_id" and not str(value).strip() and ENV_UPI:
            return ENV_UPI
        return value
    if key == "upi_id" and ENV_UPI:
        return ENV_UPI
    return default

def set_setting(key, value):
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value))
    )
    db.commit()

def is_admin(uid):
    return uid == OWNER_ID

# Per-user temporary conversation state for admin edits and payment proof uploads.
state = {}
# Keep payment timer tasks strongly referenced so they cannot be garbage-collected.
payment_timer_tasks = set()

async def clear_user_media(uid):
    """Delete EVERY message belonging to the current welcome/plan screen.

    Media albums are multiple Telegram messages, so every returned message_id
    is persisted and deleted individually. Payment messages are never tracked
    here and therefore are not touched.
    """
    ids = set()
    for table in ("user_media_messages", "user_screen_messages"):
        rows = db.execute(
            f"SELECT message_id FROM {table} WHERE user_id=?", (uid,)
        ).fetchall()
        ids.update(int(r["message_id"]) for r in rows)

    # Delete newest first; this also removes the text/card before its media
    # only when Telegram requires a specific order, while still deleting all.
    for message_id in sorted(ids, reverse=True):
        try:
            await bot.delete_message(chat_id=uid, message_id=message_id)
        except Exception as e:
            err = str(e).lower()
            # These are harmless when navigating quickly or after a restart.
            if "message to delete not found" not in err and "message can't be deleted" not in err:
                print(f"Screen message delete failed uid={uid} message_id={message_id}: {e!r}")

    # Always clear the tracking rows so an old screen can never be mixed with
    # the next plan/welcome screen.
    db.execute("DELETE FROM user_media_messages WHERE user_id=?", (uid,))
    db.execute("DELETE FROM user_screen_messages WHERE user_id=?", (uid,))
    db.commit()

def save_user_media(uid, message_ids):
    """Track all messages belonging to the current welcome/plan screen.
    Despite the legacy function name, this may contain media + text message IDs.
    """
    ids = [int(mid) for mid in message_ids if mid]
    db.execute("DELETE FROM user_media_messages WHERE user_id=?", (uid,))
    db.execute("DELETE FROM user_screen_messages WHERE user_id=?", (uid,))
    if ids:
        pairs = [(uid, mid) for mid in ids]
        db.executemany(
            "INSERT OR IGNORE INTO user_media_messages(user_id,message_id) VALUES(?,?)",
            pairs
        )
        db.executemany(
            "INSERT OR IGNORE INTO user_screen_messages(user_id,message_id) VALUES(?,?)",
            pairs
        )
    db.commit()

# Safe, neutral default plan names. Admin can rename them.
if not setting("welcome_en"):
    set_setting("welcome_en", "👋 Welcome to our Premium Membership!\n\nChoose a plan below to continue.")
if not setting("welcome_hi"):
    set_setting("welcome_hi", "👋 हमारी प्रीमियम सदस्यता में आपका स्वागत है!\n\nजारी रखने के लिए नीचे अपनी योजना चुनें।")
if not setting("upi_id") and ENV_UPI:
    set_setting("upi_id", ENV_UPI)
if not setting("upi_name"):
    set_setting("upi_name", "Premium Membership")
if not setting("purchase_notifications"):
    set_setting("purchase_notifications", "on")

if db.execute("SELECT COUNT(*) c FROM plans").fetchone()["c"] == 0:
    defaults = [
        ("Plan 1", "Plan 1", 54, "Premium access", "प्रीमियम एक्सेस"),
        ("Plan 2", "Plan 2", 94, "Premium access", "प्रीमियम एक्सेस"),
        ("Plan 3", "Plan 3", 148, "Premium access", "प्रीमियम एक्सेस"),
        ("Plan 4", "Plan 4", 179, "Premium access", "प्रीमियम एक्सेस"),
        ("Plan 5", "Plan 5", 199, "Premium access", "प्रीमियम एक्सेस"),
        ("All In One", "All In One", 249, "Complete premium access", "पूरा प्रीमियम एक्सेस"),
    ]
    db.executemany(
        "INSERT INTO plans(name,button_text,price,description_en,description_hi,sort_order) VALUES(?,?,?,?,?,?)",
        [(a,b,c,d,e,i) for i,(a,b,c,d,e) in enumerate(defaults)]
    )
    db.commit()

# Telegram clients decide the visual appearance of inline buttons.
# The Bot API supports success/danger styles on supported clients.
def btn(text, data, style="primary"):
    # Telegram Bot API supports: primary=blue, success=green, danger=red.
    # Use blue as the default so admin/menu buttons match the reference UI.
    kwargs = {"text": text, "callback_data": data, "style": style}
    return InlineKeyboardButton(**kwargs)

def back(data="admin"):
    return btn("🔙 Back", data, "danger")

def media_count(scope, plan_id=None):
    if scope == "start":
        return db.execute("SELECT COUNT(*) c FROM media WHERE scope='start'").fetchone()["c"]
    return db.execute("SELECT COUNT(*) c FROM media WHERE scope='plan' AND plan_id=?", (plan_id,)).fetchone()["c"]

def media_rows(scope, plan_id=None):
    if scope == "start":
        return db.execute("SELECT * FROM media WHERE scope='start' ORDER BY sort_order,id").fetchall()
    return db.execute("SELECT * FROM media WHERE scope='plan' AND plan_id=? ORDER BY sort_order,id", (plan_id,)).fetchall()

async def send_media_set(chat_id, scope, plan_id=None, caption=None, parse_mode=None):
    """Send up to 6 media items and return their Telegram message IDs."""
    rows = media_rows(scope, plan_id)[:6]
    if not rows:
        return []
    if len(rows) == 1:
        r = rows[0]
        if r["media_type"] == "photo":
            msg = await bot.send_photo(chat_id, r["file_id"], caption=caption, parse_mode=parse_mode)
        else:
            msg = await bot.send_video(chat_id, r["file_id"], caption=caption, parse_mode=parse_mode)
        return [msg.message_id]

    items = []
    for i, r in enumerate(rows):
        item_caption = caption if i == 0 else None
        if r["media_type"] == "photo":
            items.append(InputMediaPhoto(media=r["file_id"], caption=item_caption, parse_mode=parse_mode))
        else:
            items.append(InputMediaVideo(media=r["file_id"], caption=item_caption, parse_mode=parse_mode))
    messages = await bot.send_media_group(chat_id, media=items)
    return [msg.message_id for msg in messages]

def media_manage_kb(scope, plan_id=None):
    count = media_count(scope, plan_id)
    prefix = "startmedia" if scope == "start" else f"planmedia:{plan_id}"
    back_data = "adm:start" if scope == "start" else f"editplan:{plan_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(f"➕ Add Media ({count}/6)", f"mediaadd:{prefix}", "success")],
        [btn("🗑️ Clear All Media", f"mediaclear:{prefix}", "danger")],
        [back(back_data)]
    ])

def user_plans_kb():
    rows = []
    for p in db.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY sort_order,id"):
        rows.append([btn(f"📦 {p['button_text']} — ₹{p['price']}", f"plan:{p['id']}", "success")])
    rows.append([back("start")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("✏️ Start Message", "adm:start"), btn("📋 Plans", "adm:plans")],
        [btn("💳 Payment / UPI", "adm:upi"), btn("🤖 Bot Information", "adm:bot")],
        [btn("📊 Statistics", "adm:stats"), btn("💳 Payments", "adm:payments")],
        [btn("📢 Broadcast", "adm:broadcast"), btn("🔔 Purchase Notifications", "adm:notifications")],
        [btn("🧾 Payment Proof Settings", "adm:proof")],
        [btn("💾 Export Database", "adm:export"), btn("📥 Import Database", "adm:import")],
        [back("start")]
    ])

def plan_list_kb():
    rows = []
    for p in db.execute("SELECT * FROM plans ORDER BY sort_order,id"):
        icon = "🟢" if p["enabled"] else "🔴"
        rows.append([btn(f"{p['name']} ({icon} on)" if p["enabled"] else f"{p['name']} ({icon} off)",
                          f"editplan:{p['id']}", "success" if p["enabled"] else "danger")])
    rows.append([btn("➕ Add New Plan", "addplan", "success")])
    rows.append([back()])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def plan_editor_kb(pid):
    p = db.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
    if not p:
        return InlineKeyboardMarkup(inline_keyboard=[[back("adm:plans")]])
    status = "Disable Plan" if p["enabled"] else "Enable Plan"
    style = "danger" if p["enabled"] else "success"
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Edit Title", f"edit:title:{pid}"), btn("Edit Button", f"edit:button:{pid}")],
        [btn("Edit Description", f"edit:desc_en:{pid}")],
        [btn("Edit Hindi Description", f"edit:desc_hi:{pid}")],
        [btn("💰 Change Price", f"edit:price:{pid}")],
        [btn("🔗 Access Link", f"edit:link:{pid}")],
        [btn(f"🖼️ Plan Media ({media_count('plan', pid)}/6)", f"plan_media:{pid}")],
        [btn(status, f"toggle:{pid}", style)],
        [btn("⬆️ Move Up", f"moveup:{pid}"), btn("⬇️ Move Down", f"movedown:{pid}")],
        [btn("🗑️ Delete", f"delete:{pid}", "danger")],
        [back("adm:plans")]
    ])

async def show_text_or_caption(message, text, kb, parse_mode=None):
    # Never call edit_text on a photo/video message.
    if message.photo or message.video or message.document:
        try:
            return await message.edit_caption(caption=text, reply_markup=kb, parse_mode=parse_mode)
        except Exception:
            return await message.answer(text, reply_markup=kb, parse_mode=parse_mode)
    return await message.edit_text(text, reply_markup=kb, parse_mode=parse_mode)

def lang(uid):
    r = db.execute("SELECT language FROM users WHERE id=?", (uid,)).fetchone()
    return r["language"] if r else "en"

def user_text(uid, key, *args):
    hi = lang(uid) == "hi"
    texts = {
        "choose": ("🌐 Please choose your language\nकृपया अपनी भाषा चुनें:", "🌐 Please choose your language\nकृपया अपनी भाषा चुनें:"),
        "plans": ("📋 अपनी योजना चुनें:", "📋 Choose Your Plan"),
        "payment": ("💳 भुगतान करें", "💳 Scan & Pay"),
        "paid": ("✅ मैंने भुगतान कर दिया", "✅ I Have Paid"),
        "back": ("🔙 वापस", "🔙 Back"),
        "proof": ("📸 कृपया अपने भुगतान का स्क्रीनशॉट यहाँ भेजें।", "📸 Please send your payment screenshot here."),
        "pending": ("🟡 भुगतान सत्यापन के लिए लंबित है।", "🟡 Payment is pending verification."),
        "expired": ("⏰ भुगतान QR समाप्त हो गया", "⏰ Payment QR Expired"),
        "retry": ("🔄 दोबारा भुगतान करें", "🔄 Retry Payment"),
    }
    pair = texts.get(key)
    if not pair:
        return key
    return pair[0] if hi else pair[1]

def make_qr(amount, payment_id):
    upi = setting("upi_id", ENV_UPI).strip()
    if not upi:
        return None
    name = setting("upi_name", "Premium Membership").replace("\n", " ").strip()
    data = f"upi://pay?pa={upi}&pn={name}&am={amount}.00&cu=INR&tn={payment_id}"
    image = qrcode.make(data)
    bio = io.BytesIO()
    image.save(bio, format="PNG")
    return bio.getvalue()

def payment_row(payment_id):
    return db.execute("""
        SELECT p.*, pl.name plan_name, pl.access_link
        FROM payments p LEFT JOIN plans pl ON pl.id=p.plan_id
        WHERE p.payment_id=?
    """, (payment_id,)).fetchone()

def payment_text(uid, p, remaining):
    total = 120
    remaining = max(0, remaining)
    percent = int((remaining / total) * 100)
    filled = max(0, min(20, round(percent / 5)))
    bar = "█" * filled + "░" * (20 - filled)
    if lang(uid) == "hi":
        return (
            "💳 <b>भुगतान करें</b>\n\n"
            f"📦 <b>योजना:</b> {p['plan_name']}\n"
            f"💰 <b>राशि:</b> ₹{p['amount']}\n\n"
            f"⏱️ <b>समय शेष:</b> {remaining//60:02d}:{remaining%60:02d}\n"
            f"{bar} {percent}%\n\n"
            f"🆔 <b>भुगतान आईडी:</b> <code>{p['payment_id']}</code>\n\n"
            "📲 QR स्कैन करके भुगतान करें, फिर “मैंने भुगतान कर दिया” दबाएँ।"
        )
    return (
        "💳 <b>Scan & Pay</b>\n\n"
        f"📦 <b>Plan:</b> {p['plan_name']}\n"
        f"💰 <b>Amount:</b> ₹{p['amount']}\n\n"
        f"⏱️ <b>Time Remaining:</b> {remaining//60:02d}:{remaining%60:02d}\n"
        f"{bar} {percent}%\n\n"
        f"🆔 <b>Payment ID:</b> <code>{p['payment_id']}</code>\n\n"
        "📲 Scan and pay, then tap “I Have Paid”."
    )

def payment_kb(uid, payment_id, plan_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(user_text(uid, "paid"), f"paid:{payment_id}", "success")],
        [btn(user_text(uid, "back"), f"plan:{plan_id}", "danger")]
    ])

async def create_payment_message(message, uid, plan_id):
    p0 = db.execute("SELECT * FROM plans WHERE id=? AND enabled=1", (plan_id,)).fetchone()
    if not p0:
        await message.answer("⚠️ Plan is unavailable.")
        return
    payid = "PAY-" + uuid.uuid4().hex[:8].upper()
    expires = int(datetime.now(timezone.utc).timestamp()) + 120
    db.execute(
        "INSERT INTO payments(payment_id,user_id,plan_id,amount,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
        (payid, uid, plan_id, p0["price"], "pending", now_text(), expires)
    )
    db.commit()
    p = payment_row(payid)
    q = make_qr(p["amount"], payid)
    if not q:
        await message.answer(
            "⚠️ UPI ID is not configured.\nPlease ask the admin to configure UPI ID.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back(f"plan:{plan_id}")]])
        )
        return

    caption = payment_text(uid, p, 120)
    sent = await message.answer_photo(
        BufferedInputFile(q, filename="payment_qr.png"),
        caption=caption,
        reply_markup=payment_kb(uid, payid, plan_id),
        parse_mode="HTML"
    )
    db.execute("UPDATE payments SET chat_id=?, message_id=? WHERE payment_id=?", (sent.chat.id, sent.message_id, payid))
    db.commit()
    # A single global worker owns all countdown edits. Do not start a second
    # per-payment timer here, otherwise two tasks race to edit the same caption
    # and can cause Telegram edit/flood errors.

async def payment_timer(chat_id, message_id, payid, uid, plan_id):
    """Reliable 2-minute payment countdown.
    Uses the stored absolute expiry time, survives Telegram edit errors,
    and always attempts to mark the payment expired at the deadline.
    """
    while True:
        p = payment_row(payid)
        if not p or p["status"] != "pending":
            return

        remaining = p["expires_at"] - int(datetime.now(timezone.utc).timestamp())
        if remaining <= 0:
            db.execute("UPDATE payments SET status='expired' WHERE payment_id=? AND status='pending'", (payid,))
            db.commit()
            expired = (
                "⏰ <b>Payment QR Expired</b>\n\n"
                "This payment session has expired.\n\n"
                "Please generate a new QR code to proceed."
                if lang(uid) != "hi" else
                "⏰ <b>भुगतान QR समाप्त हो गया</b>\n\n"
                "यह भुगतान सत्र समाप्त हो गया है।\n\n"
                "आगे बढ़ने के लिए नया QR कोड बनाएं।"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [btn(user_text(uid, "retry"), f"retry:{plan_id}", "success")],
                [btn(user_text(uid, "back"), f"plan:{plan_id}", "danger")]
            ])
            # Telegram can occasionally reject an edit transiently. Retry rather than
            # silently killing the timer task.
            for _ in range(4):
                try:
                    await bot.edit_message_caption(
                        chat_id, message_id, caption=expired,
                        reply_markup=kb, parse_mode="HTML"
                    )
                    return
                except Exception as e:
                    if "message is not modified" in str(e).lower():
                        return
                    await asyncio.sleep(1)
            return

        # Update about every 5 seconds while there is time left. Near the deadline
        # wake exactly at expiry instead of drifting beyond 2 minutes.
        await asyncio.sleep(min(5, remaining))

        p = payment_row(payid)
        if not p or p["status"] != "pending":
            return
        remaining = p["expires_at"] - int(datetime.now(timezone.utc).timestamp())
        if remaining <= 0:
            continue

        try:
            await bot.edit_message_caption(
                chat_id, message_id,
                caption=payment_text(uid, p, remaining),
                reply_markup=payment_kb(uid, payid, plan_id),
                parse_mode="HTML"
            )
        except Exception as e:
            # "message is not modified" is harmless. Other Telegram/network errors
            # should not terminate the timer; try again on the next cycle.
            if "message is not modified" not in str(e).lower():
                await asyncio.sleep(1)

async def payment_expiry_worker():
    """Single owner for all payment countdown UI updates.

    One worker avoids competing edit requests for the same message. It uses the
    stored absolute expiry timestamp, so the countdown is based on real elapsed
    time rather than number of loop iterations.
    """
    while True:
        try:
            rows = db.execute(
                "SELECT * FROM payments WHERE status='pending' AND expires_at>0"
            ).fetchall()
            now = int(datetime.now(timezone.utc).timestamp())

            for p in rows:
                remaining = p["expires_at"] - now
                chat_id = int(p["chat_id"] or 0)
                message_id = int(p["message_id"] or 0)
                if not chat_id or not message_id:
                    continue

                if remaining <= 0:
                    db.execute(
                        "UPDATE payments SET status='expired' WHERE payment_id=? AND status='pending'",
                        (p["payment_id"],)
                    )
                    db.commit()
                    expired = (
                        "⏰ <b>Payment QR Expired</b>\n\n"
                        "This payment session has expired.\n\n"
                        "Please generate a new QR code to proceed."
                        if lang(p["user_id"]) != "hi" else
                        "⏰ <b>भुगतान QR समाप्त हो गया</b>\n\n"
                        "यह भुगतान सत्र समाप्त हो गया है।\n\n"
                        "आगे बढ़ने के लिए नया QR कोड बनाएं।"
                    )
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [btn(user_text(p["user_id"], "retry"), f"retry:{p['plan_id']}", "success")],
                        [btn(user_text(p["user_id"], "back"), f"plan:{p['plan_id']}", "danger")]
                    ])
                    try:
                        await bot.edit_message_caption(
                            chat_id, message_id, caption=expired,
                            reply_markup=kb, parse_mode="HTML"
                        )
                    except Exception as e:
                        if "message is not modified" not in str(e).lower():
                            print("Expiry UI update error:", repr(e))
                    continue

                try:
                    await bot.edit_message_caption(
                        chat_id, message_id,
                        caption=payment_text(p["user_id"], p, remaining),
                        reply_markup=payment_kb(p["user_id"], p["payment_id"], p["plan_id"]),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    if "message is not modified" not in str(e).lower():
                        print("Countdown UI update error:", repr(e))

        except Exception as e:
            print("Payment worker error:", repr(e))

        # Five-second UI updates keep Telegram traffic reasonable while the
        # absolute timestamp guarantees the displayed value reaches 00:00.
        await asyncio.sleep(5)


@dp.message(Command("start"))
async def cmd_start(m: Message):
    # Register the user and claim the one-time owner notification atomically.
    # The separate UNIQUE table prevents duplicate alerts if /start is received
    # more than once in quick succession.
    is_new_user = False
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM users WHERE id=?", (m.from_user.id,)).fetchone()
        db.execute("""
            INSERT INTO users(id,username,first_name,language,created_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name
        """, (m.from_user.id, m.from_user.username or "", m.from_user.first_name or "", "en", now_text()))
        claimed = db.execute(
            "INSERT OR IGNORE INTO new_user_notifications(user_id,notified_at) VALUES(?,?)",
            (m.from_user.id, now_text())
        )
        is_new_user = claimed.rowcount == 1
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise

    if is_new_user:
        try:
            me = await bot.get_me()
            username = f"@{me.username}" if me.username else "Not available"
            user_name = m.from_user.full_name or m.from_user.first_name or "Not provided"
            user_username = f"@{m.from_user.username}" if m.from_user.username else "Not provided"
            notice = (
                "👤 New User\n\n"
                f"Name: {user_name}\n"
                f"Username: {user_username}\n"
                f"Telegram ID: {m.from_user.id}\n"
                f"Date: {now_text()} UTC\n"
                f"Child Bot: {username}"
            )
            await bot.send_message(OWNER_ID, notice)
        except Exception as e:
            print(f"New-user notification failed: {e}")

    state.pop(m.from_user.id, None)
    await clear_user_media(m.from_user.id)
    language_msg = await m.answer(
        "🌐 Please choose your language /\nकृपया अपनी भाषा चुनें:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("🇮🇳 हिंदी", "lang:hi"), btn("🇬🇧 English", "lang:en")]
        ])
    )
    save_user_media(m.from_user.id, [language_msg.message_id])

@dp.callback_query(F.data.startswith("lang:"))
async def choose_language(c: CallbackQuery):
    value = c.data.split(":",1)[1]
    db.execute("UPDATE users SET language=? WHERE id=?", (value, c.from_user.id))
    db.commit()
    text = setting("welcome_hi") if value == "hi" else setting("welcome_en")
    full_text = text + "\n\n" + ("📋 अपनी योजना चुनें:" if value == "hi" else "📋 Choose Your Plan")

    # Visual order: media album first, then welcome/options below it.
    # This keeps the 6-media preview above the plan selector.
    if media_count("start"):
        try:
            await clear_user_media(c.from_user.id)
            await c.message.delete()
            media_ids = await send_media_set(c.from_user.id, "start")
            text_msg = await bot.send_message(c.from_user.id, full_text, reply_markup=user_plans_kb())
            save_user_media(c.from_user.id, media_ids + [text_msg.message_id])
        except Exception as e:
            print("Start media layout error:", repr(e))
            try:
                await c.message.answer(full_text, reply_markup=user_plans_kb())
            except Exception:
                pass
    else:
        await clear_user_media(c.from_user.id)
        await c.message.edit_text(full_text, reply_markup=user_plans_kb())
        save_user_media(c.from_user.id, [c.message.message_id])
    await c.answer()

@dp.callback_query(F.data == "start")
async def cb_start(c: CallbackQuery):
    await clear_user_media(c.from_user.id)
    try:
        await c.message.delete()
    except Exception:
        pass
    msg = await bot.send_message(
        c.from_user.id,
        "🌐 Please choose your language /\nकृपया अपनी भाषा चुनें:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("🇮🇳 हिंदी","lang:hi"),btn("🇬🇧 English","lang:en")]])
    )
    save_user_media(c.from_user.id, [msg.message_id])
    await c.answer()

@dp.callback_query(F.data.startswith("plan:"))
async def plan_detail(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    p = db.execute("SELECT * FROM plans WHERE id=? AND enabled=1", (pid,)).fetchone()
    if not p:
        await c.answer("Plan unavailable.", show_alert=True)
        return
    hi = lang(c.from_user.id) == "hi"
    desc = p["description_hi"] if hi else p["description_en"]
    title = "📦 योजना" if hi else "📦 Plan"
    price = "कीमत" if hi else "Price"
    pay_label = f"💳 ₹{p['price']} का भुगतान करें" if hi else f"💳 Pay ₹{p['price']}"
    text = f"<b>{title}: {p['name']}</b>\n\n💰 <b>{price}:</b> ₹{p['price']}\n\n📝 {desc}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn(pay_label, f"pay:{pid}", "success")],
        [btn(user_text(c.from_user.id,"back"), "plans", "danger")]
    ])

    # IMPORTANT: keep the exact visual order requested:
    # 1) all plan media first
    # 2) plan details below the media
    # 3) Pay/Back buttons below the details
    # Telegram media albums cannot contain an inline keyboard, so the album
    # is sent first and the details+buttons are sent as the next message.
    # Remove the previous plan/start album before showing the new one.
    await clear_user_media(c.from_user.id)
    if media_count("plan", pid):
        try:
            await c.message.delete()
        except Exception:
            pass
        try:
            media_ids = await send_media_set(c.from_user.id, "plan", pid)
            text_msg = await bot.send_message(
                c.from_user.id,
                text,
                reply_markup=kb,
                parse_mode="HTML"
            )
            save_user_media(c.from_user.id, media_ids + [text_msg.message_id])
        except Exception as e:
            print("Plan media layout error:", repr(e))
            try:
                await bot.send_message(
                    c.from_user.id,
                    text,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            except Exception:
                pass
    else:
        await clear_user_media(c.from_user.id)
        await show_text_or_caption(c.message, text, kb, "HTML")
        save_user_media(c.from_user.id, [c.message.message_id])
    await c.answer()

@dp.callback_query(F.data == "plans")
async def cb_plans(c: CallbackQuery):
    await clear_user_media(c.from_user.id)
    try:
        await c.message.delete()
    except Exception:
        pass
    msg = await bot.send_message(
        c.from_user.id,
        user_text(c.from_user.id, "plans"),
        reply_markup=user_plans_kb()
    )
    save_user_media(c.from_user.id, [msg.message_id])
    await c.answer()

@dp.callback_query(F.data.startswith("pay:"))
async def cb_pay(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    await create_payment_message(c.message, c.from_user.id, pid)
    await c.answer()

@dp.callback_query(F.data.startswith("retry:"))
async def cb_retry(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    await create_payment_message(c.message, c.from_user.id, pid)
    await c.answer("New payment QR generated.")

@dp.callback_query(F.data.startswith("paid:"))
async def cb_paid(c: CallbackQuery):
    payid = c.data.split(":",1)[1]
    p = payment_row(payid)
    if not p or p["user_id"] != c.from_user.id:
        await c.answer("Payment not found.", show_alert=True)
        return
    if p["status"] != "pending":
        await c.answer("This payment is no longer pending.", show_alert=True)
        return
    if p["expires_at"] <= int(datetime.now(timezone.utc).timestamp()):
        await c.answer("QR expired. Please retry payment.", show_alert=True)
        return

    # Send a NEW text message. User can now attach a screenshot to that message.
    state[c.from_user.id] = f"proof:{payid}"
    text = (
        "📸 <b>Please send your payment screenshot here.</b>\n\n"
        f"🆔 Payment ID: <code>{payid}</code>\n"
        "🟡 Status: Pending Verification"
        if lang(c.from_user.id) != "hi" else
        "📸 <b>कृपया अपने भुगतान का स्क्रीनशॉट यहाँ भेजें।</b>\n\n"
        f"🆔 भुगतान आईडी: <code>{payid}</code>\n"
        "🟡 स्थिति: सत्यापन लंबित"
    )
    await c.message.answer(text, parse_mode="HTML")
    await c.answer("Send screenshot now.")

@dp.message(F.photo | F.video)
async def receive_media_or_proof(m: Message):
    st = state.get(m.from_user.id, "")
    if st.startswith("media:"):
        if not is_admin(m.from_user.id):
            return
        _, scope, pid_s = st.split(":", 2)
        pid = int(pid_s) if pid_s != "0" else None
        if media_count(scope, pid) >= 6:
            state.pop(m.from_user.id, None)
            return await m.answer("⚠️ Maximum 6 media reached.", reply_markup=admin_kb())
        if m.photo:
            media_type, file_id = "photo", m.photo[-1].file_id
        else:
            media_type, file_id = "video", m.video.file_id
        max_order = db.execute("SELECT COALESCE(MAX(sort_order),-1) m FROM media WHERE scope=? AND ((plan_id IS NULL AND ? IS NULL) OR plan_id=?)", (scope, pid, pid)).fetchone()["m"]
        db.execute("INSERT INTO media(scope,plan_id,media_type,file_id,sort_order,created_at) VALUES(?,?,?,?,?,?)", (scope,pid,media_type,file_id,max_order+1,now_text()))
        db.commit()
        count = media_count(scope, pid)
        if count >= 6:
            state.pop(m.from_user.id, None)
            await m.answer("✅ 6/6 media added. Media set is complete.", reply_markup=admin_kb())
        else:
            await m.answer(f"✅ Added {count}/6. Send the next photo/video.\n/cancel to stop.")
        return
    return await receive_proof(m) if m.photo else None

async def receive_proof(m: Message):
    st = state.get(m.from_user.id, "")
    if not st.startswith("proof:"):
        return
    payid = st.split(":",1)[1]
    p = payment_row(payid)
    if not p or p["user_id"] != m.from_user.id:
        state.pop(m.from_user.id, None)
        return await m.answer("⚠️ Payment session not found.")
    if p["status"] != "pending":
        state.pop(m.from_user.id, None)
        return await m.answer("⚠️ This payment is already processed.")
    if p["expires_at"] <= int(datetime.now(timezone.utc).timestamp()):
        state.pop(m.from_user.id, None)
        return await m.answer("⏰ This payment session expired. Please generate a new QR.")
    fid = m.photo[-1].file_id
    db.execute("UPDATE payments SET proof_file_id=? WHERE payment_id=?", (fid, payid))
    db.commit()
    state.pop(m.from_user.id, None)
    cap = (
        "🧾 <b>Payment Proof Received</b>\n\n"
        f"🆔 Payment ID: <code>{payid}</code>\n"
        f"👤 User: {m.from_user.full_name}\n"
        f"🆔 User ID: <code>{m.from_user.id}</code>\n"
        f"📦 Plan: {p['plan_name']}\n"
        f"💰 Amount: ₹{p['amount']}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("✅ Approve", f"approve:{payid}", "success"), btn("❌ Reject", f"reject:{payid}", "danger")]
    ])
    await bot.send_photo(OWNER_ID, fid, caption=cap, reply_markup=kb, parse_mode="HTML")
    await m.answer(
        "✅ Payment screenshot received.\n🟡 Status: Pending Verification"
        if lang(m.from_user.id) != "hi" else
        "✅ भुगतान का स्क्रीनशॉट मिल गया।\n🟡 स्थिति: सत्यापन लंबित"
    )

@dp.callback_query(F.data.startswith("approve:"))
async def approve(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    payid = c.data.split(":",1)[1]
    p = payment_row(payid)
    if not p:
        return await c.answer("Not found.", show_alert=True)
    if p["status"] != "pending":
        return await c.answer("Already processed.", show_alert=True)
    db.execute("UPDATE payments SET status='approved' WHERE payment_id=?", (payid,))
    db.commit()
    link = p["access_link"]
    if link:
        msg = f"🎉 <b>Payment Approved!</b>\n\n🆔 <code>{payid}</code>\n\n🔗 Your access link:\n{link}"
    else:
        msg = f"🎉 <b>Payment Approved!</b>\n\n🆔 <code>{payid}</code>\n\nℹ️ Access link has not been configured yet."
    await bot.send_message(p["user_id"], msg, parse_mode="HTML")
    try:
        await c.message.edit_caption((c.message.caption or "") + "\n\n🟢 APPROVED", parse_mode="HTML")
    except Exception:
        pass
    await c.answer("Approved")

@dp.callback_query(F.data.startswith("reject:"))
async def reject(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    payid = c.data.split(":",1)[1]
    p = payment_row(payid)
    if not p:
        return await c.answer("Not found.", show_alert=True)
    if p["status"] != "pending":
        return await c.answer("Already processed.", show_alert=True)
    db.execute("UPDATE payments SET status='rejected' WHERE payment_id=?", (payid,))
    db.commit()
    await bot.send_message(p["user_id"], f"❌ Payment rejected.\n\n🆔 <code>{payid}</code>", parse_mode="HTML")
    try:
        await c.message.edit_caption((c.message.caption or "") + "\n\n🔴 REJECTED", parse_mode="HTML")
    except Exception:
        pass
    await c.answer("Rejected")

@dp.message(Command("admin"))
async def admin_command(m: Message):
    if is_admin(m.from_user.id):
        await m.answer("⚙️ <b>Admin Panel</b>\n\nChoose a section:", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin")
async def admin_callback(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    state.pop(c.from_user.id, None)
    await show_text_or_caption(c.message, "⚙️ <b>Admin Panel</b>\n\nChoose a section:", admin_kb(), "HTML")
    await c.answer()

@dp.callback_query(F.data == "adm:start")
async def adm_start(c: CallbackQuery):
    await c.message.edit_text(
        "✏️ <b>Start Message</b>\n\n"
        f"🇬🇧 English:\n{setting('welcome_en')}\n\n"
        f"🇮🇳 Hindi:\n{setting('welcome_hi')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("✏️ Edit English", "set:welcome_en"), btn("✏️ Edit Hindi", "set:welcome_hi")],
            [btn(f"🖼️ Welcome Media ({media_count('start')}/6)", "start_media")],
            [back()]
        ]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "start_media")
async def start_media(c: CallbackQuery):
    await c.message.edit_text(
        f"🖼️ <b>Welcome Media</b>\n\nMedia: {media_count('start')}/6\n\nAdd up to 6 photos/videos. They will be shown when a user selects a language.",
        reply_markup=media_manage_kb("start"), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("plan_media:"))
async def plan_media(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    p = db.execute("SELECT name FROM plans WHERE id=?", (pid,)).fetchone()
    if not p:
        return await c.answer("Plan not found.", show_alert=True)
    await c.message.edit_text(
        f"🖼️ <b>{p['name']} Media</b>\n\nMedia: {media_count('plan', pid)}/6\n\nAdd up to 6 photos/videos. These will be shown when this plan is selected.",
        reply_markup=media_manage_kb("plan", pid), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("mediaadd:"))
async def media_add(c: CallbackQuery):
    target = c.data.split(":",1)[1]
    if target == "startmedia":
        scope, pid = "start", None
        label = "Welcome"
    elif target.startswith("planmedia:"):
        scope, pid = "plan", int(target.split(":",1)[1])
        p = db.execute("SELECT name FROM plans WHERE id=?", (pid,)).fetchone()
        label = p["name"] if p else "Plan"
    else:
        return await c.answer("Invalid media target.", show_alert=True)
    count = media_count(scope, pid)
    if count >= 6:
        return await c.answer("Maximum 6 media reached.", show_alert=True)
    state[c.from_user.id] = f"media:{scope}:{pid if pid is not None else 0}"
    await c.message.edit_text(
        f"🖼️ <b>{label} Media</b>\n\nSend photo/video {count+1} of 6.\nSend one at a time.\n/cancel to stop.",
        parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("mediaclear:"))
async def media_clear(c: CallbackQuery):
    target = c.data.split(":",1)[1]
    if target == "startmedia":
        db.execute("DELETE FROM media WHERE scope='start'")
        db.commit()
        return await start_media(c)
    if target.startswith("planmedia:"):
        pid = int(target.split(":",1)[1])
        db.execute("DELETE FROM media WHERE scope='plan' AND plan_id=?", (pid,))
        db.commit()
        return await plan_media(c)
    await c.answer("Invalid media target.", show_alert=True)

@dp.callback_query(F.data.in_({"set:welcome_en","set:welcome_hi"}))
async def set_welcome(c: CallbackQuery):
    key = c.data.split(":",1)[1]
    state[c.from_user.id] = key
    await c.message.edit_text("✏️ Send the new message now.\n/cancel to cancel.")
    await c.answer()


@dp.callback_query(F.data == "set:upi")
async def set_upi_callback(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    state[c.from_user.id] = "upi"
    await c.message.edit_text("💳 Send the new UPI ID.\n/cancel to cancel.")
    await c.answer()

@dp.callback_query(F.data == "set:upiname")
async def set_upiname_callback(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    state[c.from_user.id] = "upiname"
    await c.message.edit_text("🏷️ Send the new UPI Name.\n/cancel to cancel.")
    await c.answer()

@dp.callback_query(F.data == "adm:plans")
async def adm_plans(c: CallbackQuery):
    await c.message.edit_text("📋 <b>Plans</b>\n\nSelect a plan to edit:", reply_markup=plan_list_kb(), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("editplan:"))
async def edit_plan(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    p = db.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
    if not p:
        return await c.answer("Plan not found.", show_alert=True)
    text = (
        f"📦 <b>{p['name']}</b>\n\n"
        f"Button: {p['button_text']}\n"
        f"Price: ₹{p['price']}\n"
        f"Status: {'Enabled' if p['enabled'] else 'Disabled'}\n"
        f"Access link: {'Configured' if p['access_link'] else 'Not configured'}"
    )
    await c.message.edit_text(text, reply_markup=plan_editor_kb(pid), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("edit:"))
async def edit_field(c: CallbackQuery):
    _, field, pid_s = c.data.split(":")
    pid = int(pid_s)
    prompts = {
        "title": "✏️ Send the new plan title.",
        "button": "🔘 Send the new button text.",
        "desc_en": "📝 Send the English description.",
        "desc_hi": "📝 Send the Hindi description.",
        "price": "💰 Send the new price (numbers only).",
        "link": "🔗 Send the access link. Send `none` to clear it."
    }
    state[c.from_user.id] = f"planedit:{field}:{pid}"
    await c.message.edit_text(prompts[field] + "\n/cancel to cancel.", parse_mode="Markdown")
    await c.answer()

@dp.callback_query(F.data.startswith("toggle:"))
async def toggle_plan(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    db.execute("UPDATE plans SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (pid,))
    db.commit()
    await edit_plan(c)

@dp.callback_query(F.data.startswith("moveup:"))
async def move_up(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    cur = db.execute("SELECT sort_order FROM plans WHERE id=?", (pid,)).fetchone()
    if cur:
        other = db.execute("SELECT id,sort_order FROM plans WHERE sort_order < ? ORDER BY sort_order DESC LIMIT 1", (cur["sort_order"],)).fetchone()
        if other:
            db.execute("UPDATE plans SET sort_order=? WHERE id=?", (other["sort_order"], pid))
            db.execute("UPDATE plans SET sort_order=? WHERE id=?", (cur["sort_order"], other["id"]))
            db.commit()
    await edit_plan(c)

@dp.callback_query(F.data.startswith("movedown:"))
async def move_down(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    cur = db.execute("SELECT sort_order FROM plans WHERE id=?", (pid,)).fetchone()
    if cur:
        other = db.execute("SELECT id,sort_order FROM plans WHERE sort_order > ? ORDER BY sort_order ASC LIMIT 1", (cur["sort_order"],)).fetchone()
        if other:
            db.execute("UPDATE plans SET sort_order=? WHERE id=?", (other["sort_order"], pid))
            db.execute("UPDATE plans SET sort_order=? WHERE id=?", (cur["sort_order"], other["id"]))
            db.commit()
    await edit_plan(c)

@dp.callback_query(F.data.startswith("delete:"))
async def delete_plan(c: CallbackQuery):
    pid = int(c.data.split(":",1)[1])
    db.execute("DELETE FROM media WHERE scope='plan' AND plan_id=?", (pid,))
    db.execute("DELETE FROM plans WHERE id=?", (pid,))
    db.commit()
    await c.message.edit_text("🗑️ Plan deleted.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back("adm:plans")]]))
    await c.answer("Deleted")

@dp.callback_query(F.data == "addplan")
async def add_plan(c: CallbackQuery):
    state[c.from_user.id] = "add:name"
    await c.message.edit_text("➕ Send the new plan name.")
    await c.answer()

@dp.callback_query(F.data == "adm:upi")
async def adm_upi(c: CallbackQuery):
    await c.message.edit_text(
        f"💳 <b>Payment Settings</b>\n\nUPI ID: <code>{setting('upi_id',ENV_UPI) or 'Not configured'}</code>\nUPI Name: {setting('upi_name','Premium Membership')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("Change UPI ID", "set:upi")],
            [btn("Change UPI Name", "set:upiname")],
            [back()]
        ]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "adm:bot")
async def adm_bot(c: CallbackQuery):
    me = await bot.get_me()
    await c.message.edit_text(
        f"🤖 <b>Bot Information</b>\n\nUsername: @{me.username}\nTelegram ID: <code>{me.id}</code>\nStatus: 🟢 Running",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back()]]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "adm:stats")
async def adm_stats(c: CallbackQuery):
    users = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    payments = db.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"]
    approved = db.execute("SELECT COUNT(*) c FROM payments WHERE status='approved'").fetchone()["c"]
    pending = db.execute("SELECT COUNT(*) c FROM payments WHERE status='pending'").fetchone()["c"]
    rejected = db.execute("SELECT COUNT(*) c FROM payments WHERE status='rejected'").fetchone()["c"]
    earnings = db.execute("SELECT COALESCE(SUM(amount),0) c FROM payments WHERE status='approved'").fetchone()["c"]
    await c.message.edit_text(
        f"📊 <b>Statistics</b>\n\n👥 Users: {users}\n💳 Payments: {payments}\n🟢 Approved: {approved}\n🟡 Pending: {pending}\n🔴 Rejected: {rejected}\n💰 Earnings: ₹{earnings}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back()]]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "adm:payments")
async def adm_payments(c: CallbackQuery):
    rows = db.execute("""
        SELECT p.payment_id,p.amount,p.status,p.created_at,pl.name plan
        FROM payments p LEFT JOIN plans pl ON pl.id=p.plan_id
        ORDER BY p.id DESC LIMIT 20
    """).fetchall()
    text = "💳 <b>Recent Payments</b>\n\n"
    if not rows:
        text += "No payments yet."
    else:
        for r in rows:
            text += f"<code>{r['payment_id']}</code> • ₹{r['amount']} • {r['status']} • {r['plan'] or '-'}\n"
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back()]]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "adm:notifications")
async def adm_notifications(c: CallbackQuery):
    v = setting("purchase_notifications","on")
    label = "🟢 ON" if v == "on" else "🔴 OFF"
    await c.message.edit_text(
        f"🔔 <b>Purchase Notifications</b>\n\nStatus: {label}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("🔄 Toggle", "toggle_notifications", "success" if v != "on" else "danger")],
            [back()]
        ]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "toggle_notifications")
async def toggle_notifications(c: CallbackQuery):
    set_setting("purchase_notifications", "off" if setting("purchase_notifications","on") == "on" else "on")
    await adm_notifications(c)

@dp.callback_query(F.data == "adm:proof")
async def adm_proof(c: CallbackQuery):
    await c.message.edit_text(
        "🧾 <b>Payment Proof Settings</b>\n\n"
        "🟢 Screenshot upload: Enabled\n"
        "🟢 Payment ID tracking: Enabled\n"
        "🟢 Approve / Reject: Enabled\n"
        "🟢 User status messages: Enabled",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back()]]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "adm:broadcast")
async def adm_broadcast(c: CallbackQuery):
    state[c.from_user.id] = "broadcast"
    await c.message.edit_text("📢 Send the broadcast message now.\n/cancel to cancel.")
    await c.answer()

@dp.callback_query(F.data == "adm:export")
async def adm_export(c: CallbackQuery):
    db.commit()
    with open(DB_PATH, "rb") as f:
        data = f.read()
    await c.message.answer_document(BufferedInputFile(data, filename="bot_backup.db"), caption="💾 Database backup")
    await c.answer("Exported")

@dp.callback_query(F.data == "adm:import")
async def adm_import(c: CallbackQuery):
    state[c.from_user.id] = "import"
    await c.message.edit_text("📥 Send a .db backup file.\n/cancel to cancel.")
    await c.answer()

@dp.message(Command("cancel"))
async def cancel(m: Message):
    if is_admin(m.from_user.id):
        state.pop(m.from_user.id, None)
        await m.answer("❌ Cancelled.", reply_markup=admin_kb())

@dp.message(F.document)
async def receive_document(m: Message):
    if not is_admin(m.from_user.id) or state.get(m.from_user.id) != "import":
        return
    if not m.document.file_name.lower().endswith(".db"):
        return await m.answer("⚠️ Please send a .db backup file.")
    info = await bot.get_file(m.document.file_id)
    tmp = DB_PATH + ".import"
    await bot.download_file(info.file_path, tmp)
    try:
        src = sqlite3.connect(tmp)
        src.row_factory = sqlite3.Row
        for table in ("users","plans","payments","settings","media","new_user_notifications","user_media_messages","user_screen_messages"):
            src.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        db.execute("DELETE FROM users")
        db.execute("DELETE FROM plans")
        db.execute("DELETE FROM payments")
        db.execute("DELETE FROM settings")
        db.execute("DELETE FROM media")
        for r in src.execute("SELECT id,username,first_name,language,created_at FROM users"):
            db.execute("INSERT INTO users VALUES(?,?,?,?,?)", tuple(r))
        for r in src.execute("SELECT id,name,button_text,price,description_en,description_hi,access_link,enabled,sort_order FROM plans"):
            db.execute("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?)", tuple(r))
        for r in src.execute("SELECT id,payment_id,user_id,plan_id,amount,status,proof_file_id,created_at,expires_at FROM payments"):
            db.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?,?,?)", tuple(r))
        for r in src.execute("SELECT key,value FROM settings"):
            db.execute("INSERT INTO settings VALUES(?,?)", tuple(r))
        try:
            for r in src.execute("SELECT id,scope,plan_id,media_type,file_id,sort_order,created_at FROM media"):
                db.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?)", tuple(r))
        except sqlite3.OperationalError:
            pass
        db.commit()
        src.close()
        os.remove(tmp)
        state.pop(m.from_user.id, None)
        await m.answer("✅ Database imported successfully.", reply_markup=admin_kb())
    except Exception as e:
        try: os.remove(tmp)
        except Exception: pass
        await m.answer(f"❌ Invalid backup: {e}")

@dp.message(F.text)
async def admin_text_input(m: Message):
    if not is_admin(m.from_user.id):
        return
    st = state.get(m.from_user.id)
    if not st:
        return
    text = m.text.strip()
    if text == "/cancel":
        state.pop(m.from_user.id, None)
        return await m.answer("❌ Cancelled.", reply_markup=admin_kb())
    try:
        if st in ("welcome_en","welcome_hi"):
            set_setting(st, text)
            state.pop(m.from_user.id, None)
            return await m.answer("✅ Start message updated.", reply_markup=admin_kb())

        if st == "upi":
            if "@" not in text:
                return await m.answer("⚠️ Enter a valid UPI ID.")
            set_setting("upi_id", text)
            state.pop(m.from_user.id, None)
            return await m.answer("✅ UPI ID updated.", reply_markup=admin_kb())

        if st == "upiname":
            set_setting("upi_name", text)
            state.pop(m.from_user.id, None)
            return await m.answer("✅ UPI Name updated.", reply_markup=admin_kb())

        if st == "broadcast":
            users = db.execute("SELECT id FROM users").fetchall()
            ok = fail = 0
            for u in users:
                try:
                    await bot.send_message(u["id"], text)
                    ok += 1
                except Exception:
                    fail += 1
                await asyncio.sleep(0.05)
            state.pop(m.from_user.id, None)
            return await m.answer(f"📢 Broadcast complete.\n\n✅ Sent: {ok}\n❌ Failed: {fail}", reply_markup=admin_kb())

        if st.startswith("planedit:"):
            _, field, pid_s = st.split(":")
            pid = int(pid_s)
            if field == "price":
                value = int(text)
                if value <= 0: raise ValueError
                db.execute("UPDATE plans SET price=? WHERE id=?", (value,pid))
            elif field == "link":
                value = "" if text.lower() == "none" else text
                db.execute("UPDATE plans SET access_link=? WHERE id=?", (value,pid))
            else:
                column_map = {
                    "title": "name",
                    "button": "button_text",
                    "desc_en": "description_en",
                    "desc_hi": "description_hi",
                }
                column = column_map.get(field)
                if not column:
                    raise ValueError("Invalid plan field")
                db.execute(f"UPDATE plans SET {column}=? WHERE id=?", (text,pid))
            db.commit()
            state.pop(m.from_user.id, None)
            return await m.answer("✅ Plan updated.", reply_markup=admin_kb())

        if st == "add:name":
            state[m.from_user.id] = f"add:button:{text}"
            return await m.answer("🔘 Send the button text.")
        if st.startswith("add:button:"):
            name = st.split(":",2)[2]
            state[m.from_user.id] = f"add:price:{name}|{text}"
            return await m.answer("💰 Send the price.")
        if st.startswith("add:price:"):
            name, button = st.split(":",2)[2].split("|",1)
            price = int(text)
            if price <= 0: raise ValueError
            state[m.from_user.id] = f"add:desc:{name}|{button}|{price}"
            return await m.answer("📝 Send the English description.")
        if st.startswith("add:desc:"):
            name, button, price = st.split(":",2)[2].split("|",2)
            state[m.from_user.id] = f"add:hindidesc:{name}|{button}|{price}|{text}"
            return await m.answer("🇮🇳 Send the Hindi description.")
        if st.startswith("add:hindidesc:"):
            name, button, price, desc_en = st.split(":",2)[2].split("|",3)
            state[m.from_user.id] = f"add:link:{name}|{button}|{price}|{desc_en}|{text}"
            return await m.answer("🔗 Send access link, or `none`.")
        if st.startswith("add:link:"):
            name, button, price, desc_en, desc_hi = st.split(":",2)[2].split("|",4)
            link = "" if text.lower()=="none" else text
            max_order = db.execute("SELECT COALESCE(MAX(sort_order),-1) m FROM plans").fetchone()["m"]
            db.execute(
                "INSERT INTO plans(name,button_text,price,description_en,description_hi,access_link,enabled,sort_order) VALUES(?,?,?,?,?,?,1,?)",
                (name,button,int(price),desc_en,desc_hi,link,max_order+1)
            )
            db.commit()
            state.pop(m.from_user.id, None)
            return await m.answer("✅ New plan added.", reply_markup=admin_kb())
    except ValueError:
        await m.answer("⚠️ Invalid value. Please try again.")
    except Exception as e:
        await m.answer(f"❌ Error: {e}")

# Generic error logging: a single bad update should not stop polling.
@dp.error()
async def on_error(event):
    print("Unhandled update error:", repr(event.exception))
    return True

async def health(request):
    return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"Health server listening on {PORT}")
    asyncio.create_task(payment_expiry_worker())

    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            print("Telegram polling started")
            await dp.start_polling(bot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("Polling error:", repr(exc))
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
