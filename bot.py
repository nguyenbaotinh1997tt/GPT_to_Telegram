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

# =====================[ Các hàm quản lý thuê/trả thiết bị ]=====================
def rent_device(user, device_id, quantity):
    """
    Cho user thuê thiết bị device_id với số lượng quantity.
    Cập nhật rentals và devices. Trả về thông báo kết quả.
    """
    device_id = device_id.upper()
    if device_id not in devices:
        return f"❌ Không tìm thấy thiết bị `{device_id}`."

    available = devices[device_id]["qty"] - devices[device_id]["rented"]
    if quantity > available:
        return f"❌ Thiết bị `{device_id}` chỉ còn {available} chưa thuê. Bạn không thể thuê {quantity}."

    # Tăng 'rented' cho thiết bị
    devices[device_id]["rented"] += quantity

    # Ghi vào rentals
    if user not in rentals:
        rentals[user] = {}
    rentals[user][device_id] = rentals[user].get(device_id, 0) + quantity

    # Lưu file
    save_json(DEVICE_FILE, devices)
    save_json(RENTAL_FILE, rentals)

    return f"✅ {user} đã thuê {quantity} thiết bị `{device_id}` thành công."

def return_device(user, device_id, quantity):
    """
    Cho user trả lại thiết bị device_id với số lượng quantity.
    Cập nhật rentals và devices. Trả về thông báo kết quả.
    """
    device_id = device_id.upper()
    if user not in rentals or device_id not in rentals[user]:
        return f"❌ {user} không hề thuê thiết bị `{device_id}`."

    rented_qty = rentals[user][device_id]
    if quantity > rented_qty:
        return f"❌ {user} chỉ đang thuê {rented_qty} thiết bị `{device_id}`."

    # Giảm 'rented' trên thiết bị
    devices[device_id]["rented"] -= quantity
    if devices[device_id]["rented"] < 0:
        devices[device_id]["rented"] = 0

    # Cập nhật rentals
    new_qty = rented_qty - quantity
    if new_qty == 0:
        del rentals[user][device_id]
        if not rentals[user]:
            del rentals[user]
    else:
        rentals[user][device_id] = new_qty

    # Lưu file
    save_json(DEVICE_FILE, devices)
    save_json(RENTAL_FILE, rentals)

    return f"✅ {user} đã trả {quantity} thiết bị `{device_id}` thành công."

def generate_rentals_context():
    """
    Tạo nội dung tóm tắt việc thuê để GPT có ngữ cảnh trả lời.
    """
    lines = ["[DỮ LIỆU THUÊ THIẾT BỊ HIỆN TẠI]"]
    if not rentals:
        lines.append("- Chưa có ai thuê thiết bị nào.")
    else:
        for user_name, devs in rentals.items():
            if devs:
                items = [f"{d} (x{q})" for d, q in devs.items()]
                lines.append(f"- {user_name} đang thuê: {', '.join(items)}")
    return "\n".join(lines)

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

# =====================[ Xử lý tin nhắn văn bản thông minh ]=====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = str(message.chat_id)
    text = message.text.strip()
    user_name = update.effective_user.username or update.effective_user.first_name
    lower = text.lower()

    # === Nhận diện thêm thiết bị với số lượng ===
    add_match = re.search(r"(thêm|ghi|cập nhật).*thiết bị.*mã (\w+).*?là (.+?) với số lượng (\d+)", lower)
    if add_match:
        device_id = add_match.group(2).upper()
        description = add_match.group(3).strip()
        quantity = int(add_match.group(4))
        devices[device_id] = {"desc": description, "qty": quantity, "rented": 0}
        save_json(DEVICE_FILE, devices)
        await message.reply_text(
            f"✅ Đã lưu thiết bị `{device_id}`: {description} (số lượng: {quantity})",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # === Xem mô tả thiết bị ===
    if "thiết bị" in lower and "là gì" in lower:
        found = [d for d in devices if d.lower() in lower]
        if found:
            reply = "\n".join([
                f"📦 `{d}`: {devices[d]['desc']} (SL: {devices[d].get('qty', '?')})"
                for d in found
            ])
            await message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        else:
            matches = get_close_matches(lower, devices.keys(), n=3, cutoff=0.6)
            if matches:
                await message.reply_text(
                    "❓ Không tìm thấy thiết bị chính xác. Có phải bạn muốn hỏi về:\n" + "\n".join(matches)
                )
            else:
                await message.reply_text("❌ Không tìm thấy thiết bị nào phù hợp trong dữ liệu.")
        return

    # === Xem toàn bộ thiết bị ===
    if re.search(r"(danh sách|xem|liệt kê).*thiết bị", lower):
        if devices:
            reply = "\n".join([
                f"📦 `{d}`: {info['desc']} (SL: {info.get('qty', '?')} - Đang thuê: {info.get('rented', 0)})"
                for d, info in devices.items()
            ])
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
            await message.reply_text(
                "📊 Thiết bị còn rảnh:\n" + "\n".join(available),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await message.reply_text("❗ Hiện tất cả thiết bị đã được thuê hết.")
        return

    # === Tìm theo người hoặc loại (cũ) ===
