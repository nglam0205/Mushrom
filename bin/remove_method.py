import sys
from configobj import ConfigObj

# Functions
# Author : @trungmmmmt

def Linecounter(phrase, source, isText=0, startsAt=0):
	result = []
	if isText:
		if isText == 1 or isText == True:
			source = source.splitlines()
		for (i, line) in enumerate(source):
			if i >= startsAt: 
				if phrase in line :
					result.append(i)
		return result
	else:
		with open(source, 'r') as f:
			return Linecounter(phrase, f, 2, startsAt)
	return False

def lineNumByPhrase(phrase, source, isText=0, startsAt=0):
	if isText:
		if isText == 1 or isText == True:
			source = source.splitlines()
		for (i, line) in enumerate(source):
			if i >= startsAt: 
				if phrase in line :
					return i
	else:
		with open(source, 'r') as f:
			return lineNumByPhrase(phrase, f, 2, startsAt)
	return False


def fileReplaceRange(filename, startIndex, endIndex, content):
	if startIndex:
		lines = []
		with open(filename, 'r') as f:
			lines = f.readlines()

		with open(filename, 'w') as f:
			wrote = False
			for i, line in enumerate(lines):
				if i not in range(startIndex, endIndex + 1):
					f.write(line)
				else:
					if not wrote:
						f.write(content + '\n')
						wrote = True

def count_parameters(method_signature):
    parameter_list = method_signature.split('(')[1].split(')')[0].split(';')
    parameter_list = list(filter(lambda x: x != '', parameter_list))
    parameter_count = len(parameter_list)
    return parameter_count


def process_input(func,input_value):
    if isinstance(input_value, int):
        hex_value = hex(input_value)
        return func.replace("X1", str(input_value))

def print_line(file_path, line_number):
    with open(file_path, 'r') as file:
        lines = file.readlines()
        if line_number <= len(lines):
            line = lines[line_number - 1]
            return line.strip()

# Main script
# Loading configs



replaceFile = str(sys.argv[2])
method_name = str(sys.argv[1])

# Nếu method_name đã có ( hoặc ) thì không thêm "(" nữa
if "(" in method_name or ")" in method_name:
    phraseStart = " " + method_name
else:
    phraseStart = " " + method_name + "("

phraseEnd = '.end method'
replaceWith = '\n'

if len(sys.argv) - 1 > 0:
	counter = Linecounter(phraseStart, replaceFile)
	temp=0
	for linez in counter:
		startIndex = lineNumByPhrase(phraseStart, replaceFile, 0, temp) 
		endIndex = lineNumByPhrase(phraseEnd, replaceFile, 0, (startIndex -1 ))

		fileReplaceRange(replaceFile, startIndex, endIndex, replaceWith)
		temp = startIndex
	
 
