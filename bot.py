import os, asyncio, secrets, time
from pathlib import Path
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB = os.getenv("DB_PATH", "bot.db")
QR_PATH = os.getenv("QR_PATH", "qr.png")

PLANS = [
    ("p1", "Plan 1", 54, ["7,000 items", "Daily updates", "Priority support"]),
    ("p2", "Plan 2", 94, ["10,000 items", "Daily updates", "Direct group"]),
    ("p3", "Plan 3", 148, ["25,000 items", "Daily updates", "Lifetime entry"]),
    ("p4", "Plan 4", 179, ["VIP group entry", "Daily updates", "Permanent access"]),
    ("p5", "Plan 5", 199, ["20,000 items", "Daily updates"]),
    ("p6", "All In One", 249, ["Unlimited lifetime access", "50,000+ items", "VIP access"]),
]

router = Router()

class PayState(StatesGroup):
    waiting_proof = State()

async def db():
    conn = await aiosqlite.connect(DB)
    await conn.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT, language TEXT DEFAULT 'en',
        created_at INTEGER, blocked INTEGER DEFAULT 0)""")
    await conn.execute("""CREATE TABLE IF NOT EXISTS payments(
        id TEXT PRIMARY KEY, user_id INTEGER, plan_id TEXT, amount INTEGER,
        status TEXT, created_at INTEGER, proof_file_id TEXT)""")
    await conn.commit()
    return conn

def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇮🇳 हिंदी", callback_data="lang_hi"),
         InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")]
    ])

def plans_kb():
    rows = []
    for pid, name, price, _ in PLANS:
        rows.append([InlineKeyboardButton(text=f"{name} — ₹{price}", callback_data=f"plan:{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Plans", callback_data="plans"),
         InlineKeyboardButton(text="👤 My Account", callback_data="account")],
        [InlineKeyboardButton(text="🌐 Language", callback_data="language")]
    ])

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Stats", callback_data="a:stats"),
         InlineKeyboardButton(text="💰 Earnings", callback_data="a:earnings")],
        [InlineKeyboardButton(text="👥 Users", callback_data="a:users"),
         InlineKeyboardButton(text="📋 Plans", callback_data="a:plans")],
        [InlineKeyboardButton(text="💳 Payments", callback_data="a:payments"),
         InlineKeyboardButton(text="📢 Broadcast", callback_data="a:broadcast")],
        [InlineKeyboardButton(text="🔔 Purchase Notifications", callback_data="a:notifications")],
        [InlineKeyboardButton(text="📝 Welcome Text", callback_data="a:welcome"),
         InlineKeyboardButton(text="💰 UPI ID", callback_data="a:upi")],
        [InlineKeyboardButton(text="📤 Export Database", callback_data="a:export")]
    ])

async def save_user(message: Message):
    conn = await db()
    await conn.execute("INSERT OR IGNORE INTO users(id,username,created_at) VALUES(?,?,?)",
                       (message.from_user.id, message.from_user.username, int(time.time())))
    await conn.execute("UPDATE users SET username=? WHERE id=?",
                       (message.from_user.username, message.from_user.id))
    await conn.commit(); await conn.close()

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await save_user(message)
    conn = await db()
    cur = await conn.execute("SELECT language FROM users WHERE id=?", (message.from_user.id,))
    row = await cur.fetchone(); await conn.close()
    if not row or not row[0]:
        await message.answer("🌐 Please choose your language / कृपया अपनी भाषा चुनें:", reply_markup=lang_kb())
    else:
        await message.answer("👋 Welcome back!\n\nChoose an option:", reply_markup=main_kb())

@router.callback_query(F.data.startswith("lang_"))
async def language(c: CallbackQuery):
    value = c.data.split("_")[1]
    conn = await db()
    await conn.execute("UPDATE users SET language=? WHERE id=?", (value, c.from_user.id))
    await conn.commit(); await conn.close()
    await c.message.edit_text("👋 Welcome!\n\n📋 Choose Your Plan", reply_markup=plans_kb())
    await c.answer()

@router.callback_query(F.data == "language")
async def language_again(c: CallbackQuery):
    await c.message.edit_text("🌐 Choose your language:", reply_markup=lang_kb())
    await c.answer()

@router.callback_query(F.data == "plans")
async def plans(c: CallbackQuery):
    await c.message.edit_text("📋 Choose Your Plan", reply_markup=plans_kb())
    await c.answer()

def get_plan(pid):
    return next((p for p in PLANS if p[0] == pid), None)

@router.callback_query(F.data.startswith("plan:"))
async def plan_details(c: CallbackQuery):
    plan = get_plan(c.data.split(":")[1])
    if not plan:
        return await c.answer("Plan not found", show_alert=True)
    pid, name, price, bullets = plan
    text = f"📦 {name}\n\n" + "\n".join(f"• {x}" for x in bullets)
    text += f"\n\n💵 Price: ₹{price}\n\nContinue below to complete your payment."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟢 Pay ₹{price}", callback_data=f"pay:{pid}")],
        [InlineKeyboardButton(text="🔴 Back", callback_data="plans")]
    ])
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()

@router.callback_query(F.data.startswith("pay:"))
async def pay(c: CallbackQuery, state: FSMContext):
    plan = get_plan(c.data.split(":")[1])
    if not plan: return await c.answer("Plan not found", show_alert=True)
    pid, name, price, bullets = plan
    payment_id = "PAY-" + secrets.token_hex(4).upper()
    conn = await db()
    await conn.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?)",
                       (payment_id, c.from_user.id, pid, price, "pending", int(time.time()), None))
    await conn.commit(); await conn.close()
    await state.update_data(payment_id=payment_id, plan_id=pid)
    text = (f"🧾 Scan & Pay\n\n📦 Plan: {name}\n💵 Amount: ₹{price}\n"
            f"🆔 Payment ID: {payment_id}\n\n"
            "⏱️ Time Remaining: 02:00\n\nAfter payment, tap “I Have Paid”.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 I Have Paid", callback_data="paid")],
        [InlineKeyboardButton(text="🔴 Back", callback_data=f"plan:{pid}")]
    ])
    # Put your real QR image at QR_PATH.
    if Path(QR_PATH).exists():
        from aiogram.types import FSInputFile
        await c.message.answer_photo(FSInputFile(QR_PATH), caption=text, reply_markup=kb)
        await c.message.delete()
    else:
        await c.message.edit_text(text + "\n\n⚠️ QR image not configured yet.", reply_markup=kb)
    await c.answer()

@router.callback_query(F.data == "paid")
async def paid(c: CallbackQuery, state: FSMContext):
    await state.set_state(PayState.waiting_proof)
    await c.message.answer("📸 Please send your payment screenshot now.\n\nSend it directly as an image/photo.")
    await c.answer()

@router.message(PayState.waiting_proof, F.photo)
async def proof(message: Message, state: FSMContext):
    data = await state.get_data()
    payment_id = data.get("payment_id")
    if not payment_id:
        await message.answer("Payment session expired. Please start again with /start.")
        await state.clear(); return
    file_id = message.photo[-1].file_id
    conn = await db()
    await conn.execute("UPDATE payments SET proof_file_id=?,status='pending' WHERE id=?",
                       (file_id, payment_id))
    await conn.commit(); await conn.close()
    await state.clear()
    await message.answer(
        f"✅ Payment screenshot received.\n\nPayment ID: {payment_id}\n"
        "Status: 🟡 Pending Verification"
    )
    if OWNER_ID:
        plan_id = data.get("plan_id"); plan = get_plan(plan_id)
        uname = f"@{message.from_user.username}" if message.from_user.username else "No username"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Approve", callback_data=f"approve:{payment_id}"),
             InlineKeyboardButton(text="🔴 Reject", callback_data=f"reject:{payment_id}")]
        ])
        await message.bot.send_photo(
            OWNER_ID, file_id,
            caption=(f"🔔 New Payment Approval Request\n\n"
                     f"Payment ID: {payment_id}\nUser: {uname}\nUser ID: {message.from_user.id}\n"
                     f"Plan: {plan[1] if plan else plan_id}\nAmount: ₹{plan[2] if plan else '?'}"),
            reply_markup=kb
        )

@router.callback_query(F.data.startswith(("approve:", "reject:")))
async def moderate(c: CallbackQuery):
    if c.from_user.id != OWNER_ID:
        return await c.answer("Not authorized", show_alert=True)
    action, pid = c.data.split(":", 1)
    status = "approved" if action == "approve" else "rejected"
    conn = await db()
    cur = await conn.execute("SELECT user_id FROM payments WHERE id=?", (pid,))
    row = await cur.fetchone()
    await conn.execute("UPDATE payments SET status=? WHERE id=?", (status, pid))
    await conn.commit(); await conn.close()
    if row:
        msg = ("✅ Payment approved. Your access can now be delivered."
               if status == "approved" else
               "❌ Payment rejected. Please contact support if you think this is an error.")
        await c.bot.send_message(row[0], msg)
    await c.message.edit_reply_markup(reply_markup=None)
    await c.answer(status.title())

@router.callback_query(F.data == "account")
async def account(c: CallbackQuery):
    conn = await db()
    cur = await conn.execute("SELECT COUNT(*) FROM payments WHERE user_id=? AND status='approved'", (c.from_user.id,))
    count = (await cur.fetchone())[0]; await conn.close()
    await c.message.edit_text(f"👤 My Account\n\nApproved purchases: {count}", reply_markup=main_kb())
    await c.answer()

@router.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer("⚙️ Admin Panel\n\nManage your bot settings and content.", reply_markup=admin_kb())

@router.callback_query(F.data.startswith("a:"))
async def admin_actions(c: CallbackQuery):
    if c.from_user.id != OWNER_ID:
        return await c.answer("Not authorized", show_alert=True)
    action = c.data[2:]
    conn = await db()
    if action == "stats":
        u = (await (await conn.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        p = (await (await conn.execute("SELECT COUNT(*) FROM payments")).fetchone())[0]
        await c.message.edit_text(f"📊 Stats\n\nUsers: {u}\nPayments: {p}", reply_markup=admin_kb())
    elif action == "earnings":
        x = (await (await conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'")).fetchone())[0]
        await c.message.edit_text(f"💰 Earnings\n\nApproved revenue: ₹{x}", reply_markup=admin_kb())
    elif action == "payments":
        cur = await conn.execute("SELECT id,user_id,amount,status FROM payments ORDER BY created_at DESC LIMIT 10")
        rows = await cur.fetchall()
        text = "💳 Recent Payments\n\n" + "\n".join(f"{r[0]} | {r[1]} | ₹{r[2]} | {r[3]}" for r in rows) if rows else "No payments yet."
        await c.message.edit_text(text, reply_markup=admin_kb())
    else:
        await c.message.edit_text(f"⚙️ {action.replace('_',' ').title()}\n\nThis section is scaffolded and ready for configuration.", reply_markup=admin_kb())
    await conn.close(); await c.answer()

async def main():
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN in .env")
    await db()
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
