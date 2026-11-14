"""Add/fix Vietnamese (vi_VN) in MAML widget packages (.mtz with a nested widget zip).

Usage: maml_vi.py <src.mtz> <dst.mtz>

Entry order, compression and timestamps are kept; only description.xml,
strings/strings_vi_VN.xml and the weather sunrise/sunset math in manifest.xml
inside the nested widget change. Strings already translated are kept; fails on
any untranslated string it has no Vietnamese for.
"""
import io
import os
import re
import sys
import zipfile

# description.xml values, keyed by the en_US text
DESC = {
    "Xiaomi Themes": "Chủ đề Xiaomi",
    "Mi Themes": "Chủ đề Xiaomi",
    "Weather": "Thời tiết",
    "Weather forecast": "Dự báo thời tiết",
    "View detailed weather info": "Xem thông tin thời tiết chi tiết",
    "View current weather info": "Xem thông tin thời tiết hiện tại",
    "Simple clock": "Đồng hồ đơn giản",
    "Today's weather": "Thời tiết hôm nay",
    "Default clock widget with a removable title": "Tiện ích đồng hồ mặc định, có thể ẩn tên",
    "Classic clock (Home screen and Lock screen)": "Đồng hồ cổ điển (Màn hình chính và Màn hình khóa)",
    "Changing the clock style on the Lock screen will change it on the widget as well":
        "Đổi kiểu đồng hồ trên Màn hình khóa thì tiện ích cũng đổi theo",
}

# strings, by name. Tokens the manifest substitutes with strReplaceAll
# (SLOT_VAL, temperature, feellike, ultraviolet, wind, stand) stay as is.
VI = {
    # maml_clock_4x2_new
    "time_format_12": "EEE, d/M",
    "time_format_24": "EEE, d/M",
    "talkback_date_format_12": "EEEE, d MMMM",
    "talkback_date_format_24": "EEEE, d MMMM",
    # maml_weather_2x2
    "weather_null_tip": "Nhấn để lấy thông tin thời tiết",
    "tomorrow": "Ngày mai",
    "sunrise": "Bình minh",
    "sunset": "Hoàng hôn",
    # maml_clock_4x2_redmi / _v3
    "time_format_100": "EEE, d/M",
    "time_format_100_talkback": "EEEE, d MMMM",
    "dual_clock_date_time_12": "EEE, d/M",
    "dual_clock_date_time": "EEE, d/M",
    "dual_clock_location": "Giờ địa phương",
    "dual_clock_resident_city": "Đồng hồ thứ hai",
    "weather_sunrise_time": "Bình minh",
    "weather_sunset_time": "Hoàng hôn",
    "weather_uvi_weak": "Yếu",
    "weather_uvi_moderate": "Trung bình",
    "weather_uvi_strong": "Mạnh",
    "weather_uvi_very_strong": "Rất mạnh",
    "weather_wind_n": "Gió bắc",
    "weather_wind_ne": "Gió đông bắc",
    "weather_wind_e": "Gió đông",
    "weather_wind_se": "Gió đông nam",
    "weather_wind_s": "Gió nam",
    "weather_wind_sw": "Gió tây nam",
    "weather_wind_w": "Gió tây",
    "weather_wind_nw": "Gió tây bắc",
    "weather_wind_level_unit": "Cấp wind",
    "day_of_the_month_talkback": "Ngày trong tháng: ",
    "holiday_talkback": "Ngày lễ",
    "date_according_to_the_traditional_chinese_calendar": "Âm lịch: ",
    "weather_tomorrow_sunrise_time_talkback": "Bình minh ngày mai: ",
    "weather_today_sunrise_time_talkback": "Bình minh: ",
    "weather_sunset_time_talkback": "Hoàng hôn: ",
    "weather_humidity_percent_talkback": "Độ ẩm",
    "chance_of_precipitation_talkback": "Khả năng có mưa",
    "no_weather_data_talkback": "Nhấn để lấy thông tin thời tiết",
    "no_health_dataTalkback": "Nhấn để đặt mục tiêu",
    "health_mi_stands": "stand giờ",
    "health_mi_stands_one": "stand giờ",
    "weather_temperature_talkback": "Nhiệt độ: temperature độ",
    "weather_temperature_talkback_one": "Nhiệt độ: temperature độ",
    "air_quality_index_talkback": "Chỉ số chất lượng không khí: SLOT_VAL",
    "weather_ultraviolet_index_talkback": "Chỉ số UV: ultraviolet",
    "weather_feel_like_temperature_talkback": "Cảm giác như: feellike độ",
    "weather_feel_like_temperature_talkback_one": "Cảm giác như: feellike độ",
    "weather_pressure_hectopascal_talkback": "Áp suất khí quyển: SLOT_VAL hectopascal",
    "weather_pressure_hectopascal_talkback_one": "Áp suất khí quyển: SLOT_VAL hectopascal",
    "step_walked_current_talkback": "Số bước hiện tại: SLOT_VAL bước",
    "step_walked_current_talkback_one": "Số bước hiện tại: SLOT_VAL bước",
    "step_walked_goal_talkback": "Mục tiêu: SLOT_VAL bước",
    "step_walked_goal_talkback_one": "Mục tiêu: SLOT_VAL bước",
    "stand_walked_current_talkback": "Đã đứng dậy trong SLOT_VAL giờ",
    "stand_walked_current_talkback_one": "Đã đứng dậy trong SLOT_VAL giờ",
    "stand_walked_goal_talkback": "Mục tiêu: SLOT_VAL giờ",
    "stand_walked_goal_talkback_one": "Mục tiêu: SLOT_VAL giờ",
    "aqi": "AQI",
    # maml_weather_2x1 / 2x2_new
    "high_i18n": "Cao ",
    "low_i18n": "Thấp ",
    "now_i18n": "Bây giờ",
    "weather_i18n": "Thời tiết",
    "low_temp_i18n": "Thấp",
    "aqi_i18n": "AQI",
    "aqi_null_i18n": "AQI --",
    "weather_auth_tip_i18n": "Chạm để nhận thông tin thời tiết",
    "weather_auth_tip_1_i18n": "Cấp quyền trong Thời tiết",
    "tomorrow_i18n": "Ngày mai",
    "sunrise_i18n": "Mặt trời mọc",
    "sunset_i18n": "Mặt trời lặn",
    "sunrising_i18n": "Mặt trời đang mọc",
    "sunseting_i18n": "Mặt trời đang lặn",
    "sunrise_sunset_i18n": "Mặt trời mọc và mặt trời lặn",
    "hour_after_i18n": r"\'Sau \'+#sun_hour+\' giờ\'",
    "minute_after_i18n": r"\'Sau \'+#sun_minute+\' phút\'",
    "morning_i18n": r"#sun_morning+\' SA\'",
    "afternoon_i18n": r"#sun_afternoon+\' CH\'",
    "authorization_and_open_i18n": "Cấp quyền để xem thời tiết",
    "wind_level_i18n": "",
    "talkback_temperature_range": "Nhiệt độ tối đa: 变量1. Nhiệt độ tối thiểu: 变量2",
    "talkback_humidity": "Độ ẩm: 变量3",
    "talkback_wind": "Gió: Cấp 变量4",
    # 24 solar terms
    "slight_cold": "Tiểu hàn", "great_cold": "Đại hàn", "spring_begins": "Lập xuân",
    "the_rains": "Vũ thủy", "insects_awaken": "Kinh trập", "vernal_equinox": "Xuân phân",
    "clear_and_bright": "Thanh minh", "grain_rain": "Cốc vũ", "summer_begins": "Lập hạ",
    "grain_buds": "Tiểu mãn", "grain_in_ear": "Mang chủng", "summer_solstice": "Hạ chí",
    "slight_heat": "Tiểu thử", "great_heat": "Đại thử", "autumn_begins": "Lập thu",
    "stopping_the_heat": "Xử thử", "white_dews": "Bạch lộ", "autumn_equinox": "Thu phân",
    "cold_dews": "Hàn lộ", "hoar_frost_falls": "Sương giáng", "winter_begins": "Lập đông",
    "light_snow": "Tiểu tuyết", "heavy_snow": "Đại tuyết", "winter_solstice": "Đông chí",
}
# Per-package overrides, keyed by the .mtz name without extension.
VI_PKG = {
    # date and weekday are separate lines here
    "maml_clock_4x2_v3": {
        "time_format_12": "dd/MM",
        "time_format_24": "dd/MM",
        "week_format_12": "EEEE",
        "week_format_24": "EEEE",
        "talkback_date_format_12": "d MMMM",
        "talkback_date_format_24": "d MMMM",
        "talkback_week_format_12": "EEEE",
        "talkback_week_format_24": "EEEE",
    },
}
# Logic values and expressions: copied from strings.xml, never translated.
KEEP = re.compile(r"^(RTL|local|flag_.*|talkback_time|.*_formula)$")
# Placeholders the manifest substitutes; a translation must keep every one.
TOKEN = re.compile(r"%\d*\$?[ds]|变量\d|#\w+")

STRING = re.compile(r'(<string\s+name="([^"]+)"[^>]*>)(.*?)(</string>)', re.S)
# also matches <string name="x" />, which strings_en_US.xml uses for empty values
ANY_STRING = re.compile(r'<string\s+name="([^"]+)"[^>]*?(?:/>|>(.*?)</string>)', re.S)
# Weather reports today's sunrise/sunset as ms from local midnight, but once
# the sun is up it reports tomorrow's (e.g. 29:42), so the widget thinks it is
# night all day. Fold both back into today before adding midnight.
SUN = re.compile(r"(#weather_sun(?:rise|set)\w*)(\s*\+\s*#time0)")


def esc(v):
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def patch_strings(default_xml, en_xml, vi_xml, pkg):
    default = {m[1]: m[2] or "" for m in ANY_STRING.finditer(default_xml)}
    en = {m[1]: m[2] or "" for m in ANY_STRING.finditer(en_xml or "")}
    # MAML falls back to strings.xml (often Chinese), so cover en_US-only names too
    for name, v in en.items():
        default.setdefault(name, v)
    if vi_xml is None:
        vi_xml = '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n</resources>\n'
    have = {m[2] for m in STRING.finditer(vi_xml)}
    vi = {**VI, **VI_PKG.get(pkg, {})}
    missing = []

    def value(name, current):
        src = en.get(name, default[name])
        if current is not None and (current != src or KEEP.match(name)):
            return current  # already translated
        if KEEP.match(name):
            return default[name]
        if name not in vi:
            missing.append(name)
            return default[name]
        new = esc(vi[name])
        lost = set(TOKEN.findall(src)) - set(TOKEN.findall(new))
        if lost:
            raise SystemExit(f"{name}: Vietnamese drops {sorted(lost)}")
        return new

    vi_xml = STRING.sub(lambda m: m[1] + value(m[2], m[3]) + m[4], vi_xml)
    add = "".join(f'  <string name="{n}">{value(n, None)}</string>\n' for n in default if n not in have)
    if missing:
        raise SystemExit(f"no Vietnamese for: {missing}")
    return vi_xml.replace("</resources>", add + "</resources>", 1)


def patch_desc(xml):
    has_vi = set(re.findall(r'<(\w+) locale="vi[_-]VN"', xml))
    out = []
    for line in xml.splitlines(keepends=True):
        out.append(line)
        m = re.match(r'(\s*<(\w+) locale=)"en_US"(>(?:<!\[CDATA\[)?)(.*?)((?:\]\]>)?</\2>\s*)$', line, re.S)
        if m and m[2] not in has_vi:
            if m[4] not in DESC:
                raise SystemExit(f"no Vietnamese for description.xml {m[2]}: {m[4]!r}")
            out.append(f'{m[1]}"vi_VN"{m[3]}{esc(DESC[m[4]])}{m[5]}')
    return "".join(out)


def rebuild(src_bytes, edit):
    """Copy a zip, letting edit(name, data, infos) return new data or extra entries."""
    zin = zipfile.ZipFile(io.BytesIO(src_bytes))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            for name, new, like in edit(info, data, zin):
                zi = zipfile.ZipInfo(name, like.date_time)
                zi.compress_type, zi.external_attr, zi.create_system = like.compress_type, like.external_attr, like.create_system
                zout.writestr(zi, new)
    return buf.getvalue()


def read_en(zin, base):
    en = base.replace("strings.xml", "strings_en_US.xml")
    return zin.read(en).decode("utf-8-sig") if en in zin.namelist() else None


def edit_inner(info, data, zin):
    if info.filename == "manifest.xml":
        yield info.filename, SUN.sub(r"\1%86400000\2", data.decode("utf-8")).encode(), info
        return
    yield info.filename, data, info
    m = re.match(r"(.*/)?strings\.xml$", info.filename)
    if not m:
        return
    vi_name = (m[1] or "") + "strings_vi_VN.xml"
    names = zin.namelist()
    if vi_name in names:
        return  # patched where it sits
    new = patch_strings(data.decode("utf-8-sig"), read_en(zin, info.filename), None, PKG)
    yield vi_name, new.encode(), info


def edit_inner_existing(info, data, zin):
    if info.filename.endswith("strings_vi_VN.xml"):
        base = info.filename.replace("strings_vi_VN.xml", "strings.xml")
        new = patch_strings(zin.read(base).decode("utf-8-sig"), read_en(zin, base), data.decode("utf-8-sig"), PKG)
        yield info.filename, new.encode(), info
        return
    yield from edit_inner(info, data, zin)


def edit_outer(info, data, zin):
    if info.filename == "description.xml":
        yield info.filename, patch_desc(data.decode("utf-8")).encode(), info
    elif data[:2] == b"PK" and info.filename.startswith("widget_"):
        yield info.filename, rebuild(data, edit_inner_existing), info
    else:
        yield info.filename, data, info


if __name__ == "__main__":
    src, dst = sys.argv[1:]
    PKG = os.path.splitext(os.path.basename(src))[0]
    open(dst, "wb").write(rebuild(open(src, "rb").read(), edit_outer))
