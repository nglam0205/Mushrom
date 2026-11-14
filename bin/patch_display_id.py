import xml.etree.ElementTree as ET
import sys

def adjust_brightness_map(input_file):
    tree = ET.parse(input_file)
    root = tree.getroot()

    brightness_map = root.find(".//screenBrightnessMap")
    if brightness_map is None:
        print("<screenBrightnessMap> not found in the XML file.")
        return

    points = brightness_map.findall("point")
    if not points:
        print("No <point> elements found under <screenBrightnessMap>.")
        return

    min_value = float(points[0].find("value").text)
    max_value = float(points[-1].find("value").text)

    value_nits_array = []

    for point in points:
        value = float(point.find("value").text)
        proportion = (value - min_value) / (max_value - min_value)
        new_nits = round(2 + proportion * (3000 - 2))
        point.find("nits").text = f"{new_nits}.0"
        value_nits_array.append(f"[{value}:{new_nits}.0]")

    tree.write(input_file, encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python script.py <input_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    adjust_brightness_map(input_file)
    print("Done")
