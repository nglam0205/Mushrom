#!/usr/bin/env python3
"""Port ColorOS live wallpapers to a HyperOS wallpaper group.

Usage: coloros_live_wallpaper.py <decouping_wallpaper dir> <out group dir> <title> [subtitle]

ColorOS keeps them in decouping_wallpaper/default/liveWallpaper/: mix_wallpaper_config.json
pairs each video (dark locked frame -> lit home frame, played on unlock) with its still in
common/. HyperOS lists groups from /system/media/wallpaper/wallpaper_group/<dir>/
wallpaper_config.json; a "videowallpaper" entry with allKeyFrame=1 is played forward and
backward on unlock and lock, so the video is re-encoded the way the stock ones are:
every frame an I frame, HEVC Main 10 (not the Main 10 Intra profile x265 picks for keyint=1).
"""
import hashlib
import json
import os
import subprocess
import sys

from PIL import Image

SYSTEM_GROUPS = "/system/media/wallpaper/wallpaper_group"
CRF = 16


def encode(src, dst):
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", src, "-an",
        "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
        # keyint stays large so x265 signals Main 10; ffmpeg forces each frame to a keyframe.
        "-force_key_frames", "expr:1",
        "-x265-params", f"profile=main10:keyint=250:min-keyint=1:bframes=0:scenecut=0:crf={CRF}:"
                        "log-level=error:colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-movflags", "+faststart", dst], check=True)
    types = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pict_type",
        "-of", "csv=p=0", dst], check=True, capture_output=True, text=True).stdout.replace(",", " ").split()
    if set(types) != {"I"}:
        raise SystemExit(f"{dst}: not all-intra ({sorted(set(types))})")


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if len(sys.argv) not in (4, 5):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    root, out, title = sys.argv[1:4]
    subtitle = sys.argv[4] if len(sys.argv) == 5 else None
    live = os.path.join(root, "default", "liveWallpaper")
    config = json.load(open(os.path.join(live, "mix_wallpaper_config.json")))
    group = os.path.basename(os.path.normpath(out))
    os.makedirs(out, exist_ok=True)

    entries = []
    for item in config["wallpapers"]:
        video = os.path.join(live, item["liveName"])
        still = next((p for p in (os.path.join(root, "common", item["staticName"]),
                                  os.path.join(root, "default", item["staticName"]))
                      if os.path.exists(p)), None)
        if not os.path.exists(video) or not still:
            print(f"[live-wallpaper] skip {item}: missing file")
            continue
        name = os.path.splitext(item["liveName"])[0].lower()
        mp4, jpg = f"{name}.mp4", f"{name}.jpg"
        encode(video, os.path.join(out, mp4))
        Image.open(still).convert("RGB").save(os.path.join(out, jpg), "JPEG", quality=95)
        base = f"{SYSTEM_GROUPS}/{group}"
        entries.append({
            "thumbPath": f"{base}/{jpg}",
            "allKeyFrame": 1,
            "previewPath": f"{base}/{jpg}",
            "originPath": f"{base}/{jpg}",
            "videoPath": f"{base}/{mp4}",
            "sha1": sha1(os.path.join(out, mp4)),
            "videoType": 1,
            "type": "videowallpaper",
        })
        print(f"[live-wallpaper] {item['liveName']} -> {mp4} "
              f"({os.path.getsize(os.path.join(out, mp4)) // 1024} KiB)")

    cfg = {"group": title, "title": title}
    if subtitle:
        cfg["subtitle"] = subtitle
    cfg.update({"cardType": 1, "count": len(entries), "slideCount": len(entries),
                "randomOrder": False, "wallpapers": entries})
    with open(os.path.join(out, "wallpaper_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return 0 if entries else 1


if __name__ == "__main__":
    sys.exit(main())
