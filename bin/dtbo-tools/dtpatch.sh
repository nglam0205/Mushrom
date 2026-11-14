#!/bin/bash

new_fragment='
    fragment@9999 {
        target-path = "/firmware";
        __overlay__ {
            android {
                compatible = "android,firmware";
                verifiedbootstate = "green";
                vbmeta.device_state = "locked";
                contact_vsteam_phone = "+84974029490";
                contact_vsteam_telegram = "@bhlnk";
                vsteam = "in_your_area";
                hwc = "Global";
            };
        };
    };
'

# Check if the input argument is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <device_tree_image>"
    exit 1
fi

device_tree_image="$1"

# Dump the original device tree image
./bin/mkdtimg dump "$device_tree_image" -b dtb >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "Failed to dump the device tree image"
    exit 1
fi

# Clean up any previous data


# Convert dtb to dts and move to the dts directory
mkdir -p dts
for i in dtb.*; do
    ./bin/dtc -I dtb -O dts -@ "$i" -o "dts/$i.dts" >/dev/null 2>&1
    if [ $? -ne 0 ]; then
        echo "Failed to convert $i to dts"
        exit 1
    fi
done

rm -rf "$device_tree_image" dtb.*

# Insert the new fragment into each dts file
for i in dts/dtb.*.dts; do
    tmp_file=$(mktemp)
    awk -v new_frag="$new_fragment" '/fragment@0 {/ { print new_frag } 1' "$i" > "$tmp_file"
    if [ $? -ne 0 ]; then
        echo "Failed to insert fragment into $i"
        exit 1
    fi
    mv "$tmp_file" "$i"
done


# Convert dts back to dtb
for i in dts/dtb.*.dts; do
    output_dtb="${i%.dts}.dtb"
    ./bin/dtc -I dts -O dtb -@ -o "$output_dtb" "$i" >/dev/null 2>&1
    if [ $? -ne 0 ]; then
        echo "Failed to convert $i back to dtb"
        exit 1
    fi
done


# Create the new device tree image
./bin/mkdtimg create "$device_tree_image" dts/*.dtb >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "Failed to create the new device tree image"
    exit 1
fi

# Clean up
rm -rf dts dts.dtb.* dtb.*