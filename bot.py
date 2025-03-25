import os
import json
import logging
import openai
import nest_asyncio
import re
import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from dotenv import load_dotenv

# =====================[ Load biến môi trường ]=====================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

ALLOWED_USER_IDS = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = [int(uid) for uid in ALLOWED_USER_IDS.split(",") if uid.strip().isdigit()]
DEFAULT_SYSTEM_PROMPT = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are ChatGPT, a helpful assistant.")

# =====================[ Cấu hình file & log ]=====================
CONV_FILE = "conversations.json"
RENTAL_FILE = "rental_log.json"
logging.basicConfig(level=logging.INFO)

# =====================[ Load/Lưu Hội Thoại ]=====================
def load_conversations():
    if not os.path.exists(CONV_FILE):
        return {}
    with open(CONV_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_conversations(data):
    with open(CONV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

conversation_histories = load_conversations()

# =====================[ Load/Lưu Thiết Bị Cho Thuê ]=====================
def load_rentals():
    if not os.path.exists(RENTAL_FILE):
        return {}
    with open(RENTAL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_rentals(data):
    with open(RENTAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

rental_data = load_rentals()

# =====================[ Command Functions ]=====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Chào bạn! Tôi là trợ lý GPT-4 trong nhóm.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Khởi động bot\n"
        "/help - Hiển thị trợ giúp\n"
        "/reset - Xoá lịch sử hội thoại\n"
        "/mentionall - Gọi tất cả thành viên tương tác\n"
        "/id - Lấy ID người dùng và nhóm\n"
        "/users - Danh sách người dùng đã tương tác\n"
        "/forgetme - Xoá dữ liệu của bạn khỏi bot\n"
        "/rent [mã] [ghi chú] - Ghi thiết bị cho thuê\n"
        "/check [mã] - Kiểm tra thiết bị\n"
        "/list - Xem toàn bộ thiết bị đang cho thuê"
    )

# =====================[ Xử lý ảnh gửi lên khi có nhắc GPT ]=====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or ""
    trigger_words = ["gpt", "bot", "trợ lý", "chatgpt"]
    lower_caption = caption.lower()
    if not any(word in lower_caption for word in trigger_words):
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_path = file.file_path

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Mô tả nội dung hình ảnh này."},
                        {"type": "image_url", "image_url": {"url": file_path}},
                    ]
                }
            ],
            max_tokens=500
        )
        description = response.choices[0].message.content.strip()
        await update.message.reply_text(f"📸 {description}")
    except Exception as e:
        logging.error(f"Lỗi GPT ảnh: {e}")
        await update.message.reply_text("❌ Không thể phân tích ảnh này.")

# =====================[ MAIN BOT ]=====================
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    await app.run_polling()

if __name__ == '__main__':
    import asyncio
    nest_asyncio.apply()

    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()
