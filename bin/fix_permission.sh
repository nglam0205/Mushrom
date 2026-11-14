#!/bin/bash

APK_FILE="$1"

if [ ! -f "$APK_FILE" ]; then
  echo "File APK không tồn tại!"
  exit 1
fi

OUTPUT_DIR="$2"

if [ ! -d "$OUTPUT_DIR" ]; then
  mkdir -p "$OUTPUT_DIR"
else 
  rm -rf "$OUTPUT_DIR/*"
fi

PACKAGE_NAME=$(aapt dump badging "$APK_FILE" | grep package:\ name | awk -F"'" '{print $2}')

OUTPUT_XML="$OUTPUT_DIR/vs.per.$PACKAGE_NAME.xml"

echo '<?xml version="1.0" encoding="UTF-8"?>' > "$OUTPUT_XML"
echo "<permissions>" >> "$OUTPUT_XML"
echo "    <privapp-permissions package=\"$PACKAGE_NAME\">" >> "$OUTPUT_XML"

aapt dump permissions "$APK_FILE" | grep "uses-permission" | awk '{print $2}' | grep "'android.permission" | while read -r PERMISSION; do
  CLEAN_PERMISSION=$(echo "$PERMISSION" | sed "s/'//g")
  CLEAN_LAST=$(echo "$CLEAN_PERMISSION" | sed "s/name=//g")
  echo "       <permission name=\"$CLEAN_LAST\" />" >> "$OUTPUT_XML"
done

echo "    </privapp-permissions>" >> "$OUTPUT_XML"
echo "</permissions>" >> "$OUTPUT_XML"
