import os
import json
import logging
import openai
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          ContextTypes, filters)
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

ALLOWED_USER_IDS = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = [int(uid) for uid in ALLOWED_USER_IDS.split(",") if uid.strip().isdigit()]
DEFAULT_SYSTEM_PROMPT = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are ChatGPT, a helpful assistant.")

CONV_FILE = "conversations.json"
logging.basicConfig(level=logging.INFO)

# Load & Save functions for persistent conversation history
def load_conversations():
    if not os.path.exists(CONV_FILE):
        return {}
    with open(CONV_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_conversations(data):
    with open(CONV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

conversation_histories = load_conversations()

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
        "/forgetme - Xoá dữ liệu của bạn khỏi bot"
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    text = message.text

    if update.effective_chat.type in ["group", "supergroup"]:
        if not (f"@{context.bot.username}" in text or message.reply_to_message):
            return

    username = user.username or user.first_name

    if chat_id not in conversation_histories:
        conversation_histories[chat_id] = []
        conversation_histories[chat_id].append({"role": "system", "content": DEFAULT_SYSTEM_PROMPT})

    conversation_histories[chat_id].append({"role": "user", "content": text, "user": username})

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{k: v for k, v in msg.items() if k in ["role", "content"]} for msg in conversation_histories[chat_id]],
            temperature=0.7,
        )
        reply = response.choices[0].message.content.strip()
        conversation_histories[chat_id].append({"role": "assistant", "content": reply})
        save_conversations(conversation_histories)
    except Exception as e:
        logging.error("OpenAI API error: %s", e)
        reply = "❌ Đã xảy ra lỗi khi gọi GPT-4."

    await message.reply_text(f"@{username} {reply}", parse_mode=ParseMode.MARKDOWN)

async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("mentionall", mention_all))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("users", list_users))
    app.add_handler(CommandHandler("forgetme", forget_me))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    await app.run_polling()

if __name__ == '__main__':
    import asyncio

    try:
        asyncio.get_event_loop().run_until_complete(main())
    except RuntimeError:
        # Đã có event loop đang chạy → dùng cách khác
        import nest_asyncio
        nest_asyncio.apply()
        asyncio.run(main())

