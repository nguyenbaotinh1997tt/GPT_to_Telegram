# ChatGPT Telegram Group Bot (PTB 20.7)

Bot Telegram sử dụng GPT-4, hỗ trợ nhóm chat với các tính năng:
- Ghi nhớ hội thoại (lưu dài hạn)
- Tag người dùng hoặc tất cả
- Quản lý dữ liệu cá nhân

## Cài đặt thủ công
```bash
git clone https://github.com/yourusername/chatgpt_telegram_group_bot.git
cd chatgpt_telegram_group_bot
cp .env.example .env
pip install -r requirements.txt
python bot.py
```

## Deploy trên Railway
1. Push repo lên GitHub
2. Vào Railway > New Project > Deploy from GitHub
3. Thêm biến môi trường giống file `.env`
4. Railway sẽ tự build và chạy bot!