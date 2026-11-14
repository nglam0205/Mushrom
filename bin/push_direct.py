import telebot
import sys
from datetime import datetime
import pytz

# Config bot token và chat_id nhóm Telegram (bạn thay bằng chat_id nhóm thực tế)
BOT_TOKEN = "SHARE_REDACTED_VALUE"
GROUP_CHAT_ID = SHARE_REDACTED_VALUE  # Thay bằng ID nhóm Telegram của bạn (bắt đầu bằng -100...)

bot = telebot.TeleBot(BOT_TOKEN)

def get_current_time():
    ngay_hien_tai = datetime.now()
    timezone = pytz.timezone('Asia/Bangkok')
    ngay_gmt7 = ngay_hien_tai.astimezone(timezone)
    return ngay_gmt7.strftime('%Y-%m-%d %H:%M:%S')

def main():
    # Đọc tham số dòng lệnh dạng key=value
    args = sys.argv[1:]
    params = {}
    for arg in args:
        if '=' in arg:
            k, v = arg.split('=', 1)
            params[k.strip()] = v.strip()

    # Lấy giá trị hoặc đặt mặc định nếu không có
    device = params.get('device', 'UnknownDevice')
    iden = params.get('iden', 'UnknownVersion')
    patch = params.get('patch', 'UnknownPatch')
    directlink = params.get('directlink', 'NoDirectLink')

    time_str = get_current_time()

    message = (
        f"*New ColourVS Update*\n"
        f"Time: {time_str}\n"
        f"Device: {device}\n"
        f"Version: {iden}\n"
        f"Patch: {patch}\n"
        f"🔗 [Direct Link]({directlink})"
    )

    # Gửi tin nhắn với định dạng Markdown
    bot.send_message(
        GROUP_CHAT_ID,
        message,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

if __name__ == "__main__":
    main()
