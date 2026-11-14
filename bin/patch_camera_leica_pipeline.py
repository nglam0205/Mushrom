#!/usr/bin/env python3
"""Wire the Leica colour-look node into a MiVi camera pipeline.

Usage: patch_camera_leica_pipeline.py <pipeline.json> [--before <NodeInstance>]

MiuiCamera only switches the Leica looks on in the UI; the Authentic/Vibrant
colour itself is rendered by the vendor algo node com.xiaomi.plugin.mileicafilter.
Non-Leica siblings ship the plugin's .so + tuning (dropped in by the per-device
module) but leave the node out of the pipelines, so the look never runs.

Each pipeline carries the node just before one consumer node. --before names
that node (the downstream anchor, taken from a real Leica device); without it
the node is placed before the generic com.xiaomi.plugin.filter node. The input
link into the anchor is rerouted  SRC -> leica -> anchor. Idempotent.

Pipeline configs are JSONC (// and /* */ comments, trailing commas); they are
parsed leniently and rewritten as plain JSON, which the vendor parser accepts.
"""
import json
import sys
from pathlib import Path

FILTER_NODE = "com.xiaomi.plugin.filter"
LEICA_NODE = "com.xiaomi.plugin.mileicafilter"
LEICA_INST = "LeicaFilterInstance0"


def strip_jsonc(text):
    """Drop // and /* */ comments and trailing commas, respecting strings."""
    out = []
    i, n = 0, len(text)
    in_str = esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        else:
            out.append(c)
        i += 1
    # trailing commas: a comma followed only by whitespace then ] or }
    res, i, n = [], 0, len("".join(out))
    s = "".join(out)
    while i < n:
        if s[i] == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "]}":
                i += 1
                continue
        res.append(s[i])
        i += 1
    return "".join(res)


def main():
    args = sys.argv[1:]
    before = None
    in_format = None
    if "--before" in args:
        k = args.index("--before")
        before = args[k + 1]
        del args[k : k + 2]
    if "--in-format" in args:
        k = args.index("--in-format")
        in_format = args[k + 1]
        del args[k : k + 2]
    remove = "--remove" in args
    if remove:
        args.remove("--remove")
    if len(args) != 1 or not Path(args[0]).is_file():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    path = Path(args[0])
    data = json.loads(strip_jsonc(path.read_text(encoding="utf-8", errors="surrogateescape")))

    nodes = data.get("NodesList", {}).get("Node")
    links = data.get("PortLinkages", {}).get("Link")
    if not isinstance(nodes, list) or not isinstance(links, list):
        print(f"[leica-pipe] {path.name}: unexpected shape; skip")
        return 2

    has_node = any(n.get("NodeName") == LEICA_NODE for n in nodes)

    if remove:
        if not has_node:
            print(f"[leica-pipe] {path.name}: no node to remove; skip")
            return 0
        feed = next((l for l in links if l.get("DstPort", {}).get("NodeInstance") == LEICA_INST), None)
        bridge = next((l for l in links if l.get("SrcPort", {}).get("NodeInstance") == LEICA_INST), None)
        if feed and bridge:
            feed["DstPort"] = dict(bridge["DstPort"])  # restitch SRC -> original anchor
            links.remove(bridge)
        data["NodesList"]["Node"] = [n for n in nodes if n.get("NodeName") != LEICA_NODE]
        path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
        print(f"[leica-pipe] {path.name}: removed {LEICA_NODE}")
        return 0

    if has_node:
        print(f"[leica-pipe] {path.name}: already present; skip")
        return 0

    anchor = before
    if anchor is None:
        anchor = next(
            (n.get("NodeInstance") for n in nodes if n.get("NodeName") == FILTER_NODE),
            None,
        )
        if not anchor:
            print(f"[leica-pipe] {path.name}: no {FILTER_NODE} node and no --before; skip")
            return 2

    feed = next(
        (
            l
            for l in links
            if l.get("DstPort", {}).get("NodeInstance") == anchor
            and l.get("DstPort", {}).get("PortId", 0) == 0
        ),
        None,
    )
    if feed is None:
        print(f"[leica-pipe] {path.name}: no input link into {anchor}; skip")
        return 2

    nodes.append({"NodeName": LEICA_NODE, "NodeInstance": LEICA_INST})

    # Reroute  SRC -> anchor  into  SRC -> leica -> anchor. The node runs on a
    # Gralloc buffer; where the downstream node does not pin the pixel format
    # (virtual/split nodes, i.e. the preview paths) the INPUT port must carry it
    # explicitly via --in-format, or the LUT misreads the colour layout and
    # tints the frame (green cast in preview). Snapshot paths omit it and let
    # the filter node negotiate, matching a real Leica device.
    orig_dst = dict(feed["DstPort"])
    port_id = orig_dst.get("PortId", 0)
    leica_in = {"PortId": port_id, "NodeInstance": LEICA_INST, "BufferType": "Gralloc"}
    if in_format:
        leica_in["PortFormat"] = in_format
    leica_out = {"PortId": port_id, "NodeInstance": LEICA_INST, "BufferType": "Gralloc"}
    feed["DstPort"] = leica_in
    links.insert(links.index(feed) + 1, {"SrcPort": leica_out, "DstPort": orig_dst})

    path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    print(f"[leica-pipe] {path.name}: inserted {LEICA_NODE} before {anchor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
