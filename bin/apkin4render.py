import os
import json
import subprocess
import argparse

def extract_apk_info(aapt, apk_path):
    try:
        result = subprocess.run(
            [aapt, "dump", "badging", apk_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            print(f"Failed to process APK: {apk_path}")
            return None

        output = result.stdout
        packageName = versionCode = versionName = appLabel = None

        for line in output.splitlines():
            if line.startswith("package:"):
                raw = line.split(" ")
                packageName = raw[1].split("'")[1]
                versionCode = raw[2].split("'")[1]
                versionName = raw[3].split("'")[1]

            if line.startswith("application:"):
                raw = line.split("'")
                appLabel = raw[1]

        if not packageName or not appLabel:
            print(f"Missing information for APK: {apk_path}")
            return None

        # Return collected information
        return {
            "package_name": packageName,
            "app_label": appLabel,
            "version_name": versionName,
            "version_code": versionCode,
        }

    except Exception as e:
        print(f"Error processing APK: {apk_path}. Error: {e}")
        return None

def scan_apks_individual(aapt, apk_dir):
    """Scan all APKs in a directory and save information as individual JSON files."""
    for root, _, files in os.walk(apk_dir):
        for file in files:
            if file.endswith(".apk"):
                apk_path = os.path.join(root, file)
                print(f"Processing: {apk_path}")
                apk_info = extract_apk_info(aapt, apk_path)
                if apk_info:
                    # Include the file name in the output JSON
                    apk_info["file_name"] = file

                    # Extract type and os from the file name
                    name_parts = file.split("_")
                    type_value = name_parts[1] if len(name_parts) > 1 else "all"
                    os_value = name_parts[2].split(".")[0] if len(name_parts) > 2 else "all"

                    # Use package_name for JSON file naming
                    package_name = apk_info.get("package_name", "unknown")
                    json_file_name = f"{package_name}_{type_value}_{os_value}.json"
                    json_file_path = os.path.join(root, json_file_name)
                    
                    # Add additional info from extracted data
                    apk_info["type"] = type_value
                    apk_info["os"] = os_value
                    
                    # Save the JSON file
                    with open(json_file_path, "w") as json_file:
                        json.dump(apk_info, json_file, indent=4, ensure_ascii=False)
                    print(f"Saved JSON for {file} as {json_file_name}")

if __name__ == "__main__":
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Scan APKs and extract information.")
    parser.add_argument("aapt2f", help="Path to aapt2 binary")
    parser.add_argument("apk_directory", help="Directory containing APK files")

    # Parse arguments
    args = parser.parse_args()

    # Run the scan with parameters from command-line
    scan_apks_individual(args.aapt2f, args.apk_directory)
