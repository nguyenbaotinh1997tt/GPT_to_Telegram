# ChatGPT Telegram Group Bot

Bot Telegram sử dụng GPT-4, hỗ trợ nhóm chat với các tính năng:
- Ghi nhớ hội thoại
- Tag người dùng
- Gọi tất cả thành viên
- Xoá lịch sử, quản lý dữ liệu cá nhân

## Cài đặt

1. Clone project về:
```
git clone https://github.com/yourusername/chatgpt_telegram_group_bot.git
cd chatgpt_telegram_group_bot
```

2. Tạo file `.env` từ `.env.example` và điền thông tin:
```
cp .env.example .env
```

3. Cài thư viện:
```
pip install -r requirements.txt
```

4. Chạy bot:
```
python bot.py
```

## Deploy trên Railway

1. Push repo lên GitHub
2. Vào Railway > New Project > Deploy from GitHub
3. Thêm biến môi trường tương ứng từ `.env`
4. Railway tự động build và chạy bot