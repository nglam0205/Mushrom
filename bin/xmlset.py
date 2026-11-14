import xml.etree.ElementTree as ET
import argparse

# Định nghĩa đối số dòng lệnh
parser = argparse.ArgumentParser()
parser.add_argument('file', type=str, help='name of xml file')
parser.add_argument('type', type=str, help='type data')
parser.add_argument('name', type=str, help='name of the bool element')
parser.add_argument('value', type=str, help='new value of the bool element')
args = parser.parse_args()

# Đọc file XML và phân tích nội dung
tree = ET.parse(args.file)
root = tree.getroot()
found = False

for elem in root.iter():
    
    if elem.tag == args.type and 'name' in elem.attrib:
        if elem.attrib['name'] == args.name:            
                elem.text = args.value
                found = True

if not found:
    new_elem = ET.Element(args.type, {'name': args.name})
    new_elem.text = args.value
    root.append(new_elem)

tree.write(args.file)

