#!/usr/bin/env python3

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


THERMAL_FPS_PROPERTY = "ro.vendor.fps.switch.thermal"
THERMAL_FPS_FEATURE = "thermal_ninety_fps_thermal_limit_enabled"


def resolve_image_root(path: Path) -> Path:
    path = path.resolve()
    if path.name == "odm":
        return path.parent
    return path



def write_xml(tree: ET.ElementTree, xml_file: Path) -> None:
    ET.indent(tree, space="    ")
    tree.write(xml_file, encoding="utf-8", xml_declaration=True)


def patch_thermal_fps_map(xml_file: Path) -> bool:
    if not xml_file.is_file():
        return False

    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    tree = ET.parse(xml_file, parser=parser)
    changed = False

    for device in tree.getroot().findall(".//Device"):
        mappings = device.findall("./ThermalLevelMap")
        supported_fps = [
            int(mapping.get("fps", "0"))
            for mapping in mappings
            if mapping.get("fps", "0").isdigit()
        ]
        if not supported_fps:
            continue

        maximum_fps = str(max(supported_fps))
        for mapping in mappings:
            if mapping.get("fps") != maximum_fps:
                mapping.set("fps", maximum_fps)
                changed = True

    if changed:
        write_xml(tree, xml_file)
    return changed


def patch_cust_feature(xml_file: Path) -> bool:
    if not xml_file.is_file():
        return False

    tree = ET.parse(xml_file)
    feature = tree.getroot().find(f".//bool[@name='{THERMAL_FPS_FEATURE}']")
    if feature is None or (feature.text or "").strip().lower() == "false":
        return False

    feature.text = "false"
    write_xml(tree, xml_file)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Disable display refresh-rate thermal throttling in an extracted ROM."
    )
    parser.add_argument("image_root", type=Path, help="img_temp directory or its odm directory")
    args = parser.parse_args()

    image_root = resolve_image_root(args.image_root)

    def display_path(path: Path) -> str:
        try:
            return str(path.relative_to(image_root))
        except ValueError:
            return str(path)

    targets = (
        (
            image_root / "vendor/etc/display/thermallevel_to_fps.xml",
            patch_thermal_fps_map,
        ),
        (
            image_root / "mi_ext/product/etc/cust_features/device_features.xml",
            patch_cust_feature,
        ),
    )

    found = False
    for target, patcher in targets:
        if not target.is_file():
            continue
        found = True
        changed = patcher(target)
        status = "[DONE]" if changed else "[SKIP]"
        message = "patched" if changed else "unchanged"
        print(f"{status:<7} ThermalRefresh - {target.name}: {message} {display_path(target)}")

    if not found:
        print(f"[SKIP]  ThermalRefresh - Config: no supported config found under {image_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
