import os, io, uuid, sqlite3, asyncio, csv, shutil
from datetime import datetime
from aiohttp import web
from dotenv import load_dotenv
import qrcode
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

load_dotenv()
TOKEN=os.getenv("BOT_TOKEN","").strip()
OWNER_ID=int(os.getenv("OWNER_ID","0"))
ENV_UPI=os.getenv("UPI_ID","").strip()
PORT=int(os.getenv("PORT","10000"))
DB_PATH=os.getenv("DB_PATH","bot.db")
if not TOKEN: raise RuntimeError("BOT_TOKEN is missing")
if not OWNER_ID: raise RuntimeError("OWNER_ID is missing")

bot=Bot(TOKEN)
dp=Dispatcher()
db=sqlite3.connect(DB_PATH,check_same_thread=False)
db.row_factory=sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT DEFAULT '',first_name TEXT DEFAULT '',language TEXT DEFAULT 'en',created_at TEXT);
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,price INTEGER NOT NULL,description TEXT DEFAULT '',enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,payment_id TEXT UNIQUE,user_id INTEGER,plan_id INTEGER,amount INTEGER,status TEXT DEFAULT 'pending',proof_file_id TEXT DEFAULT '',created_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
""")
db.commit()
state={}

def now(): return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
def admin(uid): return uid==OWNER_ID
def get(k,d=""):
    r=db.execute("SELECT value FROM settings WHERE key=?",(k,)).fetchone()
    return r["value"] if r else d
def setv(k,v):
    db.execute("""INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value""",(k,str(v))); db.commit()
def B(t,d,s="primary"): return InlineKeyboardButton(text=t,callback_data=d,style=s)
def BK(d="admin"): return B("🔙 Back",d,"danger")

if not get("welcome_text"): setv("welcome_text","🌟 Welcome to Premium Membership!\n\nChoose your preferred plan below.")
if not get("upi_name"): setv("upi_name","Premium Membership")
if not get("purchase_notifications"): setv("purchase_notifications","on")
if not get("upi_id") and ENV_UPI: setv("upi_id",ENV_UPI)
if db.execute("SELECT COUNT(*) c FROM plans").fetchone()["c"]==0:
    db.executemany("INSERT INTO plans(name,price,description,enabled) VALUES(?,?,?,1)",[
        ("Plan 1",54,"Premium access"),("Plan 2",94,"Premium access"),
        ("Plan 3",148,"Premium access"),("Plan 4",179,"Premium access"),
        ("Plan 5",199,"Premium access"),("All In One",249,"Complete premium access")])
    db.commit()

def user_plans():
    rows=[[B(f"💎 {p['name']} — ₹{p['price']}",f"plan_{p['id']}")] for p in db.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY id")]
    rows.append([BK("start")]); return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [B("✏️ Start Message","adm_start"),B("📋 Plans","adm_plans")],
        [B("💳 Payment / UPI","adm_upi"),B("🤖 Bot Information","adm_bot")],
        [B("📊 Statistics","adm_stats"),B("💳 Payments","adm_payments")],
        [B("📢 Broadcast","adm_broadcast"),B("🔔 Purchase Notifications","adm_notifications")],
        [B("🧾 Payment Proof Settings","adm_proof")],
        [B("💾 Export Database","adm_export"),B("📥 Import Database","adm_import")],
        [BK("start")]])

def plan_menu(pid):
    p=db.execute("SELECT * FROM plans WHERE id=?",(pid,)).fetchone()
    st="🟢 ON" if p["enabled"] else "🔴 OFF"
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(f"🔄 Toggle — {st}",f"toggle_{pid}","success" if p["enabled"] else "danger")],
        [B("💰 Change Price",f"price_{pid}")],[B("✏️ Change Name",f"name_{pid}")],
        [B("📝 Change Description",f"desc_{pid}")],[B("🗑 Delete Plan",f"delete_{pid}","danger")],
        [BK("adm_plans")]])

def admin_plans():
    rows=[]
    for p in db.execute("SELECT * FROM plans ORDER BY id"):
        st="🟢 ON" if p["enabled"] else "🔴 OFF"
        rows.append([B(f"{p['name']} — ₹{p['price']} | {st}",f"editplan_{p['id']}","success" if p["enabled"] else "danger")])
    rows += [[B("➕ Add New Plan","add_plan","success")],[BK()]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def qr(amount,payid):
    upi=get("upi_id",ENV_UPI).strip()
    if not upi:return None
    data=f"upi://pay?pa={upi}&pn={get('upi_name','Premium Membership')}&am={amount}.00&cu=INR&tn={payid}"
    im=qrcode.make(data); b=io.BytesIO(); im.save(b,"PNG"); return b.getvalue()

@dp.message(Command("start"))
async def start(m:Message):
    db.execute("""INSERT INTO users(id,username,first_name,created_at) VALUES(?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name""",
    (m.from_user.id,m.from_user.username or "",m.from_user.first_name or "",now())); db.commit()
    state.pop(m.from_user.id,None)
    await m.answer("🌐 Please choose your language\nकृपया अपनी भाषा चुनें:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B("🇮🇳 Hindi","lang_hi"),B("🇬🇧 English","lang_en")]]))

@dp.callback_query(F.data.in_({"lang_hi","lang_en"}))
async def lang(c:CallbackQuery):
    db.execute("UPDATE users SET language=? WHERE id=?",("hi" if c.data=="lang_hi" else "en",c.from_user.id));db.commit()
    await c.message.edit_text(get("welcome_text")+"\n\n📋 Choose Your Plan",reply_markup=user_plans());await c.answer()

@dp.callback_query(F.data=="start")
async def startcb(c:CallbackQuery):
    await c.message.edit_text("🌐 Please choose your language\nकृपया अपनी भाषा चुनें:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B("🇮🇳 Hindi","lang_hi"),B("🇬🇧 English","lang_en")]]));await c.answer()

@dp.callback_query(F.data=="plans")
async def plans(c:CallbackQuery): await c.message.edit_text("📋 Choose Your Plan",reply_markup=user_plans());await c.answer()

@dp.callback_query(F.data.startswith("plan_"))
async def pdetail(c:CallbackQuery):
    pid=int(c.data.split("_")[1]);p=db.execute("SELECT * FROM plans WHERE id=? AND enabled=1",(pid,)).fetchone()
    if not p:return await c.answer("Plan unavailable",show_alert=True)
    await c.message.edit_text(f"💎 <b>{p['name']}</b>\n\n💰 Price: ₹{p['price']}\n\n📌 {p['description']}",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(f"💳 Pay ₹{p['price']}",f"pay_{pid}","success")],[BK("plans")]]),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data.startswith("pay_"))
async def pay(c:CallbackQuery):
    pid=int(c.data.split("_")[1]);p=db.execute("SELECT * FROM plans WHERE id=? AND enabled=1",(pid,)).fetchone()
    if not p:return await c.answer("Plan unavailable",show_alert=True)
    payid="PAY-"+uuid.uuid4().hex[:8].upper()
    db.execute("INSERT INTO payments(payment_id,user_id,plan_id,amount,status,created_at) VALUES(?,?,?,?,?,?)",(payid,c.from_user.id,pid,p["price"],"pending",now()));db.commit()
    q=qr(p["price"],payid)
    text=f"💳 <b>Scan & Pay</b>\n\n📦 Plan: {p['name']}\n💰 Amount: ₹{p['price']}\n🆔 Payment ID: <code>{payid}</code>\n\n📲 Scan and pay, then tap I Have Paid."
    kb=InlineKeyboardMarkup(inline_keyboard=[[B("✅ I Have Paid",f"paid_{payid}","success")],[BK(f"plan_{pid}")]])
    if q:
        await c.message.delete();await c.message.answer_photo(BufferedInputFile(q,filename="payment_qr.png"),caption=text,reply_markup=kb,parse_mode="HTML")
    else: await c.message.edit_text(text+"\n\n⚠️ UPI ID is not configured.",reply_markup=kb,parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("paid_"))
async def paid(c:CallbackQuery):
    payid=c.data[5:];p=db.execute("SELECT * FROM payments WHERE payment_id=?",(payid,)).fetchone()
    if not p:return await c.answer("Payment not found",show_alert=True)
    await c.message.edit_text("🟡 <b>Payment Pending Verification</b>\n\nSend your payment screenshot now.\n"+f"🆔 <code>{payid}</code>",parse_mode="HTML");await c.answer("Send screenshot")

@dp.message(F.photo)
async def proof(m:Message):
    p=db.execute("SELECT * FROM payments WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",(m.from_user.id,)).fetchone()
    if not p:return
    fid=m.photo[-1].file_id;db.execute("UPDATE payments SET proof_file_id=? WHERE payment_id=?",(fid,p["payment_id"]));db.commit()
    cap=f"🧾 <b>Payment Proof</b>\n\n🆔 <code>{p['payment_id']}</code>\n👤 {m.from_user.full_name}\n🆔 <code>{m.from_user.id}</code>\n💰 ₹{p['amount']}"
    kb=InlineKeyboardMarkup(inline_keyboard=[[B("✅ Approve",f"approve_{p['payment_id']}","success"),B("❌ Reject",f"reject_{p['payment_id']}","danger")]])
    await bot.send_photo(OWNER_ID,fid,caption=cap,reply_markup=kb,parse_mode="HTML")
    await m.answer("✅ Payment screenshot received.\n🟡 Status: Pending Verification")

@dp.callback_query(F.data.startswith("approve_"))
async def approve(c:CallbackQuery):
    if not admin(c.from_user.id):return
    payid=c.data[8:];p=db.execute("SELECT * FROM payments WHERE payment_id=?",(payid,)).fetchone()
    if not p:return await c.answer("Not found",show_alert=True)
    db.execute("UPDATE payments SET status='approved' WHERE payment_id=?",(payid,));db.commit()
    await bot.send_message(p["user_id"],f"🎉 <b>Payment Approved!</b>\n\n🆔 <code>{payid}</code>",parse_mode="HTML")
    await c.message.edit_caption((c.message.caption or "")+"\n\n🟢 APPROVED",parse_mode="HTML");await c.answer("Approved")

@dp.callback_query(F.data.startswith("reject_"))
async def reject(c:CallbackQuery):
    if not admin(c.from_user.id):return
    payid=c.data[7:];p=db.execute("SELECT * FROM payments WHERE payment_id=?",(payid,)).fetchone()
    if not p:return await c.answer("Not found",show_alert=True)
    db.execute("UPDATE payments SET status='rejected' WHERE payment_id=?",(payid,));db.commit()
    await bot.send_message(p["user_id"],f"❌ <b>Payment Rejected</b>\n\n🆔 <code>{payid}</code>",parse_mode="HTML")
    await c.message.edit_caption((c.message.caption or "")+"\n\n🔴 REJECTED",parse_mode="HTML");await c.answer("Rejected")

@dp.message(Command("admin"))
async def acmd(m:Message):
    if admin(m.from_user.id): await m.answer("⚙️ <b>Admin Panel</b>\n\nChoose an option:",reply_markup=admin_menu(),parse_mode="HTML")

@dp.callback_query(F.data=="admin")
async def acb(c:CallbackQuery):
    if not admin(c.from_user.id):return
    state.pop(c.from_user.id,None);await c.message.edit_text("⚙️ <b>Admin Panel</b>\n\nChoose an option:",reply_markup=admin_menu(),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data=="adm_start")
async def admstart(c:CallbackQuery):
    await c.message.edit_text("✏️ <b>Start Message</b>\n\n"+get("welcome_text"),reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B("✏️ Change Text","change_welcome")],[BK()]]),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data=="change_welcome")
async def chw(c:CallbackQuery):
    state[c.from_user.id]="welcome";await c.message.edit_text("✏️ Send the new Start Message.\n/cancel to cancel.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[BK("adm_start")]]));await c.answer()

@dp.callback_query(F.data=="adm_plans")
async def admplans(c:CallbackQuery):
    await c.message.edit_text("📋 <b>Plans</b>\n\n🟢 ON = visible\n🔴 OFF = hidden",reply_markup=admin_plans(),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data.startswith("editplan_"))
async def editplan(c:CallbackQuery):
    pid=int(c.data[9:]);p=db.execute("SELECT * FROM plans WHERE id=?",(pid,)).fetchone()
    if not p:return await c.answer("Not found",show_alert=True)
    await c.message.edit_text(f"📋 <b>{p['name']}</b>\n\n💰 ₹{p['price']}\n📝 {p['description']}\n📊 {'🟢 ON' if p['enabled'] else '🔴 OFF'}",reply_markup=plan_menu(pid),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data.startswith("toggle_"))
async def toggle(c:CallbackQuery):
    pid=int(c.data[7:]);db.execute("UPDATE plans SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(pid,));db.commit();c.data=f"editplan_{pid}";await editplan(c)

@dp.callback_query(F.data.startswith("price_"))
async def price(c:CallbackQuery):
    pid=int(c.data[6:]);state[c.from_user.id]=f"price:{pid}";await c.message.edit_text("💰 Send new price (numbers only).");await c.answer()

@dp.callback_query(F.data.startswith("name_"))
async def pname(c:CallbackQuery):
    pid=int(c.data[5:]);state[c.from_user.id]=f"name:{pid}";await c.message.edit_text("✏️ Send new plan name.");await c.answer()

@dp.callback_query(F.data.startswith("desc_"))
async def pdesc(c:CallbackQuery):
    pid=int(c.data[5:]);state[c.from_user.id]=f"desc:{pid}";await c.message.edit_text("📝 Send new description.");await c.answer()

@dp.callback_query(F.data.startswith("delete_"))
async def pdelete(c:CallbackQuery):
    pid=int(c.data[7:]);db.execute("DELETE FROM plans WHERE id=?",(pid,));db.commit();await c.message.edit_text("🗑 Plan deleted.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[BK("adm_plans")]]));await c.answer("Deleted")

@dp.callback_query(F.data=="add_plan")
async def addplan(c:CallbackQuery):
    state[c.from_user.id]="add_name";await c.message.edit_text("➕ Send new plan name.");await c.answer()

@dp.callback_query(F.data=="adm_upi")
async def admupi(c:CallbackQuery):
    await c.message.edit_text(f"💳 <b>Payment / UPI</b>\n\nUPI ID: <code>{get('upi_id',ENV_UPI) or 'Not configured'}</code>\nUPI Name: {get('upi_name','Premium Membership')}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B("💳 Change UPI ID","change_upi")],[B("✏️ Change UPI Name","change_upiname")],[BK()]]),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data=="change_upi")
async def cupi(c:CallbackQuery):
    state[c.from_user.id]="upi";await c.message.edit_text("💳 Send new UPI ID.");await c.answer()

@dp.callback_query(F.data=="change_upiname")
async def cupiname(c:CallbackQuery):
    state[c.from_user.id]="upiname";await c.message.edit_text("✏️ Send new UPI Name.");await c.answer()

@dp.callback_query(F.data=="adm_bot")
async def admbot(c:CallbackQuery):
    me=await bot.get_me();await c.message.edit_text(f"🤖 <b>Bot Information</b>\n\nUsername: @{me.username}\nTelegram ID: <code>{me.id}</code>\n🟢 Status: Online",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[BK()]]),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data=="adm_stats")
async def stats(c:CallbackQuery):
    u=db.execute("SELECT COUNT(*) x FROM users").fetchone()["x"];t=db.execute("SELECT COUNT(*) x FROM payments").fetchone()["x"];a=db.execute("SELECT COUNT(*) x FROM payments WHERE status='approved'").fetchone()["x"];p=db.execute("SELECT COUNT(*) x FROM payments WHERE status='pending'").fetchone()["x"];r=db.execute("SELECT COUNT(*) x FROM payments WHERE status='rejected'").fetchone()["x"];e=db.execute("SELECT COALESCE(SUM(amount),0) x FROM payments WHERE status='approved'").fetchone()["x"]
    await c.message.edit_text(f"📊 <b>Statistics</b>\n\n👥 Users: <b>{u}</b>\n💳 Payments: <b>{t}</b>\n🟢 Approved: <b>{a}</b>\n🟡 Pending: <b>{p}</b>\n🔴 Rejected: <b>{r}</b>\n💰 Earnings: <b>₹{e}</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[BK()]]),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data=="adm_payments")
async def payments(c:CallbackQuery):
    rows=db.execute("SELECT p.*,pl.name plan FROM payments p LEFT JOIN plans pl ON pl.id=p.plan_id ORDER BY p.id DESC LIMIT 15").fetchall()
    text="💳 <b>Recent Payments</b>\n\n"+("\n".join(f"<code>{x['payment_id']}</code> • ₹{x['amount']} • {x['status']} • {x['plan'] or '-'}" for x in rows) if rows else "No payments yet.")
    await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[BK()]]),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data=="adm_notifications")
async def notif(c:CallbackQuery):
    v=get("purchase_notifications","on");s="🟢 ON" if v=="on" else "🔴 OFF"
    await c.message.edit_text(f"🔔 <b>Purchase Notifications</b>\n\nStatus: {s}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(f"🔄 Toggle — {s}","toggle_notifications","success" if v=="on" else "danger")],[BK()]]),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data=="toggle_notifications")
async def tnotif(c:CallbackQuery):
    setv("purchase_notifications","off" if get("purchase_notifications","on")=="on" else "on");await notif(c)

@dp.callback_query(F.data=="adm_proof")
async def proofsettings(c:CallbackQuery):
    await c.message.edit_text("🧾 <b>Payment Proof Settings</b>\n\n🟢 Screenshot upload: Enabled\n🟢 Payment ID tracking: Enabled\n🟢 Approve / Reject: Enabled\n🟢 User notification: Enabled",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[BK()]]),parse_mode="HTML");await c.answer()

@dp.callback_query(F.data=="adm_broadcast")
async def broadcast(c:CallbackQuery):
    state[c.from_user.id]="broadcast";await c.message.edit_text("📢 Send the message to broadcast.\n/cancel to cancel.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[BK()]]));await c.answer()

@dp.callback_query(F.data=="adm_export")
async def exportdb(c:CallbackQuery):
    with open(DB_PATH,"rb") as f:data=f.read()
    await c.message.answer_document(BufferedInputFile(data,filename="bot_backup.db"),caption="💾 Database backup");await c.answer("Exported")

@dp.callback_query(F.data=="adm_import")
async def importdb(c:CallbackQuery):
    state[c.from_user.id]="import";await c.message.edit_text("📥 Send a .db backup file.");await c.answer()

@dp.message(Command("cancel"))
async def cancel(m:Message):
    if admin(m.from_user.id):state.pop(m.from_user.id,None);await m.answer("❌ Cancelled.",reply_markup=admin_menu())

@dp.message(F.document)
async def doc(m:Message):
    if not admin(m.from_user.id) or state.get(m.from_user.id)!="import":return
    if not m.document.file_name.lower().endswith(".db"):return await m.answer("⚠️ Send a .db file.")
    f=await bot.get_file(m.document.file_id);tmp=DB_PATH+".import"
    await bot.download_file(f.file_path,tmp)
    try:
        t=sqlite3.connect(tmp);t.execute("SELECT 1 FROM users LIMIT 1");t.execute("SELECT 1 FROM plans LIMIT 1");t.execute("SELECT 1 FROM payments LIMIT 1");t.execute("SELECT 1 FROM settings LIMIT 1");t.close()
        db.close();os.replace(tmp,DB_PATH);await m.answer("✅ Database imported. Restart the Render service.")
    except Exception as e:
        try:os.remove(tmp)
        except:pass
        await m.answer(f"❌ Invalid database: {e}")
    state.pop(m.from_user.id,None)

@dp.message(F.text)
async def admin_text(m:Message):
    if not admin(m.from_user.id):return
    a=state.get(m.from_user.id)
    if not a:return
    text=m.text.strip()
    try:
        if a=="welcome":setv("welcome_text",text);state.pop(m.from_user.id);await m.answer("✅ Start Message updated.",reply_markup=admin_menu())
        elif a=="upi":
            if "@" not in text:return await m.answer("⚠️ Enter a valid UPI ID.")
            setv("upi_id",text);state.pop(m.from_user.id);await m.answer("✅ UPI ID updated.",reply_markup=admin_menu())
        elif a=="upiname":setv("upi_name",text);state.pop(m.from_user.id);await m.answer("✅ UPI Name updated.",reply_markup=admin_menu())
        elif a=="broadcast":
            users=db.execute("SELECT id FROM users").fetchall();ok=bad=0
            for u in users:
                try:await bot.send_message(u["id"],text);ok+=1
                except:bad+=1
                await asyncio.sleep(.05)
            state.pop(m.from_user.id);await m.answer(f"📢 Done.\n\n✅ Sent: {ok}\n❌ Failed: {bad}",reply_markup=admin_menu())
        elif a.startswith("price:"):
            v=int(text);pid=int(a.split(":")[1])
            if v<=0:raise ValueError
            db.execute("UPDATE plans SET price=? WHERE id=?",(v,pid));db.commit();state.pop(m.from_user.id);await m.answer("✅ Price updated.",reply_markup=admin_menu())
        elif a.startswith("name:"):
            pid=int(a.split(":")[1]);db.execute("UPDATE plans SET name=? WHERE id=?",(text,pid));db.commit();state.pop(m.from_user.id);await m.answer("✅ Name updated.",reply_markup=admin_menu())
        elif a.startswith("desc:"):
            pid=int(a.split(":")[1]);db.execute("UPDATE plans SET description=? WHERE id=?",(text,pid));db.commit();state.pop(m.from_user.id);await m.answer("✅ Description updated.",reply_markup=admin_menu())
        elif a=="add_name":state[m.from_user.id]=f"add_price:{text}";await m.answer("💰 Send price.")
        elif a.startswith("add_price:"):
            v=int(text)
            if v<=0:raise ValueError
            state[m.from_user.id]=f"add_desc:{a[10:]}|{v}";await m.answer("📝 Send description.")
        elif a.startswith("add_desc:"):
            name,price=a[9:].rsplit("|",1);db.execute("INSERT INTO plans(name,price,description,enabled) VALUES(?,?,?,1)",(name,int(price),text));db.commit();state.pop(m.from_user.id);await m.answer("✅ Plan added.",reply_markup=admin_menu())
    except ValueError: await m.answer("⚠️ Invalid value.")
    except Exception as e: await m.answer(f"❌ Error: {e}")

async def health(r):return web.Response(text="OK")
async def main():
    app=web.Application();app.router.add_get("/",health);app.router.add_get("/health",health)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,"0.0.0.0",PORT).start()
    await bot.delete_webhook(drop_pending_updates=True);print("Bot started");await dp.start_polling(bot)
if __name__=="__main__":asyncio.run(main())
