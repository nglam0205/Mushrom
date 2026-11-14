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
                if phrase in line:
                    result.append(i)
        return result
    else:
        with open(source, "r") as f:
            return Linecounter(phrase, f, 2, startsAt)
    return False


def lineNumByPhrase(phrase, source, isText=0, startsAt=0):
    if isText:
        if isText == 1 or isText == True:
            source = source.splitlines()
        for (i, line) in enumerate(source):
            if i >= startsAt:
                if phrase in line:
                    return i
    else:
        with open(source, "r") as f:
            return lineNumByPhrase(phrase, f, 2, startsAt)
    return False


def fileReplaceRange(filename, startIndex, endIndex, content):
    if startIndex:
        lines = []
        with open(filename, "r") as f:
            lines = f.readlines()

        with open(filename, "w") as f:
            wrote = False
            for i, line in enumerate(lines):
                if i not in range(startIndex, endIndex + 1):
                    f.write(line)
                else:
                    if not wrote:
                        f.write(content + "\n")
                        wrote = True


def count_parameters(method_signature):
    parameter_list = method_signature.split("(")[1].split(")")[0].split(";")
    parameter_list = list(filter(lambda x: x != "", parameter_list))
    parameter_count = len(parameter_list)
    return parameter_count


def process_input(func, input_value):
    if isinstance(input_value, int):
        hex_value = hex(input_value)
        return func.replace("X1", str(input_value))


def print_line(file_path, line_number):
    with open(file_path, "r") as file:
        lines = file.readlines()
        if line_number <= len(lines):
            line = lines[line_number - 1]
            return line.strip()


def is_digit(s):
    if s.isdigit() or (s[0] == "-" and s[1:].isdigit()):
        return True
    else:
        return False


def format_value(input_str, get_global):
    if is_digit(input_str):
        num = int(input_str)
        formatted_str = f"""
    .locals 1

    const/16 v0, {hex(num)}

    return v0
"""
    elif input_str == "void":
        formatted_str = f"""
    .locals 1

    return-void
"""
    elif input_str == "nullz":
        formatted_str = f"""
    .locals 1

    const/4 v0, 0x0

    return-object v0
"""

    elif input_str == "global":
        formatted_str = f"""
    .locals 1

    const-string/jumbo v0, "{get_global}"

    invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

    move-result v0

    return v0
"""
    elif input_str == "globalz":
        formatted_str = f"""
    .locals 1

    const-string/jumbo v0, "{get_global}"

    invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobalZ(Ljava/lang/String;)Z

    move-result v0

    return v0
"""

    elif input_str == "float":
        formatted_str = f"""
    .locals 1

    invoke-static {{}}, Lcom/android/vsteam/Toolbox;->{get_global}()F

    move-result v0

    return v0
"""
    elif input_str == "string":
        formatted_str = f"""
    .locals 1

    invoke-static {{}}, Lcom/android/vsteam/Toolbox;->{get_global}()Ljava/lang/String;

    move-result-object v0

    return-object v0
"""

    elif input_str == "text":
        formatted_str = f"""
    .locals 1

    
    const-string/jumbo v0, "{get_global}"

    return-object v0
"""

    elif input_str == "textz":
        formatted_str = f"""
    .locals 1

    
    const-string/jumbo v0, ""

    return-object v0
"""

    elif input_str == "true":
        formatted_str = f"""
    .locals 1

    const/4 v0, 0x1

    invoke-static {{v0}}, Ljava/lang/Boolean;->valueOf(Z)Ljava/lang/Boolean;

    move-result-object v0

    return-object v0
"""
    elif input_str == "false":
        formatted_str = f"""
    .locals 1

    const/4 v0, 0x0

    invoke-static {{v0}}, Ljava/lang/Boolean;->valueOf(Z)Ljava/lang/Boolean;

    move-result-object v0

    return-object v0
"""
    elif input_str == "audio_or_gps":
        formatted_str = f"""
    .locals 1

    iget v0, p1, Lcom/android/server/am/ProcessRecord;->uid:I

    invoke-virtual {{p0, v0}}, Lcom/android/server/am/ProcessCleanerBase;->isAudioOrGPSApp(I)Z

    move-result v0

    if-eqz v0, :cond_resource_inactive

    const/4 v0, 0x1

    return v0

    :cond_resource_inactive
    invoke-virtual {{p4, p1, p2, p3}}, Lcom/android/server/am/ProcessManagerService;->isInWhiteList(Lcom/android/server/am/ProcessRecord;II)Z

    move-result v0

    return v0
"""
    elif input_str == "keep_audio_task":
        formatted_str = f"""
    .locals 4

    if-nez p0, :cond_check_audio_task

    return-void

    :cond_check_audio_task
    iget v0, p0, Lcom/android/server/wm/Task;->effectiveUid:I

    invoke-static {{}}, Lcom/android/server/am/SystemPressureController;->getInstance()Lcom/android/server/am/SystemPressureController;

    move-result-object v1

    invoke-virtual {{v1, v0}}, Lcom/android/server/am/SystemPressureController;->isAudioOrGPSApp(I)Z

    move-result v0

    if-eqz v0, :cond_remove_task

    return-void

    :cond_remove_task
    iget-object v0, p1, Lcom/android/server/wm/ActivityTaskManagerService;->mTaskSupervisor:Lcom/android/server/wm/ActivityTaskSupervisor;

    const/4 v1, 0x1

    const-string/jumbo v2, "removeTaskLocked"

    const/4 v3, 0x0

    invoke-virtual {{v0, p0, v3, v1, v2}}, Lcom/android/server/wm/ActivityTaskSupervisor;->removeTask(Lcom/android/server/wm/Task;ZZLjava/lang/String;)V

    return-void
"""
    else:
        return 0

    return formatted_str


try:
    # Bash splits unquoted args on spaces; join argv[4:] to restore full value string.
    if len(sys.argv) > 4:
        temp = " ".join(sys.argv[4:])
    else:
        temp = " "
    if not temp.strip():
        temp = " "
except Exception:
    temp = " "

replaceWith = format_value(sys.argv[2], temp)
replaceFile = str(sys.argv[3])

if not isinstance(replaceWith, str):
    print(f"Unsupported repM replacement mode: {sys.argv[2]}", file=sys.stderr)
    sys.exit(1)

if "(" in sys.argv[1]:
    phraseStart = " " + str(sys.argv[1])
else:
    phraseStart = " " + str(sys.argv[1]) + "("

phraseEnd = ".end method"

if len(sys.argv) - 1 > 0:
    counter = Linecounter(phraseStart, replaceFile)
    temp = 0
    for linez in counter:
        startIndex = lineNumByPhrase(phraseStart, replaceFile, 0, temp) + 1
        endIndex = lineNumByPhrase(phraseEnd, replaceFile, 0, (startIndex - 1)) - 1

        fileReplaceRange(replaceFile, startIndex, endIndex, replaceWith)
        temp = startIndex
