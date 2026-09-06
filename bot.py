import os, asyncio, secrets, time, csv, io
from pathlib import Path
import aiosqlite
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()
TOKEN=os.getenv("BOT_TOKEN","")
OWNER_ID=int(os.getenv("OWNER_ID","0"))
DB_PATH=os.getenv("DB_PATH","bot.db")
QR_PATH=os.getenv("QR_PATH","qr.png")

router=Router()

DEFAULT_PLANS=[
("p1","Plan 1",54,"7,000 items|Daily updates|Priority support"),
("p2","Plan 2",94,"10,000 items|Daily updates|Direct group"),
("p3","Plan 3",148,"25,000 items|Daily updates|Lifetime entry"),
("p4","Plan 4",179,"VIP group entry|Daily updates|Permanent access"),
("p5","Plan 5",199,"20,000 items|Daily updates"),
("p6","All In One",249,"Unlimited lifetime access|50,000+ items|VIP access"),
]

class States(StatesGroup):
    proof=State()
    welcome=State()
    upi=State()
    broadcast=State()
    plan_edit=State()
    qr=State()

async def connect():
    db=await aiosqlite.connect(DB_PATH)
    await db.executescript("""
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT,language TEXT,created_at INTEGER,blocked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,name TEXT,price INTEGER,details TEXT,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY,user_id INTEGER,plan_id TEXT,amount INTEGER,status TEXT,created_at INTEGER,proof_file_id TEXT);
    """)
    for k,v in [("welcome","👋 Welcome!\\n\\n📋 Choose Your Plan"),("upi",""),("purchase_notifications","1")]:
        await db.execute("INSERT OR IGNORE INTO settings VALUES(?,?)",(k,v))
    for p in DEFAULT_PLANS:
        await db.execute("INSERT OR IGNORE INTO plans(id,name,price,details) VALUES(?,?,?,?)",p)
    await db.commit()
    return db

async def setting(k):
    db=await connect(); c=await db.execute("SELECT value FROM settings WHERE key=?",(k,)); r=await c.fetchone(); await db.close()
    return r[0] if r else ""

async def set_setting(k,v):
    db=await connect(); await db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",(k,v)); await db.commit(); await db.close()

async def plans_data():
    db=await connect(); c=await db.execute("SELECT id,name,price,details FROM plans WHERE active=1 ORDER BY rowid"); r=await c.fetchall(); await db.close(); return r

def kb(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
def lang_kb(): return kb([[InlineKeyboardButton(text="🇮🇳 हिंदी",callback_data="lang:hi"),InlineKeyboardButton(text="🇬🇧 English",callback_data="lang:en")]])
def main_kb(): return kb([[InlineKeyboardButton(text="📋 Plans",callback_data="plans"),InlineKeyboardButton(text="👤 My Account",callback_data="account")],[InlineKeyboardButton(text="🌐 Language",callback_data="language")]])
def admin_kb(): return kb([
[InlineKeyboardButton(text="📊 Stats",callback_data="adm:stats"),InlineKeyboardButton(text="💰 Earnings",callback_data="adm:earnings")],
[InlineKeyboardButton(text="👥 Users",callback_data="adm:users"),InlineKeyboardButton(text="📋 Plans",callback_data="adm:plans")],
[InlineKeyboardButton(text="💳 Payments",callback_data="adm:payments"),InlineKeyboardButton(text="📢 Broadcast",callback_data="adm:broadcast")],
[InlineKeyboardButton(text="🔔 Purchase Notifications",callback_data="adm:notifications")],
[InlineKeyboardButton(text="🖼️ Welcome Image",callback_data="adm:welcome_image"),InlineKeyboardButton(text="🎬 Welcome Video",callback_data="adm:welcome_video")],
[InlineKeyboardButton(text="📝 Welcome Text",callback_data="adm:welcome"),InlineKeyboardButton(text="💰 UPI ID",callback_data="adm:upi")],
[InlineKeyboardButton(text="📸 Proof Settings",callback_data="adm:proof"),InlineKeyboardButton(text="🏷️ Bot Name",callback_data="adm:botname")],
[InlineKeyboardButton(text="📤 Export Database",callback_data="adm:export"),InlineKeyboardButton(text="📥 Import Database",callback_data="adm:import")],
[InlineKeyboardButton(text="🤖 Clone Bot",callback_data="adm:clone")],
[InlineKeyboardButton(text="🔙 Back",callback_data="adm:back")]
])

async def save_user(m):
    db=await connect()
    await db.execute("INSERT OR IGNORE INTO users(id,username,language,created_at) VALUES(?,?,?,?)",(m.from_user.id,m.from_user.username,"",int(time.time())))
    await db.execute("UPDATE users SET username=? WHERE id=?",(m.from_user.username,m.from_user.id))
    await db.commit(); await db.close()

async def plan(pid):
    db=await connect(); c=await db.execute("SELECT id,name,price,details FROM plans WHERE id=? AND active=1",(pid,)); r=await c.fetchone(); await db.close(); return r

@router.message(CommandStart())
async def start(m:Message,state:FSMContext):
    await state.clear(); await save_user(m)
    db=await connect(); c=await db.execute("SELECT language FROM users WHERE id=?",(m.from_user.id,)); r=await c.fetchone(); await db.close()
    if not r or not r[0]: await m.answer("🌐 Please choose your language / कृपया अपनी भाषा चुनें:",reply_markup=lang_kb())
    else: await m.answer(await setting("welcome"),reply_markup=main_kb())

@router.callback_query(F.data.startswith("lang:"))
async def lang(c:CallbackQuery):
    v=c.data.split(":")[1]; db=await connect(); await db.execute("UPDATE users SET language=? WHERE id=?",(v,c.from_user.id)); await db.commit(); await db.close()
    await c.message.edit_text(await setting("welcome"),reply_markup=main_kb()); await c.answer()

@router.callback_query(F.data=="language")
async def language(c): await c.message.edit_text("🌐 Choose your language:",reply_markup=lang_kb()); await c.answer()

async def show_plans(c):
    ps=await plans_data(); rows=[[InlineKeyboardButton(text=f"{p[1]} — ₹{p[2]}",callback_data=f"plan:{p[0]}")] for p in ps]
    rows.append([InlineKeyboardButton(text="🔙 Back",callback_data="home")])
    await c.message.edit_text("📋 Choose Your Plan",reply_markup=kb(rows)); await c.answer()

@router.callback_query(F.data=="plans")
async def plans(c): await show_plans(c)
@router.callback_query(F.data=="home")
async def home(c): await c.message.edit_text(await setting("welcome"),reply_markup=main_kb()); await c.answer()

@router.callback_query(F.data.startswith("plan:"))
async def details(c):
    p=await plan(c.data.split(":")[1])
    if not p:return await c.answer("Plan unavailable",show_alert=True)
    lines="\n".join("• "+x for x in p[3].split("|"))
    await c.message.edit_text(f"📦 {p[1]}\\n\\n{lines}\\n\\n💵 Price: ₹{p[2]}\\n\\nContinue below to complete your payment.",reply_markup=kb([[InlineKeyboardButton(text=f"🟢 Pay ₹{p[2]}",callback_data=f"pay:{p[0]}")],[InlineKeyboardButton(text="🔴 Back",callback_data="plans")]]))
    await c.answer()

@router.callback_query(F.data.startswith("pay:"))
async def pay(c:CallbackQuery,state:FSMContext):
    p=await plan(c.data.split(":")[1]); 
    if not p:return await c.answer("Plan unavailable",show_alert=True)
    pid="PAY-"+secrets.token_hex(4).upper()
    db=await connect(); await db.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?)",(pid,c.from_user.id,p[0],p[2],"waiting_payment",int(time.time()),None)); await db.commit(); await db.close()
    await state.update_data(payment_id=pid,plan_id=p[0])
    upi=await setting("upi")
    text=f"🧾 Scan & Pay\\n\\n📦 Plan: {p[1]}\\n💵 Amount: ₹{p[2]}\\n🆔 Payment ID: {pid}\\n\\n⏱️ Time Remaining: 02:00\\n"
    if upi:text+=f"\\nUPI ID: {upi}\\n"
    text+="\\nAfter payment, tap “I Have Paid”."
    buttons=[[InlineKeyboardButton(text="🟢 I Have Paid",callback_data="paid")],[InlineKeyboardButton(text="🔴 Back",callback_data=f"plan:{p[0]}")]]
    if Path(QR_PATH).exists():
        await c.message.answer_photo(FSInputFile(QR_PATH),caption=text,reply_markup=kb(buttons)); await c.message.delete()
    else: await c.message.edit_text(text+"\\n\\n⚠️ QR not configured.",reply_markup=kb(buttons))
    await c.answer()

@router.callback_query(F.data=="paid")
async def paid(c,state:FSMContext): await state.set_state(States.proof); await c.message.answer("📸 Please send your payment screenshot now."); await c.answer()

@router.message(States.proof,F.photo)
async def proof(m:Message,state:FSMContext):
    d=await state.get_data(); pid=d.get("payment_id")
    if not pid:return await m.answer("Session expired. Start again with /start.")
    fid=m.photo[-1].file_id; db=await connect(); await db.execute("UPDATE payments SET proof_file_id=?,status='pending' WHERE id=?",(fid,pid)); await db.commit(); await db.close(); await state.clear()
    await m.answer(f"✅ Payment screenshot received.\\n\\nPayment ID: {pid}\\nStatus: 🟡 Pending Verification")
    if OWNER_ID:
        p=await plan(d.get("plan_id")); uname="@"+m.from_user.username if m.from_user.username else "No username"
        await m.bot.send_photo(OWNER_ID,fid,caption=f"🔔 New Payment Approval Request\\n\\nPayment ID: {pid}\\nUser: {uname}\\nUser ID: {m.from_user.id}\\nPlan: {p[1]}\\nAmount: ₹{p[2]}",reply_markup=kb([[InlineKeyboardButton(text="🟢 Approve",callback_data=f"approve:{pid}"),InlineKeyboardButton(text="🔴 Reject",callback_data=f"reject:{pid}")]]))

@router.callback_query(F.data.startswith(("approve:","reject:")))
async def moderate(c:CallbackQuery):
    if c.from_user.id!=OWNER_ID:return await c.answer("Not authorized",show_alert=True)
    act,pid=c.data.split(":"); status="approved" if act=="approve" else "rejected"
    db=await connect(); cur=await db.execute("SELECT user_id FROM payments WHERE id=?",(pid,)); r=await cur.fetchone(); await db.execute("UPDATE payments SET status=? WHERE id=?",(status,pid)); await db.commit(); await db.close()
    if r: await c.bot.send_message(r[0],"✅ Payment approved. Your membership can now be activated." if status=="approved" else "❌ Payment rejected. Please contact support.")
    await c.message.edit_reply_markup(reply_markup=None); await c.answer(status.title())

@router.callback_query(F.data=="account")
async def account(c):
    db=await connect(); cur=await db.execute("SELECT COUNT(*) FROM payments WHERE user_id=? AND status='approved'",(c.from_user.id,)); n=(await cur.fetchone())[0]; await db.close()
    await c.message.edit_text(f"👤 My Account\\n\\nApproved purchases: {n}",reply_markup=main_kb()); await c.answer()

@router.message(Command("admin"))
async def admin(m):
    if m.from_user.id==OWNER_ID: await m.answer("⚙️ Admin Panel\\n\\nManage your bot settings and content.",reply_markup=admin_kb())

@router.callback_query(F.data.startswith("adm:"))
async def admin_action(c:CallbackQuery,state:FSMContext):
    if c.from_user.id!=OWNER_ID:return await c.answer("Not authorized",show_alert=True)
    a=c.data[4:]
    if a=="back": return await c.message.edit_text(await setting("welcome"),reply_markup=main_kb())
    if a=="stats":
        db=await connect(); u=(await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]; p=(await (await db.execute("SELECT COUNT(*) FROM payments")).fetchone())[0]; pend=(await (await db.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")).fetchone())[0]; await db.close()
        text=f"📊 Stats\\n\\nUsers: {u}\\nPayments: {p}\\nPending: {pend}"
    elif a=="earnings":
        db=await connect(); x=(await (await db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'")).fetchone())[0]; await db.close(); text=f"💰 Earnings\\n\\nApproved revenue: ₹{x}"
    elif a=="users":
        db=await connect(); rows=await (await db.execute("SELECT id,username,blocked FROM users ORDER BY created_at DESC LIMIT 20")).fetchall(); await db.close(); text="👥 Users\\n\\n"+"\n".join(f"{r[0]} | @{r[1] if r[1] else '-'} | {'blocked' if r[2] else 'active'}" for r in rows) if rows else "No users."
    elif a=="payments":
        db=await connect(); rows=await (await db.execute("SELECT id,user_id,amount,status FROM payments ORDER BY created_at DESC LIMIT 20")).fetchall(); await db.close(); text="💳 Payments\\n\\n"+"\n".join(f"{r[0]} | {r[1]} | ₹{r[2]} | {r[3]}" for r in rows) if rows else "No payments."
    elif a=="notifications":
        v=await setting("purchase_notifications"); await set_setting("purchase_notifications","0" if v=="1" else "1"); text=f"🔔 Purchase Notifications: {'ON' if v!='1' else 'OFF'}"
    elif a=="welcome": await state.set_state(States.welcome); return await c.message.edit_text("Send the new welcome text.")
    elif a=="upi": await state.set_state(States.upi); return await c.message.edit_text("Send the new UPI ID.")
    elif a=="proof": text="📸 Proof Settings\\n\\nManual screenshot verification is enabled. Screenshots never auto-approve payments."
    elif a=="botname": text="🏷️ Bot Name\\n\\nSet your Telegram bot name/username through BotFather."
    elif a=="plans": text="📋 Plans\\n\\nEdit plan records directly in the database or extend this menu with add/edit/delete actions."
    elif a=="broadcast": await state.set_state(States.broadcast); return await c.message.edit_text("Send the broadcast message now.")
    elif a=="welcome_image": text="🖼️ Welcome Image\\n\\nUpload/configure your welcome image in the next implementation step."
    elif a=="welcome_video": text="🎬 Welcome Video\\n\\nUpload/configure your welcome video in the next implementation step."
    elif a=="export":
        db=await connect(); users=await (await db.execute("SELECT id,username,language,created_at,blocked FROM users")).fetchall(); pays=await (await db.execute("SELECT id,user_id,plan_id,amount,status,created_at FROM payments")).fetchall(); await db.close()
        out=io.StringIO(); w=csv.writer(out); w.writerow(["USERS"]); w.writerow(["id","username","language","created_at","blocked"]); w.writerows(users); w.writerow([]); w.writerow(["PAYMENTS"]); w.writerow(["id","user_id","plan_id","amount","status","created_at"]); w.writerows(pays)
        await c.message.answer_document(BufferedInputFile(out.getvalue().encode(),"database_export.csv")); return await c.answer()
    elif a=="import": text="📥 Import Database\\n\\nFor safety, database import is disabled in this starter. Use a controlled SQLite backup/restore on your server."
    elif a=="clone": text="🤖 Clone Bot\\n\\nA clone requires a separate BotFather token and deployment target; this menu is reserved for that workflow."
    else: text="Unknown admin action."
    await c.message.edit_text(text,reply_markup=admin_kb()); await c.answer()

@router.message(States.welcome)
async def set_welcome(m,state): await set_setting("welcome",m.text or ""); await state.clear(); await m.answer("✅ Welcome text updated.",reply_markup=admin_kb())
@router.message(States.upi)
async def set_upi(m,state): await set_setting("upi",m.text or ""); await state.clear(); await m.answer("✅ UPI ID updated.",reply_markup=admin_kb())

@router.message(States.broadcast)
async def broadcast(m,state):
    if m.from_user.id!=OWNER_ID:return
    db=await connect(); rows=await (await db.execute("SELECT id FROM users WHERE blocked=0")).fetchall(); await db.close()
    sent=0
    for (uid,) in rows:
        try: await m.bot.send_message(uid,m.text); sent+=1
        except Exception: pass
    await state.clear(); await m.answer(f"📢 Broadcast complete. Sent: {sent}",reply_markup=admin_kb())

async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    await connect()

    from aiohttp import web
    from aiogram.types import Update

    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # Render Free Web Service uses HTTP/webhooks.
    # Telegram sends updates to this endpoint.
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not render_url:
        raise RuntimeError("RENDER_EXTERNAL_URL missing")

    webhook_path = "/telegram-webhook"
    webhook_url = render_url + webhook_path

    async def health(request):
        return web.Response(text="OK")

    async def telegram_webhook(request):
        try:
            data = await request.json()
            update = Update.model_validate(data)
            await dp.feed_update(bot, update)
            return web.Response(text="OK")
        except Exception as e:
            print("Webhook error:", repr(e))
            return web.Response(status=500, text="Webhook error")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post(webhook_path, telegram_webhook)

    await bot.set_webhook(webhook_url, drop_pending_updates=True)

    port = int(os.getenv("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"Webhook server running on port {port}")
    print(f"Telegram webhook: {webhook_url}")

    try:
        await asyncio.Event().wait()
    finally:
        await bot.delete_webhook()
        await runner.cleanup()
        await bot.session.close()

if __name__=="__main__":
    asyncio.run(main())
