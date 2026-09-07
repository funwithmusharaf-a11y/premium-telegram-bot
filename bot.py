import os, io, uuid, asyncio, sqlite3, time
import qrcode
from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()
TOKEN=os.getenv("BOT_TOKEN","").strip()
OWNER_ID=int(os.getenv("OWNER_ID","0"))
UPI_ID=os.getenv("UPI_ID","").strip()
PORT=int(os.getenv("PORT","10000"))
DB_PATH=os.getenv("DB_PATH","bot.db")
if not TOKEN: raise RuntimeError("BOT_TOKEN missing")
if not OWNER_ID: raise RuntimeError("OWNER_ID missing")

PLANS=[
("p1","Plan 1",54,["7,000 items","Regular updates","Priority support"]),
("p2","Plan 2",94,["10,000 items","Regular updates","Direct group access"]),
("p3","Plan 3",148,["25,000 items","Regular updates","Permanent access"]),
("p4","Plan 4",179,["VIP group access","Regular updates","Permanent access"]),
("p5","Plan 5",199,["Elite membership","20,000 items","Regular updates"]),
("p6","All In One",249,["Lifetime access","50,000+ items","VIP membership"])]
PM={x[0]:x for x in PLANS}

class PayState(StatesGroup): waiting_proof=State()

db=sqlite3.connect(DB_PATH,check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,name TEXT,lang TEXT,joined_at INTEGER)")
db.execute("CREATE TABLE IF NOT EXISTS payments(payment_id TEXT PRIMARY KEY,user_id INTEGER,plan_id TEXT,amount INTEGER,status TEXT,created_at INTEGER)")
db.commit()
bot=Bot(TOKEN); dp=Dispatcher()

def save_user(u):
    db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?)",(u.id,u.full_name[:100],"en",int(time.time())))
    db.commit()

def kb(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
def lang_kb(): return kb([[InlineKeyboardButton(text="हिंदी",callback_data="lang_hi")],[InlineKeyboardButton(text="English",callback_data="lang_en")]])
def plans_kb(): return kb([[InlineKeyboardButton(text=f"{n}  — ₹{p}",callback_data=f"plan:{i}")] for i,n,p,b in PLANS])
def pay_kb(i): return kb([[InlineKeyboardButton(text=f"💳 Pay ₹{PM[i][2]}",callback_data=f"pay:{i}")],[InlineKeyboardButton(text="⬅️ Back",callback_data="back")]])
def proof_kb(): return kb([[InlineKeyboardButton(text="📤 I Have Paid",callback_data="paid")],[InlineKeyboardButton(text="⬅️ Back",callback_data="back")]])

async def plans(m):
    await m.answer("👋 Welcome to our Premium Membership!\n\nChoose a plan below to view its details and complete payment.\n\n📋 Choose Your Plan:",reply_markup=plans_kb())

@dp.message(CommandStart())
async def start(m:Message,state:FSMContext):
    save_user(m.from_user); await state.clear()
    await m.answer("🌐 Please choose your language /\nकृपया अपनी भाषा चुनें:",reply_markup=lang_kb())

@dp.callback_query(F.data.in_({"lang_hi","lang_en"}))
async def language(c:CallbackQuery):
    db.execute("UPDATE users SET lang=? WHERE user_id=?",("hi" if c.data=="lang_hi" else "en",c.from_user.id)); db.commit()
    await c.answer(); await plans(c.message)

@dp.callback_query(F.data=="back")
async def back(c:CallbackQuery,state:FSMContext):
    await state.clear(); await c.answer(); await plans(c.message)

@dp.callback_query(F.data.startswith("plan:"))
async def detail(c:CallbackQuery):
    i=c.data.split(":")[1]
    if i not in PM: return await c.answer("Plan not found",show_alert=True)
    _,n,p,b=PM[i]
    await c.answer()
    await c.message.answer(f"📦 Plan: {n}\n💰 Price: ₹{p}\n\n⭐ {n}\n\n"+ "\n".join("• "+x for x in b)+"\n\nContinue below to complete your payment.",reply_markup=pay_kb(i))

def qr_image(amount,payment_id):
    if not UPI_ID: return None
    data=f"upi://pay?pa={UPI_ID}&pn=Premium%20Membership&am={amount}.00&cu=INR&tn={payment_id}"
    img=qrcode.make(data); bio=io.BytesIO(); img.save(bio,format="PNG"); bio.seek(0); return bio

@dp.callback_query(F.data.startswith("pay:"))
async def pay(c:CallbackQuery,state:FSMContext):
    i=c.data.split(":")[1]
    if i not in PM: return await c.answer("Plan not found",show_alert=True)
    _,n,amount,_=PM[i]; pid="PAY-"+uuid.uuid4().hex[:8].upper()
    db.execute("INSERT INTO payments VALUES(?,?,?,?,?,?)",(pid,c.from_user.id,i,amount,"pending",int(time.time()))); db.commit()
    await state.set_state(PayState.waiting_proof); await state.update_data(payment_id=pid,plan_id=i)
    cap=f"💳 Scan & Pay\n\n📦 Plan: {n}\n💰 Amount: ₹{amount}\n\n⏳ Time Remaining: 10:00\n\n🧾 Payment ID: {pid}\n\nAfter payment, tap “I Have Paid” and send your payment screenshot."
    q=qr_image(amount,pid); await c.answer()
    if q: await c.message.answer_photo(q,caption=cap,reply_markup=proof_kb())
    else: await c.message.answer(cap+"\n\n⚠️ UPI_ID is not configured.",reply_markup=proof_kb())

@dp.callback_query(F.data=="paid")
async def paid(c:CallbackQuery,state:FSMContext):
    if await state.get_state()!=PayState.waiting_proof: return await c.answer("Start a payment first.",show_alert=True)
    await c.answer(); await c.message.answer("📸 Please send your payment screenshot here.")

@dp.message(PayState.waiting_proof,F.photo)
async def proof(m:Message,state:FSMContext):
    d=await state.get_data(); pid=d.get("payment_id"); i=d.get("plan_id")
    if not pid or i not in PM: return await state.clear()
    db.execute("UPDATE payments SET status='submitted' WHERE payment_id=?",(pid,)); db.commit()
    _,n,a,_=PM[i]
    await m.answer(f"✅ Payment Submitted\n\nYour payment screenshot has been received.\nPlease wait for admin approval.\n\nStatus: 🟡 Pending Verification\nPayment ID: {pid}")
    k=kb([[InlineKeyboardButton(text="✅ Approve",callback_data=f"approve:{pid}"),InlineKeyboardButton(text="❌ Reject",callback_data=f"reject:{pid}")]])
    await bot.send_photo(OWNER_ID,m.photo[-1].file_id,caption=f"🔔 New Payment Approval Request\n\nPayment ID: {pid}\nUser: {m.from_user.full_name}\nUser ID: {m.from_user.id}\nPlan: {n}\nAmount: ₹{a}",reply_markup=k)

@dp.callback_query(F.data.startswith("approve:"),F.from_user.id==OWNER_ID)
async def approve(c:CallbackQuery):
    pid=c.data.split(":")[1]; r=db.execute("SELECT user_id,amount FROM payments WHERE payment_id=?",(pid,)).fetchone()
    if not r: return await c.answer("Not found",show_alert=True)
    db.execute("UPDATE payments SET status='approved' WHERE payment_id=?",(pid,)); db.commit(); await c.answer("Approved")
    await c.message.edit_reply_markup(reply_markup=None); await bot.send_message(r[0],f"✅ Payment Approved\n\nPayment ID: {pid}\nAmount: ₹{r[1]}\n\nYour membership payment has been approved.")

@dp.callback_query(F.data.startswith("reject:"),F.from_user.id==OWNER_ID)
async def reject(c:CallbackQuery):
    pid=c.data.split(":")[1]; r=db.execute("SELECT user_id FROM payments WHERE payment_id=?",(pid,)).fetchone()
    if not r: return await c.answer("Not found",show_alert=True)
    db.execute("UPDATE payments SET status='rejected' WHERE payment_id=?",(pid,)); db.commit(); await c.answer("Rejected")
    await c.message.edit_reply_markup(reply_markup=None); await bot.send_message(r[0],f"❌ Payment Rejected\n\nPayment ID: {pid}\nPlease contact the admin if needed.")

@dp.message(Command("admin"))
async def admin(m:Message):
    if m.from_user.id!=OWNER_ID: return
    u=db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    p=db.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    e=db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'").fetchone()[0]
    await m.answer(f"⚙️ Admin Panel\n\n👥 Users: {u}\n💳 Payments: {p}\n💰 Earnings: ₹{e}")

async def health(request): return web.Response(text="OK")
async def main():
    app=web.Application(); app.router.add_get("/",health); app.router.add_get("/health",health)
    runner=web.AppRunner(app); await runner.setup(); await web.TCPSite(runner,"0.0.0.0",PORT).start()
    await bot.delete_webhook(drop_pending_updates=True); await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
