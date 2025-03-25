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

# =====================[ Load biến môi trường ]=====================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

DEFAULT_SYSTEM_PROMPT = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are ChatGPT, a helpful assistant.")

# =====================[ Cấu hình file & log ]=====================
CONV_FILE = "conversations.json"
DEVICE_FILE = "devices.json"
RENTAL_FILE = "rentals.json"
logging.basicConfig(level=logging.INFO)

# =====================[ Load/Lưu Dữ liệu ]=====================
def load_json(path):
    return json.load(open(path, "r", encoding="utf-8")) if os.path.exists(path) else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

conversation_histories = load_json(CONV_FILE)
devices = load_json(DEVICE_FILE)
rentals = load_json(RENTAL_FILE)

# =====================[ Helper: lưu hội thoại ]=====================
def append_conversation(chat_id, role, content, user=None):
    if chat_id not in conversation_histories:
        conversation_histories[chat_id] = [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}]
    entry = {"role": role, "content": content}
    if user:
        entry["user"] = user
    conversation_histories[chat_id].append(entry)
    save_json(CONV_FILE, conversation_histories)

# =====================[ Helper: kiểm tra nếu nên phản hồi GPT ]=====================
def should_respond_to(text):
    trigger_words = ["gpt", "trợ lý", "chatgpt"]
    return any(re.search(rf"\b{re.escape(word)}\b", text.lower()) for word in trigger_words)

# =====================[ Xử lý tin nhắn ảnh + caption ]=====================
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
                {"role": "user", "content": [
                    {"type": "text", "text": caption or "Phân tích nội dung hình ảnh."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}}
                ]}
            ],
            max_tokens=500
        )
        reply = gpt_response.choices[0].message.content.strip()
        await message.reply_text(f"📸 {reply}")
    except Exception as e:
        logging.error(f"GPT Image Error: {e}")
        await message.reply_text("❌ Không thể phân tích ảnh này.")

# =====================[ Xử lý tin nhắn văn bản thông minh ]=====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = str(message.chat_id)
    text = message.text.strip()
    user = update.effective_user.username or update.effective_user.first_name
    lower = text.lower()

    # === Nhận diện thêm thiết bị với số lượng ===
    add_match = re.search(r"(thêm|ghi|cập nhật).*thiết bị.*mã (\w+).*?là (.+?) với số lượng (\d+)", lower)
    if add_match:
        device_id = add_match.group(2).upper()
        description = add_match.group(3).strip()
        quantity = int(add_match.group(4))
        devices[device_id] = {"desc": description, "qty": quantity, "rented": 0}
        save_json(DEVICE_FILE, devices)
        await message.reply_text(f"✅ Đã lưu thiết bị `{device_id}`: {description} (số lượng: {quantity})", parse_mode=ParseMode.MARKDOWN)
        return

    # === Xem mô tả thiết bị ===
    if "thiết bị" in lower and "là gì" in lower:
        found = [d for d in devices if d.lower() in lower]
        if found:
            reply = "\n".join([f"📦 `{d}`: {devices[d]['desc']} (SL: {devices[d].get('qty', '?')})" for d in found])
            await message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        else:
            matches = get_close_matches(lower, devices.keys(), n=3, cutoff=0.6)
            if matches:
                await message.reply_text("❓ Không tìm thấy thiết bị chính xác. Có phải bạn muốn hỏi về:\n" + "\n".join(matches))
            else:
                await message.reply_text("❌ Không tìm thấy thiết bị nào phù hợp trong dữ liệu.")
        return

    # === Xem toàn bộ thiết bị ===
    if re.search(r"(danh sách|xem|liệt kê).*thiết bị", lower):
        if devices:
            reply = "\n".join([f"📦 `{d}`: {info['desc']} (SL: {info.get('qty', '?')} - Đang thuê: {info.get('rented', 0)})" for d, info in devices.items()])
            await message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("⚠️ Hiện chưa có thiết bị nào được lưu.")
        return

    # === Thống kê thiết bị đang rảnh ===
    if re.search(r"(thiết bị )?(rảnh|còn trống|chưa thuê)", lower):
        available = [
            f"✅ `{d}`: {info['desc']} (Còn: {info.get('qty', 0) - info.get('rented', 0)})"
            for d, info in devices.items()
            if info.get("qty", 0) - info.get("rented", 0) > 0
        ]
        if available:
            await message.reply_text("📊 Thiết bị còn rảnh:\n" + "\n".join(available), parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("❗ Hiện tất cả thiết bị đã được thuê hết.")
        return

    # === Tìm theo người hoặc loại ===
    find_match = re.search(r"(ai|người nào).*thuê.*(\w+)|thiết bị.*(\w+).*được.*(ai|người) thuê", lower)
    if find_match:
        keyword = find_match.group(2) or find_match.group(3)
        if keyword:
            matched = [f"📦 `{d}`: {info['desc']} (SL: {info.get('qty', '?')})" for d, info in devices.items() if keyword.lower() in info['desc'].lower()]
            if matched:
                await message.reply_text("🔎 Kết quả tìm thấy:\n" + "\n".join(matched), parse_mode=ParseMode.MARKDOWN)
            else:
                suggestions = get_close_matches(keyword.lower(), [info['desc'].lower() for info in devices.values()], n=3, cutoff=0.6)
                if suggestions:
                    await message.reply_text("❓ Không tìm thấy chính xác. Có phải bạn muốn tìm:\n" + "\n".join(suggestions))
                else:
                    await message.reply_text("❌ Không tìm thấy thiết bị nào phù hợp.")
        return

    # === Ghi nhớ hội thoại GPT (chỉ nếu chứa từ khóa) ===
    if not should_respond_to(lower):
        return

    append_conversation(chat_id, "user", text, user)
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{k: v for k, v in m.items() if k in ["role", "content"]} for m in conversation_histories[chat_id]],
            temperature=0.7,
        )
        reply = response.choices[0].message.content.strip()
        append_conversation(chat_id, "assistant", reply)
    except Exception as e:
        logging.error(f"GPT Text Error: {e}")
        reply = "❌ Đã xảy ra lỗi khi gọi GPT."

    await message.reply_text(f"@{user} {reply}", parse_mode=ParseMode.MARKDOWN)

# =====================[ MAIN BOT ]=====================
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot đã sẵn sàng.")))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    await app.run_polling()

if __name__ == '__main__':
    import asyncio
    nest_asyncio.apply()
    asyncio.run(main())
