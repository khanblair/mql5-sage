import os
import requests
import sys

def send_telegram_message(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("⚠️ Telegram credentials not found. Skipping notification.")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("✅ Telegram notification sent.")
        return True
    except Exception as e:
        print(f"❌ Failed to send Telegram message: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        msg = sys.argv[1]
        send_telegram_message(msg)
    else:
        print("Usage: python notify.py \"your message\"")
