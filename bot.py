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
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv
from datetime import datetime

# =====================[ Load biến môi trường ]=====================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY
DEFAULT_SYSTEM_PROMPT = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are ChatGPT, a helpful assistant.")

# =====================[ Cấu hình file & log ]=====================
CONV_FILE = "conversations.json"
DEVICE_FILE = "devices.json"
logging.basicConfig(level=logging.INFO)

# =====================[ Load/Lưu Dữ liệu ]=====================
def load_json(path):
    return json.load(open(path, "r", encoding="utf-8")) if os.path.exists(path) else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# Lưu lịch sử hội thoại và dữ liệu thiết bị
conversation_histories = load_json(CONV_FILE)
devices = load_json(DEVICE_FILE)

# =====================[ Helper: Lưu hội thoại ]=====================
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

# =====================[ Lệnh cập nhật dữ liệu thiết bị /capnhat ]=====================
async def update_devices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lệnh: /capnhat
    Nội dung tin nhắn sau lệnh là danh sách thiết bị, mỗi dòng có định dạng: Tên thiết bị, tổng số lượng
    Ví dụ:
      sony a73, 4
      Đèn nanlite, 1
    Nếu không cung cấp số lượng thì số lượng sẽ được lưu là "Chưa cập nhật" (None).
    """
    message = update.message
    text = message.text.strip()
    command_len = len("/capnhat")
    content = text[command_len:].strip()
    if not content:
        await message.reply_text("⚠️ Vui lòng gửi danh sách thiết bị sau lệnh /capnhat.")
        return

    responses = []
    lines = content.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Mỗi dòng theo định dạng: Tên thiết bị, tổng số lượng
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 1:
            name = parts[0]
            qty = None
        elif len(parts) == 2:
            name = parts[0]
            qty = parts[1] if parts[1] != "" else None
            if qty is not None:
                try:
                    qty = int(qty)
                except ValueError:
                    responses.append(f"❌ Số lượng không hợp lệ ở dòng: {line}")
                    continue
        else:
            responses.append(f"❌ Dòng không hợp lệ: {line}")
            continue

        # Sử dụng tên thiết bị (lowercase) làm key
        devices[name.lower()] = {"name": name, "qty": qty, "rented": devices.get(name.lower(), {}).get("rented", 0)}
        responses.append(f"✅ Đã cập nhật thiết bị **{name}** với số lượng {qty if qty is not None else 'Chưa cập nhật'}.")

    save_json(DEVICE_FILE, devices)
    responses.append("Đã cập nhật thiết bị.")
    await message.reply_text("\n".join(responses), parse_mode=ParseMode.MARKDOWN)

# =====================[ Xử lý tin nhắn ảnh (GPT-4o) ]=====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    caption = message.caption or ""
    # Chỉ xử lý ảnh nếu caption chứa từ khóa kích hoạt
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
    
    # Chỉ xử lý tin nhắn có chứa từ khóa kích hoạt
    if not should_respond_to(lower):
        return
    
    # Lưu lại hội thoại
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
    # Thêm handler cho lệnh cập nhật thiết bị /capnhat
    app.add_handler(CommandHandler("capnhat", update_devices_command, filters=filters.COMMAND))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    await app.run_polling()

if __name__ == '__main__':
    import asyncio
    nest_asyncio.apply()
    asyncio.run(main())
