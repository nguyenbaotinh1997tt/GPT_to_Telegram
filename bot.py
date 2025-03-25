import os
import json
import logging
import openai
import nest_asyncio
import re
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
        "/check [mã] - Kiểm tra thiết bị"
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conversation_histories.pop(chat_id, None)
    save_conversations(conversation_histories)
    await update.message.reply_text("✅ Đã xoá hội thoại nhóm này.")

async def mention_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id not in conversation_histories:
        await update.message.reply_text("Chưa có ai tương tác với bot trong nhóm.")
        return
    users = set()
    for msg in conversation_histories[chat_id]:
        if msg.get("role") == "user" and "user" in msg:
            users.add(msg["user"])
    tags = " ".join([f"@{u}" for u in users if u])
    await update.message.reply_text(tags or "Không có thành viên nào để tag.")

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👤 User ID: `{update.effective_user.id}`\n💬 Chat ID: `{update.effective_chat.id}`",
        parse_mode=ParseMode.MARKDOWN
    )

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    users = set()
    for msg in conversation_histories.get(chat_id, []):
        if msg.get("role") == "user" and "user" in msg:
            users.add(msg["user"])
    if users:
        tag_list = "\n".join([f"• @{u}" for u in users])
        await update.message.reply_text("👥 Danh sách người dùng:\n" + tag_list)
    else:
        await update.message.reply_text("Không có người dùng nào được lưu.")

async def forget_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    username = update.effective_user.username
    if not username:
        await update.message.reply_text("Bạn cần có username để xoá dữ liệu.")
        return
    conv = conversation_histories.get(chat_id, [])
    filtered = [msg for msg in conv if msg.get("user") != username]
    conversation_histories[chat_id] = filtered
    save_conversations(conversation_histories)
    await update.message.reply_text(f"🧹 Đã xoá dữ liệu của @{username}.")

# =====================[ Ghi thiết bị cho thuê theo lệnh ]=====================
async def rent_device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❗ Cú pháp: /rent [mã thiết bị] [ghi chú]")
        return

    device_id = args[0]
    note = " ".join(args[1:])
    username = update.effective_user.username or update.effective_user.first_name
    date = update.message.date.strftime("%Y-%m-%d")

    rental_data[device_id] = {
        "renter": username,
        "date_rented": date,
        "note": note
    }
    save_rentals(rental_data)
    await update.message.reply_text(f"✅ Đã ghi nhận thiết bị `{device_id}` được cho thuê.", parse_mode=ParseMode.MARKDOWN)

# =====================[ Kiểm tra thiết bị theo lệnh ]=====================
async def check_device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("❗ Cú pháp: /check [mã thiết bị]")
        return

    device_id = args[0]
    if device_id in rental_data:
        info = rental_data[device_id]
        await update.message.reply_text(
            f"📦 Thiết bị `{device_id}`:\n"
            f"👤 Người thuê: {info['renter']}\n"
            f"📅 Ngày thuê: {info['date_rented']}\n"
            f"📝 Ghi chú: {info['note']}",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(f"❌ Không tìm thấy thiết bị `{device_id}`.", parse_mode=ParseMode.MARKDOWN)

# =====================[ Xử lý tin nhắn tự nhiên ]=====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    text = message.text.strip()
    lower_text = text.lower()
    username = user.username or user.first_name

    # ✅ Tự động nhận diện "cho thuê [mã] cho [tên]"
    match = re.search(r"cho thuê (\w+)[^\n]* cho (\w+)", lower_text)
    if match:
        device_id = match.group(1)
        renter = match.group(2)
        date = update.message.date.strftime("%Y-%m-%d")
        note = text

        rental_data[device_id] = {
            "renter": renter,
            "date_rented": date,
            "note": note
        }
        save_rentals(rental_data)
        await message.reply_text(
            f"✅ Đã tự động ghi nhận thiết bị `{device_id}` được cho thuê cho {renter}.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ✅ ChatGPT ghi nhớ hội thoại
    if chat_id not in conversation_histories:
        conversation_histories[chat_id] = []
        conversation_histories[chat_id].append({"role": "system", "content": DEFAULT_SYSTEM_PROMPT})

    conversation_histories[chat_id].append({"role": "user", "content": text, "user": username})

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{k: v for k, v in msg.items() if k in ["role", "content"]} for msg in conversation_histories[chat_id]],
            temperature=0.7,
        )
        reply = response.choices[0].message.content.strip()
        conversation_histories[chat_id].append({"role": "assistant", "content": reply})
        save_conversations(conversation_histories)
    except Exception as e:
        logging.error("OpenAI API error: %s", e)
        reply = "❌ Đã xảy ra lỗi khi gọi GPT."

    await message.reply_text(f"@{username} {reply}", parse_mode=ParseMode.MARKDOWN)

# =====================[ MAIN BOT ]=====================
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("mentionall", mention_all))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("users", list_users))
    app.add_handler(CommandHandler("forgetme", forget_me))
    app.add_handler(CommandHandler("rent", rent_device))
    app.add_handler(CommandHandler("check", check_device))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    await app.run_polling()

# =====================[ Chạy Bot ]=====================
if __name__ == '__main__':
    import asyncio
    nest_asyncio.apply()

    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()
