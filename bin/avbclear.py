import argparse
import fileinput

# Create argument parser to accept character and file path as arguments
parser = argparse.ArgumentParser()
parser.add_argument("char_to_remove", help="Character to remove from the file")
parser.add_argument("file_path", help="Path to file to remove character from")
args = parser.parse_args()

# Open the file for editing
with fileinput.FileInput(args.file_path, inplace=True) as file:
    # Loop over each line in the file
    for line in file:
        # Replace all occurrences of the given character with an empty string
        print(line.replace(args.char_to_remove, ""), end="")