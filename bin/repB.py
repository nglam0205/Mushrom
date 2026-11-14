import sys
from configobj import ConfigObj
import random
import string
import re

def generate_random_lowercase(length=8):
    characters = string.ascii_lowercase  # Chỉ bao gồm chữ cái thường
    return ''.join(random.choices(characters, k=length))

def line_num_by_phrase(phrase, source, is_text=False, starts_at=0):
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


def add(filename, start_index, end_index, content, bump_registers=True):
    """
    Adds content in a file after the end_index and increments the number 
    in .locals or .registers if present in the specified range.
    """
    if start_index is not False and end_index is not False:
        with open(filename, 'r') as f:
            lines = f.readlines()

        with open(filename, 'w') as f:
            for i, line in enumerate(lines):
                if i == end_index and bump_registers:
                    # Check for .locals or .registers and increment the number
                    match = re.search(r"(    \.(locals|registers)) (\d+)", line)
                    if match:
                        directive = match.group(1)  # .locals or .registers
                        number = int(match.group(3)) + 1
                        # Replace line with updated number
                        line = f"{directive} {number}\n"
                
                f.write(line)
                
                # Add new content after the end_index
                if i == end_index:
                    f.write(content + '\n')


def render(var, val):

    random = generate_random_lowercase()


    template = f"""
    const-string v0, "{var}"

    invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

    move-result v0

    if-eqz v0, :{random}

    const/4 v0, 0x{val}

    return v0

    :{random}
"""

    if val == "void":
        template = f"""
        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        return-void

        :{random}
"""

    if val == "voidz":
        template = f"""
        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobalZ(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        return-void

        :{random}
"""

    if val == "sig":
        template = f"""
        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->corepatchBypass(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        const/4 v0, 0x1

        return v0

        :{random}
"""

    if val == "sig0":
        template = f"""
        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->corepatchBypass(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        const/4 v0, 0x0

        return v0

        :{random}
"""

    if val == "sigvoid":
        template = f"""
        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->corepatchBypass(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        return-void

        :{random}
"""

    if val == "clayout":
        template = f"""
        const-string v0, "vs_os2_custom_layout"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        const/16 p2, 0xb

        invoke-static {{}}, Lcom/android/vsteam/Toolbox;->os2_custom_layout()Ljava/lang/Object;

        move-result-object p3

        :{random}

    """

    if val == "clayout_com":
        template = f"""
        const-string/jumbo v0, "vs_os2_custom_layout"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getIntGlobal(Ljava/lang/String;)I

        move-result v0

        return v0
        
        :{random}

    """


    if val == "getCellWidth":
        template = f"""
        const-string/jumbo v0, "vs_os2_custom_layout"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        iget-object p0, p0, Lcom/miui/home/launcher/GridConfig;->mGridSizeCalRules:Lcom/miui/home/launcher/compat/GridSizeCalRules;

        invoke-virtual {{p0}}, Lcom/miui/home/launcher/compat/GridSizeCalRules;->getCellWidth()I

        move-result p0

        return p0
        
        :{random}

    """


    if val == "getCellHeight":
        template = f"""
        const-string/jumbo v0, "vs_os2_custom_layout"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}
        
        iget-object p0, p0, Lcom/miui/home/launcher/GridConfig;->mGridSizeCalRules:Lcom/miui/home/launcher/compat/GridSizeCalRules;

        invoke-virtual {{p0}}, Lcom/miui/home/launcher/compat/GridSizeCalRules;->getCellHeight()I

        move-result p0

        return p0
        
        :{random}

    """


    if val == "calGridSize":
        template = f"""
        const-string/jumbo v0, "vs_os2_custom_layout"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}
        
        iget-object p0, p0, Lcom/miui/home/launcher/GridConfig;->mGridSizeCalRules:Lcom/miui/home/launcher/compat/GridSizeCalRules;

        invoke-virtual {{p0}}, Lcom/miui/home/launcher/compat/GridSizeCalRules;->getCellHeight()I

        move-result p0

        return v0
        
        :{random}

    """

    if val == "getIndicatorHeight":
        template = f"""
        const-string/jumbo v0, "vs_layout_hide_hotseat"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}
        
        const/4 v0, 0x0

        return v0
        
        :{random}

    """


    if val == "getHotseatMarginBottom":
        template = f"""
        const-string/jumbo v0, "vs_layout_hide_hotseat"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}
        
        iget-object p0, p0, Lcom/miui/home/launcher/GridConfig;->mGridSizeCalRules:Lcom/miui/home/launcher/compat/GridSizeCalRules;

        invoke-virtual {{p0}}, Lcom/miui/home/launcher/compat/GridSizeCalRules;->getHotseatMarginBottom_VS()I

        move-result p0

        return v0
        
        :{random}

    """

    if val == "getWorkspaceIndicatorMarginBottom":
        template = f"""
        const-string/jumbo v0, "vs_layout_hide_hotseat"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}
        
        iget-object p0, p0, Lcom/miui/home/launcher/GridConfig;->mGridSizeCalRules:Lcom/miui/home/launcher/compat/GridSizeCalRules;

        invoke-virtual {{p0}}, Lcom/miui/home/launcher/compat/GridSizeCalRules;->getWorkspaceIndicatorMarginBottom_VS()I

        move-result p0

        return v0
        
        :{random}

    """
    if val == "action_void":
        template = f"""
        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getStrGlobal(Ljava/lang/String;)Ljava/lang/String;

        move-result-object v2

        if-eqz v2, :{random}

        invoke-static {{v2}}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z

        move-result v3

        if-nez v3, :{random}

        invoke-static {{v2, v0}}, Lcom/android/vsteam/Toolbox;->Handle_VS_Action(Ljava/lang/String;Ljava/lang/String;)V

        return-void

        :{random}

    """

    if val == "action_true":
        template = f"""
        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getStrGlobal(Ljava/lang/String;)Ljava/lang/String;

        move-result-object v2

        if-eqz v2, :{random}

        invoke-static {{v2}}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z

        move-result v3

        if-nez v3, :{random}

        invoke-static {{v2, v0}}, Lcom/android/vsteam/Toolbox;->Handle_VS_Action(Ljava/lang/String;Ljava/lang/String;)V

        const/4 v1, 0x1

        return v1

        :{random}

    """
    if val == "gallery":
        template = f"""


        invoke-static {{p0}}, Lcom/android/vsteam/Toolbox;->galleryhooking(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        const/4 v0, 0x1

        return v0

        :{random}
    """

    if val == "shortcut":
        template = f"""

        const-string v0, "{var}"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getStrGlobal(Ljava/lang/String;)Ljava/lang/String;

        move-result-object v0

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->Handle_VS_Action_Boolean(Ljava/lang/String;)Z

        move-result v0

        if-eqz v0, :{random}

        return-void

        :{random}
    """

    if val == "spurcekey":
        template = f"""

        invoke-static {{p1}}, Lcom/android/vsteam/Toolbox;->Handle_VS_Action_ColorOS(Landroid/view/KeyEvent;)Z

        move-result v0

        if-eqz v0, :{random}

        return-void

        :{random}
    """



    if val == "maxrf":
        template = f"""
        const-string/jumbo v0, "vs_max_rf_define"

        invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getFloatGlobalOrZero(Ljava/lang/String;)F

        move-result v0

        const/4 v1, 0x0

        cmpl-float v1, v0, v1

        if-eqz v1, :{random}

        return v0

        :{random}

    """



    return template


# Main script
# Loading configs

if len(sys.argv) > 3:

    phraseStart = " " + str(sys.argv[1]) + "("
    if '(' in sys.argv[1]:
        phraseStart = " " + str(sys.argv[1])
    possible_ends = ['    .registers', '    .locals'] 

    replace_with = render(sys.argv[2], sys.argv[3])
    replace_file = str(sys.argv[4])
    bump_registers = "--no-register-bump" not in sys.argv[5:]

    start_index = line_num_by_phrase(phraseStart, replace_file)

    end_index = None
    for phrase in possible_ends:
        end_index = line_num_by_phrase(phrase, replace_file, starts_at=start_index)
        if end_index is not False:
            add(replace_file, start_index, end_index, replace_with, bump_registers)
            break          

else:
    print("Usage: python script.py <function name> <variable name> <return type> <smali file>")
