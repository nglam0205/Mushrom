import argparse

parser = argparse.ArgumentParser()
parser.add_argument('filename', help='Tên file')
parser.add_argument('search_string', help='Chuỗi tìm kiếm')
parser.add_argument('replace', help='string thay thees')
args = parser.parse_args()

with open(args.filename, 'r') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if args.search_string in line:
        continue
    else:
        if "/" in lines[i]:
            lines[i] = line.rstrip() + ' u:object_r:' + args.replace + '_file:s0' + '\n'


with open(args.filename, 'w') as f:
    f.writelines(lines)