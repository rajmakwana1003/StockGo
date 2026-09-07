import requests
import json
import urllib.parse
import re
import time
import random
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
BASE_URL = "https://accounts.stockgro.club/api"
FIREBASE_URL = "https://my-stockgo-db-default-rtdb.asia-southeast1.firebasedatabase.app"
DEFAULT_REFERRAL_CODE = "H3HBDBEG"
REFER_FILE = "refer.txt"

# Name lists for generating realistic names
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
# HELPER FUNCTIONS
# ==========================================

def generate_random_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"

def get_server_state():
    """Fetch app.stockgro.club and extract server-generated state token"""
    try:
        res = requests.get("https://app.stockgro.club", headers={
            "user-agent": "Mozilla/5.0 (Linux; Android 14; SM-A135F) AppleWebKit/537.36 Chrome/151.0.7922.169 Mobile Safari/537.36",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }, timeout=10)
        match = re.search(r'state=([^&"]+)', res.text)
        if match:
            return urllib.parse.unquote(match.group(1))
    except Exception as e:
        print(f"  [!] Failed to get server state: {e}")
    return None

def build_headers(state):
    sg_info = urllib.parse.quote(json.dumps({
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
        "user-agent": "Mozilla/5.0 (Linux; Android 14; SM-A135F) AppleWebKit/537.36 Chrome/151.0.7922.169 Mobile Safari/537.36",
        "cookie": f"sgInfo={sg_info}"
    }

def stockgro_api(url, payload, headers):
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=12)
        return res.json()
    except Exception as e:
        return {"success": False, "error": str(e)}

def safe_get(data, *keys, default=None):
    """Safely navigate nested dictionaries."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
        if current is None:
            return default
    return current

def save_to_firebase(phone, referral_code, display_name, user_id, status):
    """Save signup record to Firebase."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "phone": phone,
            "referral_code": referral_code,
            "name": display_name,
            "user_id": user_id,
            "status": status,
            "timestamp": timestamp
        }
        url = f"{FIREBASE_URL}/signups.json"
        res = requests.post(url, json=record, timeout=10)
        if res.status_code == 200:
            print(f"  [+] Saved to Firebase successfully")
        else:
            print(f"  [!] Firebase save returned status {res.status_code}")
    except Exception as e:
        print(f"  [!] Firebase save error: {e}")

def save_to_file(phone, referral_code, display_name, user_id):
    """Save signup record to local refer.txt file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"{phone} | RefCode: {referral_code} | Name: {display_name} | UserID: {user_id} | Time: {timestamp}\n"
    with open(REFER_FILE, "a") as f:
        f.write(entry)

def load_stats():
    """Load total successful signups count from Firebase."""
    try:
        url = f"{FIREBASE_URL}/signups.json?shallow=true"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, dict):
                return len(data)
    except Exception:
        pass
    return 0

# ==========================================
# MAIN SIGNUP FLOW
# ==========================================

def do_signup(phone, referral_code):
    """Execute full StockGro signup for one phone number."""
    display_name = generate_random_name()

    print(f"\n{'='*55}")
    print(f"  Phone: {phone} | Referral: {referral_code} | Name: {display_name}")
    print(f"{'='*55}")

    # --- Step 1: Get server state ---
    print(f"\n  [Step 1/6] Getting server state token...")
    state = get_server_state()
    if not state:
        print(f"  [-] FAILED: Could not get server state. Check your internet.")
        return False
    headers = build_headers(state)
    print(f"  [+] Server state obtained")

    # --- Step 2: Check identity (new or existing user) ---
    print(f"  [Step 2/6] Checking if {phone} is registered...")
    d0 = stockgro_api(f"{BASE_URL}/getIdentity",
                      {"phone_number": phone, "country_code": "IN", "otp_channel": "sms"}, headers)

    if not d0 or not d0.get("success"):
        error_msg = safe_get(d0, "message", default=safe_get(d0, "error", default="Unknown error"))
        print(f"  [-] FAILED: getIdentity error: {error_msg}")
        save_to_firebase(phone, referral_code, display_name, "", "failed_identity")
        return False

    existing = safe_get(d0, "data", "existing_user", default=False)
    if existing:
        print(f"  [!] Number {phone} is ALREADY REGISTERED on StockGro. Skipping.")
        save_to_firebase(phone, referral_code, display_name, "", "already_registered")
        return False

    print(f"  [+] Number is NEW (unregistered) - proceeding")

    # --- Step 3: Send OTP ---
    print(f"  [Step 3/6] Sending OTP to {phone}...")
    d1 = stockgro_api(f"{BASE_URL}/login/createOtp",
                      {"phone_number": phone, "country_code": "IN", "otp_channel": "sms",
                       "invitation_code": referral_code, "flow_type": "signup"},
                      headers)

    if not d1 or not d1.get("success"):
        error_msg = safe_get(d1, "message", default=safe_get(d1, "error", default="Unknown error"))
        print(f"  [-] FAILED: Could not send OTP: {error_msg}")
        save_to_firebase(phone, referral_code, display_name, "", "failed_otp_send")
        return False

    session_id = safe_get(d1, "data", "session_id")
    if not session_id:
        print(f"  [-] FAILED: No session_id in response")
        save_to_firebase(phone, referral_code, display_name, "", "no_session")
        return False

    print(f"  [+] OTP sent successfully! Session: {session_id[:20]}...")

    # --- Step 4: Get OTP from user ---
    print(f"\n  >> OTP has been sent to {phone}")
    otp = input("  >> Enter the 6-digit OTP: ").strip()

    if not otp or len(otp) != 6 or not otp.isdigit():
        print(f"  [-] Invalid OTP format. Must be 6 digits.")
        save_to_firebase(phone, referral_code, display_name, "", "invalid_otp_input")
        return False

    # --- Step 5: Validate OTP ---
    print(f"  [Step 4/6] Validating OTP {otp}...")
    d2 = stockgro_api(f"{BASE_URL}/login/validateOtp",
                      {"session_id": session_id, "otp": otp, "phone_number": phone,
                       "country_code": "IN", "otp_channel": "sms", "flow_type": "signup"},
                      headers)

    if not d2 or not d2.get("success"):
        error_msg = safe_get(d2, "message", default=safe_get(d2, "error", default="Unknown error"))
        print(f"  [-] FAILED: OTP validation failed: {error_msg}")
        save_to_firebase(phone, referral_code, display_name, "", "otp_invalid")
        return False

    print(f"  [+] OTP verified!")

    # --- Step 6: Register user ---
    print(f"  [Step 5/6] Registering '{display_name}' with referral '{referral_code}'...")
    d3 = stockgro_api(f"{BASE_URL}/signup/registerUser",
                      {"display_name": display_name, "invitation_code": referral_code, "otp": otp,
                       "session_id": session_id, "whatsapp_consent": True,
                       "phone_number": phone, "country_code": "IN", "otp_channel": "sms"},
                      headers)

    if not d3 or not d3.get("success"):
        error_msg = safe_get(d3, "message", default=safe_get(d3, "error", default="Unknown error"))
        print(f"  [-] FAILED: Registration failed: {error_msg}")
        save_to_firebase(phone, referral_code, display_name, "", "registration_failed")
        return False

    user_id = safe_get(d3, "data", "user_id", default="unknown")
    redirect_uri = safe_get(d3, "data", "redirect_uri", default="")

    print(f"  [+] Registered! User ID: {user_id}")

    # --- Step 7: Finalize login ---
    print(f"  [Step 6/6] Finalizing login...")
    if redirect_uri and "access_code=" in redirect_uri:
        access_code = redirect_uri.split("access_code=")[-1]
        stockgro_api("https://app.stockgro.club/api/login", {"code": access_code}, headers)
        print(f"  [+] Login finalized")
    else:
        print(f"  [+] Skipped login finalization (no access code)")

    # --- Save records ---
    save_to_firebase(phone, referral_code, display_name, user_id, "success")
    save_to_file(phone, referral_code, display_name, user_id)

    print(f"\n  {'='*55}")
    print(f"  SUCCESS! Signup complete for {phone}")
    print(f"  Name: {display_name} | UserID: {user_id}")
    print(f"  Referral '{referral_code}' applied!")
    print(f"  {'='*55}\n")
    return True

# ==========================================
# MAIN MENU
# ==========================================

def main():
    print("="*60)
    print("   STOCKGRO REFERRAL SIGNUP TOOL")
    print("="*60)
    print(f"  Firebase: {FIREBASE_URL}")

    # Load stats
    total = load_stats()
    if total > 0:
        print(f"  Total signups recorded: {total}")

    # Get referral code
    print(f"\n  Default referral code: {DEFAULT_REFERRAL_CODE}")
    referral_input = input(f"  Enter Referral Code (or press Enter for default): ").strip()
    referral_code = referral_input if referral_input else DEFAULT_REFERRAL_CODE
    print(f"  Using referral code: {referral_code}\n")

    success_count = 0
    fail_count = 0

    while True:
        print("-"*60)
        phone = input("  Enter Mobile Number (10 digits, or 'q' to quit): ").strip()

        if phone.lower() in ('q', 'quit', 'exit'):
            break

        # Clean the number
        phone = phone.replace("+91", "").replace(" ", "").replace("-", "")

        if len(phone) != 10 or not phone.isdigit() or phone[0] not in "6789":
            print("  [!] Invalid number. Must be 10 digits starting with 6/7/8/9")
            continue

        result = do_signup(phone, referral_code)
        if result:
            success_count += 1
        else:
            fail_count += 1

        print(f"  [Stats] Success: {success_count} | Failed: {fail_count}")
        print()

    # Final summary
    print(f"\n{'='*60}")
    print(f"  SESSION SUMMARY")
    print(f"  Successful signups: {success_count}")
    print(f"  Failed attempts:    {fail_count}")
    print(f"  Results saved to:   {REFER_FILE}")
    print(f"  Firebase:           {FIREBASE_URL}/signups")
    print(f"{'='*60}")
    print("  Goodbye!")

if __name__ == "__main__":
    main()
