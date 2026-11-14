import sys
import fileinput
import re

def getboolean(file_path, field_type, field_value):
    pattern = fr'iget-boolean (\w+), (\w+), {field_type}'
    replacement = fr'const/4 \1, 0x{field_value}'

    with fileinput.FileInput(file_path, inplace=True) as file:
        for line in file:
            line = re.sub(pattern, replacement, line)
            sys.stdout.write(line)

def putboolean(file_path, field_type, field_value):
    search_pattern = fr'iput-boolean (\w+), (\w+), {field_type}'
    insert_line = fr'const \1, 0x{field_value}'

    with open(file_path, "r") as file:
        content = file.read()

    match = re.search(search_pattern, content)
    if match:
        p1 = match.group(1)
        modified_line = insert_line.format(p1=p1)
        modified_content = re.sub(search_pattern, modified_line + "\n" + match.group(), content)
        with open(file_path, "w") as file:
            file.write(modified_content)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Missing parameters")
    else:
        func = sys.argv[1]
        file_path = sys.argv[2]
        field_type = sys.argv[3]
        field_value = sys.argv[4]
        
        if func == "get":
            getboolean(file_path, field_type, field_value)
        if func == "put" :
            putboolean(file_path, field_type, field_value)