import sys
from configobj import ConfigObj

# Functions
# Author: @trungmmmmt

def line_num_by_phrase(phrase, source, is_text=False, starts_at=0):
    """Finds the line number of the first occurrence of a phrase in the source."""
    if is_text:
        if isinstance(source, str):
            source = source.splitlines()
        for i, line in enumerate(source):
            if i >= starts_at and phrase in line:
                return i
    else:
        with open(source, 'r') as f:
            return line_num_by_phrase(phrase, f, True, starts_at)
    return False

def file_replace_range(filename, start_index, end_index, content):
    """Replaces lines in a file within a specified range with new content."""
    if start_index is not False and end_index is not False:
        with open(filename, 'r') as f:
            lines = f.readlines()

        with open(filename, 'w') as f:
            wrote = False
            for i, line in enumerate(lines):
                if i not in range(start_index, end_index + 1):
                    f.write(line)
                elif not wrote:
                    f.write(content + '\n')
                    wrote = True

def file_add_before_phrase_in_range(filename, start_index, end_index, phrase_end, content):
    """Adds content in a file before the line containing the phrase_end within the specified range."""
    if start_index is not False and end_index is not False:
        with open(filename, 'r') as f:
            lines = f.readlines() 

        with open(filename, 'w') as f:
            for i, line in enumerate(lines):
                if i in range(start_index, end_index + 1) and phrase_end in line:
                    f.write(content + '\n')
                f.write(line)

def file_add_before_phrase(filename, phrase, content):
    """Adds content in a file before the line containing the specified phrase."""
    with open(filename, 'r') as f:
        lines = f.readlines()

    with open(filename, 'w') as f:
        for line in lines:
            if phrase in line:
                f.write(content + '\n')
            f.write(line)

def file_add_after_phrase(filename, phrase, content):
    """Adds content in a file after the line containing the specified phrase."""
    with open(filename, 'r') as f:
        lines = f.readlines()

    with open(filename, 'w') as f:
        for line in lines:
            f.write(line)
            if phrase in line:
                f.write(content + '\n')

def file_add_after_phrase_in_range(filename, start_index, end_index, phrase_start, content):
    """Adds content in a file after the line containing the phrase_start within the specified range."""
    if start_index is not False and end_index is not False:
        with open(filename, 'r') as f:
            lines = f.readlines()

        with open(filename, 'w') as f:
            for i, line in enumerate(lines):
                f.write(line)
                if i in range(start_index, end_index + 1) and phrase_start in line:
                    f.write(content + '\n')

# Main script
# Loading configs

if len(sys.argv) > 3:
    config_file = str(sys.argv[1])
    replace_file = str(sys.argv[2])
    operation = str(sys.argv[3])  # 'replace', 'add', 'add_before', 'add_after', or 'add_after_in_range'

    ini = ConfigObj(config_file)
    config = ini['main']
    phrase_start = config['phraseStart']
    phrase_end = config['phraseEnd']
    replace_with = config['replaceWith']

    start_index = line_num_by_phrase(phrase_start, replace_file)
    end_index = line_num_by_phrase(phrase_end, replace_file, starts_at=start_index)
    
    if operation == 'replace':
        file_replace_range(replace_file, start_index, end_index, replace_with)
    elif operation == 'add':
        file_add_before_phrase_in_range(replace_file, start_index, end_index, phrase_end, replace_with)
    elif operation == 'add_before':
        file_add_before_phrase(replace_file, phrase_end, replace_with)
    elif operation == 'add_after':
        file_add_after_phrase(replace_file, phrase_end, replace_with)
    elif operation == 'add_after_in_range':
        file_add_after_phrase_in_range(replace_file, start_index, end_index, phrase_start, replace_with)
    else:
        print("Invalid operation. Use 'replace', 'add', 'add_before', 'add_after', or 'add_after_in_range'.")
else:
    print("Usage: python script.py <config_file> <replace_file> <operation>")
    print("operation: 'replace' to replace range, 'add' to add content before phrase_end in range, 'add_before' to add content before phrase_end, 'add_after' to add content after phrase_end, 'add_after_in_range' to add content after phrase_start in range")
