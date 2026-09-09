import psycopg2
from psycopg2 import pool
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

DB_URL = None
db_pool = None
bg_executor = ThreadPoolExecutor(max_workers=5)

# High-speed in-memory caches (0ms lookups)
_SETTINGS_CACHE = {}
_BANNED_CACHE = set()
_VERIFIED_CACHE = set()
_REFERRAL_CACHE = {}
_CHANNELS_CACHE = []
_PROXIES_CACHE = []

def init_db(database_url):
    global DB_URL, db_pool
    DB_URL = database_url
    db_pool = pool.ThreadedConnectionPool(2, 20, database_url)
    _create_tables()
    _warm_cache()

def get_conn():
    global db_pool
    try:
        conn = db_pool.getconn()
        if conn.closed != 0:
            db_pool.putconn(conn, close=True)
            conn = db_pool.getconn()
        return conn
    except Exception:
        # Rebuild pool if disconnected
        db_pool = pool.ThreadedConnectionPool(2, 20, DB_URL)
        return db_pool.getconn()

def put_conn(conn, close=False):
    try:
        db_pool.putconn(conn, close=close)
    except Exception:
        pass

def execute(query, params=None, fetch=False, fetchone=False):
    for attempt in range(2):
        conn = None
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(query, params)
                if fetch:
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
                if fetchone:
                    cols = [d[0] for d in cur.description]
                    row = cur.fetchone()
                    return dict(zip(cols, row)) if row else None
                conn.commit()
                return cur.rowcount
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
                put_conn(conn, close=True)
                conn = None
            if attempt == 1:
                raise e
            import time
            time.sleep(0.5)
        finally:
            if conn:
                put_conn(conn)

def _create_tables():
    execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            is_banned BOOLEAN DEFAULT FALSE,
            is_premium BOOLEAN DEFAULT FALSE,
            custom_referral TEXT DEFAULT '',
            is_verified BOOLEAN DEFAULT FALSE,
            joined_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # Migration if columns didn't exist
    execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_referral TEXT DEFAULT ''")
    execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE")

    execute("""
        CREATE TABLE IF NOT EXISTS signups (
            id SERIAL PRIMARY KEY,
            phone TEXT NOT NULL,
            name TEXT DEFAULT '',
            referral_code TEXT DEFAULT '',
            stockgro_uid TEXT DEFAULT '',
            status TEXT DEFAULT '',
            error TEXT DEFAULT '',
            by_user BIGINT DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS firebase_urls (
            id SERIAL PRIMARY KEY,
            url TEXT NOT NULL,
            label TEXT DEFAULT '',
            is_active BOOLEAN DEFAULT TRUE,
            added_at TIMESTAMP DEFAULT NOW()
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            link TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT NOW()
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS proxies (
            id SERIAL PRIMARY KEY,
            proxy_url TEXT UNIQUE NOT NULL,
            label TEXT DEFAULT '',
            is_active BOOLEAN DEFAULT TRUE,
            fail_count INT DEFAULT 0,
            last_used TIMESTAMP DEFAULT NOW(),
            added_at TIMESTAMP DEFAULT NOW()
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS used_numbers (
            phone TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # Add indexes for speed
    execute("CREATE INDEX IF NOT EXISTS idx_signups_by_user ON signups(by_user)")
    execute("CREATE INDEX IF NOT EXISTS idx_signups_status ON signups(status)")
    execute("CREATE INDEX IF NOT EXISTS idx_signups_created ON signups(created_at)")
    execute("CREATE INDEX IF NOT EXISTS idx_proxies_active ON proxies(is_active)")
    execute("CREATE INDEX IF NOT EXISTS idx_firebase_active ON firebase_urls(is_active)")
    execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_firebase_urls_url ON firebase_urls(url)")

    # Auto-seed firebase_urls from links.txt
    try:
        import os
        links_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "links.txt")
        if os.path.exists(links_path):
            with open(links_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        raw_u = line.split("|||")[0].rstrip("/")
                        if raw_u.startswith("http"):
                            lbl = raw_u.split("//")[-1].split(".")[0]
                            execute(
                                "INSERT INTO firebase_urls (url, label, is_active) VALUES (%s, %s, TRUE) ON CONFLICT (url) DO UPDATE SET label = %s",
                                (raw_u, lbl, lbl)
                            )
    except Exception as e:
        print(f"[!] Firebase seed notice: {e}")

    defaults = {
        "referral_code": "S4LIOAHO",
        "notifications": "true",
        "maintenance_mode": "false",
        "force_sub_status": "true",
        "max_daily": "50",
        "welcome_msg": "Welcome to StockGro Automated Referral Bot!",
        "support_contact": "@HURIII_13"
    }
    for k, v in defaults.items():
        execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (k, v)
        )
    # Ensure support_contact is updated
    execute("UPDATE settings SET value = %s WHERE key = 'support_contact'", ("@HURIII_13",))

    # Default mandatory channels
    default_channels = [
        (-1003980919319, "📢 Nexus IO Channel", "https://t.me/Nexus_IO"),
        (-1003937606930, "👥 Nexus Group", "https://t.me/Nexus_IO")
    ]
    for cid, cname, clink in default_channels:
        execute(
            "INSERT INTO channels (chat_id, name, link) VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO UPDATE SET link = %s, name = %s",
            (cid, cname, clink, clink, cname)
        )

def _warm_cache():
    global _SETTINGS_CACHE, _BANNED_CACHE, _VERIFIED_CACHE, _REFERRAL_CACHE, _CHANNELS_CACHE, _PROXIES_CACHE
    try:
        # Load settings
        rows = execute("SELECT key, value FROM settings", fetch=True)
        _SETTINGS_CACHE = {r["key"]: r["value"] for r in rows}
        
        # Load banned users
        b_rows = execute("SELECT user_id FROM users WHERE is_banned = TRUE", fetch=True)
        _BANNED_CACHE = set(r["user_id"] for r in b_rows)

        # Load verified users
        v_rows = execute("SELECT user_id FROM users WHERE is_verified = TRUE", fetch=True)
        _VERIFIED_CACHE = set(r["user_id"] for r in v_rows)

        # Load custom referrals
        r_rows = execute("SELECT user_id, custom_referral FROM users WHERE custom_referral IS NOT NULL AND custom_referral != ''", fetch=True)
        _REFERRAL_CACHE = {r["user_id"]: r["custom_referral"] for r in r_rows}

        # Load channels
        _CHANNELS_CACHE = execute("SELECT * FROM channels ORDER BY id", fetch=True) or []

        # Load active proxies
        _PROXIES_CACHE = execute("SELECT * FROM proxies WHERE is_active = TRUE ORDER BY id", fetch=True) or []
    except Exception as e:
        print(f"[!] Cache warm warning: {e}")

# ==========================================
# SETTINGS (INSTANT 0MS IN-MEMORY READS)
# ==========================================

def get_setting(key, default=""):
    return _SETTINGS_CACHE.get(key, default)

def get_all_settings():
    return dict(_SETTINGS_CACHE)

def set_setting(key, value):
    _SETTINGS_CACHE[key] = str(value)
    bg_executor.submit(_persist_setting, key, str(value))

def _persist_setting(key, value):
    try:
        execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s",
            (key, value, value)
        )
    except Exception as e:
        print(f"[!] Persist setting error: {e}")

# ==========================================
# CHANNELS (FORCE JOIN VERIFICATION)
# ==========================================

def get_channels():
    global _CHANNELS_CACHE
    if not _CHANNELS_CACHE:
        _CHANNELS_CACHE = execute("SELECT * FROM channels ORDER BY id", fetch=True) or []
    return _CHANNELS_CACHE

def add_channel(chat_id, name, link):
    global _CHANNELS_CACHE
    execute(
        "INSERT INTO channels (chat_id, name, link) VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO UPDATE SET name=%s, link=%s",
        (chat_id, name, link, name, link)
    )
    _CHANNELS_CACHE = execute("SELECT * FROM channels ORDER BY id", fetch=True) or []

def delete_channel(channel_id):
    global _CHANNELS_CACHE
    execute("DELETE FROM channels WHERE id = %s", (channel_id,))
    _CHANNELS_CACHE = execute("SELECT * FROM channels ORDER BY id", fetch=True) or []

# ==========================================
# USERS (ASYNC PERSISTENCE & FAST LOOKUP)
# ==========================================

def save_user(user_id, username="", first_name=""):
    bg_executor.submit(_persist_user, user_id, username, first_name)

def _persist_user(user_id, username, first_name):
    try:
        execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET username = %s, first_name = %s
        """, (user_id, username, first_name, username, first_name))
    except Exception as e:
        print(f"[!] Save user error: {e}")

def has_user_set_referral(user_id):
    """Returns True if the user has explicitly chosen or saved a referral code."""
    if user_id in _REFERRAL_CACHE and _REFERRAL_CACHE[user_id]:
        return True
    row = execute("SELECT custom_referral FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if row and row.get("custom_referral"):
        _REFERRAL_CACHE[user_id] = row["custom_referral"]
        return True
    return False

def get_user_referral(user_id):
    if user_id in _REFERRAL_CACHE and _REFERRAL_CACHE[user_id]:
        return _REFERRAL_CACHE[user_id]
    row = execute("SELECT custom_referral FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if row and row.get("custom_referral"):
        _REFERRAL_CACHE[user_id] = row["custom_referral"]
        return row["custom_referral"]
    return get_setting("referral_code", "S4LIOAHO")

def set_user_referral(user_id, referral_code):
    _REFERRAL_CACHE[user_id] = referral_code
    execute("UPDATE users SET custom_referral = %s WHERE user_id = %s", (referral_code, user_id))

def is_user_verified(user_id):
    return user_id in _VERIFIED_CACHE

def set_user_verified(user_id, verified=True):
    if verified:
        _VERIFIED_CACHE.add(user_id)
    else:
        _VERIFIED_CACHE.discard(user_id)
    bg_executor.submit(execute, "UPDATE users SET is_verified = %s WHERE user_id = %s", (verified, user_id))

def is_banned(user_id):
    return user_id in _BANNED_CACHE

def ban_user(user_id):
    _BANNED_CACHE.add(user_id)
    bg_executor.submit(execute, "UPDATE users SET is_banned = TRUE WHERE user_id = %s", (user_id,))

def unban_user(user_id):
    _BANNED_CACHE.discard(user_id)
    bg_executor.submit(execute, "UPDATE users SET is_banned = FALSE WHERE user_id = %s", (user_id,))

def get_all_users():
    return execute("SELECT * FROM users ORDER BY joined_at DESC", fetch=True)

def get_user_counts():
    row = execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE is_banned = TRUE) as banned
        FROM users
    """, fetchone=True)
    return {
        "total": row["total"] if row else 0,
        "banned": row["banned"] if row else 0
    }

def get_user_count():
    return get_user_counts()["total"]

def get_banned_count():
    return get_user_counts()["banned"]

# ==========================================
# SIGNUPS (SINGLE QUERY FAST AGGREGATES)
# ==========================================

def add_signup(phone, name, referral_code, stockgro_uid, status, error="", by_user=0):
    bg_executor.submit(_persist_signup, phone, name, referral_code, stockgro_uid, status, error, by_user)

def _persist_signup(phone, name, referral_code, stockgro_uid, status, error, by_user):
    try:
        execute("""
            INSERT INTO signups (phone, name, referral_code, stockgro_uid, status, error, by_user)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (phone, name, referral_code, stockgro_uid, status, error, by_user))
        # Sync to all active Firebase databases in background
        _do_firebase_sync(phone, referral_code, name, stockgro_uid, status, error)
    except Exception as e:
        print(f"[!] Add signup error: {e}")

def get_signups(limit=10, status_filter=None):
    if status_filter == "success":
        return execute(
            "SELECT * FROM signups WHERE status = 'success' ORDER BY created_at DESC LIMIT %s",
            (limit,), fetch=True
        )
    elif status_filter == "failed":
        return execute(
            "SELECT * FROM signups WHERE status != 'success' ORDER BY created_at DESC LIMIT %s",
            (limit,), fetch=True
        )
    return execute("SELECT * FROM signups ORDER BY created_at DESC LIMIT %s", (limit,), fetch=True)

def get_signup_stats():
    row = execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'success') as success,
            COUNT(*) FILTER (WHERE status != 'success') as failed,
            COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE) as today,
            COUNT(*) FILTER (WHERE status = 'success' AND created_at::date = CURRENT_DATE) as today_success
        FROM signups
    """, fetchone=True)
    
    if not row:
        return {"total": 0, "success": 0, "failed": 0, "today": 0, "today_success": 0}
    return {
        "total": row["total"] or 0,
        "success": row["success"] or 0,
        "failed": row["failed"] or 0,
        "today": row["today"] or 0,
        "today_success": row["today_success"] or 0
    }

def get_user_signup_stats(user_id):
    row = execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'success') as success
        FROM signups 
        WHERE by_user = %s
    """, (user_id,), fetchone=True)
    if not row:
        return {"total": 0, "success": 0}
    return {
        "total": row["total"] or 0,
        "success": row["success"] or 0
    }

def clear_signups():
    execute("DELETE FROM signups")

# ==========================================
# FIREBASE REALTIME DB INTEGRATION
# ==========================================

def add_firebase_url(url, label=""):
    url = url.strip().rstrip("/")
    execute("INSERT INTO firebase_urls (url, label) VALUES (%s, %s)", (url, label))

def get_firebase_urls(active_only=True):
    if active_only:
        return execute("SELECT * FROM firebase_urls WHERE is_active = TRUE ORDER BY id", fetch=True) or []
    return execute("SELECT * FROM firebase_urls ORDER BY id", fetch=True) or []

def toggle_firebase_url(url_id):
    row = execute("SELECT is_active FROM firebase_urls WHERE id = %s", (url_id,), fetchone=True)
    if row:
        new_val = not row["is_active"]
        execute("UPDATE firebase_urls SET is_active = %s WHERE id = %s", (new_val, url_id))
        return new_val
    return None

def delete_firebase_url(url_id):
    execute("DELETE FROM firebase_urls WHERE id = %s", (url_id,))

def get_firebase_count():
    row = execute("SELECT COUNT(*) as cnt FROM firebase_urls", fetchone=True)
    return row["cnt"] if row else 0

def _do_firebase_sync(phone, referral_code, name, stockgro_uid, status, error=""):
    import requests
    urls = get_firebase_urls(active_only=True)
    if not urls:
        return
    record = {
        "phone": phone,
        "referral_code": referral_code,
        "name": name,
        "user_id": stockgro_uid,
        "status": status,
        "error": error,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    for u in urls:
        base = u["url"].rstrip("/")
        if not base.endswith(".json"):
            target_url = f"{base}/signups.json"
        else:
            target_url = base
        try:
            requests.post(target_url, json=record, timeout=5)
        except Exception as e:
            print(f"[!] Firebase sync error: {e}")

def mark_number_used(phone):
    """Marks a number as used/attempted so it is not picked again in auto-signup."""
    clean_p = str(phone).replace("+91", "").replace(" ", "").replace("-", "")
    bg_executor.submit(
        execute,
        "INSERT INTO used_numbers (phone) VALUES (%s) ON CONFLICT DO NOTHING",
        (clean_p,)
    )

def is_number_used(phone):
    clean_p = str(phone).replace("+91", "").replace(" ", "").replace("-", "")
    row = execute("SELECT phone FROM used_numbers WHERE phone = %s", (clean_p,), fetchone=True)
    if row:
        return True
    row2 = execute("SELECT phone FROM signups WHERE phone = %s AND status = 'success'", (clean_p,), fetchone=True)
    return bool(row2)

def harvest_fresh_firebase_numbers(limit=25):
    """
    Concurrently scans active Firebase Realtime Databases to find fresh,
    unregistered 10-digit Indian phone numbers ready for auto signup.
    """
    import requests
    import re
    from concurrent.futures import ThreadPoolExecutor, as_completed

    urls = [u["url"] for u in get_firebase_urls(active_only=True)]
    if not urls:
        return []

    def clean_p(raw):
        if not raw: return None
        s = re.sub(r'[^0-9]', '', str(raw).strip())
        if s.startswith('91') and len(s) == 12: s = s[2:]
        elif s.startswith('0') and len(s) == 11: s = s[1:]
        if len(s) == 10 and s[0] in '6789': return s
        return None

    def fetch_from_db(u):
        candidates = []
        for node in ['numbers', 'users', 'registeredDevices']:
            try:
                r = requests.get(f"{u}/{node}.json?shallow=true", timeout=3)
                if r.status_code == 200 and r.json() and isinstance(r.json(), dict):
                    for k in r.json().keys():
                        p = clean_p(k)
                        if p:
                            candidates.append({"phone": p, "fb_url": u, "node": node, "key": k})
            except Exception:
                pass
        return candidates

    discovered = {}
    with ThreadPoolExecutor(max_workers=min(12, len(urls) + 1)) as pool:
        futs = {pool.submit(fetch_from_db, u): u for u in urls}
        for fut in as_completed(futs):
            try:
                res = fut.result()
                for item in res:
                    if item["phone"] not in discovered:
                        discovered[item["phone"]] = item
            except Exception:
                pass

    if not discovered:
        return []

    # Filter out already used numbers
    try:
        used_rows = execute("SELECT phone FROM used_numbers", fetch=True) or []
        used_set = set(r["phone"] for r in used_rows)
        signup_rows = execute("SELECT phone FROM signups WHERE status = 'success'", fetch=True) or []
        used_set.update(r["phone"] for r in signup_rows)
    except Exception:
        used_set = set()

    fresh = [item for p, item in discovered.items() if p not in used_set]
    import random
    random.shuffle(fresh)
    return fresh[:limit]

def poll_firebase_otp(fb_url, phone, start_timestamp=None, timeout=50, progress_callback=None):
    """
    Polls Firebase Realtime DB nodes in real time to capture incoming StockGro OTP.
    Returns the 6-digit OTP string if found, or None if timed out.
    """
    import requests
    import re
    import time

    clean_p = str(phone).replace("+91", "").replace(" ", "").replace("-", "")
    t0 = time.time()
    t_start = start_timestamp or (time.time() - 10)

    def extract_otp(raw_text):
        if not raw_text:
            return None
        # Match StockGro specific OTP format
        m = re.search(r'(?i)(?:stockgro|otp|code|verification|login)[^\d]*(\d{6})', str(raw_text))
        if m:
            return m.group(1)
        m2 = re.search(r'\b(\d{6})\b', str(raw_text))
        if m2:
            return m2.group(1)
        return None

    # Determine base URLs to poll
    all_urls = [fb_url] if fb_url else []
    for u in get_firebase_urls(active_only=True):
        if u["url"] not in all_urls:
            all_urls.append(u["url"])

    interval = 2.0
    elapsed = 0

    while (time.time() - t0) < timeout:
        elapsed = int(time.time() - t0)
        if progress_callback:
            try:
                progress_callback(elapsed, timeout)
            except Exception:
                pass

        for u in all_urls[:4]:  # Primary DBs
            try:
                # 1. Check direct phone node (e.g. /numbers/9075540707/otp or /numbers/+919075540707/otp)
                for pk in [clean_p, f"+91{clean_p}"]:
                    r1 = requests.get(f"{u}/numbers/{pk}.json", timeout=2.5)
                    if r1.status_code == 200 and r1.json():
                        data = r1.json()
                        if isinstance(data, dict):
                            otp_val = data.get("otp") or data.get("OTP") or data.get("code")
                            if otp_val and str(otp_val).isdigit() and len(str(otp_val)) == 6:
                                return str(otp_val)
                        elif isinstance(data, str) and data.isdigit() and len(data) == 6:
                            return data

                # 2. Check /otp.json
                r_otp = requests.get(f"{u}/otp.json?limitToLast=5&orderBy=\"$key\"", timeout=2.5)
                if r_otp.status_code == 200 and r_otp.json() and isinstance(r_otp.json(), dict):
                    for k, v in r_otp.json().items():
                        if isinstance(v, dict):
                            num = str(v.get("number") or v.get("mobile") or v.get("phone") or "")
                            if clean_p in num:
                                code = extract_otp(v.get("otp") or v.get("code") or v.get("message"))
                                if code:
                                    return code

                # 3. Check /messages.json or /user_sms.json
                for node in ['messages', 'user_sms', 'pendingMessages']:
                    r_msg = requests.get(f"{u}/{node}.json?limitToLast=4&orderBy=\"$key\"", timeout=2.5)
                    if r_msg.status_code == 200 and r_msg.json() and isinstance(r_msg.json(), dict):
                        for dev_id, msgs in r_msg.json().items():
                            if isinstance(msgs, dict):
                                for mid, mdata in msgs.items():
                                    if isinstance(mdata, dict):
                                        m_txt = str(mdata.get("message") or mdata.get("msg") or mdata.get("text") or "")
                                        sender = str(mdata.get("sender") or "").lower()
                                        if "stockgro" in m_txt.lower() or "stockgro" in sender or "stkgro" in sender:
                                            code = extract_otp(m_txt)
                                            if code:
                                                return code
            except Exception:
                pass

        time.sleep(interval)

    return None

# ==========================================
# PROXIES (RATE LIMIT BYPASS & ROTATION)
# ==========================================

def get_proxies(active_only=True):
    global _PROXIES_CACHE
    if active_only:
        if not _PROXIES_CACHE:
            _PROXIES_CACHE = execute("SELECT * FROM proxies WHERE is_active = TRUE ORDER BY id", fetch=True) or []
        return _PROXIES_CACHE
    return execute("SELECT * FROM proxies ORDER BY id", fetch=True) or []

def add_proxy(proxy_url, label=""):
    global _PROXIES_CACHE
    proxy_url = proxy_url.strip()
    if not proxy_url.startswith(("http://", "https://", "socks5://", "socks4://")):
        proxy_url = "http://" + proxy_url
    lbl = label or proxy_url.split("@")[-1]
    execute("""
        INSERT INTO proxies (proxy_url, label, is_active)
        VALUES (%s, %s, TRUE)
        ON CONFLICT (proxy_url) DO UPDATE SET is_active = TRUE, label = %s
    """, (proxy_url, lbl, lbl))
    _PROXIES_CACHE = execute("SELECT * FROM proxies WHERE is_active = TRUE ORDER BY id", fetch=True) or []

def add_bulk_proxies(lines_text):
    global _PROXIES_CACHE
    added = 0
    for line in lines_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            add_proxy(line)
            added += 1
        except Exception:
            pass
    _PROXIES_CACHE = execute("SELECT * FROM proxies WHERE is_active = TRUE ORDER BY id", fetch=True) or []
    return added

def toggle_proxy(proxy_id):
    global _PROXIES_CACHE
    row = execute("SELECT is_active FROM proxies WHERE id = %s", (proxy_id,), fetchone=True)
    if row:
        new_val = not row["is_active"]
        execute("UPDATE proxies SET is_active = %s WHERE id = %s", (new_val, proxy_id))
        _PROXIES_CACHE = execute("SELECT * FROM proxies WHERE is_active = TRUE ORDER BY id", fetch=True) or []
        return new_val
    return None

def delete_proxy(proxy_id):
    global _PROXIES_CACHE
    execute("DELETE FROM proxies WHERE id = %s", (proxy_id,))
    _PROXIES_CACHE = execute("SELECT * FROM proxies WHERE is_active = TRUE ORDER BY id", fetch=True) or []

def clear_proxies():
    global _PROXIES_CACHE
    execute("DELETE FROM proxies")
    _PROXIES_CACHE = []

def get_proxy_count():
    row = execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE is_active = TRUE) as active
        FROM proxies
    """, fetchone=True)
    return {
        "total": row["total"] if row else 0,
        "active": row["active"] if row else 0
    }

def get_random_proxy():
    global _PROXIES_CACHE
    if not _PROXIES_CACHE:
        _PROXIES_CACHE = execute("SELECT * FROM proxies WHERE is_active = TRUE ORDER BY id", fetch=True) or []
    if not _PROXIES_CACHE:
        return None
    import random
    return random.choice(_PROXIES_CACHE)["proxy_url"]
