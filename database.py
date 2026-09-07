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

def init_db(database_url):
    global DB_URL, db_pool
    DB_URL = database_url
    db_pool = pool.ThreadedConnectionPool(2, 20, database_url)
    _create_tables()
    _warm_cache()

def get_conn():
    return db_pool.getconn()

def put_conn(conn):
    db_pool.putconn(conn)

def execute(query, params=None, fetch=False, fetchone=False):
    conn = get_conn()
    try:
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
        conn.rollback()
        raise e
    finally:
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
    # Add indexes for speed
    execute("CREATE INDEX IF NOT EXISTS idx_signups_by_user ON signups(by_user)")
    execute("CREATE INDEX IF NOT EXISTS idx_signups_status ON signups(status)")
    execute("CREATE INDEX IF NOT EXISTS idx_signups_created ON signups(created_at)")

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
    global _SETTINGS_CACHE, _BANNED_CACHE, _VERIFIED_CACHE, _REFERRAL_CACHE, _CHANNELS_CACHE
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
# FIREBASE URLS
# ==========================================

def add_firebase_url(url, label=""):
    execute("INSERT INTO firebase_urls (url, label) VALUES (%s, %s)", (url, label))

def get_firebase_urls(active_only=True):
    if active_only:
        return execute("SELECT * FROM firebase_urls WHERE is_active = TRUE ORDER BY id", fetch=True)
    return execute("SELECT * FROM firebase_urls ORDER BY id", fetch=True)

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
