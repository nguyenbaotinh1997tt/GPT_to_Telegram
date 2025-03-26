import os
import json
import logging
import openai
import nest_asyncio
import re
import requests
import base64
from difflib import get_close_matches
from telegram import Update, Message
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from dotenv import load_dotenv
from datetime import datetime

# -------------------- IMPORT Google Sheets API --------------------
from google.oauth2 import service_account
from googleapiclient.discovery import build

# =====================[ Load biến môi trường ]=====================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

DEFAULT_SYSTEM_PROMPT = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are ChatGPT, a helpful assistant.")

# =====================[ Cấu hình file & log ]=====================
CONV_FILE = "conversations.json"
logging.basicConfig(level=logging.INFO)

# =====================[ Load/Lưu Dữ liệu hội thoại ]=====================
def load_json(path):
    return json.load(open(path, "r", encoding="utf-8")) if os.path.exists(path) else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

conversation_histories = load_json(CONV_FILE)

# -------------------- [Google Sheets Helper] --------------------
def get_sheet_values(credentials_file="credentials.json"):
    """
    Hàm này truy xuất dữ liệu từ Google Sheet bằng Google Sheets API.
    Bạn có thể thay đổi spreadsheet_id, range_name tùy nhu cầu.
    """
    spreadsheet_id = "1etFuXi-aowcqAEwPHbvluWHnWM7PQ8YD54KPmkwQ6jI"  # <-- ID sheet bạn cung cấp
    range_name = "'Tháng 03'!A3:S900"  # <-- Phạm vi bạn cung cấp
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

    # Tạo credentials từ file JSON
    creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=SCOPES)

    # Tạo service Google Sheets
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    # Gọi API để lấy dữ liệu
    result = sheet.values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
    values = result.get('values', [])
    return values

# =====================[ Helper: Lưu hội thoại GPT ]=====================
def append_conversation(chat_id, role, content, user=None):
    if chat_id not in conversation_histories:
        conversation_histories[chat_id] = [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}]
    entry = {"role": role, "content": content}
    if user:
        entry["user"] = user
    conversation_histories[chat_id].append(entry)
    save_json(CONV_FILE, conversation_histories)

# =====================[ Helper: Kiểm tra từ khóa kích hoạt GPT ]=====================
def should_respond_to(text):
    trigger_words = ["gpt", "trợ lý", "chatgpt"]
    return any(re.search(rf"\b{re.escape(word)}\b", text.lower()) for word in trigger_words)

# =====================[ Lệnh /getdata – Lấy dữ liệu từ Google Sheet ]=====================
async def getdata_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Khi người dùng gõ /getdata, bot sẽ lấy dữ liệu từ Google Sheet và gửi lại.
    """
    try:
        data = get_sheet_values("credentials.json")
        if not data:
            response = "Không tìm thấy dữ liệu trong Google Sheet."
        else:
            # Tùy ý bạn xử lý/định dạng dữ liệu. Ở đây chỉ ghép mỗi hàng thành 1 dòng.
            lines = []
            for row in data:
                lines.append(" | ".join(row))
            response = "\n".join(lines)
    except Exception as e:
        logging.error(f"Lỗi khi lấy dữ liệu từ Google Sheet: {e}")
        response = "❌ Đã xảy ra lỗi khi lấy dữ liệu từ Google Sheet."

    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)

# =====================[ Xử lý tin nhắn ảnh (GPT-4o) ]=====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    caption = message.caption or ""
    if not should_respond_to(caption):
        return
    try:
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        response = requests.get(file.file_path)
        encoded_image = base64.b64encode(response.content).decode("utf-8")

        gpt_response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": caption or "Phân tích nội dung hình ảnh."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}}
                    ]
                }
            ],
            max_tokens=500
        )
        reply = gpt_response.choices[0].message.content.strip()
        await message.reply_text(f"📸 {reply}")
    except Exception as e:
        logging.error(f"GPT Image Error: {e}")
        await message.reply_text("❌ Không thể phân tích ảnh này.")

# =====================[ Xử lý tin nhắn văn bản (GPT-4o) ]=====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = str(message.chat_id)
    text = message.text.strip()
    user = update.effective_user.username or update.effective_user.first_name
    lower = text.lower()

    # Chỉ xử lý nếu có từ khóa kích hoạt
    if not should_respond_to(lower):
        return

    append_conversation(chat_id, "user", text, user)

    try:
        gpt_response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[{k: v for k, v in m.items() if k in ["role", "content"]}
                      for m in conversation_histories[chat_id]],
            temperature=0.7,
        )
        reply = gpt_response.choices[0].message.content.strip()
        append_conversation(chat_id, "assistant", reply)
    except Exception as e:
        logging.error(f"GPT Text Error: {e}")
        reply = "❌ Đã xảy ra lỗi khi gọi GPT."

    await message.reply_text(f"@{user} {reply}", parse_mode=ParseMode.MARKDOWN)

# =====================[ MAIN BOT ]=====================
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot đã sẵn sàng.")))
    # Lệnh /getdata để lấy dữ liệu từ Google Sheet
    app.add_handler(CommandHandler("getdata", getdata_command))
    # Xử lý tin nhắn ảnh
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Xử lý tin nhắn văn bản
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    await app.run_polling()

if __name__ == '__main__':
    import asyncio
    nest_asyncio.apply()
    asyncio.run(main())
