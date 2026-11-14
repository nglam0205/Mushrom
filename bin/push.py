from datetime import date
import os
import sys
from datetime import datetime, timedelta
import pytz
import telebot
import base64
import requests
import json
import re
import html
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

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


def push_noti_vstools(topic, title, body, data=None):
    url = "https://vsteam.vn/api/send_notification"

    payload = {
        "secret_key": "SHARE_REDACTED_VALUE",
        "topic": topic,
        "title": title,
        "body": body
    }
    if data:
        payload["data"] = data

    headers = {
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, data=json.dumps(payload))


def get_device_display_name(codename):
    """Return 'Full Device Name (#Codename)', falling back to '#Codename'."""
    try:
        response = requests.get(
            "https://vsteam.vn/api/device/name",
            params={"codename": codename},
            timeout=10,
        )
        response.raise_for_status()
        full_name = response.json().get("full_name", "").strip()
        if full_name:
            return f"{html.escape(full_name)} (#{html.escape(codename)})"
    except (requests.RequestException, ValueError, AttributeError) as exc:
        print(f"[Device API][Warn] Cannot get full name for {codename}: {exc}", file=sys.stderr)

    return f"#{html.escape(codename)}"


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


def brand_names(brand):
    normalized = (brand or "").strip().lower()
    if normalized in ("coloros", "colouros", "colourvs", "colorvs"):
        return "ColorOS", "ColourVS"
    return "HyperOS", "HyperVS"


builder = sys.argv[1]
action_link = sys.argv[2]
device = sys.argv[3]
device_display_name = get_device_display_name(device)
miui = sys.argv[4]
link = sys.argv[5]
patch = sys.argv[6]
para = int(sys.argv[7])
commit = sys.argv[8]
runid = sys.argv[9]
is_stock = len(sys.argv) > 10 and sys.argv[10].lower() == "true"
input_brand = sys.argv[11] if len(sys.argv) > 11 else ""



rom = "ColorOS"
ver = "ColourVS"
act = "Building"
act1 = "Built"
pbranch = "STABLE"
gl = ''
pregion = "CN"

if "miui" in link.lower():
    rom = "HyperOS"
    ver = "HyperVS"

if input_brand:
    rom, ver = brand_names(input_brand)

if is_stock:
    ver = "STOCK"
    act = "Building"
    act1 = "Built"

is_preview = ".PRE" in patch

if is_preview:
     pbranch = "PREVIEW"


sever = f"OFFICIAL - {ver} - {pbranch} - {pregion}"

if para > 1:
    #make rom
    if is_stock:
        lines = [
            f"Download",
            f"Extract",
            f"Pack",
            f"Upload",
        ]
        current_index = {
            2: 0,
            3: 1,
            4: 2,
            5: 2,
            6: 3,
        }.get(para, 0)
    else:
        para = para - 1
        lines = [
            f"Download",
            f"Extract", 
            f"Patch", 
            f"Pack", 
            f"Upload" 
        ]
        current_index = para - 1

    lline6 = ""
    for i in range(len(lines)):
        if current_index == i:
            statel = f"<b> > {lines[i]}ing</b>"
        elif current_index >= i:
            statel = f"{lines[i]}ed"
        else :
            statel = lines[i]

        lline6 = f"{lline6}\n{statel}"

    lline1 = f"<b>Start {act} {ver}</b>" if is_stock else f"<b>Start {act}</b>"
    lline2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    lline3 = f"<b>Device</b> : {device_display_name}{gl}"
    lline4 = f"<b>{rom}</b> : <a href='{link}'>{miui}</a>"
    lline5 = f"<b>{ver}</b> : <a href='https://vsteam.vn/#changelog'>{patch}</a>"
    lline6t = f"<b>State</b>"

    messageid = extract_chatid_from_file("chatid")
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"

    if is_stock:
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n\n{lline6t}{lline6}\n\n{runid2}"
    else:
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n{lline5}\n{line_cm1}\n{line_cm2}\n\n{lline6t}{lline6}\n\n{runid2}"
    safe_edit_message_text("@anti_tho_online", messageid, lcontent, runid, parse_mode="html", disable_web_page_preview=True)


elif para == 1:
    #prepare rom
    line1 = f"<b>Prepare New {ver}</b>"
    line2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"

    line4 = f"<b>Device</b> : {device_display_name}{gl}"
    line5 = f"<b>{rom}</b> : <a href='{link}'>{miui}</a>"
    line6 = f"<b>{ver}</b> : <a href='https://vsteam.vn/#changelog'>{patch}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    runid1 = f"ID : <code>{runid}</code>"

    if is_stock:
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n\n{runid1}"
    else:
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line6}\n{line_cm1}\n{line_cm2}\n\n{runid1}"
    Message = bot.send_message("@anti_tho_online", content, parse_mode="html", disable_web_page_preview=True)
    
    messageid = Message.message_id
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"

    if is_stock:
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n\n{runid2}"
    else:
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line6}\n{line_cm1}\n{line_cm2}\n\n{runid2}"
    edited_message = safe_edit_message_text(
        "@anti_tho_online",
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
    line1 = f"<b>New {ver} Build</b>"
    line2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    line4 = f"<b>Device</b> : {device_display_name}{gl}"
    line5 = f"<b>{rom}</b> : <a href='{link}'>{miui}</a>"
    line6 = f"<b>{ver}</b> : <a href='https://vsteam.vn/#changelog'>{patch}</a>"
    line7 = f"<b><a href='https://vsteam.vn/#download#{device}'>Download</a></b>"
    line8 = f"{sever}"
    

    messageid = extract_chatid_from_file("chatid")
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"


    if is_preview:
        if is_stock:
            content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}"
        else:
            content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line6}\n{line_cm1}\n{line_cm2}"
    elif is_stock:
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n\n{line7}"
    else:
        content = f"{line1}\n{line2}\n{line3}\n\n{line4}\n{line5}\n{line6}\n{line_cm1}\n{line_cm2}\n\n{line7}"

    success_message_options = {
        "parse_mode": "html",
        "disable_web_page_preview": True,
        "reply_markup": InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "Download",
                    url=f"https://vsteam.vn/#download#{device}",
                )
            ]]
        ),
    }

    bot.send_message("@miuivs", content, **success_message_options)
    safe_edit_message_text(
        "@anti_tho_online",
        messageid,
        content,
        runid,
        **success_message_options,
    )

    if "BETA" in patch:
        suffixes = ["PRE.BETA" if "PRE" in patch else "BETA"]
    elif "PRE" in patch:
        # ROMs built before the PRE/ST split report PRE.ST and VSTools on
        # them subscribes to that topic, so notify both preview channels.
        suffixes = ["PRE", "PRE.ST"]
    else:
        suffixes = ["ST"]

    for suffix in suffixes:
        push_noti_vstools(f"{device}.{suffix}", f"New {patch} Build", f"Commit : {commit}")

    # The uploaded OTA package, for the devices following its branch (VSTools
    # OtaBranch.syncTopic): shown as a Live Update with an Update button.
    ota_package = os.environ.get("VS_OTA_PACKAGE", "")
    if ota_package and not is_stock:
        push_noti_vstools(
            f"{device}.OTA.{'PRE' if 'PRE' in patch else 'ST'}",
            f"New {patch} Build",
            f"Commit : {commit}",
            {"type": "ota_update", "name": ota_package, "version": patch, "commit": commit},
        )



elif para == -1:

    lline1 = f"<b>Stop building</b>"
    lline2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    lline3 = f"<b>Device</b> : {device_display_name}{gl}"
    lline4 = f"<b>{rom}</b> : <a href='{link}'>{miui}</a>"
    lline5 = f"<b>{ver}</b> : <a href='https://vsteam.vn/#changelog'>{patch}</a>"
    lline6t = f"<b>Reason</b> : <i>Cancelled</i>"
    messageid = extract_chatid_from_file("chatid")
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"

    if is_stock:
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n\n{lline6t}"
    else:
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n{lline5}\n{line_cm1}\n{line_cm2}\n\n{lline6t}"

    safe_edit_message_text("@anti_tho_online", messageid, lcontent, runid, parse_mode="html", disable_web_page_preview=True)

elif para == -2:

    lline1 = f"<b>Stop building</b>"
    lline2 = ngay_gmt7.strftime("%H:%M, %A %d/%m/%Y ")
    line3 = f"by <a href='{action_link}'>{builder}</a>"
    line_cm1 = f"<b>Commit</b> :"
    line_cm2 = f"<i>{commit}</i>"
    lline3 = f"<b>Device</b> : {device_display_name}{gl}"
    lline4 = f"<b>{rom}</b> : <a href='{link}'>{miui}</a>"
    lline5 = f"<b>{ver}</b> : <a href='https://vsteam.vn/#changelog'>{patch}</a>"
    lline6t = f"<b>Reason</b> : <i>Failure</i>"
    messageid = extract_chatid_from_file("chatid")
    pid = f"{runid}.{messageid}"
    b64 = base64.b64encode(pid.encode()).decode()
    runid2 = f"ID : <code>{b64}</code>"

    if is_stock:
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n\n{lline6t}"
    else:
        lcontent = f"{lline1}\n{lline2}\n{line3}\n\n{lline3}\n{lline4}\n{lline5}\n{line_cm1}\n{line_cm2}\n\n{lline6t}"

    safe_edit_message_text("@anti_tho_online", messageid, lcontent, runid, parse_mode="html", disable_web_page_preview=True)

else :
    pass

    
