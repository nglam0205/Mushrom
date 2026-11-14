from configobj import ConfigObj

def lineNumByPhrase(phrase, filePath, start=0, limit=None):
    """
    Find the line number of the phrase in the file starting from a specific line.
    """
    with open(filePath, 'r') as f:
        lines = f.readlines()
        for i in range(start, len(lines) if limit is None else limit):
            if phrase in lines[i]:
                return i
    return -1

def fileReplaceRange(filePath, start, end, addWith):
    """
    Replace lines in a file from start to end with the specified content above lines containing 'return-'.
    """
    with open(filePath, 'r') as f:
        lines = f.readlines()

    modified_lines = []
    
    for i in range(start, end + 1):
        if "return" in lines[i]:
            if "return-void" not in lines[i]:
                modified_lines.append(addWith.replace("this_is_temp_variable", lines[i].split(" ")[-1].strip()) + "\n")
            else:
                modified_lines.append(addWith + "\n")
        
        modified_lines.append(lines[i])
 
    with open(filePath, 'w') as f:
        f.writelines(lines[:start] + modified_lines + lines[end + 1:])


def processFile(configPath, filePath):
    """
    Main function to process the file based on the ConfigObj settings.
    """
    ini = ConfigObj(configPath)
    config = ini['main']

    phraseStart = config['phraseStart']
    phraseEnd = ".end method"
    addWith = config['addWith']

    temp = 0
    while True:
        startIndex = lineNumByPhrase(phraseStart, filePath, temp) + 1
        if startIndex == 0:
            break

        endIndex = lineNumByPhrase(phraseEnd, filePath, startIndex) - 1
        if endIndex < 0:
            break

        fileReplaceRange(filePath, startIndex, endIndex, addWith)  # Pass the patch_only_last parameter
        temp = endIndex + 1

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python script.py <config_file> <target_file>")
        sys.exit(1)

    config_file = sys.argv[1]
    target_file = sys.argv[2]

    processFile(config_file, target_file)
