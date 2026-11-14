import argparse

def extract_functions_containing_strings(file_path, target_strings):
    functions_with_strings = []

    with open(file_path, 'r') as file:
        lines = file.readlines()
        current_function = None
        inside_function = False
        function_lines = []

        for line in lines:
            if line.startswith('.method'):
                current_function = line.strip()
                inside_function = True
                function_lines = []

            if inside_function:
                function_lines.append(line.strip())
                if line.startswith('.end method'):
                    inside_function = False
                    function_body = ' '.join(function_lines)
                    if all(target_string in function_body for target_string in target_strings):
                        functions_with_strings.append(current_function)
                    function_lines = []

    return functions_with_strings

def main():
    parser = argparse.ArgumentParser(description='Extract functions containing specific strings in a Smali file')
    parser.add_argument('file_path', help='Path to the Smali file')
    parser.add_argument('target_strings', nargs='+', help='Strings to search for in the functions')

    args = parser.parse_args()
    smali_file_path = args.file_path
    target_strings = args.target_strings

    functions_with_strings = extract_functions_containing_strings(smali_file_path, target_strings)
    if functions_with_strings:
        for function in functions_with_strings:
            print(function.split()[-1])

if __name__ == "__main__":
    main()
