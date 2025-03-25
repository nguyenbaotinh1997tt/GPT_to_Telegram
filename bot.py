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

    devices[device_id]["rented"] += quantity

    if user not in rentals:
        rentals[user] = {}
    rentals[user][device_id] = rentals[user].get(device_id, 0) + quantity

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

    devices[device_id]["rented"] -= quantity
    if devices[device_id]["rented"] < 0:
        devices[device_id]["rented"] = 0

    new_qty = rented_qty - quantity
    if new_qty == 0:
        del rentals[user][device_id]
        if not rentals[user]:
            del rentals[user]
    else:
        rentals[user][device_id] = new_qty

    save_json(DEVICE_FILE, devices)
    save_json(RENTAL_FILE, rentals)

    return f"✅ {user} đã trả {quantity} thiết bị `{device_id}` thành công."

def generate_rentals_context():
    """
    Tạo nội dung tóm tắt dữ liệu thuê để đưa vào prompt cho GPT.
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

# =====================[ Hàm xử lý danh sách thiết bị dạng tự nhiên ]=====================
def parse_device_list(text):
    """
    Parse danh sách thiết bị dạng nhiều dòng.
    Mỗi dòng chứa mô tả thiết bị và số lượng (cách nhau bởi tab, dấu phẩy, dấu gạch ngang hoặc dấu hai chấm).
    Nếu thiết bị đã tồn tại (so sánh theo mô tả, không phân biệt hoa thường) thì cập nhật số lượng,
    nếu chưa tồn tại thì tạo mã thiết bị tự động (dựa vào các ký tự đầu của từ).
    Nếu số lượng = 0 thì xoá thiết bị.
    """
    responses = []
    lines = text.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Ví dụ: "Thẻ touch 80GB	3" hoặc "Thẻ XQD: 1"
        match = re.match(r"^(.*?)\s*[\t,\-:]+\s*(\d+)$", line)
        if match:
            desc = match.group(1).strip()
            qty = int(match.group(2))
            # Tìm xem có thiết bị nào với mô tả tương tự không
            existing_code = None
            for code, info in devices.items():
                if info["desc"].lower() == desc.lower():
                    existing_code = code
                    break
            if existing_code:
                if qty == 0:
                    del devices[existing_code]
                    responses.append(f"Xoá thiết bị `{existing_code}`: {desc} vì số lượng = 0.")
                else:
                    devices[existing_code]["qty"] = qty
                    responses.append(f"Cập nhật thiết bị `{existing_code}`: {desc} với số lượng {qty}.")
            else:
                if qty == 0:
                    responses.append(f"Bỏ qua dòng vì số lượng = 0: {desc}")
                else:
                    # Tạo mã thiết bị tự động từ các ký tự đầu của từ
                    words = desc.split()
                    code = "".join([w[0] for w in words]).upper()
                    orig_code = code
                    i = 1
                    while code in devices:
                        code = f"{orig_code}{i}"
                        i += 1
                    devices[code] = {"desc": desc, "qty": qty, "rented": 0}
                    responses.append(f"Thêm thiết bị mới `{code}`: {desc} với số lượng {qty}.")
        else:
            responses.append(f"Không hiểu dòng: {line}")
    save_json(DEVICE_FILE, devices)
    return "\n".join(responses)

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
    user = update.effective_user.username or update.effective_user.first_name
    lower = text.lower()

    # --- [ Block A: Nhận diện danh sách thiết bị dạng nhiều dòng] ---
    # Nếu tin nhắn có nhiều dòng và tất cả dòng đều chứa số (giả định là danh sách thiết bị)
    lines = text.splitlines()
    if len(lines) > 1 and all(re.search(r"\d+", line) for line in lines if line.strip() != ""):
        response = parse_device_list(text)
        await message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
        return

    # --- [ Block B: Xoá thiết bị theo lệnh tự nhiên] ---
    if "xoá" in lower and "thiết bị" in lower:
        # Ví dụ: "Xoá thiết bị mã XYZ" hoặc "Hãy xoá thiết bị XYZ"
        match = re.search(r"xoá.*thiết bị.*mã\s*(\w+)", lower)
        if match:
            device_id = match.group(1).upper()
            if device_id in devices:
                if devices[device_id]["rented"] > 0:
                    await message.reply_text(f"❌ Thiết bị `{device_id}` đang có {devices[device_id]['rented']} đang thuê, không thể xoá.")
                else:
                    del devices[device_id]
                    save_json(DEVICE_FILE, devices)
                    await message.reply_text(f"✅ Đã xoá thiết bị `{device_id}`.")
            else:
                await message.reply_text(f"❌ Không tìm thấy thiết bị `{device_id}`.")
            return

    # --- Các lệnh xử lý khác ---
    # 1. Thêm/Cập nhật thiết bị (đơn lẻ)
    add_match = re.search(r"(thêm|ghi|cập nhật).*thiết bị.*mã\s*(\w+).*?là\s*(.+?)\s*(với\s+số lượng|số lượng)\s+(\d+)", lower)
    if add_match:
        device_id = add_match.group(2).upper()
        description = add_match.group(3).strip()
        quantity = int(add_match.group(5))
        devices[device_id] = {"desc": description, "qty": quantity, "rented": devices.get(device_id, {}).get("rented", 0)}
        save_json(DEVICE_FILE, devices)
        await message.reply_text(
            f"✅ Đã lưu thiết bị `{device_id}`: {description} (số lượng: {quantity})",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # 2. Xem mô tả thiết bị
    if "thiết bị" in lower and "là gì" in lower:
        found = [d for d in devices if d.lower() in lower or devices[d]["desc"].lower() in lower]
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

    # 3. Xem toàn bộ thiết bị
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

    # 4. Thống kê thiết bị còn rảnh
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

    # 5. Xử lý thuê thiết bị
    rent_match = re.search(r"(muốn thuê|mượn)\s+(?:thiết bị|mã)?\s*(\w+)\s+(?:với\s+số lượng|số lượng|là)\s+(\d+)", lower)
    if rent_match:
        device_id = rent_match.group(2)
        quantity = int(rent_match.group(3))
        msg = rent_device(user, device_id, quantity)
        await message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    # 6. Xử lý trả thiết bị
    return_match = re.search(r"(muốn trả|trả)\s+(?:thiết bị|mã)?\s*(\w+)\s+(?:với\s+số lượng|số lượng|là)\s+(\d+)", lower)
    if return_match:
        device_id = return_match.group(2)
        quantity = int(return_match.group(3))
        msg = return_device(user, device_id, quantity)
        await message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    # 7. Hỏi "Tôi đang thuê gì?"
    if re.search(r"(tôi|mình)\s+(đang thuê|thuê gì|thuê thiết bị nào)", lower):
        if user not in rentals or not rentals[user]:
            await message.reply_text("❌ Bạn hiện không thuê thiết bị nào.")
        else:
            lines = [f"🔹 `{dev}` x {qty}" for dev, qty in rentals[user].items()]
            reply = "Bạn đang thuê:\n" + "\n".join(lines)
            await message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        return

    # 8. Hỏi "Ai đang thuê thiết bị X?"
    who_rent_match = re.search(r"ai\s+đang\s+thuê\s+(?:thiết bị|mã)?\s*(\w+)", lower)
    if who_rent_match:
        device_id = who_rent_match.group(1).upper()
        renters = []
        for usr, devs in rentals.items():
            if device_id in devs:
                renters.append(f"{usr} (x{devs[device_id]})")
        if renters:
            reply = f"👥 Thiết bị `{device_id}` đang được thuê bởi:\n" + "\n".join(renters)
        else:
            reply = f"❌ Không tìm thấy ai đang thuê thiết bị `{device_id}`."
        await message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        return

    # 9. Nếu chứa từ khóa cho GPT, thêm ngữ cảnh thuê thiết bị vào prompt
    if should_respond_to(lower):
        append_conversation(chat_id, "user", text, user)
        rentals_context = generate_rentals_context()
        if len(conversation_histories[chat_id]) < 2 or "DỮ LIỆU THUÊ THIẾT BỊ" not in conversation_histories[chat_id][1]["content"]:
            conversation_histories[chat_id].insert(1, {
                "role": "system",
                "content": rentals_context
            })
            save_json(CONV_FILE, conversation_histories)
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
        return

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
