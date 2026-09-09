import telebot
from telebot import types
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import urllib.parse
import re
import time
import random
import os
from datetime import datetime
import database as db
from keep_alive import keep_alive

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8613530439:AAEWBOAYLNlvqlpkltlhVO_1oMZEUxPs57I")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7136012990"))
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_U0cKZyMsoDQ3@ep-plain-bread-azvssnsf-pooler.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
)
SG_API = "https://accounts.stockgro.club/api"

# High-concurrency bot instance with 16 worker threads
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=16)
user_states = {}
START_TIME = datetime.now()

# High-speed persistent HTTP Session with connection pooling
sg_session = requests.Session()
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=50, max_retries=Retry(total=2, backoff_factor=0.2))
sg_session.mount("https://", adapter)
sg_session.mount("http://", adapter)

FIRST_NAMES = [
    "Vishal", "Rahul", "Amit", "Rohan", "Priya", "Neha", "Ankit", "Saurav",
    "Vikas", "Manish", "Karan", "Pooja", "Deepak", "Aman", "Ravi", "Sachin",
    "Aditya", "Yash", "Nitin", "Rohit", "Sameer", "Gaurav", "Varun", "Abhishek",
    "Sneha", "Kavita", "Ritu", "Megha", "Shweta", "Anjali", "Divya", "Sonam"
]
LAST_NAMES = [
    "Sharma", "Verma", "Singh", "Kumar", "Gupta", "Joshi", "Mehta", "Patel",
    "Yadav", "Chauhan", "Mishra", "Pandey", "Rajput", "Rathore", "Agarwal", "Bansal",
    "Malhotra", "Kapoor", "Saxena", "Saini", "Goyal", "Tiwari", "Deshmukh", "Reddy"
]

# ==========================================
# MANDATORY MEMBERSHIP (FORCE-JOIN) VERIFICATION
# ==========================================

def check_user_membership(user_id, on_verify_click=False):
    """
    Checks if a user has joined all required community channels.
    """
    if db.get_setting("force_sub_status", "true") != "true":
        return True, []

    if user_id == ADMIN_ID:
        return True, []

    if not on_verify_click and db.is_user_verified(user_id):
        return True, []

    channels = db.get_channels()
    if not channels:
        return True, []

    not_joined = []

    for ch in channels:
        cid = ch.get("chat_id")
        link = ch.get("link", "")
        targets = []

        if cid:
            targets.append(cid)
        if "t.me/" in link:
            uname = link.split("t.me/")[-1].replace("@", "").strip().split("/")[0]
            if uname and not uname.startswith("+"):
                targets.append(f"@{uname}")

        is_member = False
        definitely_not_joined = False

        for t in targets:
            try:
                m = bot.get_chat_member(chat_id=t, user_id=user_id)
                if m.status in ["member", "administrator", "creator", "restricted"]:
                    is_member = True
                    break
                elif m.status in ["left", "kicked"]:
                    definitely_not_joined = True
                    break
            except Exception as e:
                err_msg = str(e).lower()
                if "user_not_participant" in err_msg or "user not found" in err_msg:
                    definitely_not_joined = True
                    break
                # If Telegram returned permissions error / member list inaccessible
                pass

        if definitely_not_joined:
            not_joined.append(ch)
        elif not is_member:
            # If Telegram couldn't query directly due to channel privacy/bot admin rights
            if on_verify_click or db.is_user_verified(user_id):
                is_member = True
            else:
                not_joined.append(ch)

    if len(not_joined) == 0:
        db.set_user_verified(user_id, True)
        return True, []

    return False, not_joined

def kb_membership_prompt(not_joined):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in not_joined:
        link = ch.get("link") or "https://t.me/Nexus_IO"
        name = ch.get("name") or "Join Community Channel"
        kb.add(types.InlineKeyboardButton(f"👉 {name}", url=link))
    kb.add(types.InlineKeyboardButton("🔄 Verify Membership", callback_data="check_join_status"))
    return kb

def txt_membership_required(not_joined=None):
    ch_list_str = ""
    if not_joined:
        ch_list_str = "\n📌 <b>Channels to Join:</b>\n"
        for idx, ch in enumerate(not_joined, start=1):
            ch_list_str += f"{idx}. <b>{ch.get('name', 'Community Channel')}</b>\n"

    return (
        "╔═══════════════════════════════╗\n"
        "║  ⚠️ <b>MEMBERSHIP REQUIRED</b>         ║\n"
        "╚═══════════════════════════════╝\n\n"
        "To prevent automated abuse and use the <b>StockGro Referral Bot</b>, you must first join our official channels!\n"
        f"{ch_list_str}\n"
        "📢 <b>Steps to Unlock:</b>\n"
        "1️⃣ Click each button below to join.\n"
        "2️⃣ Tap <b>🔄 Verify Membership</b>.\n"
        "3️⃣ Instant access unlocked! 🚀\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )

# ==========================================
def get_sg_proxies():
    p = db.get_random_proxy()
    if p:
        return {"http": p, "https": p}
    return None

def test_proxy_connectivity(proxy_url):
    try:
        t0 = time.time()
        p_dict = {"http": proxy_url, "https": proxy_url}
        r = requests.get("https://accounts.stockgro.club", proxies=p_dict, timeout=6)
        elapsed = round((time.time() - t0) * 1000)
        return True, f"Online 🟢 ({elapsed}ms)"
    except Exception as e:
        return False, f"Offline 🔴 ({str(e)[:25]})"

def sg_get_state(proxy_dict=None):
    proxies = proxy_dict if proxy_dict is not None else get_sg_proxies()
    try:
        r = sg_session.get("https://app.stockgro.club", headers={
            "user-agent": "Mozilla/5.0 (Linux; Android 14; SM-A135F) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }, proxies=proxies, timeout=10)
        m = re.search(r'state=([^&"]+)', r.text)
        return urllib.parse.unquote(m.group(1)) if m else None
    except Exception:
        if proxies:
            try:
                r = sg_session.get("https://app.stockgro.club", headers={
                    "user-agent": "Mozilla/5.0 (Linux; Android 14; SM-A135F) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                }, timeout=8)
                m = re.search(r'state=([^&"]+)', r.text)
                return urllib.parse.unquote(m.group(1)) if m else None
            except Exception:
                return None
        return None

def sg_headers(state):
    si = urllib.parse.quote(json.dumps({
        "client_id": "b711c4dd-7df5-42e6-80e6-d111c1255cd7",
        "client_name": "stockgro_web",
        "redirect_uri": "https://app.stockgro.club?",
        "state": state,
        "theme": "dark"
    }))
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
        "client-state": state,
        "origin": "https://accounts.stockgro.club",
        "referer": f"https://accounts.stockgro.club/?client_id=b711c4dd-7df5-42e6-80e6-d111c1255cd7&client_name=stockgro_web&redirect_uri=https%3A%2F%2Fapp.stockgro.club%3F&state={urllib.parse.quote(state)}&theme=dark",
        "user-agent": "Mozilla/5.0 (Linux; Android 14; SM-A135F) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
        "cookie": f"sgInfo={si}"
    }

def sg_post(url, payload, headers, proxy_dict=None):
    proxies = proxy_dict if proxy_dict is not None else get_sg_proxies()
    try:
        r = sg_session.post(url, json=payload, headers=headers, proxies=proxies, timeout=12)
        try:
            data = r.json()
            if isinstance(data, dict):
                data["_status_code"] = r.status_code
                return data
            return {"success": False, "error": f"Invalid response (HTTP {r.status_code})", "_status_code": r.status_code}
        except Exception:
            if r.status_code == 429 or "rate limit" in r.text.lower():
                return {"success": False, "error": "Rate limit active. Please wait 30s before retrying.", "_status_code": 429}
            return {"success": False, "error": f"HTTP {r.status_code}", "_status_code": r.status_code}
    except Exception as e:
        return {"success": False, "error": str(e), "_status_code": 0}

def sget(data, *keys, default=None):
    c = data
    for k in keys:
        if not isinstance(c, dict):
            return default
        c = c.get(k, default)
        if c is None:
            return default
    return c

def get_uptime_str():
    d = datetime.now() - START_TIME
    dy, h = d.days, d.seconds // 3600
    m = (d.seconds % 3600) // 60
    return f"{dy}d {h}h {m}m" if dy else (f"{h}h {m}m" if h else f"{m}m")

# ==========================================
# ==========================================
# PERSISTENT REPLY KEYBOARDS (INPUT BUTTONS)
# ==========================================

def user_reply_keyboard(is_admin=False):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, input_field_placeholder="🚀 Choose an automated service below:")
    kb.row(types.KeyboardButton("⚡ Auto Signup (Firebase)"), types.KeyboardButton("📱 Manual Signup"))
    kb.row(types.KeyboardButton("🎫 Set Referral Code"), types.KeyboardButton("📊 My Statistics"))
    kb.row(types.KeyboardButton("📋 My Signup History"), types.KeyboardButton("🆘 Help & Support"))
    kb.row(types.KeyboardButton("🔄 Refresh Dashboard"))
    if is_admin:
        kb.row(types.KeyboardButton("🔐 Admin Control Panel"))
    return kb

def admin_reply_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, input_field_placeholder="🛠️ Admin Control Panel...")
    kb.row(types.KeyboardButton("⚡ Auto Signup (Firebase)"), types.KeyboardButton("📱 Manual Signup"))
    kb.row(types.KeyboardButton("📊 Live Statistics"), types.KeyboardButton("📋 All Signups History"))
    kb.row(types.KeyboardButton("🎫 Referral Code"), types.KeyboardButton("👥 User Management"))
    kb.row(types.KeyboardButton("🌐 Proxy Manager"), types.KeyboardButton("🔥 Firebase URLs"))
    kb.row(types.KeyboardButton("📢 Channel Manager"), types.KeyboardButton("⚙️ Bot Settings"))
    kb.row(types.KeyboardButton("📣 Broadcast Message"), types.KeyboardButton("🚪 Exit Admin Panel"))
    return kb

def cancel_reply_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, input_field_placeholder="Type /cancel or press below to abort...")
    kb.row(types.KeyboardButton("❌ Cancel"))
    return kb

def restore_dashboard_keyboard(chat_id, user_id, message_text=None):
    """Restores the persistent bottom reply keyboard whenever a flow ends."""
    is_admin = (user_id == ADMIN_ID)
    kb = admin_reply_keyboard() if is_admin else user_reply_keyboard(is_admin)
    txt = message_text or "👇 <b>Main Dashboard Menu Restored:</b>"
    bot.send_message(chat_id, txt, reply_markup=kb)

# ==========================================
# INLINE KEYBOARDS
# ==========================================

def kb_user_inline(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⚡ Auto Signup (Firebase)", callback_data="btn_auto_signup"),
        types.InlineKeyboardButton("📱 Manual Signup", callback_data="btn_manual_signup")
    )
    kb.add(
        types.InlineKeyboardButton("🎫 Set Referral", callback_data="btn_referral_choice"),
        types.InlineKeyboardButton("📊 My Stats", callback_data="btn_my_stats")
    )
    kb.add(
        types.InlineKeyboardButton("📋 History", callback_data="btn_my_history"),
        types.InlineKeyboardButton("🆘 Help & Support", callback_data="btn_support")
    )
    return kb

def kb_referral_options(target_action="auto"):
    default_ref = db.get_setting("referral_code", "S4LIOAHO")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"⭐ Use Default Code ({default_ref})", callback_data=f"ref_use_default_{target_action}"))
    kb.add(types.InlineKeyboardButton("✏️ Enter Custom Referral Code", callback_data=f"ref_custom_input_{target_action}"))
    kb.add(types.InlineKeyboardButton("◀️ Back to Dashboard", callback_data="btn_back_user"))
    return kb

def kb_admin_inline():
    prx = db.get_proxy_count()
    fc = db.get_firebase_count()
    prx_label = f"🌐 Proxies ({prx['active']})" if prx['total'] > 0 else "🌐 Proxy Pool"
    fb_label = f"🔥 Firebase ({fc})"
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⚡ Auto Signup", callback_data="btn_auto_signup"),
        types.InlineKeyboardButton("📱 Manual Signup", callback_data="btn_manual_signup")
    )
    kb.add(
        types.InlineKeyboardButton("📊 Live Stats", callback_data="btn_adm_stats"),
        types.InlineKeyboardButton("📋 Signups Log", callback_data="btn_adm_history")
    )
    kb.add(
        types.InlineKeyboardButton("🎫 Edit Referral", callback_data="btn_adm_referral"),
        types.InlineKeyboardButton("👥 Manage Users", callback_data="btn_adm_users")
    )
    kb.add(
        types.InlineKeyboardButton(prx_label, callback_data="btn_adm_proxies"),
        types.InlineKeyboardButton(fb_label, callback_data="btn_adm_firebase")
    )
    kb.add(
        types.InlineKeyboardButton("📢 Channels", callback_data="btn_adm_channels"),
        types.InlineKeyboardButton("⚙️ Settings", callback_data="btn_adm_settings")
    )
    kb.add(types.InlineKeyboardButton("📣 Broadcast", callback_data="btn_adm_broadcast"),
           types.InlineKeyboardButton("🔄 Refresh Panel", callback_data="btn_adm_refresh"))
    return kb

def kb_back_user():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("◀️ Back to Menu", callback_data="btn_back_user"))
    return kb

def kb_back_admin():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="btn_back_admin"))
    return kb

def kb_auto_signup_success():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⚡ Next Auto Signup", callback_data="btn_auto_signup"),
        types.InlineKeyboardButton("📱 Manual Signup", callback_data="btn_manual_signup")
    )
    kb.add(types.InlineKeyboardButton("🏠 Main Dashboard", callback_data="btn_back_user"))
    return kb

def kb_signup_success():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⚡ Auto Signup", callback_data="btn_auto_signup"),
        types.InlineKeyboardButton("📱 Manual Signup", callback_data="btn_manual_signup")
    )
    kb.add(types.InlineKeyboardButton("🏠 Main Dashboard", callback_data="btn_back_user"))
    return kb

def kb_auto_timeout_options(phone=""):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔄 Try Next Firebase Number", callback_data="btn_auto_signup"))
    if phone:
        kb.add(types.InlineKeyboardButton(f"✏️ Enter OTP Manually for {phone}", callback_data=f"auto_manual_otp_{phone}"))
    kb.add(types.InlineKeyboardButton("🏠 Main Dashboard", callback_data="btn_back_user"))
    return kb

def kb_admin_history_filters():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("Last 5", callback_data="hfilter_5"),
        types.InlineKeyboardButton("Last 10", callback_data="hfilter_10"),
        types.InlineKeyboardButton("Last 25", callback_data="hfilter_25")
    )
    kb.add(
        types.InlineKeyboardButton("✅ Success Only", callback_data="hfilter_success"),
        types.InlineKeyboardButton("❌ Failed Only", callback_data="hfilter_failed")
    )
    kb.add(
        types.InlineKeyboardButton("🗑 Clear All Records", callback_data="hfilter_clear_confirm"),
        types.InlineKeyboardButton("◀️ Back", callback_data="btn_back_admin")
    )
    return kb

def kb_admin_settings():
    s = db.get_all_settings()
    notif = "🟢 ON" if s.get("notifications") == "true" else "🔴 OFF"
    maint = "🔴 ON" if s.get("maintenance_mode") == "true" else "🟢 OFF"
    fsub = "🟢 ON" if s.get("force_sub_status") == "true" else "🔴 OFF"

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"🔔 Notifications: {notif}", callback_data="tog_notif"))
    kb.add(types.InlineKeyboardButton(f"🛠️ Maintenance Mode: {maint}", callback_data="tog_maint"))
    kb.add(types.InlineKeyboardButton(f"📢 Force Join Channels: {fsub}", callback_data="tog_fsub"))
    kb.add(types.InlineKeyboardButton(f"📊 Daily Signup Limit ({s.get('max_daily', '50')})", callback_data="set_daily_limit"))
    kb.add(types.InlineKeyboardButton("📞 Edit Support Username", callback_data="set_support_username"))
    kb.add(types.InlineKeyboardButton("◀️ Back to Panel", callback_data="btn_back_admin"))
    return kb

def kb_admin_channels_mgr():
    channels = db.get_channels()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        kb.add(types.InlineKeyboardButton(f"❌ Remove: {ch['name']}", callback_data=f"del_ch_{ch['id']}"))
    kb.add(types.InlineKeyboardButton("➕ Add New Channel / Group", callback_data="add_ch_prompt"))
    kb.add(types.InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="btn_back_admin"))
    return kb

def kb_firebase_manager():
    urls = db.get_firebase_urls(active_only=False)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for u in urls[:12]:
        status_icon = "🟢" if u["is_active"] else "🔴"
        label = u["label"] or u["url"].split("//")[-1][:22]
        kb.add(types.InlineKeyboardButton(f"{status_icon} {label}", callback_data=f"fbu_{u['id']}"))
    kb.add(types.InlineKeyboardButton("➕ Add New Firebase URL", callback_data="fbu_add"))
    kb.add(types.InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="btn_back_admin"))
    return kb

def kb_firebase_item(fid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔄 Toggle Status", callback_data=f"fbu_toggle_{fid}"),
        types.InlineKeyboardButton("🗑 Delete", callback_data=f"fbu_del_{fid}")
    )
    kb.add(types.InlineKeyboardButton("◀️ Back to URLs", callback_data="btn_adm_firebase"))
    return kb

def kb_proxy_manager():
    proxies = db.get_proxies(active_only=False)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for p in proxies[:12]:
        status_icon = "🟢" if p["is_active"] else "🔴"
        lbl = p.get("label") or p["proxy_url"].split("@")[-1][:22]
        kb.add(types.InlineKeyboardButton(f"{status_icon} {lbl}", callback_data=f"prx_item_{p['id']}"))
    kb.add(
        types.InlineKeyboardButton("➕ Add Single Proxy", callback_data="prx_add"),
        types.InlineKeyboardButton("📋 Add Bulk Proxies", callback_data="prx_bulk_add")
    )
    if proxies:
        kb.add(types.InlineKeyboardButton("🧪 Test Active Proxies", callback_data="prx_test_all"))
        kb.add(types.InlineKeyboardButton("🗑 Clear All Proxies", callback_data="prx_clear_confirm"))
    kb.add(types.InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="btn_back_admin"))
    return kb

def kb_proxy_item(pid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔄 Toggle Status", callback_data=f"prx_toggle_{pid}"),
        types.InlineKeyboardButton("🧪 Test Proxy", callback_data=f"prx_test_{pid}")
    )
    kb.add(
        types.InlineKeyboardButton("🗑 Delete", callback_data=f"prx_del_{pid}"),
        types.InlineKeyboardButton("◀️ Back to Proxies", callback_data="btn_adm_proxies")
    )
    return kb

def kb_user_mgmt():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("👥 View User Directory", callback_data="um_list"))
    kb.add(types.InlineKeyboardButton("🚫 Ban User by ID", callback_data="um_ban"))
    kb.add(types.InlineKeyboardButton("✅ Unban User by ID", callback_data="um_unban"))
    kb.add(types.InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="btn_back_admin"))
    return kb

def kb_confirm(action_key):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_{action_key}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="btn_back_admin")
    )
    return kb

# ==========================================
# LUXURY UI TEXT TEMPLATES
# ==========================================

def txt_user_dashboard(user_name, user_id):
    ref = db.get_user_referral(user_id)
    us = db.get_user_signup_stats(user_id)
    s = db.get_all_settings()
    maint_badge = "🔴 <b>Maintenance</b>" if s.get("maintenance_mode") == "true" else "🟢 <b>Online & Active</b>"

    return (
        f"🚀 <b>STOCKGRO REFERRAL AUTOMATION</b> 🎟️\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Welcome, <b>{user_name}</b>!\n"
        f"Ready to execute instant, verified referral signups?\n\n"
        f"✨ <b>CONFIGURATION & STATUS:</b>\n"
        f"├─ ⚡ <b>Bot Status:</b> {maint_badge}\n"
        f"├─ 🎫 <b>Active Referral Code:</b> <code>{ref}</code>\n"
        f"├─ 📱 <b>Your Total Signups:</b> <code>{us['total']}</code>\n"
        f"└─ ✅ <b>Successful:</b> <code>{us['success']}</code>\n\n"
        f"🛍️ <b>MODES AVAILABLE:</b>\n"
        f"⚡ <b>1-Click Auto Signup (Firebase):</b>\n"
        f"<i>Bot automatically fetches fresh numbers from Firebase SMS devices, requests OTP, reads incoming SMS, and registers account hands-free!</i>\n\n"
        f"📱 <b>Manual Mobile Signup:</b>\n"
        f"<i>Enter any Indian 10-digit number and input the OTP code manually.</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>Select a signup method or menu option below:</i>"
    )

def txt_referral_choice(user_id):
    curr_ref = db.get_user_referral(user_id)
    default_ref = db.get_setting("referral_code", "S4LIOAHO")
    return (
        f"╔═══════════════════════════════╗\n"
        f"║    🎫 <b>CHOOSE REFERRAL CODE</b>     ║\n"
        f"╚═══════════════════════════════╝\n\n"
        f"🎯 <b>Currently Active Code:</b> <code>{curr_ref}</code>\n"
        f"⭐ <b>System Default Code:</b> <code>{default_ref}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Please select which code you want to use for your referral signups:\n\n"
        f"⭐ <b>Use Default:</b> Applies system code (<code>{default_ref}</code>)\n"
        f"✏️ <b>Enter Custom:</b> Enter your own StockGro invite code to earn referral rewards\n\n"
        f"👇 <i>Select an option below to proceed:</i>"
    )

def txt_admin_dashboard():
    st = db.get_signup_stats()
    uc_info = db.get_user_counts()
    uc = uc_info["total"]
    bc = uc_info["banned"]
    fc = db.get_firebase_count()
    prx = db.get_proxy_count()
    ch_count = len(db.get_channels())
    ref = db.get_setting("referral_code", "S4LIOAHO")
    s = db.get_all_settings()
    maint_badge = "🔴 MAINTENANCE" if s.get("maintenance_mode") == "true" else "🟢 ACTIVE"
    notif_badge = "🟢 ON" if s.get("notifications") == "true" else "🔴 OFF"
    rate = round(st['success'] / st['total'] * 100) if st['total'] > 0 else 0

    return (
        f"👑 <b>ADMIN MASTER DASHBOARD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>SYSTEM OVERVIEW:</b>\n"
        f"├─ 🔘 <b>Mode:</b> {maint_badge}\n"
        f"├─ 🔔 <b>Notifications:</b> {notif_badge}\n"
        f"├─ ⏱ <b>Uptime:</b> <code>{get_uptime_str()}</code>\n"
        f"└─ 🎫 <b>Default Referral:</b> <code>{ref}</code>\n\n"
        f"📈 <b>PERFORMANCE METRICS:</b>\n"
        f"├─ 👥 <b>Total Users:</b> <code>{uc}</code> (Banned: <code>{bc}</code>)\n"
        f"├─ 📱 <b>Total Signups:</b> <code>{st['total']}</code>\n"
        f"├─ ✅ <b>Successful:</b> <code>{st['success']}</code>\n"
        f"├─ ❌ <b>Failed:</b> <code>{st['failed']}</code>\n"
        f"├─ 🎯 <b>Success Rate:</b> <code>{rate}%</code>\n"
        f"├─ 📅 <b>Today's Total:</b> <code>{st['today']}</code>\n"
        f"└─ 🏆 <b>Today's Success:</b> <code>{st['today_success']}</code>\n\n"
        f"🔥 <b>INTEGRATIONS & PROXIES:</b>\n"
        f"├─ 🗄️ <b>Postgres Database:</b> 🟢 Connected\n"
        f"├─ 🌐 <b>Proxy Pool:</b> <code>{prx['active']}/{prx['total']} Active</code>\n"
        f"├─ 🔗 <b>Firebase Sync URLs:</b> <code>{fc}</code>\n"
        f"└─ 📢 <b>Required Channels:</b> <code>{ch_count}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>Select an administrative action below:</i>"
    )

def txt_support_info():
    sup = db.get_setting("support_contact", "@HURIII_13")
    return (
        f"🆘 <b>CUSTOMER SUPPORT & HELP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Need assistance with StockGro referral signups or have questions?\n\n"
        f"📞 <b>Direct Support Contact:</b>\n"
        f"👉 <b>Admin:</b> {sup}\n\n"
        f"💡 <b>COMMON QUESTIONS:</b>\n"
        f"├─ <b>Q: What numbers work?</b>\n"
        f"│  <i>A: Any active Indian 10-digit number not previously registered on StockGro.</i>\n"
        f"├─ <b>Q: How long is OTP valid?</b>\n"
        f"│  <i>A: Please enter the OTP within 60 seconds of receiving it.</i>\n"
        f"└─ <b>Q: When is referral counted?</b>\n"
        f"   <i>A: Instantly upon successful completion.</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )

# ==========================================
# UI HELPER: SAFE EDIT / SEND
# ==========================================

def safe_edit(cid, mid, text, reply_markup=None):
    try:
        bot.edit_message_text(text, cid, mid, reply_markup=reply_markup)
    except Exception:
        bot.send_message(cid, text, reply_markup=reply_markup)

# ==========================================
# COMMAND HANDLERS
# ==========================================

@bot.message_handler(commands=['start'])
def handle_cmd_start(msg):
    uid = msg.from_user.id
    uname = msg.from_user.username or ""
    fname = msg.from_user.first_name or "Valued User"
    db.save_user(uid, uname, fname)
    user_states.pop(uid, None)

    if db.is_banned(uid):
        bot.reply_to(msg, "🚫 <b>Access Denied:</b> Your account is suspended.")
        return

    is_admin = (uid == ADMIN_ID)

    # Force Join Check (Non-admins only)
    if not is_admin:
        is_member, not_joined = check_user_membership(uid)
        if not is_member:
            bot.send_message(uid, txt_membership_required(not_joined), reply_markup=kb_membership_prompt(not_joined))
            return

    if is_admin:
        bot.send_message(
            uid,
            txt_admin_dashboard(),
            reply_markup=admin_reply_keyboard()
        )
        bot.send_message(
            uid,
            "⚡ <b>Admin Control Active:</b> Quick actions available below:",
            reply_markup=kb_admin_inline()
        )
    else:
        bot.send_message(
            uid,
            txt_user_dashboard(fname, uid),
            reply_markup=user_reply_keyboard(is_admin=False)
        )
        bot.send_message(
            uid,
            "👇 <b>Interactive Dashboard:</b>",
            reply_markup=kb_user_inline(uid)
        )

@bot.message_handler(commands=['admin', 'panel'])
def handle_cmd_admin(msg):
    uid = msg.from_user.id
    if uid != ADMIN_ID:
        bot.reply_to(msg, "🚫 <b>Unauthorized:</b> Admin access only.")
        return
    user_states.pop(uid, None)
    bot.send_message(uid, txt_admin_dashboard(), reply_markup=admin_reply_keyboard())
    bot.send_message(uid, "⚡ <b>Quick Actions:</b>", reply_markup=kb_admin_inline())

@bot.message_handler(commands=['cancel'])
def handle_cmd_cancel(msg):
    uid = msg.from_user.id
    user_states.pop(uid, None)
    restore_dashboard_keyboard(msg.chat.id, uid, "❌ <b>Operation Aborted.</b> Returning to dashboard...")

# ==========================================
# INLINE CALLBACK QUERY HANDLER
# ==========================================

@bot.callback_query_handler(func=lambda c: True)
def handle_callback_router(call):
    uid = call.from_user.id
    cid = call.message.chat.id
    mid = call.message.message_id
    d = call.data
    is_admin = (uid == ADMIN_ID)

    if db.is_banned(uid):
        bot.answer_callback_query(call.id, "🚫 Account Banned.", show_alert=True)
        return

    # ---- CHECK JOIN CALLBACK ----
    if d == "check_join_status":
        is_member, not_joined = check_user_membership(uid, on_verify_click=True)
        if not is_member:
            bot.answer_callback_query(call.id, "⚠️ You must join all channels first before verifying!", show_alert=True)
            safe_edit(cid, mid, txt_membership_required(not_joined), kb_membership_prompt(not_joined))
            return

        db.set_user_verified(uid, True)
        bot.answer_callback_query(call.id, "✅ Membership Verified!", show_alert=False)
        fname = call.from_user.first_name or "Valued User"
        safe_edit(cid, mid, txt_user_dashboard(fname, uid), kb_user_inline(uid))
        restore_dashboard_keyboard(cid, uid, "🎉 <b>Access Granted!</b> Welcome to StockGro Referral Bot.")
        return

    # Membership check on any non-admin action
    if not is_admin:
        is_member, not_joined = check_user_membership(uid)
        if not is_member:
            bot.answer_callback_query(call.id, "⚠️ Please join our channels first!", show_alert=True)
            safe_edit(cid, mid, txt_membership_required(not_joined), kb_membership_prompt(not_joined))
            return

    # ---- BACK ACTIONS ----
    if d == "btn_back_user":
        user_states.pop(uid, None)
        fname = call.from_user.first_name or "User"
        safe_edit(cid, mid, txt_user_dashboard(fname, uid), kb_user_inline(uid))
        restore_dashboard_keyboard(cid, uid)
        bot.answer_callback_query(call.id)
        return

    if d in ("btn_back_admin", "btn_adm_refresh"):
        if uid != ADMIN_ID:
            bot.answer_callback_query(call.id, "🚫 Admin Only", show_alert=True)
            return
        user_states.pop(uid, None)
        safe_edit(cid, mid, txt_admin_dashboard(), kb_admin_inline())
        restore_dashboard_keyboard(cid, uid)
        bot.answer_callback_query(call.id, "Dashboard Refreshed 🔄")
        return

    # ---- AUTO SIGNUP (FIREBASE POOL) ----
    if d == "btn_auto_signup":
        if db.has_user_set_referral(uid):
            saved_ref = db.get_user_referral(uid)
            execute_auto_firebase_signup(cid, uid, mid=mid, ref_code=saved_ref)
        else:
            safe_edit(cid, mid, txt_referral_choice(uid), kb_referral_options("auto"))
        bot.answer_callback_query(call.id)
        return

    # ---- MANUAL SIGNUP ----
    if d in ("btn_manual_signup", "btn_signup_flow"):
        if db.has_user_set_referral(uid):
            saved_ref = db.get_user_referral(uid)
            start_manual_signup_flow(cid, uid, mid=mid, ref_code=saved_ref)
        else:
            safe_edit(cid, mid, txt_referral_choice(uid), kb_referral_options("manual"))
        bot.answer_callback_query(call.id)
        return

    if d == "btn_referral_choice":
        safe_edit(cid, mid, txt_referral_choice(uid), kb_referral_options("auto"))
        bot.answer_callback_query(call.id)
        return

    if d.startswith("ref_use_default"):
        target_action = "auto" if "auto" in d else ("manual" if "manual" in d else "auto")
        default_ref = db.get_setting("referral_code", "S4LIOAHO")
        db.set_user_referral(uid, default_ref)
        bot.answer_callback_query(call.id, f"Saved Default Code: {default_ref} ⭐")
        if target_action == "auto":
            execute_auto_firebase_signup(cid, uid, mid=mid, ref_code=default_ref)
        else:
            start_manual_signup_flow(cid, uid, mid=mid, ref_code=default_ref)
        return

    if d.startswith("ref_custom_input"):
        target_action = "auto" if "auto" in d else ("manual" if "manual" in d else "auto")
        user_states[uid] = {"step": "custom_referral_input", "target_action": target_action}
        bot.send_message(
            uid,
            "✏️ <b>Enter Custom Referral Code:</b>\n\n"
            "Please send your StockGro invitation code in chat (e.g. <code>MYREF123</code>):\n\n"
            "<i>(Tap ❌ Cancel below to abort)</i>",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    if d.startswith("auto_manual_otp_"):
        ph = d.split("auto_manual_otp_")[-1]
        st = user_states.get(uid)
        if st and st.get("phone") == ph:
            user_states[uid]["step"] = "otp_input"
            bot.send_message(
                uid,
                f"✏️ <b>Manual OTP Entry for {ph}:</b>\n\n"
                f"Send the 6-digit OTP in chat:",
                reply_markup=cancel_reply_keyboard()
            )
        else:
            bot.send_message(uid, "⚠️ Session expired. Please start a new signup.", reply_markup=user_reply_keyboard(is_admin))
        bot.answer_callback_query(call.id)
        return

    # ---- USER STATS ----
    if d == "btn_my_stats":
        us = db.get_user_signup_stats(uid)
        failed = us['total'] - us['success']
        safe_edit(
            cid, mid,
            f"📊 <b>YOUR PERSONAL STATISTICS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"┌──────────────────────────┐\n"
            f"│ 📱 <b>Total Signups:</b>    <code>{us['total']}</code>\n"
            f"│ ✅ <b>Successful:</b>       <code>{us['success']}</code>\n"
            f"│ ❌ <b>Failed Attempts:</b>  <code>{failed}</code>\n"
            f"└──────────────────────────┘\n\n"
            f"Keep registering new numbers to rack up your referral count! 🚀",
            kb_back_user()
        )
        bot.answer_callback_query(call.id)
        return

    # ---- USER HISTORY ----
    if d == "btn_my_history":
        rows = db.get_signups(limit=10)
        user_rows = [r for r in rows if r.get("by_user") == uid][:10]
        if not user_rows:
            safe_edit(cid, mid, "📋 <b>No Signup History Found</b>\n\nYou haven't registered any phone numbers yet.", kb_back_user())
        else:
            txt = "📋 <b>YOUR RECENT SIGNUPS (Last 10)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for r in user_rows:
                icon = "✅" if r["status"] == "success" else "❌"
                ts = r["created_at"].strftime("%d %b %H:%M") if r.get("created_at") else "?"
                txt += f"{icon} <code>{r['phone']}</code> │ {r.get('name', 'User')[:12]} │ <i>{ts}</i>\n"
            safe_edit(cid, mid, txt, kb_back_user())
        bot.answer_callback_query(call.id)
        return

    # ---- SUPPORT ----
    if d == "btn_support":
        safe_edit(cid, mid, txt_support_info(), kb_back_user())
        bot.answer_callback_query(call.id)
        return

    # ---- ADMIN: STATS ----
    if d == "btn_adm_stats":
        if uid != ADMIN_ID: return
        safe_edit(cid, mid, txt_admin_dashboard(), kb_admin_inline())
        bot.answer_callback_query(call.id)
        return

    # ---- ADMIN: REFERRAL ----
    if d == "btn_adm_referral":
        if uid != ADMIN_ID: return
        ref = db.get_setting("referral_code", "S4LIOAHO")
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(f"✏️ Change System Default (Current: {ref})", callback_data="act_edit_referral"))
        kb.add(types.InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="btn_back_admin"))
        safe_edit(
            cid, mid,
            f"🎫 <b>STOCKGRO SYSTEM REFERRAL CODE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Current System Default: <code>{ref}</code>\n\n"
            f"<i>This default code is used for all general signups.</i>",
            kb
        )
        bot.answer_callback_query(call.id)
        return

    if d == "act_edit_referral":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "edit_referral"}
        bot.send_message(
            uid,
            "🎫 <b>Edit System Referral Code</b>\n\n"
            "Please send the <b>new referral code</b> in chat (or tap Cancel):",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    # ---- ADMIN: SETTINGS ----
    if d == "btn_adm_settings":
        if uid != ADMIN_ID: return
        safe_edit(
            cid, mid,
            "⚙️ <b>BOT SYSTEM SETTINGS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Configure global automation behavior and security switches below:",
            kb_admin_settings()
        )
        bot.answer_callback_query(call.id)
        return

    if d == "tog_notif":
        if uid != ADMIN_ID: return
        cur = db.get_setting("notifications", "true")
        nv = "false" if cur == "true" else "true"
        db.set_setting("notifications", nv)
        bot.answer_callback_query(call.id, f"Notifications: {'ON 🟢' if nv == 'true' else 'OFF 🔴'}")
        safe_edit(cid, mid, "⚙️ <b>BOT SYSTEM SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━", kb_admin_settings())
        return

    if d == "tog_maint":
        if uid != ADMIN_ID: return
        cur = db.get_setting("maintenance_mode", "false")
        nv = "false" if cur == "true" else "true"
        db.set_setting("maintenance_mode", nv)
        bot.answer_callback_query(call.id, f"Maintenance Mode: {'ON 🔴' if nv == 'true' else 'OFF 🟢'}")
        safe_edit(cid, mid, "⚙️ <b>BOT SYSTEM SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━", kb_admin_settings())
        return

    if d == "tog_fsub":
        if uid != ADMIN_ID: return
        cur = db.get_setting("force_sub_status", "true")
        nv = "false" if cur == "true" else "true"
        db.set_setting("force_sub_status", nv)
        bot.answer_callback_query(call.id, f"Force Join Verification: {'ON 🟢' if nv == 'true' else 'OFF 🔴'}")
        safe_edit(cid, mid, "⚙️ <b>BOT SYSTEM SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━", kb_admin_settings())
        return

    if d == "set_daily_limit":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "set_daily_limit"}
        bot.send_message(
            uid,
            f"📊 <b>Set Daily Signup Limit</b>\n\n"
            f"Current limit: <b>{db.get_setting('max_daily', '50')}</b>\n"
            f"Send a new integer limit (e.g. 100):",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    if d == "set_support_username":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "set_support_username"}
        bot.send_message(
            uid,
            f"📞 <b>Edit Support Contact</b>\n\n"
            f"Current: <b>{db.get_setting('support_contact', '@HURIII_13')}</b>\n"
            f"Send new username/link (e.g. @HURIII_13):",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    # ---- ADMIN: CHANNELS MANAGER ----
    if d == "btn_adm_channels":
        if uid != ADMIN_ID: return
        channels = db.get_channels()
        safe_edit(
            cid, mid,
            f"📢 <b>MANDATORY JOIN CHANNELS ({len(channels)})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Users must join these channels/groups before using the bot.",
            kb_admin_channels_mgr()
        )
        bot.answer_callback_query(call.id)
        return

    if d == "add_ch_prompt":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "add_channel_info"}
        bot.send_message(
            uid,
            "📢 <b>Add Required Channel / Group</b>\n\n"
            "Please send the Channel details in this format:\n"
            "<code>ChatID | Channel Name | Invite Link</code>\n\n"
            "<i>Example:</i>\n"
            "<code>-1003980919319 | Nexus IO Channel | https://t.me/Nexus_IO</code>",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    if d.startswith("del_ch_"):
        if uid != ADMIN_ID: return
        chid = int(d.split("_")[-1])
        db.delete_channel(chid)
        bot.answer_callback_query(call.id, "Channel Removed! 🗑")
        channels = db.get_channels()
        safe_edit(
            cid, mid,
            f"📢 <b>MANDATORY JOIN CHANNELS ({len(channels)})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Channel deleted successfully.",
            kb_admin_channels_mgr()
        )
        return

    # ---- ADMIN: HISTORY ----
    if d == "btn_adm_history":
        if uid != ADMIN_ID: return
        safe_edit(
            cid, mid,
            "📋 <b>SIGNUP LOG VIEWER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose a filter or record count to display:",
            kb_admin_history_filters()
        )
        bot.answer_callback_query(call.id)
        return

    if d.startswith("hfilter_"):
        if uid != ADMIN_ID: return
        action = d.split("_", 1)[1]
        if action == "clear_confirm":
            safe_edit(
                cid, mid,
                "⚠️ <b>DANGER ZONE: Clear All Signups Log?</b>\n\n"
                "This will permanently delete all signup records from the PostgreSQL database!",
                kb_confirm("clear_all_signups")
            )
            bot.answer_callback_query(call.id)
            return

        filt = None
        limit = 10
        if action == "success": filt = "success"; limit = 25
        elif action == "failed": filt = "failed"; limit = 25
        elif action.isdigit(): limit = int(action)

        rows = db.get_signups(limit=limit, status_filter=filt)
        if not rows:
            safe_edit(cid, mid, "📋 <b>No Records Found</b> for the selected filter.", kb_admin_history_filters())
        else:
            txt = f"📋 <b>SIGNUP LOGS ({len(rows)} Records)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for r in rows:
                icon = "✅" if r["status"] == "success" else "❌"
                ts = r["created_at"].strftime("%d/%m %H:%M") if r.get("created_at") else "?"
                uid_ref = f"U:{r['by_user']}" if r.get("by_user") else "Direct"
                txt += f"{icon} <code>{r['phone']}</code> │ {r.get('name', '-')[:10]} │ {uid_ref} │ <i>{ts}</i>\n"
            safe_edit(cid, mid, txt, kb_admin_history_filters())
        bot.answer_callback_query(call.id)
        return

    if d == "confirm_clear_all_signups":
        if uid != ADMIN_ID: return
        db.clear_signups()
        safe_edit(cid, mid, "✅ <b>Database Cleaned:</b> All signup records deleted.", kb_back_admin())
        restore_dashboard_keyboard(cid, uid)
        bot.answer_callback_query(call.id, "History Cleared")
        return

    # ---- ADMIN: FIREBASE MANAGER ----
    if d == "btn_adm_firebase":
        if uid != ADMIN_ID: return
        urls = db.get_firebase_urls(active_only=False)
        active_count = sum(1 for u in urls if u["is_active"])
        safe_edit(
            cid, mid,
            f"🔥 <b>FIREBASE REALTIME DB MANAGER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Manage remote device databases for automated OTP reading.\n\n"
            f"├─ 📊 <b>Total Configured:</b> <code>{len(urls)}</code>\n"
            f"├─ 🟢 <b>Active:</b> <code>{active_count}</code>\n"
            f"└─ 🔴 <b>Inactive:</b> <code>{len(urls) - active_count}</code>\n\n"
            f"<i>Tap any entry below to toggle/delete or add a new URL:</i>",
            kb_firebase_manager()
        )
        bot.answer_callback_query(call.id)
        return

    if d == "fbu_add":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "add_firebase"}
        bot.send_message(
            uid,
            "🔥 <b>Add Firebase Database URL</b>\n\n"
            "Send the Firebase Realtime Database URL:\n"
            "<i>Example: https://my-project-default-rtdb.firebaseio.com</i>",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    if d.startswith("fbu_toggle_"):
        if uid != ADMIN_ID: return
        fid = int(d.split("_")[-1])
        st_new = db.toggle_firebase_url(fid)
        bot.answer_callback_query(call.id, f"Status: {'Active 🟢' if st_new else 'Inactive 🔴'}")
        safe_edit(cid, mid, "🔥 <b>FIREBASE REALTIME DB MANAGER</b>\n━━━━━━━━━━━━━━━━━━━━━━━━", kb_firebase_manager())
        return

    if d.startswith("fbu_del_"):
        if uid != ADMIN_ID: return
        fid = int(d.split("_")[-1])
        db.delete_firebase_url(fid)
        bot.answer_callback_query(call.id, "Deleted! 🗑")
        safe_edit(cid, mid, "🔥 <b>FIREBASE REALTIME DB MANAGER</b>\n━━━━━━━━━━━━━━━━━━━━━━━━", kb_firebase_manager())
        return

    if d.startswith("fbu_") and d[4:].isdigit():
        if uid != ADMIN_ID: return
        fid = int(d[4:])
        urls = db.get_firebase_urls(active_only=False)
        target = next((u for u in urls if u["id"] == fid), None)
        if target:
            status_text = "🟢 Active & Polling" if target["is_active"] else "🔴 Inactive"
            ts = target["added_at"].strftime("%d %b %Y %H:%M") if target.get("added_at") else "?"
            safe_edit(
                cid, mid,
                f"🔥 <b>FIREBASE ENDPOINT DETAILS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"├─ 🏷 <b>Label:</b> <b>{target.get('label') or 'Default'}</b>\n"
                f"├─ 🔗 <b>URL:</b> <code>{target['url']}</code>\n"
                f"├─ 📊 <b>Status:</b> {status_text}\n"
                f"└─ 📅 <b>Added:</b> {ts}",
                kb_firebase_item(fid)
            )
        bot.answer_callback_query(call.id)
        return

    # ---- ADMIN: PROXIES MANAGER ----
    if d == "btn_adm_proxies":
        if uid != ADMIN_ID: return
        prx = db.get_proxy_count()
        safe_edit(
            cid, mid,
            f"🌐 <b>STOCKGRO PROXY MANAGER & ROTATION</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Proxies automatically bypass Cloudflare rate-limits during OTP dispatch.\n\n"
            f"├─ 📊 <b>Total Configured:</b> <code>{prx['total']}</code>\n"
            f"├─ 🟢 <b>Active in Rotation:</b> <code>{prx['active']}</code>\n"
            f"└─ 🔴 <b>Inactive / Disabled:</b> <code>{prx['total'] - prx['active']}</code>\n\n"
            f"<i>Tap any proxy below to manage, or add new proxies:</i>",
            kb_proxy_manager()
        )
        bot.answer_callback_query(call.id)
        return

    if d == "prx_add":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "add_proxy_single"}
        bot.send_message(
            uid,
            "🌐 <b>Add Single Proxy</b>\n\n"
            "Send the proxy in one of these formats:\n"
            "• <code>http://user:pass@ip:port</code>\n"
            "• <code>http://ip:port</code>\n"
            "• <code>socks5://ip:port</code>\n\n"
            "<i>(Tap Cancel below to abort)</i>",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    if d == "prx_bulk_add":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "add_proxies_bulk"}
        bot.send_message(
            uid,
            "📋 <b>Add Bulk Proxies (One per line)</b>\n\n"
            "Send a list of proxies (one per line):\n\n"
            "<code>http://ip1:port1\nhttp://user:pass@ip2:port2\nsocks5://ip3:port3</code>",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    if d.startswith("prx_toggle_"):
        if uid != ADMIN_ID: return
        pid = int(d.split("_")[-1])
        st_new = db.toggle_proxy(pid)
        bot.answer_callback_query(call.id, f"Proxy: {'Active 🟢' if st_new else 'Inactive 🔴'}")
        safe_edit(cid, mid, "🌐 <b>STOCKGRO PROXY MANAGER & ROTATION</b>\n━━━━━━━━━━━━━━━━━━━━━━━━", kb_proxy_manager())
        return

    if d.startswith("prx_del_"):
        if uid != ADMIN_ID: return
        pid = int(d.split("_")[-1])
        db.delete_proxy(pid)
        bot.answer_callback_query(call.id, "Proxy Deleted! 🗑")
        safe_edit(cid, mid, "🌐 <b>STOCKGRO PROXY MANAGER & ROTATION</b>\n━━━━━━━━━━━━━━━━━━━━━━━━", kb_proxy_manager())
        return

    if d.startswith("prx_test_"):
        if uid != ADMIN_ID: return
        pid = int(d.split("_")[-1])
        proxies = db.get_proxies(active_only=False)
        target = next((p for p in proxies if p["id"] == pid), None)
        if target:
            bot.answer_callback_query(call.id, "Testing connectivity to StockGro...")
            ok, res_str = test_proxy_connectivity(target["proxy_url"])
            safe_edit(
                cid, mid,
                f"🌐 <b>PROXY DETAILS & TEST RESULT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"├─ 🏷 <b>Label:</b> <b>{target.get('label') or 'Proxy'}</b>\n"
                f"├─ 🔗 <b>URL:</b> <code>{target['proxy_url']}</code>\n"
                f"├─ 🧪 <b>Health Status:</b> <b>{res_str}</b>\n"
                f"└─ 📊 <b>Active in Bot:</b> {'🟢 Yes' if target['is_active'] else '🔴 No'}",
                kb_proxy_item(pid)
            )
        return

    if d.startswith("prx_item_"):
        if uid != ADMIN_ID: return
        pid = int(d.split("_")[-1])
        proxies = db.get_proxies(active_only=False)
        target = next((p for p in proxies if p["id"] == pid), None)
        if target:
            status_text = "🟢 Active" if target["is_active"] else "🔴 Disabled"
            safe_edit(
                cid, mid,
                f"🌐 <b>PROXY DETAILS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"├─ 🏷 <b>Label:</b> <b>{target.get('label') or 'Proxy'}</b>\n"
                f"├─ 🔗 <b>URL:</b> <code>{target['proxy_url']}</code>\n"
                f"└─ 📊 <b>Status:</b> {status_text}",
                kb_proxy_item(pid)
            )
        bot.answer_callback_query(call.id)
        return

    if d == "prx_test_all":
        if uid != ADMIN_ID: return
        proxies = db.get_proxies(active_only=True)
        if not proxies:
            bot.answer_callback_query(call.id, "No active proxies to test!", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"Testing {len(proxies)} proxies...")
        online = 0
        offline = 0
        for p in proxies:
            ok, _ = test_proxy_connectivity(p["proxy_url"])
            if ok: online += 1
            else: offline += 1
        safe_edit(
            cid, mid,
            f"🧪 <b>PROXY POOL TEST RESULTS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"├─ 🟢 <b>Online & Responding:</b> <code>{online}</code>\n"
            f"├─ 🔴 <b>Failed / Offline:</b> <code>{offline}</code>\n"
            f"└─ 📊 <b>Total Tested:</b> <code>{len(proxies)}</code>",
            kb_proxy_manager()
        )
        return

    if d == "prx_clear_confirm":
        if uid != ADMIN_ID: return
        safe_edit(
            cid, mid,
            "⚠️ <b>Delete ALL Proxies from Pool?</b>\n\nThis will remove all proxy configurations.",
            kb_confirm("clear_all_proxies")
        )
        bot.answer_callback_query(call.id)
        return

    if d == "confirm_clear_all_proxies":
        if uid != ADMIN_ID: return
        db.clear_proxies()
        safe_edit(cid, mid, "✅ <b>Proxy Pool Cleared!</b>", kb_back_admin())
        restore_dashboard_keyboard(cid, uid)
        bot.answer_callback_query(call.id, "All proxies deleted")
        return
    if d == "btn_adm_users":
        if uid != ADMIN_ID: return
        uc_info = db.get_user_counts()
        uc = uc_info["total"]
        bc = uc_info["banned"]
        safe_edit(
            cid, mid,
            f"👥 <b>USER BASE MANAGEMENT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"├─ 👥 <b>Total Registered Users:</b> <code>{uc}</code>\n"
            f"├─ 🟢 <b>Active Users:</b> <code>{uc - bc}</code>\n"
            f"└─ 🚫 <b>Banned Accounts:</b> <code>{bc}</code>",
            kb_user_mgmt()
        )
        bot.answer_callback_query(call.id)
        return

    if d == "um_list":
        if uid != ADMIN_ID: return
        users = db.get_all_users()
        if not users:
            safe_edit(cid, mid, "👥 No users recorded yet.", kb_user_mgmt())
        else:
            txt = f"👥 <b>USER DIRECTORY (Top {min(25, len(users))})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for u in users[:25]:
                status_ic = "🚫" if u.get("is_banned") else "👤"
                uname = f"@{u['username']}" if u.get("username") else (u.get("first_name") or "User")
                txt += f"{status_ic} <code>{u['user_id']}</code> │ {uname[:14]}\n"
            if len(users) > 25:
                txt += f"\n<i>...and {len(users) - 25} more users in database</i>"
            safe_edit(cid, mid, txt, kb_user_mgmt())
        bot.answer_callback_query(call.id)
        return

    if d == "um_ban":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "ban_user_id"}
        bot.send_message(uid, "🚫 <b>Ban User:</b> Send the numeric Telegram User ID to ban:", reply_markup=cancel_reply_keyboard())
        bot.answer_callback_query(call.id)
        return

    if d == "um_unban":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "unban_user_id"}
        bot.send_message(uid, "✅ <b>Unban User:</b> Send the numeric Telegram User ID to unban:", reply_markup=cancel_reply_keyboard())
        bot.answer_callback_query(call.id)
        return

    # ---- ADMIN: BROADCAST ----
    if d == "btn_adm_broadcast":
        if uid != ADMIN_ID: return
        user_states[uid] = {"step": "broadcast_text"}
        uc = db.get_user_count()
        bot.send_message(
            uid,
            f"📣 <b>BROADCAST ANNOUNCEMENT</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Target Audience: <b>{uc}</b> registered users.\n\n"
            f"Send your broadcast message below (HTML supported) or tap Cancel:",
            reply_markup=cancel_reply_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)

# ==========================================
# FLOW HELPERS: AUTO SIGNUP (FIREBASE) & MANUAL SIGNUP
# ==========================================

def execute_auto_firebase_signup(chat_id, user_id, mid=None, ref_code=None, retry_count=0):
    """
    1-Click Automated Referral Signup from Firebase Realtime DB Pool.
    Fetches fresh numbers from connected Android SMS forwarders, dispatches OTP,
    polls Firebase in real-time, and completes account registration hands-free!
    """
    s = db.get_all_settings()
    if s.get("maintenance_mode") == "true" and user_id != ADMIN_ID:
        bot.send_message(chat_id, "🔧 <b>System Notice:</b> Bot is currently in maintenance mode. Please check back shortly.")
        return

    # Membership check
    if user_id != ADMIN_ID:
        is_member, not_joined = check_user_membership(user_id)
        if not is_member:
            bot.send_message(chat_id, txt_membership_required(not_joined), reply_markup=kb_membership_prompt(not_joined))
            return

    # Check daily limit
    st = db.get_signup_stats()
    max_d = int(db.get_setting("max_daily", "50"))
    if st["today"] >= max_d and user_id != ADMIN_ID:
        bot.send_message(chat_id, f"⚠️ <b>Daily Limit Reached:</b> Maximum of {max_d} signups allowed today. Please try again tomorrow.")
        return

    target_ref = ref_code or db.get_user_referral(user_id)

    # Initial status message
    msg_txt = (
        f"⚡ <b>1-CLICK AUTO SIGNUP (FIREBASE POOL)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎫 <b>Referral Code:</b> <code>{target_ref}</code>\n"
        f"⏳ <b>Step 1/4:</b> Querying 9 Firebase Cloud databases for fresh number..."
    )
    if mid:
        safe_edit(chat_id, mid, msg_txt)
        status_mid = mid
    else:
        pm = bot.send_message(chat_id, msg_txt)
        status_mid = pm.message_id

    # Run signup pipeline in background executor so Telegram polling never blocks
    def _run_auto_pipeline():
        try:
            # 1. Fetch fresh numbers from Firebase pool
            candidates = db.harvest_fresh_firebase_numbers(limit=10)
            if not candidates:
                safe_edit(
                    chat_id, status_mid,
                    f"⚠️ <b>Number Pool Empty:</b>\n\n"
                    f"No fresh numbers currently available in Firebase devices.\n"
                    f"Please use <b>📱 Manual Signup</b> to enter your number.",
                    kb_auto_timeout_options()
                )
                return

            chosen = None
            for cand in candidates:
                phone_num = cand["phone"]
                db.mark_number_used(phone_num)
                chosen = cand
                break

            if not chosen:
                safe_edit(chat_id, status_mid, "⚠️ Could not allocate a number. Please retry.", kb_auto_timeout_options())
                return

            phone = chosen["phone"]
            fb_url = chosen["fb_url"]
            assigned_name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"

            safe_edit(
                chat_id, status_mid,
                f"⚡ <b>1-CLICK AUTO SIGNUP (FIREBASE POOL)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>Allocated Number:</b> <code>{phone}</code>\n"
                f"🎫 <b>Referral Code:</b> <code>{target_ref}</code>\n"
                f"👤 <b>Target Profile:</b> <b>{assigned_name}</b>\n\n"
                f"⏳ <b>Step 2/4:</b> Handshaking with StockGro Cloud & Checking Identity..."
            )

            # 2. Get server state token & headers
            state_token = sg_get_state()
            if not state_token:
                safe_edit(
                    chat_id, status_mid,
                    f"❌ <b>Cloud Connection Notice:</b>\n"
                    f"Unable to connect to StockGro servers. Retrying with next device...",
                    kb_auto_timeout_options()
                )
                return

            headers = sg_headers(state_token)

            # 3. Check getIdentity
            d0 = sg_post(f"{SG_API}/getIdentity",
                         {"phone_number": phone, "country_code": "IN", "otp_channel": "sms"}, headers)

            if sget(d0, "data", "existing_user", default=False):
                # Mark as used and auto-retry next number
                db.add_signup(phone, assigned_name, target_ref, "", "already_registered", "Existing user", user_id)
                if retry_count < 3:
                    execute_auto_firebase_signup(chat_id, user_id, mid=status_mid, ref_code=target_ref, retry_count=retry_count+1)
                else:
                    safe_edit(chat_id, status_mid, f"⚠️ Number <code>{phone}</code> is already registered. Please try another.", kb_auto_timeout_options())
                return

            # 4. Dispatch OTP via createOtp
            safe_edit(
                chat_id, status_mid,
                f"⚡ <b>1-CLICK AUTO SIGNUP (FIREBASE POOL)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>Phone:</b> <code>{phone}</code>\n"
                f"🎫 <b>Referral Code:</b> <code>{target_ref}</code>\n"
                f"👤 <b>Profile:</b> <b>{assigned_name}</b>\n\n"
                f"⏳ <b>Step 3/4:</b> Dispatching SMS OTP to phone device..."
            )

            time.sleep(1.0) # Rate limit pacing
            d1 = sg_post(f"{SG_API}/login/createOtp",
                         {"phone_number": phone, "country_code": "IN", "otp_channel": "sms",
                          "invitation_code": target_ref, "flow_type": "signup"}, headers)

            if not d1 or not d1.get("success"):
                err = sget(d1, "message") or sget(d1, "error") or "SMS dispatch cooldown"
                db.add_signup(phone, assigned_name, target_ref, "", "otp_failed", str(err), user_id)
                if retry_count < 2:
                    execute_auto_firebase_signup(chat_id, user_id, mid=status_mid, ref_code=target_ref, retry_count=retry_count+1)
                else:
                    safe_edit(
                        chat_id, status_mid,
                        f"⚠️ <b>SMS Notice:</b> StockGro was unable to send SMS to {phone} ({err}).",
                        kb_auto_timeout_options()
                    )
                return

            sid = sget(d1, "data", "session_id")
            if not sid:
                safe_edit(chat_id, status_mid, "❌ Session error. Please try again.", kb_auto_timeout_options())
                return

            # Save state for manual fallback if user wants to enter OTP
            user_states[user_id] = {
                "step": "otp_input",
                "phone": phone,
                "sid": sid,
                "headers": headers,
                "ref": target_ref,
                "name": assigned_name
            }

            # 5. Listen in Real-Time to Firebase for OTP
            t_dispatch = time.time()
            safe_edit(
                chat_id, status_mid,
                f"╔═══════════════════════════════╗\n"
                f"║  📩 <b>WAITING FOR FIREBASE OTP</b>   ║\n"
                f"╚═══════════════════════════════╝\n\n"
                f"📱 <b>Target Device:</b> <code>{phone}</code>\n"
                f"👤 <b>Profile:</b> <b>{assigned_name}</b>\n"
                f"🎫 <b>Referral:</b> <code>{target_ref}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⏳ <b>Step 4/4:</b> Polling Firebase SMS forwarders in real time...\n"
                f"⏱ <i>Listening for OTP: [0s / 45s]</i>"
            )

            last_edit_time = [time.time()]

            def _progress_cb(elapsed, total_limit):
                if time.time() - last_edit_time[0] >= 6.0:
                    last_edit_time[0] = time.time()
                    try:
                        safe_edit(
                            chat_id, status_mid,
                            f"╔═══════════════════════════════╗\n"
                            f"║  📩 <b>WAITING FOR FIREBASE OTP</b>   ║\n"
                            f"╚═══════════════════════════════╝\n\n"
                            f"📱 <b>Target Device:</b> <code>{phone}</code>\n"
                            f"👤 <b>Profile:</b> <b>{assigned_name}</b>\n"
                            f"🎫 <b>Referral:</b> <code>{target_ref}</code>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"⏳ <b>Step 4/4:</b> Polling Firebase SMS forwarders in real time...\n"
                            f"⏱ <i>Listening for OTP: [{elapsed}s / {total_limit}s]</i>"
                        )
                    except Exception:
                        pass

            # Poll Firebase
            captured_otp = db.poll_firebase_otp(fb_url, phone, start_timestamp=t_dispatch, timeout=45, progress_callback=_progress_cb)

            if not captured_otp:
                safe_edit(
                    chat_id, status_mid,
                    f"⚠️ <b>OTP Timeout Notice:</b>\n\n"
                    f"📱 <b>Phone:</b> <code>{phone}</code>\n"
                    f"OTP was not received from the device within 45s (or SMS was delayed).\n\n"
                    f"💡 <i>You can tap below to try another number or enter OTP manually:</i>",
                    kb_auto_timeout_options(phone)
                )
                db.add_signup(phone, assigned_name, target_ref, "", "otp_timeout", "Firebase timeout", user_id)
                return

            # 6. OTP Captured! Validate & Register Account
            safe_edit(
                chat_id, status_mid,
                f"⚡ <b>OTP CAPTURED FROM FIREBASE!</b> 🔐 <code>{captured_otp}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳ Validating OTP & registering account with referral <code>{target_ref}</code>..."
            )

            d2 = sg_post(f"{SG_API}/login/validateOtp",
                         {"session_id": sid, "otp": captured_otp, "phone_number": phone,
                          "country_code": "IN", "otp_channel": "sms", "flow_type": "signup"}, headers)

            if not d2 or not d2.get("success"):
                err = sget(d2, "message", default=sget(d2, "error", default="OTP validation error"))
                safe_edit(chat_id, status_mid, f"❌ OTP Verification error for {phone}: {err}", kb_auto_timeout_options())
                db.add_signup(phone, assigned_name, target_ref, "", "otp_invalid", str(err), user_id)
                return

            d3 = sg_post(f"{SG_API}/signup/registerUser",
                         {"display_name": assigned_name, "invitation_code": target_ref, "otp": captured_otp,
                          "session_id": sid, "whatsapp_consent": True,
                          "phone_number": phone, "country_code": "IN", "otp_channel": "sms"}, headers)

            if not d3 or not d3.get("success"):
                err = sget(d3, "message", default=sget(d3, "error", default="Registration error"))
                safe_edit(chat_id, status_mid, f"❌ Registration error for {phone}: {err}", kb_auto_timeout_options())
                db.add_signup(phone, assigned_name, target_ref, "", "register_failed", str(err), user_id)
                return

            sg_uid = sget(d3, "data", "user_id", default="unknown")
            redir = sget(d3, "data", "redirect_uri", default="")
            if redir and "access_code=" in redir:
                sg_post("https://app.stockgro.club/api/login", {"code": redir.split("access_code=")[-1]}, headers)

            # Record success in PostgreSQL
            db.add_signup(phone, assigned_name, target_ref, sg_uid, "success", "", user_id)
            user_states.pop(user_id, None)

            # Send luxury success receipt
            safe_edit(
                chat_id, status_mid,
                f"╔═══════════════════════════════╗\n"
                f"║   🎉 <b>AUTO SIGNUP COMPLETED!</b>   ║\n"
                f"╚═══════════════════════════════╝\n\n"
                f"┌────────────────────────────┐\n"
                f"│ 📱 <b>Phone:</b>    <code>{phone}</code>\n"
                f"│ 👤 <b>Name:</b>     <b>{assigned_name}</b>\n"
                f"│ 🎫 <b>Referral:</b> <code>{target_ref}</code>\n"
                f"│ 🆔 <b>User ID:</b>  <code>{sg_uid}</code>\n"
                f"│ ⚡ <b>Source:</b>   Firebase Device Cloud\n"
                f"│ ⏰ <b>Time:</b>     {datetime.now().strftime('%H:%M:%S')}\n"
                f"└────────────────────────────┘\n\n"
                f"🌟 <i>Referral registered and credited to your account!</i>",
                kb_auto_signup_success()
            )
            restore_dashboard_keyboard(chat_id, user_id, "👇 <b>Main Dashboard Menu Restored:</b>")

            # Notify Admin
            if db.get_setting("notifications") == "true" and user_id != ADMIN_ID:
                try:
                    bot.send_message(
                        ADMIN_ID,
                        f"🔔 <b>AUTO REFERRAL SIGNUP SUCCESS!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📱 <b>Phone:</b> <code>{phone}</code>\n"
                        f"👤 <b>Name:</b> {assigned_name}\n"
                        f"🎫 <b>Referral:</b> <code>{target_ref}</code>\n"
                        f"🆔 <b>StockGro UID:</b> <code>{sg_uid}</code>\n"
                        f"👤 <b>By User:</b> <code>{user_id}</code>"
                    )
                except Exception:
                    pass

        except Exception as e:
            print(f"[!] Auto signup pipeline error: {e}")
            safe_edit(chat_id, status_mid, f"⚠️ Error executing auto signup: {e}", kb_auto_timeout_options())

    db.bg_executor.submit(_run_auto_pipeline)

def start_manual_signup_flow(chat_id, user_id, mid=None, ref_code=None):
    """Starts the interactive manual phone number entry flow."""
    s = db.get_all_settings()
    if s.get("maintenance_mode") == "true" and user_id != ADMIN_ID:
        bot.send_message(chat_id, "🔧 <b>System Notice:</b> Bot is currently in maintenance mode. Please check back shortly.")
        return

    if user_id != ADMIN_ID:
        is_member, not_joined = check_user_membership(user_id)
        if not is_member:
            bot.send_message(chat_id, txt_membership_required(not_joined), reply_markup=kb_membership_prompt(not_joined))
            return

    target_ref = ref_code or db.get_user_referral(user_id)
    user_states[user_id] = {
        "step": "phone_input",
        "ref": target_ref
    }

    prompt_text = (
        f"╔═══════════════════════════════╗\n"
        f"║   📱 <b>MANUAL MOBILE SIGNUP</b>     ║\n"
        f"╚═══════════════════════════════╝\n\n"
        f"🎫 <b>Referral Code:</b> <code>{target_ref}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 <b>Please enter the 10-digit Indian Mobile Number:</b>\n\n"
        f"<i>Example: 9876543210</i>\n"
        f"<i>(Must start with 6, 7, 8 or 9)</i>\n\n"
        f"Tap <b>❌ Cancel</b> anytime to abort."
    )
    if mid:
        safe_edit(chat_id, mid, prompt_text, kb_back_user() if user_id != ADMIN_ID else kb_back_admin())
        bot.send_message(chat_id, "👇 <i>Send mobile number in chat:</i>", reply_markup=cancel_reply_keyboard())
    else:
        bot.send_message(chat_id, prompt_text, reply_markup=cancel_reply_keyboard())

def start_signup_flow(chat_id, user_id, mid=None, ref_code=None):
    """Fallback alias for start_manual_signup_flow"""
    start_manual_signup_flow(chat_id, user_id, mid=mid, ref_code=ref_code)

# ==========================================
# TEXT MESSAGE DISPATCHER (REPLY BUTTONS & INPUTS)
# ==========================================

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text_dispatcher(msg):
    uid = msg.from_user.id
    cid = msg.chat.id
    text = msg.text.strip()
    is_admin = (uid == ADMIN_ID)

    # Save/Update user profile in background worker (0ms blocking)
    db.save_user(uid, msg.from_user.username or "", msg.from_user.first_name or "")

    # Fast in-memory ban lookup (0ms)
    if db.is_banned(uid):
        bot.reply_to(msg, "🚫 <b>Account Suspended.</b> Contact admin for help.")
        return

    # ======== HANDLE GLOBAL CANCEL ========
    if text in ("❌ Cancel", "/cancel"):
        user_states.pop(uid, None)
        restore_dashboard_keyboard(cid, uid, "❌ <b>Operation Aborted.</b> Returning to dashboard...")
        return

    # Force join check for regular users
    if not is_admin:
        is_member, not_joined = check_user_membership(uid)
        if not is_member:
            bot.send_message(cid, txt_membership_required(not_joined), reply_markup=kb_membership_prompt(not_joined))
            return

    # ======== PERSISTENT REPLY KEYBOARD BUTTON DISPATCH ========
    if text in ("⚡ Auto Signup (Firebase)", "⚡ Auto Signup"):
        if db.has_user_set_referral(uid):
            saved_ref = db.get_user_referral(uid)
            execute_auto_firebase_signup(cid, uid, ref_code=saved_ref)
        else:
            bot.send_message(cid, txt_referral_choice(uid), reply_markup=kb_referral_options("auto"))
        return

    if text in ("📱 Manual Signup", "📱 Start New Signup", "📱 New Signup"):
        if db.has_user_set_referral(uid):
            saved_ref = db.get_user_referral(uid)
            start_manual_signup_flow(cid, uid, ref_code=saved_ref)
        else:
            bot.send_message(cid, txt_referral_choice(uid), reply_markup=kb_referral_options("manual"))
        return

    if text in ("🎫 Set Referral Code", "🎫 Referral Setup"):
        bot.send_message(cid, txt_referral_choice(uid), reply_markup=kb_referral_options("auto"))
        return

    if text == "📊 My Statistics":
        us = db.get_user_signup_stats(uid)
        failed = us['total'] - us['success']
        bot.send_message(
            cid,
            f"📊 <b>YOUR PERSONAL STATISTICS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"├─ 📱 <b>Total Signups:</b> <code>{us['total']}</code>\n"
            f"├─ ✅ <b>Successful:</b> <code>{us['success']}</code>\n"
            f"└─ ❌ <b>Failed Attempts:</b> <code>{failed}</code>\n\n"
            f"Keep referring and earning rewards! 🌟",
            reply_markup=user_reply_keyboard(is_admin)
        )
        return

    if text == "📋 My Signup History":
        rows = db.get_signups(limit=10)
        user_rows = [r for r in rows if r.get("by_user") == uid][:10]
        if not user_rows:
            bot.send_message(cid, "📋 <b>No Signups Found.</b> You haven't registered any numbers yet.", reply_markup=user_reply_keyboard(is_admin))
        else:
            txt = "📋 <b>YOUR RECENT SIGNUPS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for r in user_rows:
                icon = "✅" if r["status"] == "success" else "❌"
                ts = r["created_at"].strftime("%d %b %H:%M") if r.get("created_at") else "?"
                txt += f"{icon} <code>{r['phone']}</code> │ {r.get('name', 'User')[:12]} │ <i>{ts}</i>\n"
            bot.send_message(cid, txt, reply_markup=user_reply_keyboard(is_admin))
        return

    if text == "🆘 Help & Support":
        bot.send_message(cid, txt_support_info(), reply_markup=user_reply_keyboard(is_admin))
        return

    if text == "🔄 Refresh Dashboard":
        fname = msg.from_user.first_name or "User"
        bot.send_message(cid, txt_user_dashboard(fname, uid), reply_markup=user_reply_keyboard(is_admin))
        return

    # ======== ADMIN PERSISTENT BUTTONS ========
    if text == "🔐 Admin Control Panel" and is_admin:
        bot.send_message(cid, txt_admin_dashboard(), reply_markup=admin_reply_keyboard())
        bot.send_message(cid, "⚡ <b>Quick Actions:</b>", reply_markup=kb_admin_inline())
        return

    if text == "📊 Live Statistics" and is_admin:
        bot.send_message(cid, txt_admin_dashboard(), reply_markup=admin_reply_keyboard())
        return

    if text == "📋 All Signups History" and is_admin:
        rows = db.get_signups(limit=15)
        if not rows:
            bot.send_message(cid, "📋 <b>No Signups in Database.</b>", reply_markup=admin_reply_keyboard())
        else:
            txt = f"📋 <b>ALL RECENT SIGNUPS ({len(rows)} Records)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for r in rows:
                icon = "✅" if r["status"] == "success" else "❌"
                ts = r["created_at"].strftime("%d/%m %H:%M") if r.get("created_at") else "?"
                txt += f"{icon} <code>{r['phone']}</code> │ {r.get('name', '-')[:10]} │ U:<code>{r.get('by_user', 0)}</code> │ <i>{ts}</i>\n"
            bot.send_message(cid, txt, reply_markup=admin_reply_keyboard())
        return

    if text == "🎫 Referral Code" and is_admin:
        ref = db.get_setting("referral_code", "S4LIOAHO")
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(f"✏️ Change Code (Current: {ref})", callback_data="act_edit_referral"))
        bot.send_message(
            cid,
            f"🎫 <b>ACTIVE STOCKGRO REFERRAL CODE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Current Code: <code>{ref}</code>",
            reply_markup=kb
        )
        return

    if text == "🔥 Firebase URLs" and is_admin:
        urls = db.get_firebase_urls(active_only=False)
        bot.send_message(
            cid,
            f"🔥 <b>FIREBASE ENDPOINTS ({len(urls)})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Manage remote device databases:",
            reply_markup=kb_firebase_manager()
        )
        return

    if text == "📢 Channel Manager" and is_admin:
        channels = db.get_channels()
        bot.send_message(
            cid,
            f"📢 <b>MANDATORY JOIN CHANNELS ({len(channels)})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=kb_admin_channels_mgr()
        )
        return

    if text == "👥 User Management" and is_admin:
        uc_info = db.get_user_counts()
        uc = uc_info["total"]
        bc = uc_info["banned"]
        bot.send_message(
            cid,
            f"👥 <b>USER BASE MANAGEMENT</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"├─ 👥 <b>Total Users:</b> <code>{uc}</code>\n"
            f"├─ 🟢 <b>Active:</b> <code>{uc - bc}</code>\n"
            f"└─ 🚫 <b>Banned Accounts:</b> <code>{bc}</code>",
            reply_markup=kb_user_mgmt()
        )
        return

    if text in ("🌐 Proxy Manager", "🌐 Proxies") and is_admin:
        prx = db.get_proxy_count()
        bot.send_message(
            cid,
            f"🌐 <b>STOCKGRO PROXY MANAGER & ROTATION</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Proxies automatically bypass Cloudflare rate-limits during OTP dispatch.\n\n"
            f"├─ 📊 <b>Total Configured:</b> <code>{prx['total']}</code>\n"
            f"├─ 🟢 <b>Active in Rotation:</b> <code>{prx['active']}</code>\n"
            f"└─ 🔴 <b>Inactive / Disabled:</b> <code>{prx['total'] - prx['active']}</code>",
            reply_markup=kb_proxy_manager()
        )
        return

    if text == "⚙️ Bot Settings" and is_admin:
        bot.send_message(
            cid,
            "⚙️ <b>BOT SYSTEM SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=kb_admin_settings()
        )
        return

    if text == "📣 Broadcast Message" and is_admin:
        user_states[uid] = {"step": "broadcast_text"}
        uc = db.get_user_count()
        bot.send_message(
            cid,
            f"📣 <b>BROADCAST ANNOUNCEMENT</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Target Audience: <b>{uc}</b> users.\nSend your message below (HTML supported):",
            reply_markup=cancel_reply_keyboard()
        )
        return

    if text == "🚪 Exit Admin Panel" and is_admin:
        fname = msg.from_user.first_name or "Admin"
        bot.send_message(
            cid,
            f"🚪 <b>Switched to User Dashboard</b>\n\n" + txt_user_dashboard(fname, uid),
            reply_markup=user_reply_keyboard(is_admin=True)
        )
        return

    # ======== STATE MACHINE HANDLING ========
    state = user_states.get(uid)
    if not state:
        return

    step = state.get("step")

    # ---- USER INPUT: CUSTOM REFERRAL CODE ----
    if step == "custom_referral_input":
        code = text.upper().replace(" ", "").strip()
        if len(code) < 3 or len(code) > 20:
            bot.reply_to(msg, "❌ Invalid referral code. Please enter a valid code (3-20 characters):")
            return
        target_action = state.get("target_action", "auto")
        db.set_user_referral(uid, code)
        user_states.pop(uid, None)
        bot.send_message(
            cid,
            f"✅ <b>Referral Code Saved Permanently:</b> <code>{code}</code>\n\n"
            f"All your signups will automatically use this code.\n"
            f"Proceeding to {'Auto Signup (Firebase Cloud)' if target_action == 'auto' else 'Mobile Registration'}...",
            reply_markup=cancel_reply_keyboard()
        )
        if target_action == "auto":
            execute_auto_firebase_signup(cid, uid, ref_code=code)
        else:
            start_manual_signup_flow(cid, uid, ref_code=code)
        return

    # ---- ADMIN INPUT: ADD CHANNEL ----
    if step == "add_channel_info" and is_admin:
        parts = text.split("|")
        if len(parts) < 3:
            bot.reply_to(msg, "❌ Invalid format. Use: <code>ChatID | Name | Link</code>\n<i>Example: -1003980919319 | Nexus IO | https://t.me/Nexus_IO</i>")
            return
        try:
            ch_id = int(parts[0].strip())
            ch_name = parts[1].strip()
            ch_link = parts[2].strip()
            db.add_channel(ch_id, ch_name, ch_link)
            user_states.pop(uid, None)
            bot.send_message(
                cid,
                f"✅ <b>Channel Added Successfully!</b>\n\n"
                f"├─ 🆔 <code>{ch_id}</code>\n"
                f"├─ 🏷 <b>{ch_name}</b>\n"
                f"└─ 🔗 {ch_link}",
                reply_markup=admin_reply_keyboard()
            )
        except ValueError:
            bot.reply_to(msg, "❌ Chat ID must be an integer (e.g. -1003980919319).")
        return

    # ---- ADMIN INPUT: EDIT REFERRAL ----
    if step == "edit_referral" and is_admin:
        new_code = text.upper().strip()
        db.set_setting("referral_code", new_code)
        user_states.pop(uid, None)
        bot.send_message(
            cid,
            f"✅ <b>System Referral Code Updated!</b>\n\nNew Default Code: <code>{new_code}</code>",
            reply_markup=admin_reply_keyboard()
        )
        return

    # ---- ADMIN INPUT: DAILY LIMIT ----
    if step == "set_daily_limit" and is_admin:
        if not text.isdigit():
            bot.reply_to(msg, "❌ Please enter a valid positive number.")
            return
        db.set_setting("max_daily", text)
        user_states.pop(uid, None)
        bot.send_message(cid, f"✅ <b>Daily Limit Set:</b> <b>{text}</b> signups per day.", reply_markup=admin_reply_keyboard())
        return

    # ---- ADMIN INPUT: SUPPORT USERNAME ----
    if step == "set_support_username" and is_admin:
        db.set_setting("support_contact", text)
        user_states.pop(uid, None)
        bot.send_message(cid, f"✅ <b>Support Contact Updated:</b> <b>{text}</b>", reply_markup=admin_reply_keyboard())
        return

    # ---- ADMIN INPUT: ADD FIREBASE URL ----
    if step == "add_firebase" and is_admin:
        url = text.strip().rstrip("/")
        if not url.startswith("http"):
            url = "https://" + url
        label = url.split("//")[-1].split(".")[0]
        db.add_firebase_url(url, label)
        user_states.pop(uid, None)
        bot.send_message(cid, f"✅ <b>Firebase Database Added:</b>\n<code>{url}</code>", reply_markup=admin_reply_keyboard())
        return

    # ---- ADMIN INPUT: ADD SINGLE PROXY ----
    if step == "add_proxy_single" and is_admin:
        raw_proxy = text.strip()
        user_states.pop(uid, None)
        try:
            db.add_proxy(raw_proxy)
            bot.send_message(
                cid,
                f"✅ <b>Proxy Added to Rotation Pool!</b>\n\n"
                f"🔗 <code>{raw_proxy}</code>\n\n"
                f"Testing connectivity to StockGro...",
                reply_markup=admin_reply_keyboard()
            )
            ok, res_str = test_proxy_connectivity(raw_proxy)
            bot.send_message(cid, f"🧪 <b>Proxy Test:</b> {res_str}")
        except Exception as e:
            bot.send_message(cid, f"❌ Failed to add proxy: {e}", reply_markup=admin_reply_keyboard())
        return

    # ---- ADMIN INPUT: ADD BULK PROXIES ----
    if step == "add_proxies_bulk" and is_admin:
        user_states.pop(uid, None)
        added_count = db.add_bulk_proxies(text)
        prx = db.get_proxy_count()
        bot.send_message(
            cid,
            f"✅ <b>Bulk Proxies Processed!</b>\n\n"
            f"├─ ➕ <b>Newly Added:</b> <code>{added_count}</code>\n"
            f"└─ 🌐 <b>Total in Pool:</b> <code>{prx['active']} Active</code>",
            reply_markup=admin_reply_keyboard()
        )
        return

    # ---- ADMIN INPUT: BAN USER ----
    if step == "ban_user_id" and is_admin:
        if not text.isdigit():
            bot.reply_to(msg, "❌ Invalid user ID. Numbers only.")
            return
        target_uid = int(text)
        db.ban_user(target_uid)
        user_states.pop(uid, None)
        bot.send_message(cid, f"🚫 <b>User {target_uid} has been banned.</b>", reply_markup=admin_reply_keyboard())
        return

    # ---- ADMIN INPUT: UNBAN USER ----
    if step == "unban_user_id" and is_admin:
        if not text.isdigit():
            bot.reply_to(msg, "❌ Invalid user ID. Numbers only.")
            return
        target_uid = int(text)
        db.unban_user(target_uid)
        user_states.pop(uid, None)
        bot.send_message(cid, f"✅ <b>User {target_uid} has been unbanned.</b>", reply_markup=admin_reply_keyboard())
        return

    # ---- ADMIN INPUT: BROADCAST ----
    if step == "broadcast_text" and is_admin:
        user_states.pop(uid, None)
        users = db.get_all_users()
        sent = 0
        failed = 0
        status_msg = bot.send_message(cid, f"⏳ <i>Broadcasting to {len(users)} users...</i>")
        for u in users:
            if u.get("is_banned"):
                continue
            try:
                bot.send_message(u["user_id"], f"📢 <b>ANNOUNCEMENT</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n{text}")
                sent += 1
            except Exception:
                failed += 1
            time.sleep(0.04)
        bot.edit_message_text(
            f"📣 <b>BROADCAST COMPLETED</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ <b>Successfully Sent:</b> <code>{sent}</code>\n"
            f"❌ <b>Failed / Blocked:</b> <code>{failed}</code>",
            cid, status_msg.message_id
        )
        restore_dashboard_keyboard(cid, uid, "👇 <b>Admin Control Ready:</b>")
        return

    # ======== SIGNUP FLOW: PHONE NUMBER ========
    if step == "phone_input":
        phone = text.replace("+91", "").replace(" ", "").replace("-", "")
        if len(phone) != 10 or not phone.isdigit() or phone[0] not in "6789":
            bot.reply_to(
                msg,
                "❌ <b>Invalid Phone Number!</b>\n\n"
                "Please enter a valid <b>10-digit Indian mobile number</b> starting with 6, 7, 8 or 9."
            )
            return

        # Check daily limits (instant DB aggregate)
        st = db.get_signup_stats()
        max_d = int(db.get_setting("max_daily", "50"))
        if st["today"] >= max_d and not is_admin:
            bot.reply_to(msg, f"⚠️ <b>Daily Limit Reached:</b> Maximum of {max_d} signups allowed today. Please try again tomorrow.")
            user_states.pop(uid, None)
            restore_dashboard_keyboard(cid, uid)
            return

        pm = bot.send_message(
            cid,
            f"📱 <code>{phone}</code>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ <b>Step 1/3:</b> Handshaking with StockGro Cloud..."
        )

        # 1. State Token
        state_token = sg_get_state()
        if not state_token:
            bot.edit_message_text(
                f"📱 <code>{phone}</code>\n\n❌ <b>Connection Error:</b> Unable to connect to StockGro. Please try again.",
                cid, pm.message_id
            )
            user_states.pop(uid, None)
            restore_dashboard_keyboard(cid, uid)
            return

        headers = sg_headers(state_token)

        # 2. Check Identity
        bot.edit_message_text(
            f"📱 <code>{phone}</code>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Step 1/3:</b> Cloud Connected\n"
            f"⏳ <b>Step 2/3:</b> Checking Number Registration...",
            cid, pm.message_id
        )

        d0 = sg_post(f"{SG_API}/getIdentity",
                     {"phone_number": phone, "country_code": "IN", "otp_channel": "sms"}, headers)

        if not d0 or not d0.get("success"):
            err = sget(d0, "message") or sget(d0, "error") or sget(d0, "error_code") or "Verification request failed"
            if d0.get("_status_code") == 429 or "rate" in str(err).lower():
                err = "⏳ <b>Rate Limit Protection:</b> StockGro requires a 30-second cooldown between requests. Please wait a moment and try again."
            bot.edit_message_text(
                f"📱 <code>{phone}</code>\n\n❌ <b>Verification Notice:</b>\n{err}",
                cid, pm.message_id, reply_markup=kb_signup_success()
            )
            db.add_signup(phone, "", "", "", "failed_identity", str(err), uid)
            user_states.pop(uid, None)
            restore_dashboard_keyboard(cid, uid)
            return

        # 2b. Already Registered check
        if sget(d0, "data", "existing_user", default=False):
            bot.edit_message_text(
                f"╔═══════════════════════════════╗\n"
                f"║  ⚠️ <b>NUMBER ALREADY REGISTERED</b> ║\n"
                f"╚═══════════════════════════════╝\n\n"
                f"📱 <b>Mobile Number:</b> <code>{phone}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"ℹ️ <b>Status Details:</b>\n"
                f"This phone number already has an active StockGro account. Referral rewards only apply to <b>new, unregistered numbers</b>.\n\n"
                f"💡 <b>What's Next:</b>\n"
                f"• Tap <b>📱 Another Signup</b> below to try another number.\n"
                f"• Or use the main menu options below.",
                cid, pm.message_id, reply_markup=kb_signup_success()
            )
            db.add_signup(phone, "", "", "", "already_registered", "", uid)
            user_states.pop(uid, None)
            restore_dashboard_keyboard(cid, uid, "👇 <b>Main Dashboard Menu Restored:</b>")
            return

        # 3. Create & Send OTP
        ref = state.get("ref") or db.get_user_referral(uid)
        bot.edit_message_text(
            f"📱 <code>{phone}</code>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Step 1/3:</b> Cloud Connected\n"
            f"✅ <b>Step 2/3:</b> Number is Unregistered\n"
            f"⏳ <b>Step 3/3:</b> Dispatching OTP (Referral: <code>{ref}</code>)...",
            cid, pm.message_id
        )

        time.sleep(1.0)  # Cloudflare pacing delay

        d1 = sg_post(f"{SG_API}/login/createOtp",
                     {"phone_number": phone, "country_code": "IN", "otp_channel": "sms",
                      "invitation_code": ref, "flow_type": "signup"}, headers)

        if not d1 or not d1.get("success"):
            err = sget(d1, "message") or sget(d1, "error") or "Failed to dispatch SMS OTP"
            if d1.get("_status_code") == 429 or "rate" in str(err).lower():
                err = "⏳ <b>Cooldown Active:</b> Please wait 30 seconds before requesting another OTP for security."
            elif "something went wrong" in str(err).lower():
                err = "⚠️ StockGro was unable to send OTP to this number right now. It may have reached daily SMS limits or cooldown. Please retry with another number or wait 1 minute."
            bot.edit_message_text(
                f"📱 <code>{phone}</code>\n\n❌ <b>OTP Dispatch Notice:</b>\n{err}",
                cid, pm.message_id, reply_markup=kb_signup_success()
            )
            db.add_signup(phone, "", ref, "", "otp_failed", str(err), uid)
            user_states.pop(uid, None)
            restore_dashboard_keyboard(cid, uid)
            return

        sid = sget(d1, "data", "session_id")
        if not sid:
            bot.edit_message_text(f"📱 <code>{phone}</code>\n\n❌ Session token error.", cid, pm.message_id)
            user_states.pop(uid, None)
            restore_dashboard_keyboard(cid, uid)
            return

        assigned_name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        user_states[uid] = {
            "step": "otp_input",
            "phone": phone,
            "sid": sid,
            "headers": headers,
            "ref": ref,
            "name": assigned_name
        }

        bot.edit_message_text(
            f"╔═══════════════════════════════╗\n"
            f"║     📩 <b>OTP DISPATCHED!</b>         ║\n"
            f"╚═══════════════════════════════╝\n\n"
            f"📱 <b>Phone:</b> <code>{phone}</code>\n"
            f"👤 <b>Assigned Profile:</b> <b>{assigned_name}</b>\n"
            f"🎫 <b>Referral Code:</b> <code>{ref}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔐 <b>Enter the 6-digit OTP received via SMS:</b>",
            cid, pm.message_id
        )
        return

    # ======== SIGNUP FLOW: OTP VALIDATION & REGISTRATION ========
    if step == "otp_input":
        otp = text.strip()
        if len(otp) != 6 or not otp.isdigit():
            bot.reply_to(msg, "❌ <b>Invalid OTP!</b> Please send the exact 6-digit numeric code.")
            return

        phone = state["phone"]
        sid = state["sid"]
        headers = state["headers"]
        ref = state["ref"]
        assigned_name = state["name"]

        pm = bot.send_message(
            cid,
            f"📱 <code>{phone}</code> │ OTP: <code>{otp}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Validating verification code..."
        )

        # 1. Validate OTP
        d2 = sg_post(f"{SG_API}/login/validateOtp",
                     {"session_id": sid, "otp": otp, "phone_number": phone,
                      "country_code": "IN", "otp_channel": "sms", "flow_type": "signup"}, headers)

        if not d2 or not d2.get("success"):
            err = sget(d2, "message", default=sget(d2, "error", default="Invalid OTP Code"))
            bot.edit_message_text(
                f"╔═══════════════════════════════╗\n"
                f"║   ❌ <b>OTP VERIFICATION FAILED</b>   ║\n"
                f"╚═══════════════════════════════╝\n\n"
                f"📱 <code>{phone}</code>\n"
                f"❌ <b>Error:</b> {err}\n\n"
                f"<i>Please restart signup with a fresh OTP request.</i>",
                cid, pm.message_id, reply_markup=kb_signup_success()
            )
            db.add_signup(phone, assigned_name, ref, "", "otp_invalid", str(err), uid)
            user_states.pop(uid, None)
            restore_dashboard_keyboard(cid, uid)
            return

        # 2. Register Account
        bot.edit_message_text(
            f"📱 <code>{phone}</code>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ OTP Verified!\n"
            f"⏳ Creating account for <b>{assigned_name}</b> with Referral: <code>{ref}</code>...",
            cid, pm.message_id
        )

        d3 = sg_post(f"{SG_API}/signup/registerUser",
                     {"display_name": assigned_name, "invitation_code": ref, "otp": otp,
                      "session_id": sid, "whatsapp_consent": True,
                      "phone_number": phone, "country_code": "IN", "otp_channel": "sms"}, headers)

        if not d3 or not d3.get("success"):
            err = sget(d3, "message", default=sget(d3, "error", default="Registration Failed"))
            bot.edit_message_text(
                f"╔═══════════════════════════════╗\n"
                f"║    ❌ <b>REGISTRATION ERROR</b>      ║\n"
                f"╚═══════════════════════════════╝\n\n"
                f"📱 <code>{phone}</code>\n"
                f"❌ <b>Error:</b> {err}",
                cid, pm.message_id, reply_markup=kb_signup_success()
            )
            db.add_signup(phone, assigned_name, ref, "", "register_failed", str(err), uid)
            user_states.pop(uid, None)
            restore_dashboard_keyboard(cid, uid)
            return

        sg_uid = sget(d3, "data", "user_id", default="unknown")
        redir = sget(d3, "data", "redirect_uri", default="")
        if redir and "access_code=" in redir:
            sg_post("https://app.stockgro.club/api/login", {"code": redir.split("access_code=")[-1]}, headers)

        # Record in Postgres Database (async background write)
        db.add_signup(phone, assigned_name, ref, sg_uid, "success", "", uid)

        # Notify User
        bot.edit_message_text(
            f"╔═══════════════════════════════╗\n"
            f"║   🎉 <b>SIGNUP COMPLETED!</b>       ║\n"
            f"╚═══════════════════════════════╝\n\n"
            f"┌────────────────────────────┐\n"
            f"│ 📱 <b>Phone:</b>    <code>{phone}</code>\n"
            f"│ 👤 <b>Name:</b>     <b>{assigned_name}</b>\n"
            f"│ 🎫 <b>Referral:</b> <code>{ref}</code>\n"
            f"│ 🆔 <b>User ID:</b>  <code>{sg_uid}</code>\n"
            f"│ ⏰ <b>Time:</b>     {datetime.now().strftime('%H:%M:%S')}\n"
            f"└────────────────────────────┘\n\n"
            f"✅ <i>Record saved permanently in PostgreSQL Database!</i>",
            cid, pm.message_id, reply_markup=kb_signup_success()
        )

        user_states.pop(uid, None)
        restore_dashboard_keyboard(cid, uid, "👇 <b>Main Dashboard Menu Restored:</b>")

        # Notify Admin (if configured and not performed by admin)
        if db.get_setting("notifications") == "true" and not is_admin:
            try:
                bot.send_message(
                    ADMIN_ID,
                    f"🔔 <b>NEW REFERRAL SIGNUP!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📱 <b>Phone:</b> <code>{phone}</code>\n"
                    f"👤 <b>Name:</b> {assigned_name}\n"
                    f"🎫 <b>Referral:</b> <code>{ref}</code>\n"
                    f"🆔 <b>StockGro UID:</b> <code>{sg_uid}</code>\n"
                    f"👤 <b>By User:</b> <code>{uid}</code> (@{msg.from_user.username or 'N/A'})"
                )
            except Exception:
                pass
        return

# ==========================================
# BOT INITIALIZATION & LAUNCH
# ==========================================

def start_bot():
    print("=" * 60)
    print("  🚀 STOCKGRO BOT — FORCE-JOIN & HIGH SPEED POSTGRESQL")
    print(f"  👑 Admin ID: {ADMIN_ID}")
    print("=" * 60)

    # Start Flask keep-alive thread immediately for 0ms Render port binding
    keep_alive()
    print("[+] Keep-alive web server active on :10000 / $PORT")

    # Initialize Postgres DB Connection Pool & Tables
    db.init_db(DATABASE_URL)
    print("[+] Database connected, indexed & in-memory cache warmed up!")
    print("[+] Multi-threaded (16 workers) polling active!\n")

    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"[!] Bot Polling Warning: {e}")
            time.sleep(3)

if __name__ == "__main__":
    start_bot()
