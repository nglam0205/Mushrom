from datetime import date
import sys
from datetime import datetime, timedelta
import pytz
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import base64
import requests
import json
import re
from telebot.apihelper import ApiTelegramException

arguments = sys.argv

bot = telebot.TeleBot("SHARE_REDACTED_VALUE")
ngay_hien_tai = datetime.now()
timezone = pytz.timezone('Asia/Bangkok')
ngay_gmt7 = ngay_hien_tai.astimezone(timezone)


def extract_chatid_from_file(filename):
    try:
        # Mở file và đọc nội dung
        with open(filename, 'r') as file:
            content = file.read()

        # Tìm dòng chứa giá trị chatid
        chatid_line = next((line for line in content.split('\n') if line.startswith('chatid=')), None)

        if chatid_line:
            # Tách giá trị chatid
            chatid_value = chatid_line.split('=')[1].strip()
            return chatid_value
        else:
            print("Không tìm thấy giá trị chatid trong file.", file=sys.stderr)
            return None
    except FileNotFoundError:
        print(f"Tệp {filename} không tồn tại.", file=sys.stderr)
        return None


def safe_edit_message_text(chat_id, message_id, content, runid_value=None, emit_new_id=True, **kwargs):
    if message_id:
        try:
            return bot.edit_message_text(content, chat_id, message_id, **kwargs)
        except ApiTelegramException as exc:
            description = str(getattr(exc, "description", exc)).lower()
            if "message is not modified" in description:
                return None
            if "message to edit not found" not in description and "message can't be edited" not in description:
                raise

            print(f"[Telegram][Warn] edit failed, send new message: {exc}", file=sys.stderr)

    message = bot.send_message(chat_id, content, **kwargs)
    if runid_value:
        new_id = str(message.message_id)
        encoded = base64.b64encode(f"{runid_value}.{new_id}".encode()).decode()
        stamped_content = re.sub(r"ID : <code>.*?</code>", f"ID : <code>{encoded}</code>", content)
        if stamped_content != content:
            try:
                bot.edit_message_text(stamped_content, chat_id, new_id, **kwargs)
            except ApiTelegramException as exc:
                print(f"[Telegram][Warn] stamp new message id failed: {exc}", file=sys.stderr)
    if emit_new_id:
        print(message.message_id)
    return message


builder = sys.argv[1]
action_link = sys.argv[2]
device = sys.argv[3]
miui = sys.argv[4]
link = sys.argv[5]
patch = sys.argv[6]
is_stock = sys.argv[7]
para = int(sys.argv[8])
commit = sys.argv[9]
runid = sys.argv[10]
dllink = sys.argv[11]


rom = "HyperOS"

if is_stock == "true":
    ver = "STOCK"
    act = "Converting"
    act1 = "Converted"
    prever = "STOCK"
else :
    act = "Building"
    act1 = "Built"
    ver = "HyperVS"
    prever = "PAID"

gl = ''


sever = f"{prever} - OneDrive"

if para > 1:
    para = para - 1
    #make rom
    lines = [
        f"Download",
        f"Extract", 
        f"Patch", 
        f"Pack", 
        f"Upload" 
    ]

    lline6 = ""
    for i in range(len(lines)):
        if para -1 == i:
            statel = f"<b> > {lines[i]}ing</b>"
        elif para -1 >= i:
            statel = f"{lines[i]}ed"
        else :
            statel = lines[i]

        lline6 = f"{lline6}\n{statel}"

    lline1 = f"<b>Start building {ver}</b>"
    lline2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    lline3 = f"<b>Device</b> : #{device} {gl}"
    lline4 = f"<b>{rom} Version</b> : <a href='{link}'>{miui}</a>"
    lline5 = f"<b>Sub Version</b> : <a href='https://changelog.vsteam.vn'>{patch}</a>"
    lline6t = f"<b>State</b>"

    messageid = extract_chatid_from_file("chatid")
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"


    if is_stock == "true":
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n\n{lline6t}{lline6}\n\n{runid2}"
    else :
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n{lline5}\n{line_cm1}\n{line_cm2}\n\n{lline6t}{lline6}\n\n{runid2}"

    safe_edit_message_text("SHARE_REDACTED_VALUE", messageid, lcontent, runid, parse_mode="html", disable_web_page_preview=True)


elif para == 1:
    #prepare rom
    line1 = f"<b>Prepare New {ver}</b>"
    line2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"

    line4 = f"<b>Device</b> : #{device} {gl}"
    line5 = f"<b>{rom} Version</b> : <a href='{link}'>{miui}</a>"
    line6 = f"<b>Sub Version</b> : <a href='https://changelog.vsteam.vn'>{patch}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    runid1 = f"ID : <code>{runid}</code>"

    if is_stock == "true":
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line7}\n\n{line8}\n{line9}\n\n{runid1}"
    else :
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line6}\n{line_cm1}\n{line_cm2}\n\n{runid1}"

    Message = bot.send_message("SHARE_REDACTED_VALUE", content, parse_mode="html", disable_web_page_preview=True)
    messageid = Message.message_id
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"

    if is_stock == "true":
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line7}\n\n{line8}\n{line9}\n\n{runid2}"
    else :
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line6}\n{line_cm1}\n{line_cm2}\n\n{runid2}"

    edited_message = safe_edit_message_text(
        "SHARE_REDACTED_VALUE",
        messageid,
        content,
        runid,
        emit_new_id=False,
        parse_mode="html",
        disable_web_page_preview=True,
    )

    print(getattr(edited_message, "message_id", messageid))

elif para == 0:
    #new rom
    line1 = f"<b>New {ver} Test Build</b>"
    line2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    line4 = f"<b>Device</b> : #{device} {gl}"
    line5 = f"<b>{rom} Version</b> : <a href='{link}'>{miui}</a>"
    line6 = f"<b>Sub Version</b> : <a href='https://changelog.vsteam.vn'>{patch}</a>"

    messageid = extract_chatid_from_file("chatid")
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"


    content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line6}\n{line_cm1}\n{line_cm2}"

    # Tạo inline keyboard button
    keyboard = [[InlineKeyboardButton("Download", url=f"{dllink}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    safe_edit_message_text("SHARE_REDACTED_VALUE", messageid, content, runid, parse_mode="html", disable_web_page_preview=True, reply_markup=reply_markup)



elif para == -1:

    lline1 = f"<b>Stop building {ver}</b>"
    lline2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    lline3 = f"<b>Device</b> : #{device} {gl}"
    lline4 = f"<b>{rom} Version</b> : <a href='{link}'>{miui}</a>"
    lline5 = f"<b>Sub Version</b> : <a href='https://changelog.vsteam.vn'>{patch}</a>"
    lline6t = f"<b>Reason</b> : <i>Cancelled</i>"
    messageid = extract_chatid_from_file("chatid")
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"


    if is_stock == "true":
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n\n{lline6t}{lline6}"
    else :
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n{lline5}\n{line_cm1}\n{line_cm2}\n\n{lline6t}"

    safe_edit_message_text("SHARE_REDACTED_VALUE", messageid, lcontent, runid, parse_mode="html", disable_web_page_preview=True)

elif para == -2:

    lline1 = f"<b>Stop building {ver}</b>"
    lline2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    lline3 = f"<b>Device</b> : #{device} {gl}"
    lline4 = f"<b>{rom} Version</b> : <a href='{link}'>{miui}</a>"
    lline5 = f"<b>Sub Version</b> : <a href='https://changelog.vsteam.vn'>{patch}</a>"
    lline6t = f"<b>Reason</b> : <i>Failure</i>"
    messageid = extract_chatid_from_file("chatid")
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"


    if is_stock == "true":
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n\n{lline6t}"
    else :
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n{lline5}\n{line_cm1}\n{line_cm2}\n\n{lline6t}{lline6}"

    safe_edit_message_text("SHARE_REDACTED_VALUE", messageid, lcontent, runid, parse_mode="html", disable_web_page_preview=True)

else :
    pass

    
