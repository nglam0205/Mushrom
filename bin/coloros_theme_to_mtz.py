#!/usr/bin/env python3
"""Port a ColorOS / realme UI .theme to a HyperOS .mtz.

Usage: coloros_theme_to_mtz.py <in.theme | my_product/media/theme> <out.mtz> [title]
                               [--data <dir> --rights-dir <abs dir>]

A ColorOS .theme is a zip of nested zips:
  themeInfo.xml          author / title / uuid
  icons                  allApps.xml (icon file -> package), res/drawable-xxhdpi/*.png,
                         fancy_icons/<icon name>/manifest.xml (MAML clock / calendar)
  wallpaper              drawable-xxhdpi-<h>x<w>/oppo_default_{,keyguard_}wallpaper.png
  com.android.launcher   res/drawable-xxhdpi/new_iphone_style_mask.png (shape for apps
                         the pack does not draw) and a few extra icons
  picture                res/drawable-xxhdpi-<h>x<w>/{preview_lock,preview_menu,thumbnail}

The system icon set of ColorOS 15+ (my_product/media/theme) is a folder instead:
  uxicons/hdpi/<package>/recbg.png + recfg.png   layers of the default (rectangle) style,
                                                 square; the launcher cuts them to a squircle
  ../../decouping_wallpaper/default/default_wallpaper{,_lock}.webp   2500x2500, center-cropped
The clock and calendar are drawn by the ColorOS launcher, so their MAML is generated here.

A HyperOS .mtz keeps description.xml, icons (nested zip of res/drawable-xxhdpi/<pkg>.png,
icon_mask.png, fancy_icons/<pkg>/), wallpaper/ and preview/. ColorOS packages are mapped to
their Xiaomi counterparts; third-party packages keep their names.

--data also writes the HyperOS 4 resource store layout ThemeManager scans (precust_theme or
its local .data): meta/<code>/<id>.mrm (JSON), content/<code>/<id>.mrc, preview/theme/<id>/
and rights/theme/<id>.mra. The theme has no onlineId/productId, so the ROM's LocalImportDrm
patch (patch_theme.py) lets it apply; the rights file only mirrors the stock layout.
"""
import glob
import hashlib
import math
import io
import json
import os
import re
import sys
import uuid
import zipfile
from xml.sax.saxutils import escape

from PIL import Image, ImageChops, ImageDraw

ICON_SIZE = 235  # what HyperOS packs ship; the launcher scales from there

# ColorOS package -> HyperOS packages (CN and global builds) drawing the same app.
PACKAGE_MAP = {
    "com.android.phone": ["com.android.contacts.activities.TwelveKeyDialer", "com.android.incallui"],
    "com.android.contacts": ["com.android.contacts"],
    "com.android.mms": ["com.android.mms", "com.google.android.apps.messaging"],
    "com.android.settings": ["com.android.settings"],
    "com.android.email": ["com.android.email"],
    "com.android.stk": ["com.android.stk"],
    "com.android.fmradio": ["com.miui.fm", "com.miui.fmradio"],
    "com.coloros.backuprestore": ["com.miui.backup", "com.miui.huanji"],
    "com.coloros.calculator": ["com.miui.calculator", "com.android.calculator2"],
    "com.coloros.compass2": ["com.miui.compass"],
    "com.coloros.filemanager": ["com.android.fileexplorer", "com.mi.android.globalFileexplorer"],
    "com.coloros.findmyphone": ["com.xiaomi.finddevice", "com.miui.findmy"],
    "com.coloros.gallery3d": ["com.miui.gallery", "com.android.gallery3d"],
    "com.coloros.ocrscanner": ["com.xiaomi.scanner", "com.miui.barcodescanner"],
    "com.coloros.phonemanager": ["com.miui.securitycenter", "com.miui.securitycenter.Main",
                                 "com.miui.securitycenter.MainActivity"],
    "com.coloros.soundrecorder": ["com.android.soundrecorder"],
    "com.coloros.video": ["com.miui.video", "com.miui.videoplayer"],
    "com.coloros.weather2": ["com.miui.weather2"],
    "com.coloros.note": ["com.miui.notes"],
    "com.heytap.browser": ["com.android.browser", "com.mi.globalbrowser"],
    "com.heytap.cloud": ["com.miui.cloudservice"],
    "com.heytap.health": ["com.mi.health"],
    "com.heytap.market": ["com.xiaomi.market", "com.xiaomi.mipicks"],
    "com.heytap.music": ["com.miui.player", "com.android.music"],
    "com.heytap.quicksearchbox": ["com.android.quicksearchbox"],
    "com.heytap.reader": ["com.duokan.reader"],
    "com.heytap.smarthome": ["com.xiaomi.smarthome"],
    "com.heytap.speechassist": ["com.miui.voiceassist", "com.miui.voiceassistProxy"],
    "com.heytap.themestore": ["com.android.thememanager"],
    "com.finshell.wallet": ["com.mipay.wallet"],
    "com.nearme.gamecenter": ["com.xiaomi.gamecenter"],
    "com.oplus.camera": ["com.android.camera"],
    "com.oplus.consumerIRApp": ["com.duokan.phone.remotecontroller"],
    "com.oplus.games": ["com.xiaomi.glgm"],
    "com.oplus.vip": ["com.xiaomi.vipaccount"],
    "com.oppo.community": ["com.xiaomi.bbs", "com.mi.global.bbs"],
    "com.oplus.tips": ["com.miui.miservice"],
    "com.coloros.digitalwellbeing": ["com.miui.greenguard"],
}
# ColorOS-only apps with no HyperOS counterpart: nothing to draw them on.
SKIP = {"com.android.launcher", "com.coloros.familyguard", "com.coloros.findphone.client2",
        "com.coloros.musiclink", "com.coloros.onekeylockscreen", "com.coloros.shortcuts",
        "com.coloros.translate", "com.heytap.lock", "com.heytap.yoli", "com.market.heydemo",
        "com.nearme.instant.platform", "com.oneplus.bbs", "com.oneplus.brickmode",
        "com.oneplus.member", "com.oplus.ai.magicstudio", "com.oplus.aimemory",
        "com.oplus.beaconlink", "com.oplus.member", "com.oplus.play", "com.oplus.safecenter",
        "com.oplus.studycenter", "com.oplus.themestore", "com.opluskgd.onetaplocking",
        "com.redteamobile.roaming", "com.caf.fmradio", "com.nearme.note",
        "com.coloros.alarmclock", "com.coloros.calendar", "com.oplus.calendar", "com.oppo.camera"}
# Dynamic icons: ColorOS fancy folder -> HyperOS package.
FANCY_MAP = {"ic_launcher_clock": "com.android.deskclock",
             "ic_launcher_calender": "com.android.calendar"}


def nested(theme, name):
    try:
        return zipfile.ZipFile(io.BytesIO(theme.read(name)))
    except KeyError:
        return None


def junk(name):
    return "__MACOSX" in name or name.endswith(".DS_Store")


# HyperOS icon shape, ConfigIconMask of /product/media/theme/default/icons (0..100).
# The app-close animation always ends on this shape, so pack icons are cut to it too;
# a ColorOS squircle (a little squarer) visibly swaps corners on the last frame.
HYPEROS_ICON_PATH = (
    "M 0 42.7 C 0 27.7 0 20.3 2.9 14.6 C 5.5 9.5 9.5 5.5 14.6 2.9 C 20.3 0 27.7 0 42.7 0 "
    "L 57.3 0 C 72.3 0 79.7 0 85.4 2.9 C 90.5 5.5 94.5 9.5 97.1 14.6 C 100 20.3 100 27.7 100 42.7 "
    "V 57.3 C 100 72.3 100 79.7 97.1 85.4 C 94.5 90.5 90.5 94.5 85.4 97.1 C 79.7 100 72.3 100 57.3 100 "
    "H 42.7 C 27.7 100 20.3 100 14.6 97.1 C 9.5 94.5 5.5 90.5 2.9 85.4 C 0 79.7 0 72.3 0 57.3 "
    "L 0 42.7 Z")
_shape_cache = {}


def icon_shape(size, ss=8):
    """Rasterize HYPEROS_ICON_PATH (absolute M/L/H/V/C/Z) to an L mask of size x size."""
    if size in _shape_cache:
        return _shape_cache[size]
    tokens = re.findall(r"[MLHVCZ]|-?[\d.]+", HYPEROS_ICON_PATH)
    pts, cur, cmd, i = [], (0.0, 0.0), None, 0
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd == "Z":
                continue
        if cmd in ("M", "L"):
            cur = (float(tokens[i]), float(tokens[i + 1]))
            pts.append(cur)
            i += 2
        elif cmd == "H":
            cur = (float(tokens[i]), cur[1])
            pts.append(cur)
            i += 1
        elif cmd == "V":
            cur = (cur[0], float(tokens[i]))
            pts.append(cur)
            i += 1
        elif cmd == "C":
            c1, c2, end = [(float(tokens[i + k]), float(tokens[i + k + 1])) for k in (0, 2, 4)]
            for n in range(1, 17):
                u = n / 16
                pts.append(tuple((1 - u) ** 3 * p0 + 3 * (1 - u) ** 2 * u * p1
                                 + 3 * (1 - u) * u * u * p2 + u ** 3 * p3
                                 for p0, p1, p2, p3 in zip(cur, c1, c2, end)))
            cur = end
            i += 6
        else:
            i += 1
    big = size * ss
    im = Image.new("L", (big, big))
    ImageDraw.Draw(im).polygon([(x * big / 100, y * big / 100) for x, y in pts], fill=255)
    _shape_cache[size] = im.resize((size, size), Image.LANCZOS)
    return _shape_cache[size]


def shaped(im):
    """Cut an icon to the HyperOS shape (the ColorOS squircle fully contains it)."""
    im = im.convert("RGBA")
    if im.size != (ICON_SIZE, ICON_SIZE):
        im = im.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    alpha = ImageChops.multiply(im.split()[3], icon_shape(ICON_SIZE))
    im.putalpha(alpha)
    return im


def icon_png(data):
    im = shaped(Image.open(io.BytesIO(data)))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def biggest(names, pattern):
    """Pick the entry of the largest resolution folder (drawable-xxhdpi-<h>x<w>)."""
    best, area = None, -1
    for name in names:
        m = re.search(pattern, name)
        if not m or junk(name):
            continue
        dims = [int(x) for x in re.findall(r"\d+", name.split("/")[-2])]
        a = dims[0] * dims[1] if len(dims) >= 2 else 0
        if a > area:
            best, area = name, a
    return best


def transform_config():
    """Size and shape the launcher gives the pack's icons.

    HyperOS draws pack icons inset to 90% of the icon cell; full-bleed packs (Leica
    Vibrant) map 0..90 to -5..95 to fill it, otherwise the app-close animation lands
    on a bigger icon and snaps smaller on the last frame. ConfigIconMask states the
    shape the icons are cut to (the stock HyperOS one).
    """
    path = HYPEROS_ICON_PATH
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<IconTransform>
    <PointsMapping>
        <Point fromX="0" fromY="0" toX="-5" toY="-5"/>
        <Point fromX="0" fromY="90" toX="-5" toY="95"/>
        <Point fromX="90" fromY="90" toX="95" toY="95"/>
        <Point fromX="90" fromY="0" toX="95" toY="-5"/>
    </PointsMapping>
    <Config name="ConfigIconMask" value="{path}"/>
</IconTransform>
"""


def build_icons(theme, launcher):
    icons = nested(theme, "icons")
    names = icons.namelist()
    mapping = re.findall(r'<icon\s+name="([^"]+)"\s+package="([^"]+)"',
                         icons.read("allApps.xml").decode("utf-8", "replace"))
    out = io.BytesIO()
    written, unmapped = set(), []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        def put(target, data):
            path = f"res/drawable-xxhdpi/{target}.png"
            if path not in written:
                z.writestr(path, icon_png(data))
                written.add(path)

        for icon, pkg in mapping:
            src = f"res/drawable-xxhdpi/{icon}"
            if pkg in SKIP or src not in names:
                continue
            targets = PACKAGE_MAP.get(pkg)
            if targets is None:
                if pkg.startswith(("com.coloros.", "com.oplus.", "com.heytap.", "com.oppo.",
                                   "com.nearme.", "com.oneplus.")):
                    unmapped.append(pkg)
                    continue
                targets = [pkg]  # third-party app, same package on HyperOS
            for t in targets:
                put(t, icons.read(src))

        # Shape the launcher cuts apps without a themed icon to.
        z.writestr("res/drawable-xxhdpi/icon_mask.png", png_bytes(Image.merge(
            "RGBA", (Image.new("L", (ICON_SIZE, ICON_SIZE)),) * 3 + (icon_shape(ICON_SIZE),))))
        z.writestr("transform_config.xml", transform_config())

        for folder, pkg in FANCY_MAP.items():
            files = [n for n in names if n.startswith(f"fancy_icons/{folder}/") and not junk(n)
                     and not n.endswith("/") and not n.endswith("/.png")]
            for n in files:
                data = icons.read(n)
                base = n.split("/")[-1]
                if base.startswith("manifest"):
                    # ColorOS fonts are not on HyperOS; let MAML use the system font.
                    data = re.sub(rb'\s*typeface="[^"]*"', b"", data)
                z.writestr(f"fancy_icons/{pkg}/{base}", data)
            # The static fallback (shown before MAML draws) is the 1x1 picture.
            static = f"res/drawable-xxhdpi/com_{'coloros_alarmclock' if 'clock' in folder else 'coloros_calendar'}.png"
            if static in names:
                put(pkg, icons.read(static))
    return out.getvalue(), len(written), unmapped


def to_jpg(data, max_side=None):
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=92)
    return out.getvalue()


def description(info, title):
    def tag(name, default=""):
        m = re.search(rf"<{name}>(.*?)</{name}>", info, re.S)
        return (m.group(1).strip() if m else default) or default
    author = escape(tag("Author", "ColorOS"))
    title = escape(title or tag("Description") or tag("Summary", "ColorOS"))
    desc = escape(f"Ported from ColorOS ({tag('Summary')}) by VSTeam")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<theme>
    <version><![CDATA[1.0.0]]></version>
    <uiVersion>17</uiVersion>
    <author><![CDATA[{author}]]></author>
    <designer><![CDATA[{author}]]></designer>
    <title><![CDATA[{title}]]></title>
    <description><![CDATA[{desc}]]></description>
    <authors>
        <author locale="en_US"><![CDATA[{author}]]></author>
        <author locale="zh_CN"><![CDATA[{author}]]></author>
    </authors>
    <designers>
        <designer locale="en_US"><![CDATA[{author}]]></designer>
        <designer locale="zh_CN"><![CDATA[{author}]]></designer>
    </designers>
    <titles>
        <title locale="en_US"><![CDATA[{title}]]></title>
        <title locale="zh_CN"><![CDATA[{title}]]></title>
        <title locale="vi_VN"><![CDATA[{title}]]></title>
    </titles>
    <descriptions>
        <description locale="en_US"><![CDATA[{desc}]]></description>
        <description locale="zh_CN"><![CDATA[{desc}]]></description>
    </descriptions>
</theme>
"""


RIGHTS = """<o-ex:rights xmlns:o-ex="http://odrl.net/1.1/ODRL-EX" xmlns:o-dd="http://odrl.net/1.1/ODRL-DD" \
xmlns:oma-dd="http://www.openmobilealliance.com/oma-dd" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" \
xmlns:xenc="http://www.w3.org/2001/04/xmlenc#" o-ex:id="0"><o-ex:context><o-dd:version>2.1</o-dd:version>\
<o-dd:uid>0</o-dd:uid></o-ex:context><o-ex:agreement><o-ex:asset><o-ex:context><o-dd:uid>{uid}</o-dd:uid>\
</o-ex:context><o-ex:digest><ds:DigestMethod Algorithm="nop"/><ds:DigestValue/></o-ex:digest><ds:KeyInfo>\
<xenc:EncryptedKey><xenc:EncryptionMethod Algorithm="nop"/><ds:KeyInfo><ds:RetrievalMethod URI="nop"/></ds:KeyInfo>\
<xenc:CipherData><xenc:CipherValue/></xenc:CipherData></xenc:EncryptedKey></ds:KeyInfo></o-ex:asset><o-ex:permission>\
<o-dd:display><o-ex:constraint><o-dd:count>0</o-dd:count></o-ex:constraint></o-dd:display><o-dd:execute>\
<o-ex:constraint><oma-dd:individual><o-ex:context><o-dd:uid>m-1</o-dd:uid><o-dd:uid>d-1</o-dd:uid></o-ex:context>\
</oma-dd:individual></o-ex:constraint></o-dd:execute><o-dd:export/></o-ex:permission></o-ex:agreement></o-ex:rights>"""


def write_data(out, rights_dir, seed, title, author, parts, previews):
    """parts: {resourceCode: bytes}; previews: {file name: bytes} of the theme."""
    def rid(code):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"vsteam-coloros:{seed}:{code}"))

    theme_id = rid("theme")
    hashes = {code: hashlib.sha1(data).hexdigest() for code, data in parts.items()}
    theme_hash = hashlib.sha1("".join(hashes[c] for c in sorted(hashes)).encode()).hexdigest()
    rights_name = hashlib.sha1(theme_id.encode()).hexdigest() + ".mra"
    rights_path = f"{rights_dir.rstrip('/')}/{rights_name}"

    def meta(local_id, code, data_hash, size, thumbs, parent):
        return {
            "localId": local_id, "onlineId": None, "assemblyId": None, "productId": None,
            "hash": data_hash, "platform": 17, "size": size, "updatedTime": 0, "version": "1.0",
            "authors": {"fallback": author}, "designers": {"fallback": author},
            "titles": {"fallback": title}, "descriptions": {"fallback": "Ported from ColorOS by VSTeam"},
            "builtInThumbnails": {"fallback": thumbs}, "builtInPreviews": {"fallback": thumbs},
            "thumbnails": [], "previews": [],
            "parentResources": [] if parent is None else [
                {"localId": parent, "resourceCode": "theme", "extraMeta": {},
                 "metaPath": None, "contentPath": None}],
            "subResources": [], "extraMeta": {}, "metaPath": None, "contentPath": None,
            "rightsPath": rights_path, "screenRatio": None, "supportHomeSearchBar": False,
            "packageVersion": None, "packageName": None, "officialIcons": False, "iconsCount": None,
            "fontWeight": None, "price": -1, "isBackUpVersion": False, "themeType": 0,
            "miuiAdapterVersion": "3.3",
        }

    def put(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    thumbs_for = {"icons": ["preview_icons_0.jpg"], "lockscreen": ["preview_lockscreen_0.jpg"]}
    subs = []
    for code, data in parts.items():
        local_id = rid(code)
        put(f"{out}/content/{code}/{local_id}.mrc", data)
        m = meta(local_id, code, hashes[code], len(data), thumbs_for.get(code, []), theme_id)
        put(f"{out}/meta/{code}/{local_id}.mrm", json.dumps(m, indent=2, ensure_ascii=False).encode())
        subs.append({"localId": local_id, "resourceCode": code, "extraMeta": {},
                     "metaPath": None, "contentPath": None})
        if code in thumbs_for:
            for name in thumbs_for[code]:
                if name in previews:
                    put(f"{out}/preview/theme/{local_id}/{name}", previews[name])

    order = ["preview_launcher_0.jpg", "preview_lockscreen_0.jpg", "preview_icons_0.jpg"]
    shown = [n for n in order if n in previews]
    m = meta(theme_id, "theme", theme_hash, sum(len(d) for d in parts.values()), shown, None)
    m["subResources"] = subs
    put(f"{out}/meta/theme/{theme_id}.mrm", json.dumps(m, indent=2, ensure_ascii=False).encode())
    put(f"{out}/content/theme/{theme_id}.mrc", b"")
    for name, data in previews.items():
        put(f"{out}/preview/theme/{theme_id}/{name}", data)
    uid = theme_hash + ":" + ",".join(hashes[c] for c in parts)
    put(f"{out}/rights/theme/{rights_name}", RIGHTS.format(uid=uid).encode())
    return theme_id


UX_SRC = 180      # uxicons layer size
WALL_SIZE = (1440, 3200)


def png_bytes(im):
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def center_crop(path, size=WALL_SIZE):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    tw, th = size
    scale = max(tw / w, th / h)
    im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    x, y = (im.size[0] - tw) // 2, (im.size[1] - th) // 2
    return im.crop((x, y, x + tw, y + th))


def jpg_bytes(im, quality=92):
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=quality)
    return out.getvalue()


def clock_maml(face):
    """ColorOS clock: face from the icon with its painted hands erased, live hands on top."""
    k = ICON_SIZE / UX_SRC
    c = ICON_SIZE / 2
    hands = ((7 * k, 40 * k, "#hour12*30+#minute/2"), (5.5 * k, 56 * k, "#minute*6"))
    rects = "\n".join(
        f'\t<Rectangle x="{c:.1f}" y="{c:.1f}" w="{w:.1f}" h="{h:.1f}" align="center" alignV="bottom" '
        f'pivotX="{w / 2:.1f}" pivotY="{h:.1f}" fillColor="#2C3D5A" cornerRadius="{w / 2:.1f}" '
        f'rotation="{rot}" />' for w, h, rot in hands)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Icon version="2" width="{ICON_SIZE}" height="{ICON_SIZE}" screenWidth="1080" resDensity="480" scaleByDensity="false" useVariableUpdater="DateTime.Minute" type="awesome">
\t<ExternalCommands>
\t\t<Trigger action="resume">
\t\t\t<FrameRateCommand rate="5" />
\t\t\t<FrameRateCommand rate="0" delay="10" />
\t\t</Trigger>
\t</ExternalCommands>
\t<Image x="0" y="0" w="{ICON_SIZE}" h="{ICON_SIZE}" src="iconBg.png" />
{rects}
\t<Circle x="{c:.1f}" y="{c:.1f}" r="{6 * k:.1f}" fillColor="#2C3D5A" />
</Icon>
"""


def calendar_maml():
    """ColorOS calendar: blue weekday over a black day of month, at the stock positions."""
    k = ICON_SIZE / UX_SRC
    c = ICON_SIZE / 2
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Icon version="2" width="{ICON_SIZE}" height="{ICON_SIZE}" screenWidth="1080" resDensity="480" scaleByDensity="false" useVariableUpdater="DateTime.Hour" type="awesome">
\t<ExternalCommands>
\t\t<Trigger action="resume">
\t\t\t<FrameRateCommand rate="5" />
\t\t\t<FrameRateCommand rate="0" delay="10" />
\t\t</Trigger>
\t</ExternalCommands>
\t<Image x="0" y="0" w="{ICON_SIZE}" h="{ICON_SIZE}" src="iconBg.png" />
\t<DateTime x="{c:.1f}" y="{28 * k:.1f}" align="center" color="#0A7FFB" size="{28 * k:.0f}" bold="true" format="EEE" />
\t<Text x="{c:.1f}" y="{58 * k:.1f}" align="center" color="#000000" size="{94 * k:.0f}" textExp="#date" />
</Icon>
"""


def load_uxicons(src):
    """ColorOS system icon set (my_product/media/theme) -> icons zip, wallpapers, previews."""
    hdpi = os.path.join(src, "uxicons", "hdpi")
    mask = icon_shape(ICON_SIZE)

    def layered(folder, fg=True, prefix=""):
        bg = Image.open(os.path.join(folder, f"{prefix}recbg.png")).convert("RGBA")
        if fg and os.path.exists(os.path.join(folder, f"{prefix}recfg.png")):
            bg.alpha_composite(Image.open(os.path.join(folder, f"{prefix}recfg.png")).convert("RGBA").resize(bg.size))
        im = bg.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
        im.putalpha(Image.composite(im.split()[3], Image.new("L", im.size), mask))
        return im

    out = io.BytesIO()
    written, unmapped, shown = set(), [], []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        def put(target, im):
            path = f"res/drawable-xxhdpi/{target}.png"
            if path not in written:
                z.writestr(path, png_bytes(im))
                written.add(path)

        for folder in sorted(glob.glob(os.path.join(hdpi, "*"))):
            pkg = os.path.basename(folder)
            if pkg.startswith("icons-") or not os.path.exists(os.path.join(folder, "recbg.png")):
                continue
            if pkg in ("com.coloros.alarmclock", "com.coloros.calendar"):
                continue
            targets = PACKAGE_MAP.get(pkg)
            if targets is None:
                if pkg in SKIP or pkg.startswith(("com.coloros.", "com.oplus.", "com.heytap.",
                                                  "com.oppo.", "com.nearme.", "com.oneplus.",
                                                  "andes.", "com.finshell.", "com.tick.")):
                    unmapped.append(pkg)
                    continue
                targets = [pkg]
            im = layered(folder)
            if len(shown) < 20:
                shown.append(im)
            for t in targets:
                put(t, im)

        # The dialer has no folder of its own; its layers sit in contacts as dialer_*.
        contacts = os.path.join(hdpi, "com.android.contacts")
        if os.path.exists(os.path.join(contacts, "dialer_recbg.png")):
            for t in PACKAGE_MAP["com.android.phone"]:
                put(t, layered(contacts, prefix="dialer_"))

        z.writestr("res/drawable-xxhdpi/icon_mask.png", png_bytes(
            Image.merge("RGBA", (Image.new("L", mask.size),) * 3 + (mask,))))
        z.writestr("transform_config.xml", transform_config())

        # Clock: erase the painted hands (all of the face inside r < 61 of 180 is hands).
        clock_dir = os.path.join(hdpi, "com.coloros.alarmclock")
        if os.path.exists(os.path.join(clock_dir, "recfg.png")):
            fg = Image.open(os.path.join(clock_dir, "recfg.png")).convert("RGBA")
            px = fg.load()
            for y in range(fg.size[1]):
                for x in range(fg.size[0]):
                    if math.hypot(x - UX_SRC / 2, y - UX_SRC / 2) < 61:
                        px[x, y] = (0, 0, 0, 0)
            face = Image.open(os.path.join(clock_dir, "recbg.png")).convert("RGBA")
            face.alpha_composite(fg)
            face = face.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
            face.putalpha(Image.composite(face.split()[3], Image.new("L", face.size), mask))
            z.writestr("fancy_icons/com.android.deskclock/iconBg.png", png_bytes(face))
            z.writestr("fancy_icons/com.android.deskclock/manifest.xml", clock_maml(face))
            put("com.android.deskclock", layered(clock_dir))
        cal_dir = os.path.join(hdpi, "com.coloros.calendar")
        if os.path.exists(os.path.join(cal_dir, "recbg.png")):
            z.writestr("fancy_icons/com.android.calendar/iconBg.png", png_bytes(layered(cal_dir, fg=False)))
            z.writestr("fancy_icons/com.android.calendar/manifest.xml", calendar_maml())
            for t in ("com.android.calendar", "com.xiaomi.calendar"):
                put(t, layered(cal_dir))

    walls = os.path.normpath(os.path.join(src, "..", "..", "decouping_wallpaper", "default"))
    home = lock = None
    for name in ("default_wallpaper.webp", "default_wallpaper.png", "default_wallpaper.jpg"):
        if os.path.exists(os.path.join(walls, name)):
            home = center_crop(os.path.join(walls, name))
            break
    for name in ("default_wallpaper_lock.webp", "default_wallpaper_lock.png", "default_wallpaper_lock.jpg"):
        if os.path.exists(os.path.join(walls, name)):
            lock = center_crop(os.path.join(walls, name))
            break
    lock = lock or home

    previews = {}
    if home:
        # Launcher preview: the home wallpaper with a 4-column grid of the ported icons.
        pv = home.resize((720, 1600), Image.LANCZOS).convert("RGBA")
        size, gap = 120, 48
        x0 = (720 - 4 * size - 3 * gap) // 2
        for i, im in enumerate(shown[:20]):
            pv.alpha_composite(im.resize((size, size), Image.LANCZOS),
                               (x0 + (i % 4) * (size + gap), 380 + (i // 4) * (size + 70)))
        previews["preview_launcher_0.jpg"] = previews["preview_icons_0.jpg"] = jpg_bytes(pv, 88)
        previews["preview_icons_small_0.jpg"] = jpg_bytes(pv.resize((360, 800)), 88)
    if lock:
        previews["preview_lockscreen_0.jpg"] = jpg_bytes(lock.resize((720, 1600), Image.LANCZOS), 88)

    info = open(os.path.join(src, "themeInfo.xml"), encoding="utf-8-sig").read() \
        if os.path.exists(os.path.join(src, "themeInfo.xml")) else ""
    return {"info": info, "icons": out.getvalue(), "count": len(written), "unmapped": unmapped,
            "wallpaper": jpg_bytes(home) if home else None,
            "lockscreen": jpg_bytes(lock) if lock else None, "previews": previews}


def load_theme_file(src):
    theme = zipfile.ZipFile(src)
    info = theme.read("themeInfo.xml").decode("utf-8", "replace")
    launcher = nested(theme, "com.android.launcher") or nested(theme, "com.oppo.launcher")
    icons, count, unmapped = build_icons(theme, launcher)
    res = {"info": info, "icons": icons, "count": count, "unmapped": unmapped,
           "wallpaper": None, "lockscreen": None, "previews": {}}
    wall = nested(theme, "wallpaper")
    if wall:
        home = biggest(wall.namelist(), r"/oppo_default_wallpaper\.(png|jpg)$")
        lock = biggest(wall.namelist(), r"/oppo_default_keyguard_wallpaper\.(png|jpg)$") or home
        if home:
            res["wallpaper"] = to_jpg(wall.read(home))
        if lock:
            res["lockscreen"] = to_jpg(wall.read(lock))
    pics = theme.namelist()
    for colors_name, mi_names in (("preview_menu", ("preview_icons_0", "preview_launcher_0")),
                                  ("preview_lock", ("preview_lockscreen_0",)),
                                  ("thumbnail", ("preview_icons_small_0",))):
        pick = biggest(pics, rf"^picture/res/[^/]+/{colors_name}\.(png|jpg)$")
        if pick:
            data = to_jpg(theme.read(pick), 1600)
            for n in mi_names:
                res["previews"][f"{n}.jpg"] = data
    return res


def main():
    args = sys.argv[1:]
    data_dir = rights_dir = None
    if "--data" in args:
        i = args.index("--data")
        data_dir = args[i + 1]
        del args[i:i + 2]
    if "--rights-dir" in args:
        i = args.index("--rights-dir")
        rights_dir = args[i + 1]
        del args[i:i + 2]
    if len(args) not in (2, 3) or (data_dir and not rights_dir):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    src, dst = args[0], args[1]
    title = args[2] if len(args) == 3 else None

    res = load_uxicons(src) if os.path.isdir(src) else load_theme_file(src)
    info, parts, previews = res["info"], {"icons": res["icons"]}, res["previews"]
    for code in ("wallpaper", "lockscreen"):
        if res[code]:
            parts[code] = res[code]
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_STORED) as mtz:
        mtz.writestr("description.xml", description(info, title))
        mtz.writestr("icons", res["icons"])
        if "wallpaper" in parts:
            mtz.writestr("wallpaper/default_wallpaper.jpg", parts["wallpaper"])
        if "lockscreen" in parts:
            mtz.writestr("wallpaper/default_lock_wallpaper.jpg", parts["lockscreen"])
        for name, data in previews.items():
            mtz.writestr(f"preview/{name}", data)

    print(f"[coloros-theme] {res['count']} icons, mask + wallpapers + previews -> {dst}")
    if data_dir:
        m = re.search(r"<UUID>([^<]+)</UUID>", info)
        title_text = title or "ColorOS"
        author = (re.search(r"<Author>([^<]*)</Author>", info) or [None, "ColorOS"])[1] or "ColorOS"
        seed = (m.group(1) if m else os.path.abspath(src)) + ":" + title_text
        theme_id = write_data(data_dir, rights_dir, seed, title_text, author, parts, previews)
        print(f"[coloros-theme] store layout theme {theme_id} -> {data_dir}")
    if res["unmapped"]:
        print(f"[coloros-theme] no HyperOS counterpart, left out: {', '.join(sorted(set(res['unmapped'])))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
