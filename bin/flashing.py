import os
import subprocess
import sys
import argparse
import time

def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

def run_cmd(cmd):
    result = subprocess.run(cmd.split())
    if result.returncode != 0:
        print(f"Lenh that bai (exit {result.returncode}): {cmd}")
        sys.exit(result.returncode)

parser = argparse.ArgumentParser(description="Flash các phân vùng trong thư mục đã cho")
parser.add_argument("dir_path", help="Đường dẫn đến thư mục chứa file .img")
args = parser.parse_args()
dir_path = args.dir_path

if not os.path.exists(dir_path):
    print(f"Thư mục '{dir_path}' không tồn tại")
    sys.exit(1)

# Danh sách không flash
non_flash_list = list(set([
    'magisk', 'odm', 'odm_dlkm', 'product', 'product_dlkm',
    'system', 'system_dlkm', 'system_ext', 'system_ext_dlkm',
    'vendor', 'vendor_dlkm', 'mi_ext', 'bhlnk',
    'my_bigball', 'my_company', 'my_heytap', 'my_preload', 'my_region',
    'my_carrier', 'my_engineering', 'my_manifest', 'my_product',
    'my_stock', 'cust', 'real_state_vendor_boot'
]))

# Danh sách phân vùng chỉ flash một lần (không theo kiểu _a/_b)
single_part_list = ['cust', 'test']

# Danh sách các phân vùng Critical
critical_partitions = [
    'abl', 'aop', 'aop_config', 'devcfg', 'hyp', 'imagefv',
    'keymaster', 'qupfw', 'tz', 'uefisecapp', 'xbl', 'xbl_config', 'xbl_ramdump'
]

# Phân loại file
normal_partitions = []
critical_files = []

run_cmd("sudo fastboot -aa")

for file in os.listdir(dir_path):
    try:
        if file.endswith(".img"):
            part_name = file.split(".")[0]
            if part_name in non_flash_list:
                continue
            elif part_name in critical_partitions:
                critical_files.append(file)
            else:
                normal_partitions.append(file)
    except:
        print("skip", os.path.join(dir_path, file))

# Giai đoạn 1: Flash các phân vùng bình thường
for file in normal_partitions:
    part_name = file.split(".")[0]
    img_path = os.path.join(dir_path, file)
    if part_name in single_part_list:
        if part_name == 'test' :
            cmd = f"sudo fastboot flash super {img_path}"
        else:
            cmd = f"sudo fastboot flash {part_name} {img_path}"
    else:
        cmd = f"sudo fastboot flash {part_name}_a {img_path}"
    run_cmd(cmd)


# (Tuỳ chọn) Flash các file critical nếu cần
if True:
    run_cmd("sudo fastboot reboot fastboot")

    time.sleep(5)

    # Giai đoạn 2: Flash các phân vùng Critical
    for file in critical_files:
        part_name = file.split(".")[0]
        img_path = os.path.join(dir_path, file)
        cmd = f"sudo fastboot flash {part_name}_a {img_path}"
        run_cmd(cmd)

# Khởi động lại thiết bị
run_cmd("sudo fastboot reboot")
