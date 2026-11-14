#!/bin/bash
nd="true"
ns="false"
funt="sget-boolean"
nd1="const"
key1="Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z"
key1x="Lmiuix/os/Build;->IS_INTERNATIONAL_BUILD:Z"
key2="Lmiui/os/Build;->IS_GLOBAL_BUILD:Z"
debug="Landroid/os/Build;->IS_DEBUGGABLE:Z"
lang_dir="$dir/module/lang"

if ! declare -F log_event >/dev/null 2>&1 && [[ -f "$dir/bin/logging.sh" ]]; then
	source "$dir/bin/logging.sh"
fi

api=35
apktool_show_logs="${apktool_show_logs:-false}"
apk_sign_output="${apk_sign_output:-false}"
apk_install_output="${apk_install_output:-false}"

android_version_at_least() {
	local minimum="$1"
	[[ "${os:-}" =~ ^[0-9]+$ ]] && ((os >= minimum))
}

# Android 17+ ROMs are patched with the multi-threaded apktool 3
# (bin/apktool.jar); older versions stay on the single-threaded apktool 2.4.1
# (bin/apktool_legacy.jar).
use_legacy_apktool() {
	! android_version_at_least 17
}

versioned_config() {
	local stem="$1"
	local extension="$2"
	local candidate="${stem}_${os}${extension}"
	local config
	local config_version
	local selected=""
	local selected_version=-1

	if [[ -f "$candidate" ]]; then
		echo "$candidate"
		return 0
	fi

	# OEM methods often keep the same ABI for consecutive Android releases.
	# Pick the newest known config that is not newer than the current ROM. This
	# lets Android 17 reuse a compatible Android 16 patch while Android 16 still
	# resolves its exact file. A dedicated _17 file always wins above.
	if [[ "${os:-}" =~ ^[0-9]+$ ]]; then
		for config in "${stem}"_[0-9]*"${extension}"; do
			[[ -f "$config" ]] || continue
			config_version="${config#"${stem}_"}"
			config_version="${config_version%"${extension}"}"
			[[ "$config_version" =~ ^[0-9]+$ ]] || continue
			if ((config_version <= os && config_version > selected_version)); then
				selected="$config"
				selected_version="$config_version"
			fi
		done
	fi

	if [[ -n "$selected" ]]; then
		echo "$selected"
	elif [[ -f "${stem}${extension}" ]]; then
		echo "${stem}${extension}"
	else
		echo "$candidate"
	fi
}

apply_smali_config() {
	local config="$1"
	local file_pattern="$2"
	local operation="${3:-replace}"
	local target
	local found=false

	if [[ ! -f "$config" ]]; then
		log_warn Config "$(basename "$config")" "missing; skip $file_pattern"
		return 0
	fi

	while IFS= read -r -d '' target; do
		found=true
		if ! $repS "$config" "$target" "$operation"; then
			log_fail Config "$(basename "$config")" "failed: ${target#./}"
			return 1
		fi
	done < <(find . -type f -name "$file_pattern" -print0)

	if [[ "$found" == "false" ]]; then
		log_warn Config "$(basename "$config")" "$file_pattern not found; skip"
	fi
}

log() {
	if [[ $3 ]]; then
		log_event info MushromTools "$1" "" "$2"
	else
		log_event info Permanent "$1" "" "$2"
	fi
}

logcore() {
	if [[ $3 ]]; then
		log_event info MushromTools "$1" "" "$2"
	else
		log_event info Permanent "$1" "" "$2"
	fi
}

log_command_output() {
	local scope="$1"
	local target="$2"
	local output
	local status
	local line

	shift 2
	output=$("$@" 2>&1)
	status=$?

	while IFS= read -r line; do
		[[ -n "$line" ]] && log_event info "$scope" "$target" "" "$line"
	done <<<"$output"

	return "$status"
}

get_java_runtime_for_tool() {
	local tool_name="$1"
	local java_bin="${JAVA_BIN:-java}"

	case "$tool_name" in
	apktool-vs | apktool | smali | baksmali)
		if [[ -x /usr/lib/jvm/java-17-openjdk-amd64/bin/java ]]; then
			java_bin="/usr/lib/jvm/java-17-openjdk-amd64/bin/java"
		fi
		;;
	esac

	echo "$java_bin"
}

java_tool_opts() {
	local tool_name="$1"
	local java_bin="$2"
	local version_out
	local major_version

	version_out=$("$java_bin" -version 2>&1 | head -n 1)
	major_version=$(sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p' <<<"$version_out")

	case "$tool_name" in
	apktool-vs | apktool | smali | baksmali)
		if [[ -n "$major_version" && "$major_version" -ge 21 ]]; then
			echo "-XX:TieredStopAtLevel=1 -Xms128m"
		fi
		;;
	esac
}

run_java_tool() {
	local tool_name="$1"
	local jar_path="$2"
	local java_bin
	local opts
	local status

	shift 2
	java_bin=$(get_java_runtime_for_tool "$tool_name")
	opts=$(java_tool_opts "$tool_name" "$java_bin")

	if [[ -n "$opts" ]]; then
		# Disable problematic higher-tier JIT on newer JVMs for legacy apk/smali tooling.
		"$java_bin" $opts -jar "$jar_path" "$@"
	else
		"$java_bin" -jar "$jar_path" "$@"
	fi
	status=$?

	if ((status != 0)); then
		log_warn Java "$tool_name" "exit $status using $(basename "$java_bin")"
	fi

	return "$status"
}

replaceM() {
	local method_to_remove="$1"
	local smali_path="$2"
	local config_path="$3"

	remM "$method_to_remove" "$smali_path"
	add_method "$config_path" "$smali_path"
}

patch_work_name() {
	local source_name="$2"

	echo "$source_name"
}

apk_entry_path() {
	local entry="$1"
	entry="${entry%%:=*}"
	entry="${entry%%:*}"
	echo "$entry"
}

apk_entry_package() {
	local entry="$1"
	if [[ "$entry" == *":="* ]]; then
		echo "${entry#*:=}"
	else
		echo "${entry##*:}"
	fi
}

resolve_lk_image() {
	local firmware_dir="${1:-$dir/$device/firmware-update}"
	echo "$firmware_dir/lk.img"
}

find_apk_path_by_package() {
	local package_name="$1"

	awk -v pkg="$package_name" '{
		suffix = ":" pkg
		if (substr($0, length($0) - length(suffix) + 1) == suffix) {
			print substr($0, 1, length($0) - length(suffix))
		}
	}' "$dir/apk_paths"
}

next_smali_classes_dir() {
	local smali_dir
	local dex_num
	local max_dex=1

	for smali_dir in smali smali_classes[0-9]*; do
		[[ -d "$smali_dir" ]] || continue

		if [[ "$smali_dir" == "smali" ]]; then
			dex_num=1
		else
			dex_num="${smali_dir#smali_classes}"
			[[ "$dex_num" =~ ^[0-9]+$ ]] || continue
		fi

		((dex_num > max_dex)) && max_dex="$dex_num"
	done

	echo "smali_classes$((max_dex + 1))"
}

# Some OEM framework.jar builds decode into a smali_classesN split so large
# that dexlib2 can't re-encode it: the round-trip through baksmali/smali
# pushes that dex's method/field/string/type reference table past the
# per-dex 65536 (unsigned short) ceiling, and the build aborts on whatever
# unrelated method dexlib2 happens to be writing when the index overflows.
# Peel classes off any oversized split into fresh smali_classesN dirs before
# building; where a class physically lives among a jar's dex files doesn't
# affect resolution on API 21+, so this is safe.
rebalance_smali_splits() {
	local max_files="${1:-6500}"
	local jar_label="${2:-$jarf}"
	local smali_dir
	local count
	local excess
	local dest
	local f
	local rel
	local -a files

	for smali_dir in smali smali_classes[0-9]*; do
		[[ -d "$smali_dir" ]] || continue

		# Read the full list instead of `find | head`: head closing the pipe
		# early makes find print "Broken pipe / write error" on CI runners,
		# where SIGPIPE is ignored.
		mapfile -t files < <(find "$smali_dir" -type f -name "*.smali")
		count=${#files[@]}
		if ((count > max_files)); then
			excess=$((count - max_files))
			dest=$(next_smali_classes_dir)
			mkdir -p "$dest"
			log_warn "$jar_label" "$smali_dir" \
				"oversized ($count classes); moving $excess into $dest to avoid dex reference overflow"

			for f in "${files[@]:0:excess}"; do
				rel="${f#"$smali_dir"/}"
				mkdir -p "$dest/$(dirname "$rel")"
				mv "$f" "$dest/$rel"
			done
		fi
	done
}

apktool_retry() {
	local label="$1"
	local cleanup_path="$2"
	local attempts="${APKTOOL_RETRY:-3}"
	local delay="${APKTOOL_RETRY_DELAY:-2}"
	local attempt
	local output
	local line
	local status=0

	shift 2
	[[ "$attempts" =~ ^[0-9]+$ ]] || attempts=3
	((attempts < 1)) && attempts=1

	for ((attempt = 1; attempt <= attempts; attempt++)); do
		[[ -n "$cleanup_path" ]] && rm -rf "$cleanup_path"
		if [[ "$apktool_show_logs" == "true" ]]; then
			log_event info APKTool "$label" "" "attempt $attempt/$attempts"
			"$@" 2>&1
			status=$?
			output=""
		else
			output=$("$@" 2>&1)
			status=$?
		fi
		((status == 0)) && return 0

		log_warn APKTool "$label" "attempt $attempt/$attempts exit $status"
		if ((attempt == attempts)) && [[ "$apktool_show_logs" != "true" ]]; then
			while IFS= read -r line; do
				[[ -n "$line" ]] && log_event error APKTool "$label" "" "$line"
			done < <(tail -n 20 <<<"$output")
		fi
		((attempt < attempts)) && sleep "$delay"
	done

	return "$status"
}

is_backup_img_path() {
	local path="$1"

	[[ "$path" == "$dir"/backup/*/img_temp/* ]]
}

copy_back_target() {
	local target="$1"
	local label="$2"
	local relative_img_path

	if [[ "$apk_patch_mode" == "r" ]] && is_backup_img_path "$target"; then
		relative_img_path="${target##*/img_temp/}"
		target="$dir/img_temp/$relative_img_path"
		log_warn R-Mode "$label" "Refuse writing backup, redirect to ${target#"$dir/"}" >&2
	fi

	echo "$target"
}

ensure_not_backup_target() {
	local target="$1"
	local label="$2"

	if is_backup_img_path "$target"; then
		log_fail R-Mode "$label" "refuse writing backup: ${target#"$dir/"}" >&2
		return 1
	fi
}

decp() {

	if [[ ! -d $dir/jar_temp ]]; then
		mkdir $dir/jar_temp
	fi

	package_name=$(apk_entry_package "$1")
	file_path=$(apk_entry_path "$1")
	display_name=$(basename "$file_path")
	file_name=$(patch_work_name "$package_name" "$(basename "$file_path")")

	log_start "$package_name" "$display_name"
	cd $dir/jar_temp
	if ! cp "$file_path" "$dir/jar_temp/$file_name"; then
		log_fail "$package_name" "$display_name" "Unable to stage APK in jar_temp"
		cd "$dir"
		return 1
	fi

	if use_legacy_apktool; then
		if [[ $2 == "rs" ]]; then
			apktool_retry "$package_name][$display_name][Decode" "$file_name.out" \
				run_java_tool apktool "$dir/bin/apktool_legacy.jar" -b -f d -c "$file_name" -o "$file_name.out"
		else
			apktool_retry "$package_name][$display_name][Decode" "$file_name.out" \
				run_java_tool apktool "$dir/bin/apktool_legacy.jar" -r -b -f d -c -api "$api" "$file_name" -o "$file_name.out"
		fi
	elif [[ $2 == "rs" ]]; then
		apktool_retry "$package_name][$display_name][Decode" "$file_name.out" \
			run_java_tool apktool "$dir/bin/apktool.jar" d --no-debug-info -f "$file_name" -o "$file_name.out"
	else
		apktool_retry "$package_name][$display_name][Decode" "$file_name.out" \
			run_java_tool apktool "$dir/bin/apktool.jar" d -r --no-debug-info -f "$file_name" -o "$file_name.out"
	fi

	if [[ $? -ne 0 || ! -d "$file_name.out" ]]; then
		rm -f "$file_name"
		log_fail "$package_name" "$display_name" "Decode failed"
		cd "$dir"
		return 1
	fi

	rm -f "$file_name"
	cd "$file_name.out" || return 1
}

recp() {
	local entry="$1"
	local sign_mode="$2"
	local build_mode="$3"
	local package_name
	local file_path
	local display_name
	local file_name
	local copy_target
	local relative_img_path
	local adb_status

	package_name=$(apk_entry_package "$entry")
	file_path=$(apk_entry_path "$entry")
	display_name=$(basename "$file_path")
	file_name=$(patch_work_name "$package_name" "$display_name")

	cleanup_recp() {
		local keep_output="$1"

		rm -rf \
			"$file_name.out" \
			"$file_name.apk" \
			"$file_name.idsig"

		if [[ "$keep_output" != "keep" ]]; then
			rm -f "$file_name"
		fi
	}

	cd "$dir/jar_temp" || return 1

	if ! use_legacy_apktool; then
		apktool_retry "$package_name][$display_name][Build" "$file_name.apk" \
			run_java_tool apktool "$dir/bin/apktool.jar" b -f --copy-original "$file_name.out" -o "$file_name.apk"
	elif [[ "$build_mode" == "rs" ]]; then
		apktool_retry "$package_name][$display_name][Build" "$file_name.apk" \
			run_java_tool apktool "$dir/bin/apktool_legacy.jar" -b -f b -c "$file_name.out" -o "$file_name.apk"
	else
		apktool_retry "$package_name][$display_name][Build" "$file_name.apk" \
			run_java_tool apktool "$dir/bin/apktool_legacy.jar" -r -b -f b -c -api "$api" "$file_name.out" -o "$file_name.apk"
	fi

	if [[ $? -ne 0 || ! -f "$file_name.apk" ]]; then
		log_fail "$package_name" "$display_name" "Build failed"
		cleanup_recp
		cd "$dir"
		return 1
	fi

	"$zipalign_sa" -p -v 4 "$file_name.apk" "$file_name" >/dev/null 2>&1
	if [[ $? -ne 0 || ! -f "$file_name" ]]; then
		log_fail "$package_name" "$display_name" "Zipalign failed"
		cleanup_recp
		cd "$dir"
		return 1
	fi

	if [[ "$sign_mode" == "sign" || "$apk_sign_output" == "true" ]]; then
		log_event info APKSign "$package_name/$display_name" "" "Signing rebuilt APK"
		apksigner sign --ks "$dir/module/miuivs" --ks-pass pass:linkcute "$file_name" >/dev/null 2>&1
		if [[ $? -ne 0 ]]; then
			log_fail "$package_name" "$display_name" "Sign failed"
			cleanup_recp
			cd "$dir"
			return 1
		fi
	elif [[ $brand == "ColorOS" ]] && android_version_at_least 17; then
		# apktool -c only restores META-INF (v1); v2/v3-only APKs would end up
		# unsigned, so put the original APK Signing Block back after zipalign.
		python3 "$dir/bin/copy_apk_sigblock.py" "$file_path" "$file_name"
		case $? in
		0) ;;
		2) log_warn "$package_name" "$display_name" "Original has no v2/v3 signing block" ;;
		*)
			log_fail "$package_name" "$display_name" "Copy signing block failed"
			cleanup_recp
			cd "$dir"
			return 1
			;;
		esac
	fi

	copy_target="$file_path"
	if [[ -n "$patch_copy_back_to" ]]; then
		copy_target="$patch_copy_back_to"
	fi

	copy_target=$(copy_back_target "$copy_target" "$package_name/$display_name")
	ensure_not_backup_target "$copy_target" "$package_name/$display_name" || {
		cleanup_recp
		cd "$dir"
		return 1
	}

	sudo cp -f "$dir/jar_temp/$file_name" "$copy_target"
	if [[ $? -ne 0 ]]; then
		log_fail "$package_name" "$display_name" "Copy back failed"
		cleanup_recp
		cd "$dir"
		return 1
	fi

	if [[ "$apk_install_output" == "true" ]]; then
		if ! command -v adb >/dev/null 2>&1; then
			log_fail ADB "$package_name/$display_name" "adb not found"
			cleanup_recp keep
			cd "$dir"
			return 1
		fi

		log_event info ADB "$package_name/$display_name" "" "Installing ${copy_target#"$dir/"}"
		log_command_output ADB "$package_name/$display_name" adb install -r "$copy_target"
		adb_status=$?
		if ((adb_status != 0)); then
			log_fail ADB "$package_name/$display_name" "Install failed (exit $adb_status)"
			cleanup_recp keep
			cd "$dir"
			return "$adb_status"
		fi
		log_done ADB "$package_name/$display_name" "Installed signed APK"
	fi

	patch_copy_back_from=""
	patch_copy_back_to=""

	cleanup_recp keep
	cd "$dir"
	log_success "$package_name" "$display_name"
}

mv2sys() {
	if [[ $1 ]]; then
		ip=$(check_file_exist $1)
		if [[ $ip ]]; then
			package_name=$(echo $ip | cut -d ':' -f2 | cut -d '=' -f2)
			file_path=$(echo $ip | cut -d ':' -f1 | cut -d '=' -f1)
			file_name=$(basename $file_path)

			string_to_check=$file_path

			dat2sys $string_to_check

			log_success Move2System "$package_name" "$file_name"
		fi
	fi

}

jar_util() {
	cd $dir
	local source_jar_path=""
	local target_path=""

	if [[ ! -d $dir/jar_temp ]]; then
		mkdir $dir/jar_temp
	fi
	if [[ $os -eq 15 ]]; then
		bak="run_java_tool baksmali $dir/bin/baksmali.jar d"
		sma="run_java_tool smali $dir/bin/smali.jar a"
	else
		bak="run_java_tool baksmali $dir/bin/baksmali_14.jar d"
		sma="run_java_tool smali $dir/bin/smali_14.jar a"
	fi

	if [[ $1 == "d" ]]; then
		log_start Core "$2"
		source_jar_path=$(get_file_dir "$2")
		if [[ -n "$source_jar_path" ]]; then
			sudo cp "$source_jar_path" "$dir/jar_temp"
			sudo chown $(whoami) $dir/jar_temp/$2
			unzip $dir/jar_temp/$2 -d $dir/jar_temp/$2.out >/dev/null 2>&1
			if [[ -d $dir/jar_temp/"$2.out" ]]; then
				rm -rf $dir/jar_temp/$2
				for dex in $(sudo find $dir/jar_temp/"$2.out" -maxdepth 1 -name "*dex"); do
					if [[ $4 ]]; then
						if [[ "$dex" != *"$4"* && "$dex" != *"$5"* ]]; then
							$bak $dex -o "$dex.out"
							[[ -d "$dex.out" ]] && rm -rf $dex
						fi
					else
						$bak $dex -o "$dex.out"
						[[ -d "$dex.out" ]] && rm -rf $dex
					fi

				done
			fi
		fi
	else
		if [[ $1 == "a" ]]; then
			if [[ -d $dir/jar_temp/$2.out ]]; then
				cd $dir/jar_temp/$2.out
				for fld in $(sudo find -maxdepth 1 -name "*.out"); do
					if [[ $4 ]]; then
						if [[ "$fld" != *"$4"* && "$fld" != *"$5"* ]]; then
							$sma $fld -o $(echo ${fld//.out/}) --api 34
							[[ -f $(echo ${fld//.out/}) ]] && rm -rf $fld
						fi
					else
						$sma $fld -o $(echo ${fld//.out/}) --api 34
						[[ -f $(echo ${fld//.out/}) ]] && rm -rf $fld
					fi
				done
				7za a -tzip -mx=0 $dir/jar_temp/$2_notal $dir/jar_temp/$2.out/. >/dev/null 2>&1
				$zipalign_sa -p -v 4 $dir/jar_temp/$2_notal $dir/jar_temp/$2 >/dev/null 2>&1
				if [[ -f $dir/jar_temp/$2 ]]; then
					rm -rf $dir/jar_temp/$2.out $dir/jar_temp/$2_notal
					target_path=$(get_file_dir_img $2)
					[[ -n "$jar_patch_target_path" ]] && target_path="$jar_patch_target_path"
					target_path=$(copy_back_target "$target_path" "Core/$2")
					if [[ -z "$target_path" ]]; then
						log_fail Core "$2" "target not found"
						return 1
					fi
					ensure_not_backup_target "$target_path" "Core/$2" || return 1
					sudo cp -rf $dir/jar_temp/$2 "$target_path"
					jar_patch_target_path=""
					log_success Core "$2"
				else
					log_fail Core "$2"
				fi
			fi
		fi
	fi
}

new_jar_util() {
	cd $dir
	local source_jar_path=""
	local target_path=""

	if [[ ! -d $dir/jar_temp ]]; then
		mkdir $dir/jar_temp
	fi

	if [[ $1 == "d" ]]; then
		log_start Core Framework
		source_jar_path=$(get_file_dir "$2")
		if [[ -n "$source_jar_path" ]]; then
			sudo cp "$source_jar_path" "$dir/jar_temp"
			sudo chown "$(whoami)" "$dir/jar_temp/$2"
			apktool_retry "Core Framework][$2][Decode" "$dir/jar_temp/$2.out" \
				run_java_tool apktool-vs "$dir/bin/apktool-vs.jar" d "$dir/jar_temp/$2" -o "$dir/jar_temp/$2.out"

			if [[ $? -ne 0 || ! -d "$dir/jar_temp/$2.out" ]]; then
				log_fail Core Framework "Decode failed: $2"
				return 1
			fi

		fi
	else
		if [[ $1 == "a" ]]; then
			if [[ -d $dir/jar_temp/$2.out ]]; then
				apktool_retry "Core Framework][$2][Build" "$dir/jar_temp/$2" \
					run_java_tool apktool-vs "$dir/bin/apktool-vs.jar" b -f --copy-original "$dir/jar_temp/$2.out" -o "$dir/jar_temp/$2"

				if [[ $? -ne 0 || ! -f "$dir/jar_temp/$2" ]]; then
					log_fail Core Framework "Build failed: $2"
					return 1
				fi
			else
				log_fail Core Framework "Missing decoded dir: $2.out"
				return 1
			fi
		fi

		if [[ -f $dir/jar_temp/$2 ]]; then
			target_path=$(get_file_dir_img "$2")
			if [[ -z "$target_path" ]]; then
				log_fail Core Framework "target not found: $2"
				return 1
			fi
			target_path=$(copy_back_target "$target_path" "Core Framework/$2")
			ensure_not_backup_target "$target_path" "Core Framework/$2" || return 1
			sudo cp -rf "$dir/jar_temp/$2" "$target_path"
			rm -rf "$dir/jar_temp/$2.out"
			log_success Core Framework
		else
			log_fail Core Framework
		fi
	fi

}

remM() {
	local funcName="$1"
	local filePattern="$2"

	files=$(find -type f -name "$filePattern")

	for file in $files; do
		$remM $funcName $file
	done
}

invoke() {
	type=$1
	file=$2
	para=$4
	value=$4
	if [[ -f $2 ]]; then
		$invoke $1 $2 $3 $4
	elif [[ -d $2 ]]; then
		for i in $(find $(pwd) -type f -name "*.smali"); do
			if [[ $(grep $3 $i) ]]; then
				$invoke $1 $i $3 $4
			fi

		done
	fi
}

ftrue() {

	if [[ -f $1 ]]; then
		for i in $(grep -nr $2 $1 | cut -d':' -f1); do
			if [[ $(sed -n "${i}p" $1 | grep "sget-boolean") ]]; then
				sed -i "${i}s@$2@$nd@" $1
				sed -i "${i}s@$funt@$nd1@" $1
			fi
		done
	else
		if [[ -d $1 ]]; then
			for i in $(grep -nr $2 $1 | cut -d':' -f1,2); do
				filename="$(cut -d':' -f1 <<<"$i")"
				lineNo="$(cut -d':' -f2 <<<"$i")"
				if [[ $(sed -n "${lineNo}p" $filename | grep "sget-boolean") ]]; then
					sed -i "${lineNo}s@$2@$nd@" $filename
					sed -i "${lineNo}s@$funt@$nd1@" $filename
				fi
			done
		fi
	fi

}

ffalse() {
	if [[ -f $1 ]]; then
		for i in $(grep -nr $2 $1 | cut -d':' -f1); do
			if [[ $(sed -n "${i}p" $1 | grep "sget-boolean") ]]; then
				sed -i "${i}s@$2@$ns@" $1
				sed -i "${i}s@$funt@$nd1@" $1
			fi
		done
	else
		if [[ -d $1 ]]; then
			for i in $(grep -nr $2 $1 | cut -d':' -f1,2); do
				filename="$(cut -d':' -f1 <<<"$i")"
				lineNo="$(cut -d':' -f2 <<<"$i")"
				if [[ $(sed -n "${lineNo}p" $filename | grep "sget-boolean") ]]; then
					sed -i "${lineNo}s@$2@$ns@" $filename
					sed -i "${lineNo}s@$funt@$nd1@" $filename
				fi
			done
		fi
	fi

}

fdevice() {
	first='Landroid/os/Build;->DEVICE:Ljava/lang/String;'
	second='sget-object'
	third='const-string'
	four="\"$2\""

	if [[ -f $1 ]]; then
		for i in $(grep -nr $first $1 | cut -d':' -f1); do
			if [[ $(sed -n "${i}p" $1 | grep "sget-object") ]]; then
				sed -i "${i}s@$first@$four@" $1
				sed -i "${i}s@$second@$third@" $1
			fi
		done
	else
		if [[ -d $1 ]]; then
			for i in $(grep -nr $first $1 | cut -d':' -f1,2); do
				filename="$(cut -d':' -f1 <<<"$i")"
				lineNo="$(cut -d':' -f2 <<<"$i")"
				if [[ $(sed -n "${lineNo}p" $filename | grep "sget-object") ]]; then
					sed -i "${lineNo}s@$first@$four@" $filename
					sed -i "${lineNo}s@$second@$third@" $filename
				fi
			done
		fi
	fi

}

repM() {
	if [[ $4 == "r" ]]; then
		if [[ -f $3 ]]; then
			$repM "$1" "$2" "$3" "$4"
		fi
	elif [[ $4 == "f" ]]; then
		for i in $3; do
			$repM "$1" "$2" "$i" "$4"
		done
	else
		if [[ $5 == "r" ]]; then
			$repM "$1" "$2" "$3" "$4"
		elif [[ -f "$3" ]]; then
			$repM "$1" "$2" "$3" "${@:4}"
		else
			files=$(sudo find -name "$3")
			for file in $files; do
				$repM "$1" "$2" "$file" "${@:4}"
			done
		fi
	fi
}

repB() {
	if [[ $5 == "r" ]]; then
		# recursive call nếu truyền file trực tiếp
		if [[ -f $4 ]]; then
			$repB "$1" "$2" "$3" "$4" "$5"
		fi
	elif [[ -f "$4" ]]; then
		$repB "$1" "$2" "$3" "$4" "${@:5}"
	else
		files=$(sudo find -name "$4")
		if [[ $files ]]; then
			for file in $files; do
				$repB "$1" "$2" "$3" "$file" "${@:5}"
			done
		fi
	fi
}

repR() {
	if [[ $3 == "r" ]]; then
		# recursive call nếu là file cụ thể
		if [[ -f $2 ]]; then
			$repR "$1" "$2" "$3"
		fi
	elif [[ -f "$2" ]]; then
		$repR "$1" "$2" "${@:3}"
	else
		files=$(sudo find -name "$2")
		if [[ $files ]]; then
			for file in $files; do
				$repR "$1" "$file" "${@:3}"
			done
		fi
	fi
}

apk_scan_w() {
	log_start Scan APK "apk_paths"

	rm -f "$dir/apk_paths"

	for apk_file in $(sudo find "$dir/img_temp/" -type f -name "*.apk"); do
		package_name=$(sudo $aapt2_b dump badging $apk_file 2>/dev/null | grep package | head -1 | awk '{print $2}' | sed s/name=//g | sed s/\'//g)
		echo "$apk_file:$package_name" >>$dir/apk_paths
	done

	log_done Scan APK "apk_paths"
}

get_file_dir() {
	if [[ $1 ]]; then
		test=$(sudo find "$dir/img_temp/" -type f -name "$1")
		for i in $test; do
			if [[ "$apk_patch_mode" == "r" ]]; then
				jar_patch_target_path="$i"
				backup_dir=$(resolve_backup_dir)
				if [[ -n "$backup_dir" ]]; then
					relative_path="${i#"$dir/img_temp/"}"
					backup_path="$backup_dir/img_temp/$relative_path"
					if [[ ! -f "$backup_path" ]]; then
						backup_path=$(sudo find "$backup_dir/img_temp" -type f -name "$1" | head -1)
					fi
					if [[ -f "$backup_path" ]]; then
						log_event info "R-Mode - JAR" "$1" "" "Source ${backup_path#"$dir/"}" >&2
						log_event info "R-Mode - JAR" "$1" "" "Target ${i#"$dir/"}" >&2
						echo "$backup_path"
					else
						log_warn "R-Mode - JAR" "$1" "Backup JAR not found, fallback img_temp" >&2
						echo "$i"
					fi
				else
					log_warn "R-Mode - JAR" "$1" "Backup dir not found, fallback img_temp" >&2
					echo "$i"
				fi
				return 0
			fi
			echo $i
		done
	else
		return 0
	fi

}

get_file_dir_img() {
	if [[ $1 ]]; then
		sudo find "$dir/img_temp/" -type f -name "$1" | head -1
	else
		return 0
	fi
}

check_file_exist() {

	if [[ ! -f $dir/apk_paths ]]; then
		log_warn APKIndex apk_paths "missing, scan before lookup: $1" >&2
		apk_scan_w >&2
	fi

	b=$(find_apk_path_by_package "$1")

	if [[ $b ]]; then
		base_entry="$b:=$1"
		if [[ "$apk_patch_mode" == "r" ]]; then
			prepare_patch_entry "$1" "$apk_patch_mode" "$base_entry"
			echo "$patch_entry_result"
		else
			echo "$base_entry"
		fi
	else
		return 0
	fi
}

get_entry_path() {
	if [[ $1 ]]; then
		apk_entry_path "$1"
	fi
}

resolve_backup_dir() {
	local current_device="${device,,}"
	local preferred="$dir/backup/${current_device}_${buildtime}"
	if [[ -d "$preferred/img_temp" ]]; then
		echo "$preferred"
		return 0
	fi

	for info_file in "$dir"/backup/*/info; do
		[[ -f "$info_file" ]] || continue

		local b_device=""
		local b_buildtime=""
		local b_base=""
		local b_iden=""
		local b_zipname=""

		b_device=$(grep '^device=' "$info_file" | head -1 | cut -d'=' -f2-)
		b_device="${b_device,,}"
		b_buildtime=$(grep '^buildtime=' "$info_file" | head -1 | cut -d'=' -f2-)
		b_base=$(grep '^base=' "$info_file" | head -1 | cut -d'=' -f2-)
		[[ -z "$b_base" ]] && b_base=$(grep '^miui=' "$info_file" | head -1 | cut -d'=' -f2-)
		b_iden=$(grep '^iden=' "$info_file" | head -1 | cut -d'=' -f2-)
		b_zipname=$(grep '^zipname=' "$info_file" | head -1 | cut -d'=' -f2-)

		if [[ -n "$current_device" && "$b_device" != "$current_device" ]]; then
			continue
		fi

		if [[ -n "$buildtime" && -n "$b_buildtime" && "$b_buildtime" == "$buildtime" ]]; then
			echo "$(dirname "$info_file")"
			return 0
		fi

		if [[ -n "$iden" && -n "$b_iden" && "$b_iden" == "$iden" ]]; then
			echo "$(dirname "$info_file")"
			return 0
		fi

		local current_base="${base:-$miui}"
		if [[ -n "$current_base" && -n "$b_base" && "$b_base" == "$current_base" ]]; then
			echo "$(dirname "$info_file")"
			return 0
		fi

		if [[ -n "$zipname" && -n "$b_zipname" && "$b_zipname" == "$zipname" ]]; then
			echo "$(dirname "$info_file")"
			return 0
		fi
	done

	return 1
}

prepare_patch_entry() {
	local package_name="$1"
	local mode="$2"
	local base_entry="$3"

	patch_entry_result="$base_entry"
	patch_copy_back_from=""
	patch_copy_back_to=""

	if [[ "$mode" != "r" || -z "$base_entry" ]]; then
		return 0
	fi

	local base_path=""
	base_path=$(get_entry_path "$base_entry")
	local backup_dir=""
	backup_dir=$(resolve_backup_dir)

	if [[ -z "$base_path" || -z "$backup_dir" ]]; then
		return 0
	fi

	local relative_path="${base_path#"$dir/img_temp/"}"
	local backup_path="$backup_dir/img_temp/$relative_path"

	if [[ ! -f "$backup_path" ]]; then
		while IFS= read -r apk; do
			local pkg_name=""
			pkg_name=$(sudo $aapt2_b dump badging "$apk" 2>/dev/null | grep package | head -1 | awk '{print $2}' | sed s/name=//g | sed s/\'//g)
			if [[ "$pkg_name" == "$package_name" ]]; then
				backup_path="$apk"
				break
			fi
		done < <(sudo find "$backup_dir/img_temp" -type f -name "*.apk")
	fi

	if [[ -f "$backup_path" ]]; then
		patch_copy_back_from="$backup_path"
		patch_copy_back_to="$base_path"
		patch_entry_result="$backup_path:=$package_name"
		log_event info "R-Mode - APK" "$package_name" "" "Source ${backup_path#"$dir/"}" >&2
		log_event info "R-Mode - APK" "$package_name" "" "Target ${base_path#"$dir/"}" >&2
	else
		log_warn "R-Mode - APK" "$package_name" "Backup APK not found, fallback img_temp" >&2
	fi
}

apk_scan_s() {

	rm -f "$dir/apk_paths"

	for apk_file in $(sudo find "$dir/img_temp/" -type f -name "*.apk"); do
		package_name=$(sudo $aapt2_b dump badging $apk_file 2>/dev/null | grep package | head -1 | awk '{print $2}' | sed s/name=//g | sed s/\'//g)
		echo "$apk_file:$package_name" >>$dir/apk_paths
	done

}

remove_pkg() {

	if [[ ! -f $dir/apk_paths ]]; then
		apk_scan_s
	fi

	b=$(find_apk_path_by_package "$1")
	if [[ $b ]]; then
		sudo rm -rf $b
	fi
}

dat2sys() {
	if [[ $1 == *"data-app"* ]]; then
		dest="${1/data-app/app}"
		dest="${dest/"img_temp"/"module/update/temp_data_app"}"

		# Tạo thư mục đích nếu chưa có và sao chép tập tin với quyền sudo
		sudo mkdir -p "$(dirname "$(dirname "$dest")")"
		sudo cp -r "$(dirname "$1")" "$(dirname "$(dirname "$dest")")"
		sudo chown -R "$(whoami)":"$(whoami)" "$(dirname "$(dirname "$dest")")"

		mkdir -p "$(dirname "$dest")/lib/arm64"

		unzip -j "$dest" "lib/arm64-v8a/*.so" -d "$(dirname "$dest")/lib/arm64" >/dev/null 2>&1

		manual_add true temp_data_app

		sed -i "s|^$1|${1/data-app/app}|g" "$dir/apk_paths"

		sudo rm -rf "$1"

	fi
}

s2r() {
	local str="$1"
	str="${str//\//\\/}"
	echo "$str"
}

GetGlobal() {
	int="const-string\/jumbo ${2}, \"${3}\"\ninvoke-static {${2}}, Lcom\/android\/vsteam\/Toolbox;->getIntGlobal(Ljava\/lang\/String;)I\nmove-result ${2}"
	boolean="const-string\/jumbo ${2}, \"${3}\"\ninvoke-static {${2}}, Lcom\/android\/vsteam\/Toolbox;->getBooleanGlobal(Ljava\/lang\/String;)Z\nmove-result ${2}"
	booleanz="const-string\/jumbo ${2}, \"${3}\"\ninvoke-static {${2}}, Lcom\/android\/vsteam\/Toolbox;->getBooleanGlobalZ(Ljava\/lang\/String;)Z\nmove-result ${2}"

	[[ $1 == "int" ]] && echo $int
	[[ $1 == "boolean" ]] && echo $boolean
	[[ $1 == "booleanz" ]] && echo $booleanz

}

foption() {
	smlt=$(sudo find -name $3)
	for sml in $smlt; do
		value=$(grep -Eo "sget-boolean\s+\w+,\s+${1}" "$sml" | awk -F '[ ,]+' '{print $2}')

		for values in $value; do
			regex=$(s2r $1)
			source="sget-boolean ${values}, ${regex}"
			replacement=$(GetGlobal boolean $values $2)
			sed -i "s/${source}/${replacement}/g" "$sml"
		done
	done
}

fstatic() {

	src=$1
	dest=$2
	file=$3

	if [[ $(sudo find -type f -name $3) ]]; then
		sudo sed -i "s/$(s2r $src)/$(s2r $dest)/g" $(sudo find -type f -name $3)
	fi

}

fstring() {
	src=$1
	dest=$2
	file=$3
	option=$4

	if [[ $file ]]; then
		if [[ $option == 'r' ]]; then
			if grep -q "$src" "$file"; then
				sudo sed -i "s#$src#$dest#g" "$file"
			fi
		else
			# Loop file-by-file: when the name matches multiple files (e.g. duplicated
			# across smali_classesN), passing the multi-line `find` output straight to
			# `sed -i` as one quoted argument makes sed treat it as a single bogus path.
			while IFS= read -r file_path; do
				[[ -z $file_path ]] && continue
				if grep -q "$src" "$file_path"; then
					# s2r must get $src/$dest quoted: unquoted, bash word-splits on
					# spaces before s2r ever sees the string, so only the first word
					# gets escaped and the rest of a multi-word pattern (e.g. a full
					# smali instruction line) silently falls off the replacement.
					sudo sed -i "s/$(s2r "$src")/$(s2r "$dest")/g" "$file_path"
				fi
			done < <(find . -type f -name "$file")
		fi
	else
		find . -name "*.smali" -exec grep -l "$src" {} + | while read -r smali_file; do
			sudo sed -i "s/$(s2r "$src")/$(s2r "$dest")/g" "$smali_file"
		done
	fi
}

force_put() {
	local field="$1"
	local int_val="$2"
	local file_pattern="$3"
	local hex_val
	local escaped_field
	local files

	hex_val=$(printf "0x%x" "$int_val")
	mapfile -t files < <(find . -type f -name "$file_pattern")

	if [[ ${#files[@]} -eq 0 ]]; then
		echo "Khong tim thay file: $file_pattern"
		return 1
	fi

	escaped_field=$(printf '%s\n' "$field" | sed 's/[.[\*^$/]/\\&/g')

	for file in "${files[@]}"; do
		local matches
		matches=$(grep -nE "(i|s)put[-a-z]*[[:space:]]+[vp][0-9]+(,[[:space:]]+[vp][0-9]+)?,[[:space:]]+$escaped_field" "$file")
		[[ -z "$matches" ]] && continue

		echo "$matches" | tac | while IFS= read -r line_info; do
			local line_num line_text var1
			line_num=$(echo "$line_info" | cut -d: -f1)
			line_text=$(echo "$line_info" | cut -d: -f2-)
			var1=$(echo "$line_text" | awk '{print $2}' | sed 's/,//')
			sed -i "${line_num}i\\
    const $var1, $hex_val" "$file"
		done
	done
}

force_put_option() {
	local field="$1"
	local var_name="$2"
	local file_pattern="$3"
	local escaped_field
	local files

	mapfile -t files < <(find . -type f -name "$file_pattern")

	if [[ ${#files[@]} -eq 0 ]]; then
		echo "Khong tim thay file: $file_pattern"
		return 1
	fi

	escaped_field=$(printf '%s\n' "$field" | sed 's/[.[\*^$/]/\\&/g')

	for file in "${files[@]}"; do
		local matches
		matches=$(grep -nE "(i|s)put[-a-z]*[[:space:]]+[vp][0-9]+(,[[:space:]]+[vp][0-9]+)?,[[:space:]]+$escaped_field" "$file")
		[[ -z "$matches" ]] && continue

		echo "$matches" | tac | while IFS= read -r line_info; do
			local line_num line_text var1
			line_num=$(echo "$line_info" | cut -d: -f1)
			line_text=$(echo "$line_info" | cut -d: -f2-)
			var1=$(echo "$line_text" | awk '{print $2}' | sed 's/,//')
			sed -i "${line_num}i\\
    const-string $var1, \"$var_name\"\\
    invoke-static {$var1}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z\\
    move-result $var1" "$file"
		done
	done
}

patch_miuihome_bottom_searchbar() {
	local pkg_name="${pkg:-com.miui.home}"
	local file
	file=$(sudo find -name "SearchBarConfig.smali" | head -1)

	if [[ ! -f "$file" ]]; then
		log "$pkg_name" "Bottom search bar patch target not found: SearchBarConfig.smali" true
		return 0
	fi

	perl -0pi -e 's#(\.method public static getSettingSearchBarType\(\)I\s+\.locals 5\s+).*?(\s+\.line 319\s+)#${1}${2}#s' "$file"
	perl -0pi -e 's#(\.method public static getCurrentSearchBarType\(\)I\s+\.locals 1\s+).*?(\s+\.line 399\s+)#${1}${2}#s' "$file"
	perl -0pi -e 's#(\.method public static isShowLargeSearchBar\(\)Z\s+\.locals 1\s+).*?(\s+\.line 426\s+)#${1}${2}#s' "$file"
	perl -0pi -e 's#(\.method public static isSupportGoogleSearchBarBeforeLoadScreenSize\(\)Z\s+\.locals 2\s+).*?(\s+\.line 246\s+)#${1}${2}#s' "$file"
	perl -0pi -e 's#(\.method public static isSupportQuickSearchBar\(\)Z\s+\.locals 1\s+).*?(\s+\.line 139\s+)#${1}${2}#s' "$file"

	perl -0pi -e 's#(\.method public static getSettingSearchBarType\(\)I\s+\.locals 5\s+)#${1}    const-string/jumbo v0, "vs_bottom_searchbar"\n\n    invoke-static {v0}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z\n\n    move-result v0\n\n    if-eqz v0, :cond_vs_bottom_searchbar_setting_original\n\n    const/4 v0, 0x4\n\n    return v0\n\n    :cond_vs_bottom_searchbar_setting_original\n\n#s' "$file"

	perl -0pi -e 's#(\.method public static getCurrentSearchBarType\(\)I\s+\.locals 1\s+)#${1}    const-string/jumbo v0, "vs_bottom_searchbar"\n\n    invoke-static {v0}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z\n\n    move-result v0\n\n    if-eqz v0, :cond_vs_bottom_searchbar_current_original\n\n    const/4 v0, 0x4\n\n    return v0\n\n    :cond_vs_bottom_searchbar_current_original\n\n#s' "$file"

	perl -0pi -e 's#(\.method public static isShowLargeSearchBar\(\)Z\s+\.locals 1\s+)#${1}    const-string/jumbo v0, "vs_bottom_searchbar"\n\n    invoke-static {v0}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z\n\n    move-result v0\n\n    if-eqz v0, :cond_vs_bottom_searchbar_large_original\n\n    const/4 v0, 0x1\n\n    return v0\n\n    :cond_vs_bottom_searchbar_large_original\n\n#s' "$file"

	perl -0pi -e 's#(\.method public static isSupportGoogleSearchBarBeforeLoadScreenSize\(\)Z\s+\.locals 2\s+)#${1}    const-string/jumbo v0, "vs_bottom_searchbar"\n\n    invoke-static {v0}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z\n\n    move-result v0\n\n    if-eqz v0, :cond_vs_bottom_searchbar_before_load_original\n\n    const/4 v0, 0x1\n\n    return v0\n\n    :cond_vs_bottom_searchbar_before_load_original\n\n#s' "$file"

	perl -0pi -e 's#(\.method public static isSupportQuickSearchBar\(\)Z\s+\.locals 1\s+)#${1}    const-string/jumbo v0, "vs_bottom_searchbar"\n\n    invoke-static {v0}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z\n\n    move-result v0\n\n    if-eqz v0, :cond_vs_bottom_searchbar_support_original\n\n    const/4 v0, 0x1\n\n    return v0\n\n    :cond_vs_bottom_searchbar_support_original\n\n#s' "$file"

	if grep -q "vs_bottom_searchbar" "$file"; then
		log "$pkg_name" "Bottom search bar controlled by global vs_bottom_searchbar" true
	else
		log "$pkg_name" "Bottom search bar patch failed" true
	fi
}

# getInstalledApplications()/getInstalledPackages() both route through this
# ComputerEngine.shouldFilterApplication(PackageStateInternal, callingUid,
# userId) overload before a result is ever handed to PackageInfoUtils, so
# patching it here covers both APIs. This is separate from (and runs before)
# the OEM filterApplicationInfoInGIALI/filterPackageInComputerEngineGIPB
# extension hooks, which are not touched by this patch. callingUid (p2) is
# the app calling getInstalledApplications/getInstalledPackages, not the
# target package in p1. The "caller viewing itself" check is done here with
# plain UID arithmetic (ps.getAppId() + UserHandle.getUid), NOT a
# PackageManager call: PackageManager.getPackagesForUid()/getApplicationInfo()
# route back through this exact ComputerEngine method internally, so calling
# them from Toolbox recursed infinitely and crashed system_server. Toolbox
# only does the global-list membership + UID-range check plus a bounded,
# verified-safe VSTeam-self lookup (see getVsteamUidForFilter in Toolbox.java).
patch_pm_always_hidden_packages() {
	local label="ComputerEngine (PackageManager)"
	local file
	file=$(sudo find -name "ComputerEngine.smali" | head -1)

	if [[ ! -f "$file" ]]; then
		log "$label" "shouldFilterApplication patch target not found: ComputerEngine.smali" true
		return 0
	fi

	if grep -q "isPackageAlwaysHidden" "$file"; then
		log "$label" "already patched, skip" true
		return 0
	fi

	perl -0pi -e 's#(\.method public final shouldFilterApplication\(Lcom/android/server/pm/pkg/PackageStateInternal;II\)Z\s+\.(?:locals|registers) \d+\s+\.param p1, "ps".*?\.param p3, "userId".*?\n)#${1}\n    if-eqz p1, :cond_vs_always_hidden_skip\n\n    invoke-interface {p1}, Lcom/android/server/pm/pkg/PackageStateInternal;->getAppId()I\n\n    move-result v0\n\n    invoke-static {p3, v0}, Landroid/os/UserHandle;->getUid(II)I\n\n    move-result v0\n\n    if-eq v0, p2, :cond_vs_always_hidden_skip\n\n    invoke-interface {p1}, Lcom/android/server/pm/pkg/PackageStateInternal;->getPackageName()Ljava/lang/String;\n\n    move-result-object v0\n\n    invoke-static {v0, p2, p3}, Lcom/android/vsteam/Toolbox;->isPackageAlwaysHidden(Ljava/lang/String;II)Z\n\n    move-result v0\n\n    if-eqz v0, :cond_vs_always_hidden_skip\n\n    const/4 v0, 0x1\n\n    return v0\n\n    :cond_vs_always_hidden_skip\n\n#s' "$file"

	if grep -q "isPackageAlwaysHidden" "$file"; then
		log "$label" "getInstalledApplications/getInstalledPackages visibility overridable via global vs_always_hidden_packages" true
	else
		log "$label" "shouldFilterApplication patch failed" true
	fi
}

# HyperOS 3 launcher is Rust/Flutter (no dex): the smali patches above do not
# apply and miui.os.Build spoofing never reaches it. Rename its
# MiuiOsBuild_is_{international,global}_build imports so it runs as global.
patch_miuihome_global_native() {
	local pkg_name="${pkg:-com.miui.home}"
	local patcher="$dir/bin/patch_miuihome_global.py"
	local lib
	local changed

	lib=$(sudo find . -type f -path "*/arm64-v8a/libapp_launcher.so" | head -n 1)
	if [[ -z "$lib" ]]; then
		log "$pkg_name" "Global launcher: libapp_launcher.so not found (Java launcher?)" true
		return 0
	fi

	changed=$(sudo python3 "$patcher" "$lib")
	if [[ $? -ne 0 ]]; then
		log "$pkg_name" "Global launcher native patch fail" true
		return 1
	fi

	log "$pkg_name" "Global launcher native patched ${changed:-0} import(s)" true
	return 0
}

# HyperOS 3 Rust/Flutter apps (launcher...) compute miui.os.Build region flags
# natively from ro.product.mod_device (libhyper_os_core_system_private). Point
# them at ro.hypervs.mod_device (set with the other HyperMushrom props) so the ROM
# decides their region without changing the real ro.product.mod_device used by
# Java/C++ code.
HyperOSModDevice() {
	local lib
	local changed

	lib=$(sudo find "$dir/img_temp" -type f -path "*/lib64/libhyper_os_core_system_private.dylib.so" | head -n 1)
	if [[ -z "$lib" ]]; then
		log HyperOS "ModDevice: libhyper_os_core_system_private.dylib.so not found" true
		return 0
	fi

	changed=$(sudo python3 "$dir/bin/patch_hyperos_mod_device.py" "$lib")
	if [[ $? -ne 0 ]]; then
		log HyperOS "ModDevice: patch fail" true
		return 1
	fi
	log HyperOS "ModDevice: Rust MiuiOsBuild reads ro.hypervs.mod_device (${changed:-0} patched)" true
}

# The HyperOS 3 launcher's Dart code (lib/arm64-v8a/libapp.so) also reads
# ro.product.mod_device by itself, outside MiuiOsBuild; point it at
# ro.hypervs.mod_device as well so its UI follows HyperOSModDevice.
MiuiHomeNative() {
	pkg='com.miui.home'
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		local lib
		local changed
		lib=$(sudo find . -type f -path "*/arm64-v8a/libapp.so" | head -n 1)
		if [[ -z "$lib" ]]; then
			log $pkg "ModDevice: libapp.so not found (Java launcher?)" true
		else
			changed=$(sudo python3 "$dir/bin/patch_hyperos_mod_device.py" "$lib")
			if [[ $? -ne 0 ]]; then
				log $pkg "ModDevice: libapp.so patch fail" true
				return 1
			fi
			log $pkg "ModDevice: Dart reads ro.hypervs.mod_device (${changed:-0} patched)" true
		fi

		# Dock: Phone, Messages, Chrome, Camera. Slot 2 is com.android.browser,
		# which global ROMs do not ship.
		# with apktool -r it lands in unknown/res/xml
		local hotseats
		hotseats=$(sudo find . -type f -path "*/res/xml/layout_hotseats.xml" | head -n 1)
		if [[ -n "$hotseats" ]]; then
			changed=$(sudo python3 "$dir/bin/patch_miuihome_hotseat.py" "$hotseats" \
				2 com.android.chrome com.google.android.apps.chrome.Main)
			if [[ $? -ne 0 ]]; then
				log $pkg "Hotseat: Chrome patch fail" true
				return 1
			fi
			log $pkg "Hotseat: Chrome in dock (${changed:-0} patched)" true
		else
			log $pkg "Hotseat: layout_hotseats.xml not found" true
		fi

		recp $tempstring $1
	fi
}

patch_miuihome_dock_blur() {
	local pkg_name="${pkg:-com.miui.home}"
	local patcher="$dir/bin/patch_miuihome_dock_blur.py"
	local -a files
	local changed

	mapfile -t files < <(sudo find . -type f -name "HotSeatsScreenViewContent.smali")
	if [[ ${#files[@]} -eq 0 ]]; then
		log "$pkg_name" "Dock Blur HotSeatsScreenViewContent.smali not found" true
		return 1
	fi

	if [[ ! -f "$patcher" ]]; then
		log "$pkg_name" "Dock Blur patcher not found: $patcher" true
		return 1
	fi

	changed=$(python3 "$patcher" "${files[@]}")
	if [[ $? -ne 0 ]]; then
		log "$pkg_name" "Dock Blur regex patch fail" true
		return 1
	fi

	log "$pkg_name" "Dock Blur regex patched ${changed:-0} file(s)" true
	return 0
}

patch_miuihome_folder_icon_blur() {
	local pkg_name="${pkg:-com.miui.home}"
	local patcher="$dir/bin/patch_miuihome_folder_blur.py"
	local -a files
	local changed

	mapfile -t files < <(sudo find . -type f -name "FolderBlurUtilities.smali")
	if [[ ${#files[@]} -eq 0 ]]; then
		log "$pkg_name" "Folder Icon Blur FolderBlurUtilities.smali not found" true
		return 1
	fi

	if [[ ! -f "$patcher" ]]; then
		log "$pkg_name" "Folder Icon Blur patcher not found: $patcher" true
		return 1
	fi

	changed=$(python3 "$patcher" "${files[@]}")
	if [[ $? -ne 0 ]]; then
		log "$pkg_name" "Folder Icon Blur regex patch fail" true
		return 1
	fi

	log "$pkg_name" "Folder Icon Blur patched ${changed:-0} file(s)" true
	return 0
}

ctemp() {
	file=$(sudo find $dir/img_temp -type f -name $2)
	for i in $file; do
		echo $i

		if [[ $1 == "t" ]]; then
			sudo cp -rf $i $dir/jar_temp
		elif [[ $1 == "b" ]]; then
			sudo cp -rf $dir/jar_temp/$2 $i
		fi
	done
}


lookup_device_platform() {
	local device_name="$1"
	local platform_list="${2:-$dir/fastboot/platfrom.list}"

	[[ -f "$platform_list" ]] || return 1

	awk -F= -v wanted="${device_name^^}" '
        /^[[:space:]]*($|#)/ { next }
        {
            key = $1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
            if (toupper(key) != wanted) {
                next
            }

            value = substr($0, index($0, "=") + 1)
            sub(/\r$/, "", value)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            if (value != "") {
                print value
            }
            exit
        }
    ' "$platform_list"
}

read_lk_platform() {
	local lk_image="${1:-$(resolve_lk_image "$dir/$device/firmware-update")}"
	local lk_info_script="${2:-$dir/bin/lk_info.py}"
	local report platform

	[[ -f "$lk_image" ]] || return 1

	if [[ -f "$lk_info_script" ]] && report="$(python3 "$lk_info_script" --json --no-green "$lk_image" 2>/dev/null)"; then
		platform="$(python3 -c '
import json
import sys

try:
    reports = json.load(sys.stdin)
    platforms = reports[0].get("platform_ids", []) if reports else []
except (AttributeError, IndexError, TypeError, ValueError):
    raise SystemExit(1)

for platform in platforms:
    platform = str(platform).strip()
    if platform:
        print(platform)
        raise SystemExit(0)
raise SystemExit(1)
' <<<"$report" 2>/dev/null)"
		if [[ -n "$platform" ]]; then
			printf '%s\n' "$platform"
			return 0
		fi
	fi

	# A valid platform marker can still be read when the LK container or its
	# certificate layout is newer than lk_info.py understands.
	platform="$(LC_ALL=C grep -aom1 -E 'k[0-9]{4}v[0-9]+_64' "$lk_image")" || return 1
	platform="${platform%%$'\n'*}"
	[[ -n "$platform" ]] || return 1
	printf '%s\n' "$platform"
}

# Read the board name from whichever partition properties were extracted.
# Do not assume odm/vendor exist: newer extraction profiles intentionally omit
# both partitions, while system/product/system_ext commonly still carry the
# same property.
read_platform_from_props() {
	local root="${1:-$dir/img_temp}"
	local prop_file value key
	local -a keys=(ro.board.platform ro.boot.hardware ro.hardware)

	[[ -d "$root" ]] || return 1

	for key in "${keys[@]}"; do
		while IFS= read -r -d '' prop_file; do
			value="$(awk -F= -v wanted="$key" '
                $1 == wanted {
                    value = $0
                    sub(/^[^=]*=/, "", value)
                    gsub(/[\r\n]/, "", value)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                    gsub(/^"|"$/, "", value)
                    if (value != "") { print value; exit }
                }
            ' "$prop_file")"
			if [[ -n "$value" && "$value" != "qcom" && "$value" != "mtk" && "$value" != "mediatek" ]]; then
				printf '%s\n' "$value"
				return 0
			fi
		done < <(find "$root" -type f \( -name '*.prop' -o -name 'build.prop' \) -print0 2>/dev/null)
	done
	return 1
}

# Stock builds keep the partitions as images (nothing in img_temp), so read
# build.prop straight out of vendor.img / odm.img: EROFS through extract.erofs
# (which writes <out>/<partition>/<path> but does not create the parents),
# ext4 through debugfs.
read_platform_from_images() {
	local firmware_dir="${1:-$dir/$device/firmware-update}"
	local work part path image platform
	local -a props=(vendor:/build.prop odm:/etc/build.prop vendor:/odm/etc/build.prop)

	work="$(mktemp -d)" || return 1
	for part in "${props[@]}"; do
		path="${part#*:}"
		part="${part%%:*}"
		image="$firmware_dir/$part.img"
		[[ -f "$image" ]] || continue
		mkdir -p "$work/$part$(dirname "$path")"
		"$dir/bin/extract.erofs" -i "$image" -X "$path" -o "$work" >/dev/null 2>&1
		if [[ ! -s "$work/$part$path" ]] && command -v debugfs >/dev/null 2>&1; then
			debugfs -R "cat $path" "$image" >"$work/$part$path" 2>/dev/null
		fi
	done
	rm -rf "$work/config"
	platform="$(read_platform_from_props "$work")"
	rm -rf "$work"
	[[ -n "$platform" ]] || return 1
	printf '%s\n' "$platform"
}

# Some devices expose the board name only in boot-chain binaries (usually
# boot, init_boot, vendor_boot or abl). Their embedded cmdline/property text
# can be recovered without unpacking or extracting odm/vendor.
read_platform_from_boot_images() {
	local firmware_dir="${1:-$dir/$device/firmware-update}"
	local image line value fp_vendor fp_device fp_build
	local -a images=(
		"$firmware_dir/boot.img"
		"$firmware_dir/init_boot.img"
		"$firmware_dir/vendor_boot.img"
		"$firmware_dir/abl.img"
		"$firmware_dir/abl_a.img"
		"$firmware_dir/abl_b.img"
	)

	for image in "${images[@]}"; do
		[[ -f "$image" ]] || continue
		while IFS= read -r line; do
			# Android fingerprints use vendor/device/device:version.  On
			# newer Qualcomm builds this is often the only reliable source
			# left after odm/vendor are not extracted.
			IFS=/ read -r fp_vendor fp_device fp_build <<<"$line"
			if [[ -n "$fp_device" && "$fp_build" == "$fp_device:"* ]]; then
				case "$fp_device" in
				qcom | mtk | mediatek | samsung | universal*) ;;
				*)
					printf '%s\n' "$fp_device"
					return 0
					;;
				esac
			fi

			value="${line#*=}"
			[[ "$value" == "$line" ]] && continue
			value="${value%%[[:space:]]*}"
			value="${value%$'\r'}"
			case "${line%%=*}" in
			ro.board.platform | ro.boot.hardware | androidboot.hardware | ro.hardware)
				case "$value" in
				"" | qcom | mtk | mediatek | samsung | universal*) continue ;;
				esac
				printf '%s\n' "$value"
				return 0
				;;
			esac
		done < <(strings -a -n 4 "$image" 2>/dev/null | grep -E '(^[^/[:space:]]+/[^/[:space:]]+/[^/[:space:]]+:[^[:space:]]+)|^(ro\.board\.platform|ro\.boot\.hardware|androidboot\.hardware|ro\.hardware)=')
	done
	return 1
}

device_platform() {
	local platform_list="$dir/fastboot/platfrom.list"
	local firmware_dir="$dir/$device/firmware-update"
	local lk_backup_dir="$dir/$device/mics"
	local lk_image
	local lk_platform=""
	local mapped_platform=""
	local property_platform=""
	local boot_platform=""
	local mapped_platform_upper
	local mapped_check_file

	log_start SystemProperties Checks

	declare -a patched_files=()

	# lk.img stays in firmware-update and is shipped. Workspaces from older
	# builds may only have the copy that used to be moved to mics.
	lk_image="$(resolve_lk_image "$firmware_dir")"
	if [[ ! -f "$lk_image" && -f "$lk_backup_dir/lk.img" ]]; then
		lk_image="$lk_backup_dir/lk.img"
		log_event info SystemProperties "LK" "" "reuse $device/mics/lk.img"
	fi

	if [[ -f "$lk_image" ]]; then
		lk_platform="$(read_lk_platform "$lk_image")"
		if [[ -n "$lk_platform" ]]; then
			log_event info SystemProperties "LK Platform" "" "$lk_platform"
		else
			log_warn SystemProperties "LK Platform" "not found; using platform list"
		fi
	fi

	if [[ -f "$platform_list" ]]; then
		mapped_platform="$(lookup_device_platform "$device" "$platform_list")"
	else
		log_warn SystemProperties "Platform List" "missing: $platform_list"
	fi

	property_platform="$(read_platform_from_props)"
	if [[ -n "$property_platform" ]]; then
		log_event info SystemProperties "Partition Platform" "" "$property_platform"
	fi
	if [[ -z "$property_platform" ]]; then
		property_platform="$(read_platform_from_images "$firmware_dir")"
		if [[ -n "$property_platform" ]]; then
			log_event info SystemProperties "Image Platform" "" "$property_platform"
		fi
	fi
	if [[ -z "$property_platform" ]]; then
		boot_platform="$(read_platform_from_boot_images "$firmware_dir")"
		if [[ -n "$boot_platform" ]]; then
			log_event info SystemProperties "Boot/ABL Platform" "" "$boot_platform"
		fi
	fi
	platform="$lk_platform"
	[[ -z "$platform" ]] && platform="$mapped_platform"
	[[ -z "$platform" ]] && platform="$property_platform"
	[[ -z "$platform" ]] && platform="$boot_platform"
	[[ -z "$platform" ]] && platform="$device"

	platform_upper=$(echo "$platform" | tr '[:lower:]' '[:upper:]')

	check_file="$dir/$device/firmware-update/$platform.check"
	ossi_file="$dir/$device/firmware-update/ossi.check"
	device_check_file="$dir/$device/firmware-update/$device.check"

	rm -f "$dir/$device/firmware-update/.check"

	# Platform Check
	cp "$dir/info" "$check_file"
	sed -i "s/^lower=.*/lower=$platform_upper/" "$check_file"
	sed -i "s/^device=.*/device=$platform_upper/" "$check_file"
	log_success SystemProperties "Platform Check" "$platform.check"
	patched_files+=("$platform.check")

	# OSSI Check
	cp "$dir/info" "$ossi_file"
	sed -i "s/^lower=.*/lower=OSSI/" "$ossi_file"
	sed -i "s/^device=.*/device=OSSI/" "$ossi_file"
	log_success SystemProperties "OSSI Check" "ossi.check"
	patched_files+=("ossi.check")

	# Device Codename Check
	if [[ "$device" != "$platform" ]]; then
		cp "$dir/info" "$device_check_file"
		sed -i "s/^lower=.*/lower=${device^^}/" "$device_check_file"
		sed -i "s/^device=.*/device=${device^^}/" "$device_check_file"
		log_success SystemProperties "Device Check" "$device.check"
		patched_files+=("$device.check")
	fi

	# Additional platform check from fastboot/platfrom.list
	if [[ -n "$mapped_platform" && "${mapped_platform,,}" != "${platform,,}" && "${mapped_platform,,}" != "${device,,}" ]]; then
		mapped_platform_upper="${mapped_platform^^}"
		mapped_check_file="$dir/$device/firmware-update/$mapped_platform.check"
		cp "$dir/info" "$mapped_check_file"
		sed -i "s/^lower=.*/lower=$mapped_platform_upper/" "$mapped_check_file"
		sed -i "s/^device=.*/device=$mapped_platform_upper/" "$mapped_check_file"

		log_success SystemProperties "Mapped Platform Check" "$mapped_platform.check"
		patched_files+=("$mapped_platform.check")
	fi

	log_done SystemProperties Checks "${patched_files[*]}"
}

del_feature() {
	find . -type f -name "*.xml" -exec sed -i "/$1/d" {} +
}

update_app_features() {
	local insert_file="$dir/module/lang/oneplus_features.xml"

	# Kiểm tra file chèn có tồn tại
	[[ ! -f "$insert_file" ]] && {
		echo "❌ Không tìm thấy $insert_file"
		return 1
	}

	# Lấy danh sách các feature name cần xóa
	local names_to_remove=()
	while read -r line; do
		if [[ "$line" =~ name=\"([^\"]+)\" ]]; then
			names_to_remove+=("${BASH_REMATCH[1]}")
		fi
	done <"$insert_file"

	# Danh sách các file đích
	local xml_paths=(
		"$dir/img_temp/my_stock/etc/extension/com.oplus.app-features.xml"
		"$dir/img_temp/my_region/etc/extension/com.oplus.app-features.xml"
	)

	for target in "${xml_paths[@]}"; do
		[[ ! -f "$target" ]] && {
			echo "⚠️  Bỏ qua: $target không tồn tại"
			continue
		}

		echo "🔧 Đang xử lý: $target"

		# 🔸 Xóa các node theo tên bằng xmlstarlet
		for feature_name in "${names_to_remove[@]}"; do
			xmlstarlet ed -P -L -d "//app_feature[@name='${feature_name}']" "$target"
		done

		# 🔸 Thêm nội dung trước </extend_features>
		local tmp_file="${target}.tmp"
		awk -v insert="$(<"$insert_file")" '
            /<\/extend_features>/ { print insert }
            { print }
        ' "$target" >"$tmp_file" && mv "$tmp_file" "$target"

		echo "✅ Đã cập nhật: $target"
	done
}

add_feature() {
	local feature_key="$1"
	local file="$dir/img_temp/my_stock/etc/extension/com.oplus.app-feature_vsteam.xml"

	if [[ -z "$feature_key" ]]; then
		echo "Thieu feature key"
		return 1
	fi

	if [[ ! -f "$file" ]]; then
		mkdir -p "$(dirname "$file")"
		cat >"$file" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<oplus-config>
</oplus-config>
EOF
	fi

	if grep -q "$feature_key" "$file"; then
		return 0
	fi

	sed -i "/<\/oplus-config>/i\\    <oplus-feature name=\"$feature_key\"/>" "$file"
}

# ColorOS

MiuiHome() {
	pkg='com.miui.home'
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Crack paid widget"
		repM 'themeManagerSupportPaidWidget' 0 MamlutilKt.smali
		repM 'isCanAutoStartDownload' 1 MaMlPendingHostView.smali

		log $pkg "Swipe For Assistant" true
		repM "isSupportGoogleAssist" 1 "AssistManager.smali"
		apply_smali_config "$dir/module/lang/custom_cornor_swipe.config" "FsGestureAssistHelper.smali" || return 1

		log $pkg "Control swipe up to open google search" true
		repM 'isBottomGlobalSearchEnable' global "SearchEdgeLayout.smali" "vs_swipeup_search"
		ffalse $(sudo find -name "GlobalSearchGestureMonitor.smali") $key1

		log $pkg "Allow switch personal assistant" true
		apply_smali_config "$dir/module/lang/switchPA.config" "LauncherAssistantCompat.smali" || return 1
		repM 'isSupportMIUIWidget' globalz "DeviceConfig.smali" "vs_minus_screen_ggl"
		repM 'isUseGoogleMinusScreen' global "DeviceConfig.smali" "vs_minus_screen_ggl"
		foption $key1 "vs_force_vault_gl" 'MIUIWidgetCompat.smali'
		foption $key1 "vs_force_vault_gl" 'WidgetsVerticalAdapter.smali'

		log $pkg "Support Search bar" true
		repM 'isGlobalSearchSupportSearchBar' global "SearchBarConfig.smali" "vs_search_bar"
		repM 'isGlobalSearchSupportSearchBarIcon' 1 "SearchBarConfig.smali"
		repM 'getSearchBarSource' textz "SearchBarConfig.smali" ""
		fstring '"home_search_"' '""' "SearchBarUtils.smali"
		fstring '"small_"' '""' "SearchBarUtils.smali"
		fstring '"large_"' '""' "SearchBarUtils.smali"

		log $pkg "Control bottom Google search bar" true
		patch_miuihome_bottom_searchbar

		log $pkg "Pull Down Search" true
		repM 'isPullDownGestureGlobalSearch' global "GlobalSearchUtil.smali" "vs_pulldown_google_search"

		#search engine
		apply_smali_config "$dir/module/lang/searchbar_provider.config" "OpenSearchUtil.smali" || return 1
		#ftrue $(sudo find -name 'SearchBarConfig.smali') $key1
		fstring '"home_down"' '""'
		fstring '"home_up"' '""'

		log $pkg "Support Google CTS" true
		apply_smali_config "$(versioned_config "$dir/module/lang/double_press_gestureline" ".config")" "NavStubGestureEventManager.smali" || return 1
		apply_smali_config "$(versioned_config "$dir/module/lang/long_press_gestureline" ".config")" "NavStubGestureEventManager.smali" || return 1
		repM "isSupportDoubleAction" 1 "NavStubGestureEventManager.smali"
		repM "isSupportVoiceAssistant" 1 "NavStubGestureEventManager.smali"

		patch_miuihome_dock_blur
		patch_miuihome_folder_icon_blur
		patch_miuihome_global_native || return 1

		recp $tempstring $1
	fi
}

misystemui() {
	pkg="com.android.systemui"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then

		decp $tempstring || return 1
		rm -rf classes.dex.out

		keysui='Lcom/android/systemui/controlcenter/utils/Constants;->IS_INTERNATIONAL:Z'
		keysui1='Lcom/miui/utils/configs/MiuiConfigs;->IS_INTERNATIONAL_BUILD:Z'
		keysui2='Lcom/android/systemui/statusbar/notification/NotificationSettingsManager;->USE_WHITE_LISTS:Z'
		keysui2_15='Lcom/miui/systemui/notification/NotificationSettingsManager;->USE_WHITE_LISTS:Z'
		keysui3='Lcom/miui/utils/configs/MiuiConfigs;->IS_PAD:Z'

		#for lockscreen noti
		log $pkg "Lockscreen notification"
		ftrue $(sudo find -name 'NotificationSettingsManager.smali') $keysui
		ffalse $(sudo find -name 'NotificationSettingsManager.smali') $keysui2

		repM "canShowFocus" 1 NotificationSettingsManager.smali

		log $pkg "Fix Quickshare tile"
		ftrue $(sudo find -name 'QSTileHost.smali') $keysui1
		ftrue $(sudo find -name 'QuickAccessWalletTile.smali') $key1

		log $pkg "Option to Turn On/off clipboard overlay"
		sudo sed -i 's/invoke-interface {\([^,]*\), \([^}]*\)}, Ljava\/util\/List;->contains(Ljava\/lang\/Object;)Z/const-string\/jumbo \1, "vs_overlay_clipboard"\ninvoke-static {\1}, Lcom\/android\/vsteam\/Toolbox;->getBooleanGlobal(Ljava\/lang\/String;)Z/' $(sudo find -type f -name ClipboardListener.smali)
		repM 'shouldSuppressOverlay' globalz 'ClipboardListener.smali' "vs_overlay_clipboard"

		log $pkg "Fullscreen call"
		ftrue $(sudo find -name 'MobileSignalController.smali') $key1
		ftrue $(sudo find -name 'InCallUtils.smali') $key1
		ftrue $(sudo find -name 'NotificationSettingsManager.smali') $keysui1
		ftrue $(sudo find -name 'isGlobalInCallNotification.smali') $keysui1
		ftrue $(sudo find -name 'MiuiNotificationInterruptStateProviderImpl.smali') $keysui1

		log $pkg "Control CC data usage" true
		foption 'Lmiuix/os/Build;->IS_INTERNATIONAL_BUILD:Z' "vs_disable_data_usage" 'ControlCenter.smali'

		log $pkg "Remove Chinese fingerprint fod"
		ftrue $(find -type f -name 'MiuiGxzwQuickOpenUtil.smali') $key1

		log $pkg "Hide mobile type when disconect/wifi"
		ftrue $(find -type f -name 'MiuiOperatorCustomizedPolicy.smali') $key1
		fstring "config_status_bar_show_mobile_type_when_wifi_on" "vsteam"
		for i in $(find . -type f -exec grep -l "MiuiCellularIconVM" {} + | xargs grep -l "IS_INTERNATIONAL_BUILD" | xargs grep -l "combine"); do
			ftrue $(find -type f -name $(basename "$i")) $key1
		done

		# Blur radius / tint of the panel background follow vs_cc_blur_level and
		# vs_cc_dim_level. Older SystemUI builds have no shade.blur package.
		log $pkg "Tunable control center blur and dim" true
		if python3 "$dir/bin/patch_shade_translucency.py" "$(pwd)"; then
			add_cap cc_blur
		else
			log_warn "$pkg" "Tunable control center blur and dim" "shade blur controllers not found; skip"
		fi

		# AOSP stacked dual-SIM icon, shown while vs_stacked_mobile is on.
		log $pkg "Stacked dual SIM signal icon" true
		if python3 "$dir/bin/patch_stacked_mobile.py" "$(pwd)"; then
			add_cap stacked_mobile
		else
			log_warn "$pkg" "Stacked dual SIM signal icon" "stacked mobile icon not found; skip"
		fi

		recp $tempstring $1
	fi
}

opsystemui() {
	pkg="com.android.systemui"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		if android_version_at_least 17; then
			# Long-pressing the clock runs the action picked in Mushrom, and the
			# five-minute limit of the seconds can be turned off there.
			log $pkg "Clock long press action, optional seconds without time limit"
			python3 "$dir/bin/patch_clock_seconds.py" "$(pwd)" || return 1
			add_cap clock_actions
		fi

		# Blur radius / tint of the panel background follow vs_cc_blur_level and
		# vs_cc_dim_level, same keys as the HyperOS shade.
		log $pkg "Tunable control center blur and dim" true
		if python3 "$dir/bin/patch_oplus_shade_translucency.py" "$(pwd)"; then
			add_cap cc_blur
		else
			log_warn "$pkg" "Tunable control center blur and dim" "panel blur helpers not found; skip"
		fi

		log $pkg "Support Google CTS" true
		$repS "$dir/module/lang/color_cts_15.config" $(find -type f -name GestureHomeHandleEventController.smali) "replace"
		repM 'hasListeners' 1 'GestureHomeHandleEventController.smali'
		repM 'access$enableLongPress' 1 'OplusNavigationHandle.smali'
		repM 'shouldHandlePress' 1 'OplusNavigationHandle.smali'

		log $pkg "Hidden gesture bar long press"
		sudo find . -type f -name 'FeatureOption.smali' -exec sed -i '/sput-boolean p0, .*CustomizeFeatureOption;->sIsSupportCircleToSearchNavbarHidden:Z/i\    const/4 p0, 0x1' {} +
		fstring 'Lcom/oplus/systemui/navigationbar/utils/NavBarUtils;->isSideGestureBarHide()Z' 'Lcom/android/vsteam/VsProps;->justFalse()Z' 'SideGestureDetector$$ExternalSyntheticLambda3.smali'

		log $pkg "Overlay Clipboard" true
		repM 'shouldSuppressOverlay' 0 'ClipboardListener.smali'
		fstring 'Lcom/oplusos/systemui/common/feature/FeatureOption;->isExpRegion()Z' 'Lcom/android/vsteam/Toolbox;->OverlayClipboard()Z' "ClipboardListener.smali"

		log $pkg "Usb Dialog" true
		repM 'isDisableUSBDialog' global "UsbUtils.smali" "vs_usb_dialog"

		log $pkg "Unlimited person row"
		patch_output=$($prowqs --root "$(pwd)" --summary-only 2>&1)
		patch_status=$?
		if [[ -n "$patch_output" ]]; then
			while IFS= read -r patch_line; do
				[[ "$patch_line" == "=== Patch summary ===" ]] && continue
				[[ -n "$patch_line" ]] && log_event info Patch "$pkg" "" "$patch_line"
			done <<<"$patch_output"
		fi
		if [[ $patch_status -ne 0 ]]; then
			log_fail "$pkg" "Unlimited person row" "Patch failed"
		fi

		log $pkg "Enable global coloros stuff"
		files=(
			NavBarUtils.smali
			NavBarRecoveryProxy.smali
			NavBarSettingsValueProxy.smali
			CleanTile.smali
			OplusLocationTile.smali
			OplusWifiTile.smali
			CurvedDisplayManager.smali
			OplusAodCurvedDisplayView.smali
			QuickAccessWalletTile.smali
			KeyguardNotificationStackedRuler.smali
			NotificationSortExImpl.smali
			OplusNotificationGroupExtImpl.smali
			OplusSwipeHelperExImpl.smali
			NotificationCenterProxy.smali
			NotificationMenuRowExtImpl.smali
			OplusKeyguardNotificationVisibilityProviderExImpl.smali
		)

		for file in "${files[@]}"; do
			fstring 'Lcom/oplusos/systemui/common/feature/FeatureOption;->isExpRegion()Z' \
				'Lcom/android/vsteam/VsProps;->justTrue()Z' "$file"
		done

		log $pkg "swipe cornor to call action" true
		repM 'isAssistantAvailable' 1 'NavBarUtils.smali'
		$repS "$dir/module/lang/color_wipe_cornor.config" $(sudo find -name "NavBarHelper.smali") "replace"

		log $pkg "Remove anoy dev notification"
		repM 'updateDeveloperMode' void 'SystemPromptController.smali'

		target_smali=$(find . -type f -name "*OplusQSDateView\$\$ExternalSyntheticLambda*.smali" \
			-exec grep -l "Landroid/widget/TextView;->setText" {} + |
			head -n 1)

		smali_name=$(basename "$target_smali")

		log $pkg "Allow show Lunar Year" true
		$lunar $(sudo find -name $smali_name)

		cp -rf $dir/module/lang/systemui/app_systemui_oplus_media_controller_config.xml assets/app_systemui_oplus_media_controller_config.xml
		fstring com.oplus.romupdate com.bhlnk.vsteam

		fstring com.google.android.calendar com.oplus.calendar
		fstring com.coloros.calendar com.oplus.calendar
		force_put 'Lcom/oplusos/systemui/common/feature/QSFeatureOption;->isCalendarCn:Z' 0 'QSFeatureOption.smali'

		repM 'isAodMediaSupport' 1 'AodMediaDataListener$Companion.smali'
		repM 'isAodMediaSupportWithoutFeature' 1 'AodMediaDataListener$Companion.smali'
		repM 'isFlavorTwoDevice' global "FlavorTwoFeatureOption.smali" "vs_oplus_redone"

		log $pkg "Enable Keyguard Quick Affordance (UnavailableOnDevice)" true

		mapfile -t files < <(
			find . -type f -name "*.smali" \
				-exec grep -l 'KeyguardQuickAffordanceConfig\$PickerScreenState\$UnavailableOnDevice' {} +
		)

		if [[ ${#files[@]} -gt 0 ]]; then
			for file in "${files[@]}"; do
				fstring 'Lcom/oplusos/systemui/common/feature/FeatureOption;->isExpRegion()Z' \
					'Lcom/android/vsteam/VsProps;->justTrue()Z' "$file" r
			done
		fi

		mapfile -t files < <(
			find . -type f -name "*.smali" \
				-path "*com/oplus/systemui/statusbar/notification*" \
				-exec grep -l \
				-e 'isExpRegion' \
				{} +
		)

		if [[ ${#files[@]} -gt 0 ]]; then
			for file in "${files[@]}"; do
				fstring \
					'Lcom/oplusos/systemui/common/feature/FeatureOption;->isExpRegion()Z' \
					'Lcom/android/vsteam/VsProps;->justTrue()Z' \
					"$file" r
			done
		fi

		recp $tempstring $1
	fi
}

plugin() {
	pkg="miui.systemui.plugin"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		keyplugin='Lmiui/systemui/notification/NotificationUtil;->IS_INTERNATIONAL_BUILD:Z'

		log $pkg "Toggle Super Volume" true

		repM 'voiceSupportSuperVolume' global 'VolumeUtils.smali' "vs_super_volume"
		repM 'getSUPER_VOLUME_SUPPORTED' global 'VolumeUtils.smali' "vs_super_volume"
		repM 'getSUPER_VOLUME_VOICE_CALL_SUPPORTED' global 'VolumeUtils.smali' "vs_super_volume"

		files=$(grep -rlE "$key1|$key2" .)

		repM 'isSupportBlurS' 1 Util.java
		repM 'canCustomFocus' 1 NotificationSettingsManager.smali
		ftrue $(sudo find -name 'FocusNotificationController.smali') $keyplugin

		recp $tempstring $1
	fi
}

# OS4 control center lives in MIUISystemUIPlugin. Kept apart from plugin(),
# whose patches target the pre-OS4 plugin.
plugincc() {
	pkg="miui.systemui.plugin"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Optional transparent active tiles" true
		if python3 "$dir/bin/patch_cc_flat_tile.py" "$(pwd)"; then
			add_cap cc_flat
		else
			log_warn "$pkg" "Optional transparent active tiles" "tile icon view not found; skip"
		fi

		recp $tempstring $1
	fi
}

PowerKeeper() {
	pkg="com.miui.powerkeeper"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Prevent kill app"
		find -type f -name '*.smali' -exec sed -i 's/Lmiui\/process\/ProcessManager;->kill(Lmiui\/process\/ProcessConfig;)Z/Lcom\/android\/vsteam\/Toolbox;->kill(Lmiui\/process\/ProcessConfig;)Z/g' {} +

		repM "startCloudSyncData" void LocalUpdateUtils.smali
		repM "clearApp" void PowerCheckerController.smali
		repM "clearApp" void SleepModeControllerNew.smali
		repM "clearApp" void DynamicTurboPowerHandler.smali
		log $pkg "Disable screen-off sleep restrictions"
		repM applySleepConfig void PhoneSleepModeController.smali
		repM clearApp void PhoneSleepModeController.smali
		repM isSleepModeCloudEnabled 0 PhoneSleepModeController.smali
		repM restrictAlarm void PhoneSleepModeController.smali
		repM applySleepConfig void PadSleepModeController.smali
		repM clearApp void PadSleepModeController.smali
		repM isDeepSleepModeCloudEnabled 0 PadSleepModeController.smali
		repM isSleepModeCloudEnabled 0 PadSleepModeController.smali
		repM restrictAlarm void PadSleepModeController.smali
		repM switchDifferentPowerSavePolicy void PadSleepModeController.smali
		repM "autoKillApp" 0 PowerCheckerController.smali
		repM "getKillAppEnable" 0 AudioDisguiseController.smali
		repM "getKillAppEnable" 0 PowerStateMachine.smali
		repM "getNightKillAppEnable" 0 PowerStateMachine.smali
		repM "getNightKillAppEnable" 0 SleepModeControllerNew.smali
		repM "Killapp" 0 AudioDisguiseController.smali
		repM "clearAppWhenScreenOffTimeOut" void PowerStateMachine.smali
		repM clearAppWhenScreenOffTimeOutInNight void PowerStateMachine.smali

		repM "killGmsCoreProcess" void GmsCoreUtils.smali

		# PowerKeeper never changes the refresh rate per app (games at 60 Hz,
		# video, maps...); the display keeps the rate picked in Settings, which
		# still reaches the display through applyFpsViaPowerkeeper.
		log $pkg "Keep the refresh rate picked in Settings"
		repM "isSupportVideoEnhancePkg" 1 DisplayFrameSetting.smali
		repM "setScreenEffect" void DisplayFrameSetting.smali
		repM "setScreenEffectInternal" void DisplayFrameSetting.smali

		# Cloud thermal profiles land in /data/vendor/thermal/config, which
		# mi_thermald prefers over odm/etc: they would bring back the game
		# profiles removed by game_thermal_off.
		log $pkg "Block cloud thermal config"
		repM downLoadConfigureDefault void ThermalStoreUtils.smali
		repM unZipConfig void ThermalStoreUtils.smali

		ftrue $(pwd) $key1

		recp $tempstring $1
	fi
}

misettings() {
	local patch2=""
	local lz=""
	local device_params_helper
	local external_sources
	local kg

	pkg="com.android.settings"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		patch_keep_mock_location

		find -type f -name '*.smali' -exec sed -i 's/Ljava\/lang\/Double;->parseDouble(Ljava\/lang\/String;)D/Lcom\/android\/vsteam\/Toolbox;->parseDoubleFromString(Ljava\/lang\/String;)D/g' {} +
		find -type f -name '*.smali' -exec sed -i 's/Ljava\/lang\/Float;->parseFloat(Ljava\/lang\/String;)F/Lcom\/android\/vsteam\/Toolbox;->parseFloatFromString(Ljava\/lang\/String;)F/g' {} +

		log $pkg "Allow switch thirty launcher"
		ftrue $(sudo find -name 'DefaultHomeSettings$DefaultHomeSettingsFragment.smali') $key1

		log $pkg "Google sections and Privacy"
		for f in $(sudo find -name 'MiuiSettings.smali'); do
			if [[ $(grep "AddGoogleSettingsHeaders" $f) ]]; then
				patch2=$f
				break
			fi
		done

		if [[ -n "$patch2" ]]; then
			for i in $(grep -n "$key2" "$patch2" | cut -d':' -f1); do
				lz=$i
			done
		fi

		if [[ -n "$lz" ]]; then
			sed -i "${lz}s@$key2@$nd@" "$patch2"
			sed -i "${lz}s@$funt@$nd1@" "$patch2"
		fi

		sloga="HyperMushrom $(get_patch) by Mushrom"
		repM 'getSimpleOSVersion' text 'MiuiAboutPhoneUtils.smali' "$sloga"
		repM 'addVersionSuffix' text 'MiuiAboutPhoneUtils.smali' "$iden"

		fstring 'Lcom/android/settings/device/MiuiAboutPhoneUtils;->getSimpleOSVersion' 'Lcom/android/vsteam/Toolbox;->getUpdateStringforXiaomi'

		log $pkg "My device: static background image when VS Tools set one"
		while IFS= read -r -d '' my_device_settings; do
			perl -0pi -e 's@(\.method private startRuntimeShader\(Z\)V(?:(?!\.end method).)*?invoke-static \{\}, Lcom/android/settings/MiuiUtils;->isLiteOrLowDevice\(\)Z\s*\n\s*move-result ([vp][0-9]+))(?!\s*\n\s*invoke-static \{\2\}, Lcom/android/vsteam/Toolbox;->useInfoImageBg)@$1\n\n    invoke-static {$2}, Lcom/android/vsteam/Toolbox;->useInfoImageBg(Z)Z\n\n    move-result $2@s' "$my_device_settings"
		done < <(find . -type f -path '*/com/android/settings/device/MiuiMyDeviceSettings.smali' -print0)
		# Same switch for the HyperOS logo: on the image it is the plain lite
		# vector, whose color the overlay sets to suit the image, instead of the
		# light/dark blend that turns black on a dark image in light mode.
		while IFS= read -r -d '' version_card; do
			perl -0pi -e 's@(\.method private setLogoBlur\(\)V(?:(?!\.end method).)*?invoke-static \{\}, Lcom/android/settings/MiuiUtils;->isLiteOrLowDevice\(\)Z\s*\n\s*move-result ([vp][0-9]+))(?!\s*\n\s*invoke-static \{\2\}, Lcom/android/vsteam/Toolbox;->useInfoImageBg)@$1\n\n    invoke-static {$2}, Lcom/android/vsteam/Toolbox;->useInfoImageBg(Z)Z\n\n    move-result $2@s' "$version_card"
		done < <(find . -type f -path '*/com/android/settings/device/MiuiVersionCard.smali' -print0)

		ftrue $(sudo find -name 'LocaleSettingsTree.smali') $key2
		ftrue $(sudo find -name 'LocaleController.smali') $key2
		ftrue $(sudo find -name 'OtherPersonalSettings.smali') $key2
		ftrue $(sudo find -name 'MiuiLocalePicker.smali') $key1

		log $pkg "Force Device Info API language to enUS"
		while IFS= read -r -d '' device_params_helper; do
			perl -0pi -e 's@(invoke-static \{\}, Ljava/util/Locale;->getDefault\(\)Ljava/util/Locale;\s*\n\s*move-result-object ([vp][0-9]+))(?!\s*\n\s*sget-object \2, Ljava/util/Locale;->US:Ljava/util/Locale;)@$1\n\n    sget-object $2, Ljava/util/Locale;->US:Ljava/util/Locale;@g' "$device_params_helper"
		done < <(find . -type f -path '*/com/android/settings/device/DeviceParamsInitHelper.smali' -print0)

		repM isNoveltyHaptic 1 SettingsFeatures.smali

		#Enable notification vibration controller
		repM getAvailabilityStatus 0 "VibrationPreferenceController.smali"
		repM isAvailable 1 "VibrationPreferenceController.smali"
		repM getAvailabilityStatus 0 "VibrationRampingRingerTogglePreferenceController.smali"

		#Old NFC
		repM isNeedShowMiuiNFC 0 SettingsFeatures.smali

		#Speed mode
		repM getAvailabilityStatus 0 "SpeedModeToolsPreferenceController.smali"

		#DSU
		repM isAvailable 1 "SelectDSUPreferenceController.smali"

		repM isSupport 1 "OtgUtils.smali"

		log $pkg "Pass list app to grant per"
		ftrue $(sudo find -name 'SecuritySettingsTree.smali') $key1
		ftrue $(sudo find -name 'SettingsActivity.smali') $key1
		repM isSupportSafetyEmergencySettings 1 MiuiUtils.smali
		repM isNeedRemoveOneKeyMigrate 1 SettingsFeatures.smali

		log $pkg "Skip verify mi account when install thirdty app"
		repM doUnknownSourceVerify 0 ExternalSourcesDetails.smali
		while IFS= read -r -d '' external_sources; do
			# Android 17 moved the account verification inline into
			# onPreferenceChange(). Force the intent gate off so Settings applies
			# the unknown-source permission directly, matching the legacy patch.
			sed -i -E 's#invoke-static \{[^}]*\}, Lcom/android/security/AdbUtils;->isIntentEnable\(Landroid/content/Context;Landroid/content/Intent;\)Z#invoke-static {}, Lcom/android/vsteam/VsProps;->justFalse()Z#g' "$external_sources"
		done < <(find . -type f -path '*/com/android/settings/applications/appinfo/ExternalSourcesDetails.smali' -print0)

		src='Landroid/miui/AppOpsUtils;->isXOptMode'
		dest='Lcom/android/vsteam/Toolbox;->GoogleAppDetail'
		fstatic $src $dest 'InstalledAppDetailsFragment.smali'
		fstatic $src $dest 'InstalledAppDetailsTop.smali'
		fstatic $src $dest 'PreferredSettings$PreferredSettingsFragment.smali'

		log $pkg "Control Usb choose stype" true
		repM 'isInternalSW' global "UsbModeChooserActivity.smali" "vs_usb_choose_style"
		if android_version_at_least 17; then
			repM 'isInternalSW' global "UsbStatsPreferenceFragement.smali" "vs_usb_choose_style"
			repM 'isInternalSW' global "MiHandleProgressActivity.smali" "vs_usb_choose_style"
		fi

		log $pkg "Suport never sleep"
		kg=$(find . -type f \( -name KeyguardTimeoutListPreference.smali -o -name KeyguardTimeoutDropDownPreference.smali \))
		if [[ -n "$kg" ]]; then
			sudo sed -i 's/ro.vendor.display.type/ro.hypervs.nerversleep/g' $kg
			sudo sed -i 's/ro.display.type/ro.hypervs.nerversleep/g' $kg
		fi

		log $pkg "Force IME Bottom"
		fstring "com.baidu.input_mi" "com.google.android.inputmethod.latin"

		recp $tempstring $1
	fi

}

# Stock MockLocationAppPreferenceController.onDeveloperOptionsDisabled() calls
# removeAllMockLocations(), which resets android:mock_location to MODE_ERRORED
# for every app whenever Developer options is turned off. Drop only that call
# (writeMockLocation() keeps its own call) so the selected mock app stays allowed.
# Same com/android/settings/development/MockLocationAppPreferenceController.smali
# on both MIUI/HyperOS (misettings) and ColorOS/Oplus (opsettings) builds -
# verified against songyuan_190926 (HyperOS) - so both call this.
patch_keep_mock_location() {
	local pkg_name="${pkg:-com.android.settings}"
	local file
	file=$(sudo find . -name "MockLocationAppPreferenceController.smali" | head -1)

	if [[ ! -f "$file" ]]; then
		log_warn "$pkg_name" "Mock location" "MockLocationAppPreferenceController.smali not found; skip"
		return 0
	fi

	perl -0pi -e 's#(\.method public onDeveloperOptionsDisabled\(\)V(?:(?!\.end method).)*?)[ \t]*invoke-direct \{p0\}, Lcom/android/settings/development/MockLocationAppPreferenceController;->removeAllMockLocations\(\)V\n#${1}#s' "$file"

	if perl -0ne 'exit(!/\.method public onDeveloperOptionsDisabled\(\)V(?:(?!\.end method).)*?removeAllMockLocations/s)' "$file"; then
		log_warn "$pkg_name" "Mock location" "onDeveloperOptionsDisabled patch failed"
	else
		log "$pkg_name" "Keep mock location app after Developer options is disabled" true
	fi
}

opsettings() {
	pkg="com.android.settings"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		patch_keep_mock_location

		if android_version_at_least 17; then
			# The "Display time" option starts the seconds with the same
			# duration as the long press in SystemUI (see opsystemui).
			log $pkg "Clock seconds without time limit (optional)"
			python3 "$dir/bin/patch_clock_seconds.py" "$(pwd)" || return 1
		fi

		log $pkg "Use MushromTools update checker"
		fstring '"com.oplus.ota.MAIN"' '"com.bhlnk.vsteam.ROM_UPDATE"'
		fstring '"com.oplus.ota"' '"com.bhlnk.vsteam"'
		fstring 'Lcom/android/settings/R$color;->about_phone_name_version_color:I' 'Lcom/android/settings/R$color;->about_phone_brand_color:I'
		ota_target=$(sudo find -name "AboutDeviceOtaUpdatePreference.smali" | head -1)
		ota_rstring=$(sudo find . -name 'R$string.smali' | grep 'com/android/settings/R$string.smali' | head -1)
		if [[ -n "$ota_target" && -n "$ota_rstring" ]]; then
			log_command_output MushromTools "$pkg" $otastring "$ota_target" "$ota_rstring" || {
				log_fail "$pkg" "OTA string" "Patch failed"
				cd "$dir"
				return 1
			}
		else
			log_warn "$pkg" "OTA string" "target not found"
		fi

		log $pkg "Rename version"
		repM 'getOplusOSVersion' text 'OplusDeviceInfoUtils.smali' "$patch"
		repM 'getUIVersion' text 'BrandUtils.smali' "$patch"

		log $pkg "Support font variable"
		repM "isCurrentLanguageSupportFontVariation" 1 "FontVariationUtils.smali"
		fstring "zh" "vi" $(sudo find -name "FontVariationUtils.smali") r
		#repM "resetSettingsAndNameByLanguageChanges" void "FontVariationUtils.smali"

		log $pkg "Add Locale pick"
		find . \( -wholename "*com/oplus/settings/feature/homepage*" -o -wholename "*com/oplus/settings/feature/language*" \) \
			-type f -name "*.smali" -exec grep -l "isExpVersion" {} \; | while read -r file; do
			fstring 'Lcom/oplus/settings/utils/CustomizeFeatureUtils;->isExpVersion()Z' \
				'Lcom/android/vsteam/VsProps;->justTrue()Z' "$file" r
		done
		repM "isSupportRegion" 1 "CustomizeFeatureUtils.smali"

		log $pkg "Support Font Dai"
		repM "isFontSignatureMatch" 1 "TailueUtils.smali"
		fstring 'Lcom/oplus/settings/utils/CustomizeFeatureUtils;->isExpVersion()Z' \
			'Lcom/android/vsteam/VsProps;->justTrue()Z' $(sudo find -name "TailueUtils.smali") r
		fstring 'com.monotype.android.font.kapp.custom_1976381923' 'com.bhlnk.customfont_01012000' $(sudo find -name "TailueUtils.smali") r

		$repS "$dir/module/lang/daisupport_15.config" $(sudo find -name "TailueUtils.smali") "replace"

		# Mushrom hands the .ttf to Settings, which applies it with its own FontManager
		# (no font package, no language toggle). The "Dai" path above stays as fallback.
		log $pkg "Native font apply for Mushrom"
		python3 "$dir/bin/patch_settings_font_bridge.py" "$(pwd)" || return 1

		repM "isFintechLifeNfcSupport" 1 "CustomizeFeatureUtils.smali"

		log $pkg "Hidden gesture bar long press"
		repM 'isSupportCircleToSearchNavbarHidden' 1 "CustomizeFeatureUtils.smali"

		repM "getAvailabilityStatus" 0 "RmStatusbarClockPreferenceController.smali"

		fstring 'invoke-static {p0}, Lcom/android/settings/development/DisableDevSettingsDialogFragment;->show(Lcom/android/settings/SettingsPreferenceFragment;)V' 'invoke-virtual {p0}, Lcom/android/settings/development/DevelopmentSettingsDashboardFragment;->disableDeveloperOptions()V' $(sudo find -name "DevelopmentSettingsDashboardFragment.smali") r

		repM "isSupportTopVideo" 0 "AboutDeviceOtaUpdatePreference.smali"

		repM "shouldUseColorOS161Resources" 0 "OplusDeviceInfoUtils.smali"

		fstring "about_device_top_video_last_frame" "device_ota_card_bg_16" $(sudo find -name "AboutDeviceOtaUpdatePreference.smali") r

		fstring "Lcom/oplus/settings/feature/privacy/AppPermissionJumpPreferenceController;->isPermissionMonitorEnable()Z" "Lcom/android/vsteam/VsProps;->justTrue()Z" $(sudo find -name "DisplaySettingsFragment.smali") r

		recp $tempstring $1
	fi
}

gallery() {
	local gallery_target
	local found_watermark=false
	local found_gallery_build=false

	pkg="com.miui.gallery"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Increase word limit of Protective watermark to 50"
		while IFS= read -r -d '' gallery_target; do
			found_watermark=true
			sed -i "s#0xe#0x32#g" "$gallery_target"
		done < <(find . -type f -name "PrivacyWatermarkActivity.smali" -print0)
		if [[ "$found_watermark" == "false" ]]; then
			log_warn "$pkg" "Protective watermark" "PrivacyWatermarkActivity.smali not found; skip"
		fi

		log $pkg "Support Nearby"
		repM 'supportNearby' 1 NearbyUtils.smali

		log $pkg "Support AI Entrance"
		repM 'hasUseAccess' 1 'AiGalleryUtil\$Companion.smali'
		repM 'setVisibility' void 'AiEntranceView.smali'

		log $pkg "Switch Google Photo sync" true
		while IFS= read -r -d '' gallery_target; do
			found_gallery_build=true
			$pgallery "$gallery_target" || return 1
		done < <(find . -type f -name 'Build.smali' -print0)
		if [[ "$found_gallery_build" == "false" ]]; then
			log_warn "$pkg" "Google Photo sync" "Build.smali not found; skip"
		fi

		log $pkg "Remove Chinese map"
		repM 'checkMapAvailable' 0 MapInitializerImpl.smali
		recp $tempstring $1

	fi

}

gallery3d() {
	pkg="com.coloros.gallery3d"
	tempstring=$(check_file_exist "$pkg")
	if [[ $tempstring ]]; then
		local prop_brand
		prop_brand="$(getprop 'ro.product.brand' | tr -d '\r\n')"
		if [[ "$prop_brand" == "OPPO" ]]; then
			log "$pkg" "Skip patch on OPPO brand"
			return 0
		fi

		decp "$tempstring"

		log "$pkg" "Patch Font Sliver"

		target_smali=$(find . -type f -name "*.smali" \
			-exec grep -l "WatermarkInfo.kt" {} + |
			xargs grep -l "k(Ljava/lang/String;ZZZZZ)Z" |
			xargs grep -l "l()Z" |
			head -n 1)

		if [[ -n "$target_smali" ]]; then

			smali_name=$(basename "$target_smali")

			repM "l" 1 "$smali_name"
			repM "k" 1 "$smali_name"
		fi

		target_smali=$(find . -type f -name "*.smali" \
			-exec grep -l "WatermarkMasterVM.kt" {} + |
			xargs grep -l "m0()Z" |
			head -n 1)

		if [[ -n "$target_smali" ]]; then

			smali_name=$(basename "$target_smali")

			repM "m0" 1 "$smali_name"
		fi

		recp "$tempstring" "$1"
	fi
}

contact() {
	pkg="com.android.contacts"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Remove Chinese Services"

		if [[ $os -ge 15 ]]; then
			ftrue $(find -type f -name "BusinessHallUtil.smali") $key1
		else
			a="BusinessEnvMgr"
			c=$(sudo find -name 'SystemUtil.smali')
			if [[ -f $c ]]; then
				repM $($finM $c $a) 0 $c r
			fi
		fi

		recp $tempstring $1
	fi
}

personalassistant() {
	pkg="com.miui.personalassistant"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Crack paid widget"
		repM 'themeManagerSupportPaidWidget' 0 MamlutilKt.smali
		repM 'isCanDirectAddMaMl' 1 PickerDetailViewModel.smali
		repM 'isCanDownload' 1 'PickerDetailDownloadManager\$Companion.smali'
		repM 'isCanAutoDownloadMaMl' 1 PickerDetailUtil.smali
		repM 'isPay' 0 PickerDetailResponse.smali
		repM 'isBought' 1 PickerDetailResponse.smali
		repM 'isPay' 0 PickerDetailResponseWrapper.smali
		repM 'isBought' 1 PickerDetailResponseWrapper.smali
		repM 'shouldCheckMamlBoughtState' 0 PickerDetailViewModel.smali
		repM 'isTargetPositionMamlPayAndDownloading' 0 PickerDetailViewModel.smali
		repM 'checkIsIndependentProcessWidgetForPosition' 1 PickerDetailViewModel.smali

		recp $tempstring $1
	fi
}

security() {
	local app_details
	local patched_disable_count=0

	pkg="com.miui.securitycenter"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		count=$(expr $(ls -dq *smali* | wc -l) + 1)
		mkdir "smali_classes$count"

		ftrue $(find -type d -name abnormalscan | grep "/com/miui/") $key1
		for abnormal_dir in $(find -type d -name abnormalscan | grep "/com/miui/"); do
			for i in $(find "$abnormal_dir" -type f -name "*.smali"); do
				mvst "$(basename "$i")" "smali_classes$count"
			done
		done

		log $pkg "Re-enable screen type in battery history"
		file=$(find . -type f -exec grep -l "ishtar" {} + | xargs grep -l "SDK_INT")
		[[ $file ]] && repM $($finM $file "ishtar") 1 $file r
		[[ $file ]] && fstring 'Landroid/os/Build$VERSION;->SDK_INT:I' 'Lcom/android/vsteam/Toolbox;->ENABLE_SCREEN_BATTERY:I' $file r
		for i in $file; do
			mvst "$(basename "$i")" "smali_classes$count"
		done

		log $pkg "Control Camera/geolocation icon" true
		file=$(find . -type f -exec grep -l "isXOptMode()Z" {} + | xargs grep -l "get third desktop provider exception!")
		[[ $file ]] && repM $($finM $file "isXOptMode()Z") global $file "vs_disable_green_dot" r
		for i in $file; do
			mvst "$(basename "$i")" "smali_classes$count"
		done

		log $pkg "Fix antispam not work"
		for i in $(find . -type f -exec grep -l "antispam" {} + | xargs grep -l "IS_INTERNATIONAL_BUILD" | grep -v "/com/miui"); do
			ftrue $i $key1
			mvst "$(basename "$i")" "smali_classes$count"
		done

		log $pkg "Show data usage on CC" true
		foption $key1 "vs_cc_data_usage" 'DeviceUtil.smali'
		mvst 'DeviceUtil.smali' "smali_classes$count"

		log $pkg "Allow disable system app"
		while IFS= read -r -d '' app_details; do
			if grep -q 'Ljava/util/ArrayList;->contains(Ljava/lang/Object;)Z' "$app_details"; then
				# Android 16 stores the system-app deny-list result here. Android 17
				# may retain the class without this instruction; never build a config
				# from an empty match because that corrupts the beginning of the class.
				perl -0pi -e 's@(invoke-virtual \{[^\n]*\}, Ljava/util/ArrayList;->contains\(Ljava/lang/Object;\)Z\s*\n\s*)move-result ([vp][0-9]+)@$1const/4 $2, 0x1@g' "$app_details"
				mvst "$(basename "$app_details")" "smali_classes$count"
				patched_disable_count=$((patched_disable_count + 1))
			fi
		done < <(find . -type f -name ApplicationsDetailsActivity.smali -print0)
		if ((patched_disable_count == 0)); then
			log_warn "$pkg" "Allow disable system app" "legacy deny-list gate not present; skip"
		fi

		log $pkg "Android Permission"
		fstring "miui.intent.action.APP_PERM_EDITOR_PRIVATE" "hypervs.intent.action.APP_PERM_EDITOR_PRIVATE" "ApplicationsDetailsFragment.smali"
		mvst 'ApplicationsDetailsFragment.smali' "smali_classes$count"

		log $pkg "Remove 10s confirm"
		tens="const/16 v0, 0xa"
		zeros="const/16 v0, 0x0"
		patchsecu=$(find -type f -name InterceptPermissionFragment.smali)
		sudo sed -i "s@$tens@$zeros@g" $patchsecu
		mvst 'InterceptPermissionFragment.smali' "smali_classes$count"

		log $pkg "Unlock all feature gates"
		if ! python3 "$dir/bin/patch_securitycenter_features.py" "$(pwd)"; then
			log_warn "$pkg" "Unlock all feature gates" "no known gate found; skip"
		fi

		for i in $(find . -name "*.smali" -exec grep -l "ro.product.mod_device" {} +); do
			mvst "$(basename "$i")" "smali_classes$count"
		done

		recp $tempstring $1
	fi
}

provision() {
	pkg="com.android.provision"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Enable GMS default"
		repM 'setGmsAppEnabledStateForCn' void Utils.smali
		#repM 'isAvailable' 0 'DefaultActivity$RecommendedState.java'
		#repM 'isAvailable' 0 'GlobalDefaultActivity$RecommendedState.java'

		recp $tempstring $1
	fi

}

# Inject the vendor Leica colour-look pipeline for devices that ship a
# module/leica/<device> payload (plugin .so + tuning .bin + SELinux labels).
# MiuiCamera only lights up the Leica looks in the UI; the Authentic/Vibrant
# colour is rendered by the vendor algo node com.xiaomi.plugin.mileicafilter,
# which non-Leica siblings omit from the snapshot pipeline.
leica_vendor() {
	local mdir="$dir/module/leica/${device,,}"
	[[ -d "$mdir" ]] || return 0

	local snap
	snap="$(find "$dir/img_temp" -type f -name 'leicasnapshot.json' -path '*/etc/camera/*' -print -quit 2>/dev/null)"
	[[ -n "$snap" ]] || { log_warn Leica "Vendor colour-look" "no leicasnapshot.json in odm; skip"; return 0; }

	log_start Leica "Vendor colour-look ($device)"

	# 1) drop the plugin + tuning into their partitions, preserving perms.
	local part kind frag target line
	for part in odm vendor system_ext product; do
		[[ -d "$mdir/$part" ]] && cp -a "$mdir/$part/." "$dir/img_temp/$part/"
	done

	# 2) honour the module's SELinux labels + fs modes verbatim (dedup-guarded):
	# mkfs.erofs lists every file, so a new file without an entry is mislabelled
	# and the camera service cannot load it.
	for part in odm vendor system_ext product; do
		for kind in file_contexts fs_config; do
			frag="$mdir/_config/${part}_${kind}"
			target="$dir/img_temp/config/${part}_${kind}"
			[[ -f "$frag" && -f "$target" ]] || continue
			while IFS= read -r line; do
				[[ -z "$line" ]] && continue
				grep -qxF "$line" "$target" || printf '%s\n' "$line" >> "$target"
			done < "$frag"
		done
	done

	# 2b) remove device colour configs that conflict with the ported Leica look
	# (e.g. a colorfix tuned for a different tele sensor tints Authentic). Paths
	# the native-Leica donor does not ship are listed in _config/remove_files;
	# drop the file and its fs_config/file_contexts entries so the repack omits it.
	local rm_list="$mdir/_config/remove_files" rel part2
	if [[ -f "$rm_list" ]]; then
		while IFS= read -r rel; do
			[[ -z "$rel" || "$rel" == \#* ]] && continue
			rm -f "$dir/img_temp/$rel"
			part2="${rel%%/*}"
			sed -i "\#^${rel} #d" "$dir/img_temp/config/${part2}_fs_config" 2>/dev/null
			sed -i "\#^/${rel} #d" "$dir/img_temp/config/${part2}_file_contexts" 2>/dev/null
		done < "$rm_list"
	fi

	# 2c) libmicamera_adapter refuses to configure Legendary/Leica Essential
	# (mode 0x100) unless ro.theme_customize=LCC, which only the Leica SKU sets.
	# Setting the prop makes the app assume the Leica edition hardware and
	# crash, so bypass just that HAL check on devices flagged for Legendary.
	local adapter="$dir/img_temp/odm/lib64/libmicamera_adapter.so"
	if [[ -f "$mdir/_config/enable_legendary" && -f "$adapter" ]]; then
		python3 "$dir/bin/patch_camera_legend_hal.py" "$adapter" >/dev/null 2>&1 ||
			log_warn Leica "Legendary HAL gate" "LCC check not found; skip"
	fi

	# 3) wire the colour-look node into every snapshot/preview pipeline the
	# device ships. The default photo path is SatSnapshot (not LeicaSnapshot),
	# so a single file is not enough; the module map lists each pipeline plus
	# the node it must sit before (the anchor), taken from a real Leica device.
	local map="$mdir/_config/pipelines.map"
	local cam_dir file anchor wired=0
	cam_dir="$(dirname "$snap")"
	if [[ -f "$map" ]]; then
		local informat
		while read -r file anchor informat _; do
			[[ -z "$file" || "$file" == \#* ]] && continue
			[[ -f "$cam_dir/$file" ]] || continue
			if python3 "$dir/bin/patch_camera_leica_pipeline.py" "$cam_dir/$file" ${anchor:+--before "$anchor"} ${informat:+--in-format "$informat"} >/dev/null 2>&1; then
				wired=$((wired + 1))
			fi
		done < "$map"
	else
		python3 "$dir/bin/patch_camera_leica_pipeline.py" "$snap" >/dev/null 2>&1 && wired=1
	fi

	if (( wired > 0 )); then
		log_done Leica "Vendor colour-look ($device)" "$wired pipeline(s) wired"
	else
		log_warn Leica "Vendor colour-look" "no pipeline wired"
	fi
}

micamera() {
	pkg="com.android.camera"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Patch config smali"
		$camera $(pwd) #>/dev/null 2>&1

		# Vendor side first: for devices with a module/leica/<device> payload,
		# add the colour-look node + plugin so the looks the app enables below
		# actually render on the image.
		leica_vendor

		# Leica looks/filters/watermark are only switched on when the vendor
		# camera stack ships the Leica snapshot pipeline; without it the app
		# would offer modes the HAL cannot render.
		if [[ -n "$(find "$dir/img_temp" -type f -name 'leicasnapshot.json' -path '*/etc/camera/*' -print -quit 2>/dev/null)" ]]; then
			log $pkg "Enable Leica camera profile"
			if ! python3 "$dir/bin/patch_camera_leica.py" "$(pwd)" "$device"; then
				log_warn "$pkg" "Enable Leica camera profile" "Leica capability methods not found; skip"
			fi
		else
			log_warn "$pkg" "Enable Leica camera profile" "vendor has no Leica pipeline; skip"
		fi

		# Legendary master-lens (Leica M3/M9): the vendor stack (legendST plugin,
		# legend pipelines, leica_filter_param_m{3,9}) ships it, but the app gates
		# the capability off on the plain variant. Flagged per-device in the module.
		if [[ -f "$dir/module/leica/${device,,}/_config/enable_legendary" ]]; then
			log $pkg "Enable Leica Legendary (M3/M9)"
			if ! python3 "$dir/bin/patch_camera_legendary.py" "$(pwd)"; then
				log_warn "$pkg" "Enable Leica Legendary (M3/M9)" "capability getters not found; skip"
			fi
		fi

		# Watermarks are filtered per device (Leitzphone set needs theme lcc,
		# others a device type/region/cloud id list); list every bundled one.
		log $pkg "Show all watermarks"
		if ! python3 "$dir/bin/patch_camera_watermark_all.py" "$(pwd)" >/dev/null; then
			log_warn "$pkg" "Show all watermarks" "LimitationUtil filters not found; skip"
		fi

		# Watermarks are read from a data copy of assets/ that is refreshed only
		# when its version is older, so a ROM update without a data wipe hides
		# the ones this APK adds; copy the missing files on every start.
		log $pkg "Add new watermarks to existing data"
		if ! python3 "$dir/bin/patch_camera_watermark_merge.py" "$(pwd)" >/dev/null; then
			log_warn "$pkg" "Add new watermarks to existing data" "tryCopyAssets not found; skip"
		fi

		# EXIF Model/Make use the device name set for the watermark in Mushrom.
		log $pkg "Custom device name in EXIF" true
		if python3 "$dir/bin/patch_camera_exif_name.py" "$(pwd)"; then
			add_cap device_name_watermark
		else
			log_warn "$pkg" "Custom device name in EXIF" "EXIF model writer not found; skip"
		fi

		log $pkg "Hook subscreen"
		find . -type f -exec grep -l "the second screen registerSubScreenState" {} + | while read -r file; do
			result=$($finM "$file" "the second screen registerSubScreenState")
			[[ $result ]] && repB "$result" "vs_disable_subscreen_guide" void "$file" r
		done

		recp $tempstring $1
	fi

}

mediaeditor() {
	pkg="com.miui.mediaeditor"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		# The editor's watermark list goes through the camera's LimitationUtil
		# filters (filterById also deletes what it drops from data).
		log $pkg "Show all watermarks"
		if ! python3 "$dir/bin/patch_camera_watermark_all.py" "$(pwd)" >/dev/null; then
			log_warn "$pkg" "Show all watermarks" "LimitationUtil filters not found; skip"
		fi

		# Its data copy comes from assets/cloud_watermark, an older set without
		# the Leitzphone watermarks; add the camera's ones from assets/watermarks,
		# and stop the cloud config from hiding any of them from the list.
		log $pkg "Add camera watermarks to editor"
		if ! python3 "$dir/bin/patch_mediaeditor_watermark.py" "$(pwd)" >/dev/null; then
			log_warn "$pkg" "Add camera watermarks to editor" "watermark assets not found; skip"
		fi

		# The cloud watermark list offers Leica ones only to leica_devices,
		# which also needs Build.BRAND == xiaomi (so never on Redmi/POCO).
		log $pkg "Treat device as Leica"
		if ! python3 "$dir/bin/patch_mediaeditor_leica.py" "$(pwd)" >/dev/null; then
			log_warn "$pkg" "Treat device as Leica" "Leica device check not found; skip"
		fi

		recp $tempstring $1
	fi

}

opcamera() {
	pkg="com.oplus.camera"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		local prop_brand
		prop_brand="$(getprop 'ro.product.brand' | tr -d '\r\n')"
		if [[ "$prop_brand" == "OPPO" ]]; then
			log $pkg "Skip patch on OPPO brand"
			return 0
		fi

		decp $tempstring

		log $pkg "Patch Font Sliver"

		file=$(find . -type f -exec grep -l "SystemPropertiesAddonUt" {} +)
		fstring 'Landroid/os/OplusSystemProperties;->get' 'Lcom/android/vsteam/Toolbox;->HookProp' $file r

		recp $tempstring $1
	fi
}

weather2() {
	if [[ $os -le 14 ]]; then
		pkg="com.miui.weather2"
		tempstring=$(check_file_exist $pkg)
		if [[ $tempstring ]]; then
			decp $tempstring || return 1

			log $pkg "Convert to Global Weather app"
			ftrue $(pwd) $key1

			recp $tempstring $1
		fi
	fi
}

contractprovider() {
	pkg="com.android.providers.contacts"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Support Search by number in contact"
		ftrue $(pwd) $key1

		recp $tempstring $1
	fi
}

teleservice() {
	pkg="com.android.phone"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Support Search by number in contact"
		ftrue $(pwd) $key1
		ftrue $(pwd) $key2

		recp $tempstring $1
	fi
}

telecom() {
	pkg="com.android.server.telecom"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		# VoIP calls (the app's own Telecom call, or the one Mushrom's
		# VoipWatchBridge mirrors) reach every watch app with a caller and can
		# be rejected from the watch.
		log $pkg "Bridge VoIP calls to watches"
		python3 "$dir/bin/patch_telecom_voip_bridge.py" "$(pwd)" || return 1

		recp $tempstring $1
	fi
}

miui() {
	pkg="android.miui.overlay.telephony"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring rs || return 1

		log $pkg "Add translate"
		cp -rf $lang_dir/FrameExt/* $(pwd)

		recp $tempstring $1 rs
	fi
}

joyose() {
	pkg="com.xiaomi.joyose"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		# Keep SmartOpService initialization intact. Block only the vendor-facing
		# FPS actuators (XRing schedgenius, FPSGO/perflock and Excellent FPS).
		log $pkg 'Block game FPS control'
		python3 "$dir/bin/patch_joyose_fps.py" "$(pwd)" || return 1

		# Only the configs shipped in the ROM (assets + odm default_cloud.json):
		# no teg/Settings cloud, no cached smartP cloud_config rows.
		log $pkg 'Block cloud config'
		python3 "$dir/bin/patch_joyose_cloud.py" "$(pwd)" || return 1

		log $pkg 'Enable GPU Turner'
		a="GPUTUNER_SWITCH"
		b="parseBoolean"
		c=$(find . -type f -exec grep -l $a {} + | xargs grep -l $b)

		if [[ -f $c ]]; then
			repM $($finM $c $a $b) 1 $c r
		fi

		recp $tempstring $1
	fi
}

miwallpaper() {
	pkg="com.miui.miwallpaper"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Support supper wallpaper for all device"
		repM "initEnableSuperWallpaper" 1 "SuperWallpaperUtils.smali"

		recp $tempstring $1
	fi
}

external() {
	pkg="com.android.externalstorage"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Unblock access data/obb"
		repM "shouldHideDocument" 0 "ExternalStorageProvider.smali"
		repM "shouldBlockFromTree" 0 "ExternalStorageProvider.smali"
		repM "shouldBlockDirectoryFromTree" 0 "ExternalStorageProvider.smali"

		recp $tempstring $1
	fi
}

engineermode() {
	pkg="com.oplus.engineermode"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		# SimlockTest shows "unlocked" in green when operator is "-1"/"0";
		# pin the operator reported by the modem to "-1".
		log $pkg "SimlockTest always unlocked"
		fstring 'iput-object p1, p0, Lcom/oplus/engineermode/network/manualtest/simlock/SimlockTest;->mSimlockOperator:Ljava/lang/String;' \
			'const-string p1, "-1"\n\n    iput-object p1, p0, Lcom/oplus/engineermode/network/manualtest/simlock/SimlockTest;->mSimlockOperator:Ljava/lang/String;' \
			'SimlockTest.smali'

		recp $tempstring $1
	fi
}

appdetail() {
	local checker

	pkg="com.oplus.appdetail"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		# HostCompatibleActivity drops every install whose caller is not signed
		# with the platform key; the replaced PackageInstaller is not.
		log $pkg "Trust any installer caller"
		checker=$(grep -rl '^\.source "SecurityChecker.java"' --include=*.smali . | head -1)
		if [[ -f "$checker" ]]; then
			repM 'a(Ljava/lang/String;)Z' 1 "$checker"
		else
			log_warn "$pkg" "SecurityChecker.smali" "not found; skip caller check patch"
		fi

		recp $tempstring $1
	fi
}

# Intelligent deep sleep turns Wi-Fi off after a long screen-off, so FCM only
# delivers on unlock; keep Wi-Fi up behind the uid firewall with GMS allowed
# and ship the switch off.
opbattery() {
	pkg="com.oplus.battery"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Deep sleep keeps FCM online"
		if ! python3 "$dir/bin/patch_oplus_deepsleep_net.py" "$(pwd)"; then
			log_warn "$pkg" "Deep sleep keeps FCM online" "ControllerCenter not matched; skip"
		fi

		recp $tempstring $1
	fi
}

securityadd() {
	pkg="com.miui.securityadd"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Translate Game Turbo"
		fstring "gamespace.sec.miui.com" "gamespace.sec.intl.miui.com" "GameBoosterRichWebActivity.smali"

		fstring 'ro.product.mod_device' 'ro.hypervs.mod_device'

		recp $tempstring $1
	fi
}

xmsf() {
	pkg="com.xiaomi.xmsf"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1
		files=$(grep -rlE "$key1|$key2" .)

		for f in $files; do
			ftrue "$f" "$key1"
			ftrue "$f" "$key2"
		done

		recp $tempstring $1
	fi
}

theme() {
	pkg="com.android.thememanager"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		file1=$(find . -type f -exec grep -l "check rights file:" {} +)
		file2=$(sudo find -name 'OnlineResourceDetail.smali')
		file3=$(sudo find -name 'DiscountPriceView.smali')
		file4=$(sudo find -name 'RearScreenListItemBean.smali')

		$ptheme $file1 $file2 $file3 $file4

		fstring "Lmiui/os/Build;->getRegion()" "Lcom/android/vsteam/Toolbox;->ForceRegion()"

		log $pkg "Show hidden themes"
		if ! python3 "$dir/bin/patch_theme_show_hidden.py" "$(pwd)"; then
			log_warn "$pkg" "Show hidden themes" "filter not found; skip"
		fi

		recp $tempstring $1
	fi
}

ThemeManager() {
	theme "$@" || return 1
	mv2priv "com.android.thememanager"
}

miaod() {
	pkg="com.miui.aod"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Support AI Wallpaper"

		repM 'isSupportVideoDepth' 1 'FileUtil.smali'
		repM 'isRedmi' 0 'DeviceUtil.smali'

		log $pkg "Remove depth limit"
		repM 'calculateUnAlphaPercent' 'true' 'CalculateWorker.smali'

		recp $tempstring $1
	fi
}

opaod() {
	pkg="com.oplus.aod"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Patch Panoramic AOD"

		repM "isSupportFullAod" 1 "CommonUtils.smali"
		repM "isSupportFullOrInspirationAod" 1 "CommonUtils.smali"
		repM "isSupportInspirationAod" 1 "CommonUtils.smali"

		repM "getKeyAodAllDaySupportSettings" 1 "SettingsUtils.smali"

		recp $tempstring $1
	fi
}

# Stylus button: single/double click and long press run the action picked in
# Mushrom (vs_pencil_*_action) through com.android.vsteam.PencilKey; back
# (backspace while typing) and zoom in/out are injected there directly.
exsystemservice() {
	pkg="com.oplus.exsystemservice"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Custom stylus button"
		if python3 "$dir/bin/patch_oplus_pencil_button.py" "$(pwd)"; then
			add_cap pencil_key
			add_cap pencil_gesture
		else
			log_warn "$pkg" "Custom stylus button" "pencil button handlers not found; skip"
		fi

		recp $tempstring $1
	fi
}

phrase() {
	pkg="com.miui.phrase"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Force IME Bottom"
		fstring "com.baidu.input_mi" "com.google.android.inputmethod.latin"

		recp $tempstring $1
	fi
}

lbe() {
	local permission_config

	pkg="com.lbe.security.miui"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Unmanage lockscreen, fullscreen & popup permission"

		permission_config=$(find . -type f -name 'PermissionConfiguration.smali' -print -quit)
		if [[ -n "$permission_config" ]]; then
			$plbe 'PERM_ID_SHOW_WHEN_LOCKED' "$permission_config" || return 1
			$plbe 'PERM_ID_BACKGROUND_START_ACTIVITY' "$permission_config" || return 1
			$plbe 'PERM_ID_SYSTEMALERT' "$permission_config" || return 1
		else
			log_warn "$pkg" "Remove some permission" "PermissionConfiguration.smali not found; skip"
		fi

		ffalse $(sudo find -name 'WNProvider.smali') $key1

		recp $tempstring $1
	fi
}

intentresolver() {
	pkg="com.android.intentresolver"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Control AOPS ShareSheet" true
		repM 'useAospVersion' global 'ApplicationStubImpl.smali' "vs_aosp_sharesheet"

		recp $tempstring $1
	fi
}

subscreen() {
	pkg="com.xiaomi.subscreencenter"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		python3 $dir/bin/music_session_patch.py $(pwd)

		recp $tempstring $1
	fi
}

accessibilityassistant() {
	pkg="com.coloros.accessibilityassistant"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Support Call recorder in Zalo"
		fstring '"com.tencent.mm"' '"com.zing.zalo"'

		file=$(find . -type f -exec grep -l "isNeedMixAudio " {} +)
		[[ $file ]] && repM $($finM $file "isNeedMixAudio ") 0 $file r

		# The 17.x build still asks for the microphone at runtime and runs a
		# microphone foreground service, but its manifest lost the permissions
		# the 16.x one declares, so the grant can never succeed.
		log $pkg "Restore microphone permissions"
		# apktool b --copy-original puts original/AndroidManifest.xml back
		# into the APK, so that copy needs the permissions as well.
		local manifest
		for manifest in AndroidManifest.xml original/AndroidManifest.xml; do
			[[ -f $manifest ]] || continue
			python3 "$dir/bin/axml_add_permissions.py" "$manifest" \
				android.permission.RECORD_AUDIO \
				android.permission.FOREGROUND_SERVICE_MICROPHONE \
				android.permission.CAPTURE_AUDIO_OUTPUT \
				android.permission.CAPTURE_VOICE_COMMUNICATION_OUTPUT || return 1
		done

		recp $tempstring $1
	fi
}

launcher() {
	local launcher_type
	local new_smali_dir

	launcher_type=$(awk -F= '$1 == "isType" {print $2; exit}' "$dir/info" 2>/dev/null)
	launcher_type="${launcher_type:-${isType:-phone}}"

	pkg='com.android.launcher'
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then

		decp $tempstring

		# Tạo classes mới để chứa các file đã mod
		new_smali_dir=$(next_smali_classes_dir)
		mkdir -p "$new_smali_dir"

		log $pkg "Fix badge number"
		repM 'loadBadgeSupportListFromAssets' void "LauncherAssetManager.smali"
		fstring "supportNumberBadge" "colourvs" $(sudo find -name "NotificationBadgeManager.smali") r
		repM "isAllSupportNumBadge" 1 'FeatureOption.smali'

		log $pkg "Remove lock app limit"
		force_put 'Lcom/oplus/quickstep/applock/AppLockModel;->noDefaultLockedAppLimit:I' 999 'AppLockModel.smali'

		log $pkg "Force show memory"
		force_put 'Lcom/oplus/quickstep/memory/MemoryInfoManager;->mAllowMemoryInfoDisplay:Z' 1 'MemoryInfoManager.smali'

		log $pkg "Force enable search bar"
		force_put 'Lcom/android/common/config/FeatureOption;->sIsSupportSearchEntry:Z' 1 'FeatureOption.smali'
		remM "getSearchAppAction" 'SearchEntry$Companion.smali'
		remM "getSearchAppPackageName" 'SearchEntry$Companion.smali'
		add_method "$dir/module/lang/google_search_bar_1p.config" 'SearchEntry$Companion.smali'
		remM "getSearchAppAction" 'IndicatorEntry$Companion.smali'
		remM "getSearchAppPackageName" 'IndicatorEntry$Companion.smali'
		add_method "$dir/module/lang/google_search_bar_1p.config" 'IndicatorEntry$Companion.smali'
		# The search entry by the page indicator builds its own intent and never
		# asks the getters above; route it to Google too.
		if ! python3 "$dir/bin/patch_oplus_indicator_search.py" "$(pwd)"; then
			log_warn "$pkg" "Force enable search bar" "indicator search intent not found; skip"
		fi

		if [[ "$launcher_type" != "pad" ]]; then
			log $pkg "Gird recent layout" true
			fstring 'Lcom/android/common/util/AppFeatureUtils;->isTablet()Z' 'Lcom/android/vsteam/VsProps;->justTrue()Z' "OplusGridRecentsView.smali"
			repM 'isEnable' global "OplusGridRecentsConfig.smali" "vs_layout_grid_recent"
			repM 'isLargeDisplayDevice' global "ScreenUtils.smali" "vs_layout_grid_recent"

			log $pkg "Background dock" true
			repM 'isSupportDockerExpandScreen' global "ScreenUtils.smali" "vs_dock_background"
			fstring 'Lcom/android/common/util/ScreenUtils;->hasLargeDisplayFeatures()Z' 'Lcom/android/vsteam/VsProps;->justTrue()Z' "OplusHotseat.smali"

			log $pkg "Full desktop layout"
			repM 'getShouldUsedLayout' 1 'LauncherModeManager.smali'
			repM 'isUseOldLayout' 1 'LauncherModeManager.smali'
		fi

		log $pkg "Google feed" true
		repM 'isSupportGoogleOverlay' global "AppFeatureUtils.smali" "vs_minus_screen_ggl"
		repM 'isSupportGoogleAssistant' global "FeatureOption.smali" "vs_minus_screen_ggl"
		replaceM "getAppInfo" 'FeatureOption.smali' "$dir/module/lang/google_feed_15.config"

		remM "getOverlayPackageName" 'OverlayProxy.smali'
		remM "getOverlayAction" 'OverlayProxy.smali'
		add_method "$dir/module/lang/google_feed1_15.config" 'OverlayProxy.smali'

		repM 'isSupportCircleToSearch' 1 'FeatureOption.smali'
		repM 'isSupportCircleToSearchNavbarHidden' global "FeatureOption.smali" "vs_hidden_bar_longpress"
		repM 'isDefaultCNVersion' 0 'AppFeatureUtils.smali'

		log $pkg "Show hidden gesture bar long press area"
		fstring 'sget-boolean p0, Lcom/android/common/config/FeatureOption;->isExp:Z' 'const/4 p0, 0x1' 'OplusHideNavHandleController$INSTANCE.smali'

		# Move các file đã mod vào classes mới
		mvst 'LauncherAssetManager.smali' "$new_smali_dir" true
		mvst 'NotificationBadgeManager.smali' "$new_smali_dir" true
		mvst 'FeatureOption.smali' "$new_smali_dir" true
		mvst 'AppLockModel.smali' "$new_smali_dir" true
		mvst 'MemoryInfoManager.smali' "$new_smali_dir" true
		mvst 'SearchEntry$Companion.smali' "$new_smali_dir" true
		mvst 'IndicatorEntry$Companion.smali' "$new_smali_dir" true
		mvst 'OplusGridRecentsView.smali' "$new_smali_dir" true
		mvst 'OplusGridRecentsConfig.smali' "$new_smali_dir" true
		mvst 'ScreenUtils.smali' "$new_smali_dir" true
		mvst 'OplusHotseat.smali' "$new_smali_dir" true
		mvst 'AppFeatureUtils.smali' "$new_smali_dir" true
		mvst 'OverlayProxy.smali' "$new_smali_dir" true
		mvst 'LauncherModeManager.smali' "$new_smali_dir" true
		mvst 'OplusHideNavHandleController$INSTANCE.smali' "$new_smali_dir" true
		mvst 'WorkspacePullDownDetectController.smali' "$new_smali_dir" true

		fstring com.oplus.romupdate com.bhlnk.vsteam
		recp $tempstring
	fi
}

notificationm() {
	pkg='com.oplus.notificationmanager'
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Fix all problem relatest notification manager"
		repM "isExpVersion" 1 'BaseFeatureOption.smali'
		repM "isSupportSeedlingExp" 1 'BaseFeatureOption.smali'
		repM "shouldShowThirdApp" 1 'FluidSeedingUtils.smali'
		repM "supportSmartRecommend" 1 'FluidSeedingUtils.smali'
		repM "shouldShowRequestWindow" 1 'FluidSeedingUtils.smali'

		repM "isNumBadgeSupportForPackage" 1 'NotificationBackend.smali'

		fstring com.oplus.romupdate com.bhlnk.vsteam
		fstring "content://com.oplus.pantanal.ums.settings/query_brand_settings/" "content://com.bhlnk.vsteam.other_services/query_brand_settings/"

		recp $tempstring $1
	fi
}

nfc() {
	pkg="com.android.nfc"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		# The NFC service keeps its export-only fintech features off on a CN
		# region, without which HeyTap Wallet (com.heytap.wallet) can't bring up
		# its NFC/HCE payment. isRegionCN moved from NfcUtils to NfcRegionUtils
		# on Android 17.
		log $pkg "Support Fintech, Wallet"
		repM "isRegionCN" 0 "NfcUtils.smali"
		repM "isRegionCN" 0 "NfcRegionUtils.smali"
		if ! grep -rqs --include=NfcUtils.smali --include=NfcRegionUtils.smali \
			'\.method.*isRegionCN()Z' .; then
			log_warn $pkg "isRegionCN" "not found; fintech and Wallet stay off"
		fi

		recp $tempstring $1
	fi
}

lockscreen() {
	pkg="com.oplus.keyguard.clock.base"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Toggle Red One"
		find . -type f -name "*.smali" -exec grep -l "ro.oplus.image.system_ext.brand" {} \; | while read -r file; do
			fstring 'Landroid/os/SystemProperties;->get' \
				'Lcom/android/vsteam/Toolbox;->HookProp' "$file" r
		done

		fstring '"domestic"' '"vsteam"'

		recp $tempstring $1
	fi
}

aimemory() {
	pkg="com.oplus.aimemory"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Force Support China Phone"
		fstring '"ro.oplus.image.system_ext.area"' '"ro.colourvs.area"'
		fstring '"ro.oplus.image.system_ext.brand"' '"ro.colourvs.brand"'

		recp $tempstring $1
	fi
}

aiassistant() {
	pkg="com.oplus.aiassistant"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Force Support China Phone"
		fstring '"ro.oplus.image.system_ext.area"' '"ro.colourvs.area"'
		fstring '"ro.oplus.image.system_ext.brand"' '"ro.colourvs.brand"'

		recp $tempstring $1
	fi
}

gesture() {
	local files
	local file

	pkg="com.oplus.gesture"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Patch Sprucekey"

		mapfile -t files < <(find . -type f -exec grep -l "onKeyEvent(Landroid/view/KeyEvent;)V" {} +)
		for file in "${files[@]}"; do
			repB "onKeyEvent" hehe "spurcekey" "$file" r
		done

		mapfile -t files < <(find . -type f -exec grep -l "mDataHelper = null" {} +)
		for file in "${files[@]}"; do
			repM "$($finM "$file" "mDataHelper = null")" void "$file" r
		done

		recp $tempstring $1
	fi
}

sidebar() {
	pkg="com.coloros.smartsidebar"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Patch AI Tools"

		key3='Lcom/coloros/common/utils/CommonFeatureOption;->sIsVersionExp:Z'
		ftrue $(sudo find -name 'ToolEntryHelper.smali') $key3

		file=$(find . -type f -exec grep -l "EdgePanelFileUtil.java" {} +)
		ftrue $file $key3

		repM "isToolAvailable" 1 "AiSummaryTool.smali"
		repM "isToolAvailable" 1 "ReadArticleTool.smali"
		repM "isToolAvailable" 1 "GlobalTranslationTool.smali"

		recp $tempstring $1
	fi
}

oshare() {
	pkg="com.oplus.oshare"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Disable 10 minute timeout"

		# Patch XML config file - change SwitchTimeOut from 10 to 999999999
		config_file="assets/apps_oshare_feature_config.xml"
		if [[ -f "$config_file" ]]; then
			sed -i 's/<SwitchTimeOut>10<\/SwitchTimeOut>/<SwitchTimeOut>999999999<\/SwitchTimeOut>/' "$config_file"
			log $pkg "Updated SwitchTimeOut in config XML"
		fi

		file=$(find . -type f -exec grep -l "getSwitchTimeOut " {} +)
		[[ $file ]] && repM $($finM $file "getSwitchTimeOut ") -1 $file r

		recp $tempstring $1
	fi
}

safecenter() {
	pkg="com.oplus.safecenter"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Patch VIP mode"

		fstring 'ro.boot.vbmeta.device_state' 'ro.vipmode.lockstate'

		recp $tempstring $1
	fi
}

ums() {
	pkg="com.oplus.pantanal.ums"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring

		log $pkg "Patch local card config"

		$pums assets/preset/card_config_local_config.json

		recp $tempstring $1
	fi
}

#endapk #################################################################################################

patch_region_lock_minus1() {
	logcore $jarf "Unlock region and return state -1"

	logcore $jarf "Unlock region"
	repM 'isRegionLockEnabled' 0 'RegionLockController.smali'
	repM 'isRegionLocked' 0 'RegionLockController.smali'
	repM 'isRegionUnLocked' 1 'RegionLockController.smali'
	repM 'getRegionLockState(Ljava/lang/String;)' nullz 'RegionLockController.smali'
	repM 'getRegionLockState()' nullz 'RegionLockState.smali'
}

patch_simlock_unlock() {
	logcore $jarf "Simlock operator -1 and skip simlock policy"

	# EVENT_SIM_GET_SIMLOCK_OPERATOR / handleSimLoaded / OplusCustomizeService read operator from cache
	repM 'getOperator' -1 'OplusSimlockCache.smali'
	# Skip call/sms/mms restrictions on invalid SIM
	repM 'handleSimlockPolicy' -1 'OplusSimlockManagerImpl.smali'
	# Keep OplusPolicyController.mSimlockOperator at default "-1"
	repM 'setSimlockOperator' void 'OplusPolicyController.smali'
	repM 'isSimLockVersion' 0 'OplusPolicyController.smali'

	# OplusCustomizeService locks device when operator > 0 and LockAssistant is missing
	add_feature 'oplus.software.skip_check_lockassistant'
}

patch_subsys_simlock_unlock() {
	logcore $jarf "Subsys simlock operator 0xff (unlocked)"

	# HAL ISubsysRadio.getSimlockOperatorId async response -> keyByte 0xff (RadioManager / EngineerMode SimlockTest)
	local f
	for f in $(find . -type f -name 'SubsysRadioResponse.smali'); do
		sed -i '/^\.method .* getSimlockOperatorIdResponse(/,/^\.end method/ s/^\(\s*\)\(invoke-virtual {[vp][0-9]*, p1, p2}, Lcom\/oplus\/subsys\/ResponseHelper;->responseByte(\)/\1const\/4 p2, -0x1\n\n\1\2/' "$f"
	done

	# Sync binder ISubsysRadio.getSimlockOperatorId()
	repM 'getSimlockOperatorId()B' -1 'RadioInterfaceManager.smali'
}

patch_oplus_background_notification_policy() {
	logcore $jarf "Disable Oplus background blockers"

	# App startup / background launch policy
	repM 'checkPreventIndulge' 0 'OplusAppStartupManager.smali'
	repM 'checkPreventListForPreload' 0 'OplusAppStartupManager.smali'
	repM 'interceptMaybeSendBootCompletedIfNeed' 0 'OplusAppStartupManager.smali'
	repM 'isInPreventIndulgeList' 0 'OplusAppStartupManager.smali'
	repM 'restrictApp' 0 'OplusAppStartupManager.smali'
	repM 'shouldPreventNotification' 0 'OplusAppStartupManager.smali'
	repM 'shouldPreventSendReceiver' 0 'OplusAppStartupManager.smali'
	repM 'shouldPreventStartActivity' 0 'OplusAppStartupManager.smali'
	repM 'shouldPreventStartInstrumentation' 0 'OplusAppStartupManager.smali'
	repM 'shouldPreventStartProcess' 0 'OplusAppStartupManager.smali'
	repM 'shouldPreventStartProvider' 0 'OplusAppStartupManager.smali'
	repM 'shouldPreventStartService' 0 'OplusAppStartupManager.smali'
	repM 'shouldRestrictApp' 0 'OplusAppStartupManager.smali'
	repM 'isAllowAssociateByList' 1 'OplusAppStartupManager.smali'
	repM 'isAllowAutoStartByList' 1 'OplusAppStartupManager.smali'
	repM 'isAllowRestartService' 1 'OplusAppStartupManager.smali'
	repM 'isAllowStartFromBindService' 1 'OplusAppStartupManager.smali'
	repM 'isAllowStartFromBroadCast' 1 'OplusAppStartupManager.smali'
	repM 'isAllowStartFromProvider' 1 'OplusAppStartupManager.smali'
	repM 'isAllowStartFromService' 1 'OplusAppStartupManager.smali'
	repM 'isAllowStartFromStartService' 1 'OplusAppStartupManager.smali'
	repM 'isAllowStartInHighTemp' 1 'OplusAppStartupManager.smali'

	repM 'isAllowRestartServiceStartupManager' 1 'OplusAppStartupConfigExt.smali'
	repM 'shouldPreventSendReceiverRealStartupManager' 0 'OplusAppStartupConfigExt.smali'
	repM 'shouldPreventStartProviderStartupManager' 0 'OplusAppStartupConfigExt.smali'
	repM 'shouldPreventStartServiceStartupManager' 0 'OplusAppStartupConfigExt.smali'
	repM 'shouldInterceptCustomBlack' 0 'OplusAppStartupConfigExt.smali'
	repM 'shouldInterceptByAssociateCallerBlackList' 0 'OplusAppStartupConfig.smali'
	repM 'isAllowDiscouragedRestart' 1 'OplusAppStartupConfig.smali'

	repM 'isAvailable' 0 'OplusAppStartupAIBoost2Strategy.smali'
	repM 'isAvailable' 0 'OplusAppStartupBgPureStrategy.smali'
	repM 'isAvailable' 0 'OplusAppStartupCameraSceneStrategy.smali'
	repM 'isAvailable' 0 'OplusAppStartupCommonSceneStrategy.smali'
	repM 'isAvailable' 0 'OplusAppStartupHyperGameStrategy.smali'
	repM 'isAvailable' 0 'OplusAppStartupStrategyBase.smali'
	repM 'shouldIntercept' 0 'OplusAppStartupAIBoost2Strategy.smali'
	repM 'shouldIntercept' 0 'OplusAppStartupBgPureStrategy.smali'
	repM 'shouldIntercept' 0 'OplusAppStartupCameraSceneStrategy.smali'
	repM 'shouldIntercept' 0 'OplusAppStartupCommonSceneStrategy.smali'
	repM 'shouldIntercept' 0 'OplusAppStartupHyperGameStrategy.smali'
	repM 'shouldIntercept' 0 'OplusAppStartupStrategyBase.smali'
	repM 'isAllowAssociateByList' 1 'OplusAppStartupManager$OplusStartupStrategy.smali'
	repM 'isAllowAutoStartByList' 1 'OplusAppStartupManager$OplusStartupStrategy.smali'
	repM 'isAllowSpecialAutoStart' 1 'OplusAppStartupManager$OplusStartupStrategy.smali'

	logcore $jarf "Disable Hans freeze/proxy"

	# Hans freezes/proxies background apps, jobs, alarms, network and notifications.
	repM 'hansEnabled' 0 'OplusHansManager.smali'
	repM 'hansEnabled' 0 'OplusHansManager$HansConfig.smali'
	repM 'isAbKillPolicyEnabled' 0 'OplusHansManager$HansConfig.smali'
	repM 'isFrozen' 0 'OplusHansManager.smali'
	repM 'isFrozenByHans' 0 'OplusHansManager.smali'
	repM 'isHansProxyAlarm' 0 'OplusHansManager.smali'
	repM 'isInHansFreezeList' 0 'OplusHansManager.smali'
	repM 'isProxyJob' 0 'OplusHansManager.smali'
	repM 'skipFrozenApp' 0 'OplusHansManager.smali'
	repM 'skipNotification' 0 'OplusHansManager.smali'
	repM 'freezeForPreload' 0 'OplusHansManager.smali'
	repM 'freezeMultiWindows' void 'OplusHansManager.smali'

	repM 'isAndroidFreezeEnable' 0 'OplusHansDBConfig.smali'
	repM 'isBlackHansPkgEnable' 0 'OplusHansDBConfig.smali'
	repM 'isCpuHighLoadFreezeEnable' 0 'OplusHansDBConfig.smali'
	repM 'isDozeRestrictSwitch' 0 'OplusHansDBConfig.smali'
	repM 'isFastFreezerEnable' 0 'OplusHansDBConfig.smali'
	repM 'isHansEnable' 0 'OplusHansDBConfig.smali'
	repM 'isIdleEnable' 0 'OplusHansDBConfig.smali'
	repM 'isNetRestrictionEnable' 0 'OplusHansDBConfig.smali'
	repM 'isNightDeepDozeEnable' 0 'OplusHansDBConfig.smali'
	repM 'isNightDeepDozeTimeRestrictEnable' 0 'OplusHansDBConfig.smali'
	repM 'isRestrictedNetAppInDoze' 0 'OplusHansDBConfig.smali'
	repM 'isStrictModeEnable' 0 'OplusHansDBConfig.smali'
	repM 'isStrictModeLevelEnable' 0 'OplusHansDBConfig.smali'
	repM 'isSuperFreezeEnabled' 0 'OplusHansDBConfig.smali'
	repM 'preventAsyncBinderEnable' 0 'OplusHansDBConfig.smali'
	repM 'restrictRStateNetEnable' 0 'OplusHansDBConfig.smali'
	repM 'skipNotificatioinEnable' 0 'OplusHansDBConfig.smali'

	repM 'freezeAllApps' void 'HansSceneManager.smali'
	repM 'freezeMultiWindowUids' void 'HansSceneManager.smali'
	repM 'freezeViaSM' void 'HansSceneManager.smali'
	repM 'isFrozen' 0 'HansSceneManager.smali'
	repM 'skipNotificationInCameraVocalScene' 0 'HansSceneManager.smali'
	repM 'freezeByCgroupV2' 0 'HansCGroup.smali'
	repM 'freezePackageCgroup' 0 'HansCGroup.smali'
	repM 'isProxyAlarm' 0 'OplusHansProxyManager.smali'
	repM 'isProxyGps' 0 'OplusHansProxyManager.smali'
	repM 'isProxyJob' 0 'OplusHansProxyManager.smali'
	repM 'isProxyService' 0 'OplusHansProxyManager.smali'
	repM 'isProxyed' 0 'OplusHansProxyManager.smali'

	logcore $jarf "Disable GuardElf/Athena clear policy"

	repM 'isEnableGuardElf' 0 'OplusGuardElfConfigUtil.smali'
	repM 'isJobSceneProtectSwitch' 0 'OplusGuardElfConfigUtil.smali'
	repM 'forceStopPackage' void 'OplusAthenaAmManager.smali'
	repM 'isShouldForbidRestartForClear' 0 'OplusAthenaAmManager.smali'
	repM 'shouldPreventStartForKswapdHighload' 0 'OplusAthenaAmManager.smali'
	repM 'notifyAthenaOnekeyClearRunning' void 'OplusAthenaManager.smali'
	repM 'notifyAppKillReason' void 'OplusAthenaAmManager.smali'

	logcore $jarf "Remove Oplus notification intercepts"

	repM 'isHide' 0 'OplusNotificationAccessControl.smali'
	repM 'isNeedToBlockInSuperPowerMode' 0 'OplusNotificationCommonPolicy.smali'
	repM 'shouldInterceptNotification' 0 'OplusNotificationCommonPolicy.smali'
	repM 'shouldInterceptSound' 0 'OplusNotificationCommonPolicy.smali'
	# shouldKeepNotifcationWhenForceStop is hooked by vs_keep_noti_when_clear in services();
	# forcing it to 1 here would make that toggle a no-op.
	repM 'shouldLimitChannels' 0 'OplusNotificationCommonPolicy.smali'
	repM 'shouldShowNotificationToast' 0 'OplusNotificationCommonPolicy.smali'
	repM 'shouldSuppressEffect' 0 'OplusNotificationCommonPolicy.smali'
	repM 'suppressedByGameSpace' 0 'OplusNotificationCommonPolicy.smali'

	repM 'interceptEnqueueNotificationInternal' 0 'OplusNotificationManagerServiceExtImpl.smali'
	repM 'isAppBlockedByWLB' 0 'OplusNotificationManagerServiceExtImpl.smali'
	repM 'isInterceptNotification' 0 'OplusNotificationManagerServiceExtImpl.smali'
	repM 'onUidGone' void 'OplusNotificationManagerServiceExtImpl$NotificationUidObserver.smali'

	# Background firewall chain (chain 6) is always on; without this only RUS/system/audio
	# apps keep network in background, so push stalls while screen is off.
	logcore $jarf "Allow background network for push"
	repM 'isUidAllowedNetworkWhileBackground' 1 'OplusNetworkPolicyManagerServiceEx.smali'
	repM 'matchGoogleRestrictRule' 0 'OplusNetworkPolicyManagerServiceEx.smali'

	repM 'shouldDelayRemove' 0 'OplusNotificationManagerServiceExtImpl.smali'
	repM 'shouldInterceptToast' 0 'OplusNotificationManagerServiceExtImpl.smali'
	repM 'shouldKeepNotifcationWhenTopStyleNotShow' 1 'OplusNotificationManagerServiceExtImpl.smali'
	repM 'shouldLimitChannels' 0 'OplusNotificationManagerServiceExtImpl.smali'
	repM 'shouldShowNotificationToast' 0 'OplusNotificationManagerServiceExtImpl.smali'
	repM 'shouldSuppressEffect' 0 'OplusNotificationManagerServiceExtImpl.smali'
}

mvst() {

	sudo find -type f -name "$1" | sed -e 's,^\./,,' | while read -r smali_file; do
		[[ "$smali_file" == "$2/"* ]] && continue

		src_folder=$(dirname "$smali_file")
		relative_path=$(echo "$src_folder" | cut -d'/' -f2-)
		dest_folder="$2/$relative_path"

		mkdir -p "$dest_folder"
		mv "$smali_file" "$dest_folder/"
	done

}

validate_subattest_backend() {
	local root="$1"
	local stage="${2:-PrepareFiles}"
	local subattest_smali
	local required_signature

	subattest_smali=$(find "$root" -type f -path '*/com/android/vsteam/SubAttest.smali' -print -quit)
	if [[ -z "$subattest_smali" ]]; then
		log_fail "$stage" vsteam.apk "missing com.android.vsteam.SubAttest"
		return 1
	fi

	# Toolbox in framework.jar calls these two stable keybox-selector methods.
	# Reject an old backend here instead of shipping a ROM that crashes with
	# NoSuchMethodError when KeyStore.getCertificateChain() is invoked.
	for required_signature in \
		'.method public static CreateCert([Ljava/security/cert/Certificate;Z)[Ljava/security/cert/Certificate;' \
		'.method public static modifyLeaf([BZ)[B' \
		'.method public static generateChain(Ljava/security/PublicKey;[BII[I[II[BLjava/lang/String;J[B)[Ljava/security/cert/Certificate;'; do
		if ! grep -Fq "$required_signature" "$subattest_smali"; then
			log_fail "$stage" vsteam.apk "incompatible ABI: missing $required_signature"
			return 1
		fi
	done

	# SubAttest is injected into framework.jar (the boot class path). Shipping
	# unminified org.spongycastle classes there shadows app-local versions and
	# causes NoSuchMethodError in apps such as com.xiaomi.mirror. Release/R8
	# output repackages these implementation classes under com.android.vsteam.
	if find "$root" -type f -path '*/org/spongycastle/*.smali' -print -quit | grep -q .; then
		log_fail "$stage" vsteam.apk \
			"contains boot-classpath org.spongycastle classes; use the minified release APK"
		return 1
	fi
	return 0
}

validate_attestation_bridge() {
	local root="$1"
	local toolbox_smali
	local required_call

	toolbox_smali=$(find "$root" -type f -path '*/com/android/vsteam/Toolbox.smali' -print -quit)
	if [[ -z "$toolbox_smali" ]]; then
		log_fail Framework framework.jar "missing com.android.vsteam.Toolbox"
		return 1
	fi

	for required_call in \
		'Lcom/android/vsteam/SubAttest;->CreateCert([Ljava/security/cert/Certificate;Z)[Ljava/security/cert/Certificate;' \
		'Lcom/android/vsteam/SubAttest;->modifyLeaf([BZ)[B' \
		'Lcom/android/vsteam/SubAttest;->generateChain(Ljava/security/PublicKey;[BII[I[II[BLjava/lang/String;J[B)[Ljava/security/cert/Certificate;'; do
		if ! grep -Fq "$required_call" "$toolbox_smali"; then
			log_fail Framework framework.jar "Toolbox uses an incompatible SubAttest bridge: $required_call"
			return 1
		fi
	done
	return 0
}

build_backend_apks() {
	local backend_dir="$dir/backend"
	local gradle="$backend_dir/gradlew"

	backend_vsteam_apk="$backend_dir/vsteam/build/outputs/apk/release/vsteam.apk"

	if [[ ! -x "$gradle" || ! -f "$backend_dir/settings.gradle.kts" ]]; then
		log_fail PrepareFiles VSBackend "missing unified Gradle project in $backend_dir"
		return 1
	fi

	log_start PrepareFiles VSBackend
	if ! log_command_output PrepareFiles VSBackend \
		"$gradle" --no-daemon -p "$backend_dir" \
		:vsteam:assembleRelease; then
		log_fail PrepareFiles VSBackend "Gradle release build failed"
		return 1
	fi

	if [[ ! -s "$backend_vsteam_apk" ]]; then
		log_fail PrepareFiles VSBackend "Gradle finished without vsteam.apk"
		return 1
	fi

	log_done PrepareFiles VSBackend
	return 0
}

backend() {
	local framework_dir="$dir/module/lang/framework"
	local framework_stage="$dir/module/lang/framework.new"
	local apk
	local pkg
	local src
	local dest

	if ! build_backend_apks; then
		notify_build_state -2
		return 1
	fi

	# vsteam.apk contains both Toolbox and the attestation implementation.
	declare -A pkgs=(
		["vsteam.apk"]="com.vsteam.toolbox smali/com/android/vsteam $framework_stage/vsteam"
	)
	declare -A apk_paths=(
		["vsteam.apk"]="$backend_vsteam_apk"
	)

	# Build the generated framework sources atomically. A decode/ABI failure
	# must not destroy the last known-good smali tree.
	rm -rf "$framework_stage"
	mkdir -p "$framework_stage"

	for apk in vsteam.apk; do
		read pkg src dest <<<"${pkgs[$apk]}"

		if ! decp "${apk_paths[$apk]}:=$pkg"; then
			rm -rf "$framework_stage"
			return 1
		fi
		[[ -d $dest ]] && rm -rf "$dest"
		mkdir -p "$dest"

		if [[ "$apk" == "vsteam.apk" ]]; then
			if ! validate_subattest_backend "$src" PrepareFiles; then
				rm -rf "$framework_stage"
				return 1
			fi
			mkdir -p "$dest/com/android/vsteam"
			cp "$src"/* "$dest/com/android/vsteam"
		fi
	done

	if ! validate_attestation_bridge "$framework_stage/vsteam"; then
		rm -rf "$framework_stage"
		return 1
	fi

	rm -rf "$framework_dir"
	mv "$framework_stage" "$framework_dir"
}

framework() {

	jarf='framework.jar'
	lang_dir="$dir/module/lang"
	local ecm_session_params

	new_jar_util d $jarf fw
	cd "$dir/jar_temp/$jarf.out"

	#logcore $jarf "Control AOPS ShareSheet" true
	#repM 'useAospShareSheet' global 'ResolverListAdapter.smali' "vs_aosp_sharesheet"

	logcore $jarf "Disabling signature verification" true
	python3 $dir/bin/corepatch_framework.py $(pwd)

	# Android 17+ ART rejects Field.set() on static final fields, so VsProps
	# spoofing needs Build/Build$VERSION fields to be non-final.
	logcore $jarf "Making Build spoof fields non-final" true
	python3 $dir/bin/patch_build_nonfinal.py $(pwd)

	# HyperOS liquid glass: scale refraction by Settings.Global vs_liquid_glass_level.
	logcore $jarf "Tunable liquid glass refraction" true
	python3 $dir/bin/patch_miglass_tune.py $(pwd) || return 1
	add_cap liquid_glass

	# "sig"/"sigvoid" gate the bypass on corepatchBypass() (switch ON AND not
	# the protected package), so com.bhlnk.vsteam always runs the real
	# signature-capability / V1 verification even with CorePatch enabled.
	repB 'checkCapability(Ljava/lang/String;I)' "vs_corepatch_damage" sig SigningDetails.smali
	repB 'checkCapability(Landroid/content/pm/SigningDetails;I)' "vs_corepatch_damage" sig SigningDetails.smali
	repB "checkCapabilityRecover" "vs_corepatch_damage" sig SigningDetails.smali
	repB "hasAncestorOrSelf" "vs_corepatch_damage" sig SigningDetails.smali

	repB 'checkCapability(Ljava/lang/String;I)' "vs_corepatch_damage" sig 'PackageParser$SigningDetails.smali'
	repB 'checkCapability(Landroid/content/pm/SigningDetails;I)' "vs_corepatch_damage" sig 'PackageParser$SigningDetails.smali'
	repB "checkCapabilityRecover" "vs_corepatch_damage" sig 'PackageParser$SigningDetails.smali'
	repB "hasAncestorOrSelf" "vs_corepatch_damage" sig 'PackageParser$SigningDetails.smali'

	repB "verify" "vs_corepatch_damage" sig 'StrictJarVerifier.smali'
	repB "verifyMessageDigest" "vs_corepatch_damage" sig 'StrictJarVerifier.smali'
	repB "verifyCertificate" "vs_corepatch_damage" sigvoid 'StrictJarVerifier.smali'

	# Android 15+ Enhanced Confirmation Mode (ECM) treats packages marked as
	# PACKAGE_SOURCE_LOCAL_FILE / PACKAGE_SOURCE_DOWNLOADED_FILE as untrusted and
	# blocks sensitive settings. Keep the source at UNSPECIFIED for every
	# installer so Android 16 and Android 17 both skip this restriction.
	ecm_session_params=$(find . -type f -name 'PackageInstaller$SessionParams.smali' -print -quit)
	if [[ -n "$ecm_session_params" ]]; then
		logcore $jarf "Disable Enhanced Confirmation Mode source restriction"
		$repM 'setPackageSource(I)V' void "$ecm_session_params"
	else
		log_warn "$jarf" 'PackageInstaller$SessionParams.smali' "not present; skip ECM source patch"
	fi

	count=$(expr $(ls -dq *smali* | wc -l) + 1)
	backend_smali_dir="smali_classes$count"
	mkdir "$backend_smali_dir"

	logcore $jarf "Copy VS backend $backend_smali_dir"
	cp -rf "$lang_dir/framework/vsteam/." "$backend_smali_dir/"
	[[ $brand == "ColorOS" && -d "$lang_dir/framework/oplus" ]] && cp -rf "$lang_dir/framework/oplus/." "$backend_smali_dir/"
	[[ $brand == "ColorOS" && -d "$lang_dir/framework/com" ]] && cp -rf "$lang_dir/framework/com/." "$backend_smali_dir/"
	validate_subattest_backend "$backend_smali_dir" Framework || return 1
	validate_attestation_bridge "$backend_smali_dir" || return 1

	subattest_smali="$backend_smali_dir/com/android/vsteam/SubAttest.smali"
	[[ -f "$subattest_smali" ]] || {
		log_fail Framework framework.jar "missing SubAttest target for onGetEntry patch"
		return 1
	}
	$repS $dir/module/lang/onGetEntry.config "$subattest_smali" "replace"

	logcore $jarf "MushromTools backend"
	keystore_config=$(versioned_config "$dir/module/lang/keystore.config" ".ini")
	prop1_config=$(versioned_config "$dir/module/lang/prop1.config" ".ini")
	prop2_config=$(versioned_config "$dir/module/lang/prop2.config" ".ini")
	access_config=$(versioned_config "$dir/module/lang/canAccessUnexportedComponents" ".config")
	platform_key_config=$(versioned_config "$dir/module/lang/isSignedWithPlatformKey" ".config")
	patch_prop_config=$(versioned_config "$dir/module/lang/patch_prop" ".config")

	$repR "$keystore_config" $(sudo find -name "AndroidKeyStoreSpi.smali")
	$repS "$prop1_config" $(sudo find -name "Instrumentation.smali") "replace"
	$repS "$prop2_config" $(sudo find -name "Instrumentation.smali") "replace"
	$repS "$access_config" $(sudo find -name "ActivityManager.smali") "replace"
	$repS "$platform_key_config" $(sudo find -name "ApplicationInfo.smali") "replace"

	sed -i -E 's/check-cast v([0-9]+), Landroid\/system\/keystore2\/KeyEntryResponse;/check-cast v\1, Landroid\/system\/keystore2\/KeyEntryResponse;\n    invoke-static {v\1}, Lcom\/android\/vsteam\/SubAttest;->onGetKeyEntry(Landroid\/system\/keystore2\/KeyEntryResponse;)Landroid\/system\/keystore2\/KeyEntryResponse;\n    move-result-object v\1/g' $(sudo find -name "KeyStore2.smali")

	# Route B (fully automatic): real TEE attestation is attempted first; only when it
	# fails with "attestation keys not provisioned" (KeyMint -74, typical of RKP-only
	# devices that cannot provision) does generateKeyPair retry with the challenge
	# stripped and the leaf get synthesized from the keybox at read time via
	# onGetKeyEntry. Best-effort: a ROM without this exact Spi shape is only a warning.
	logcore $jarf "Route B: attestation synth generate hook"
	python3 $dir/bin/patch_keystore_synth.py "$(pwd)" ||
		log_warn "$jarf" AndroidKeyStoreKeyPairGeneratorSpi.smali "interceptChallenge hook not applied; vs_synth_attestation will be inert"

	# Widevine L1 initializes but cannot provision on these models (the TA fails
	# derivekey_from_context), so Netflix stops with -93: MediaDrm runs them at L3.
	if [[ -f "$dir/fastboot/widevine_l3.list" ]] && grep -iqx "${device^^}" "$dir/fastboot/widevine_l3.list"; then
		logcore $jarf "Force Widevine L3"
		python3 "$dir/bin/patch_widevine_l3.py" "$(pwd)" ||
			log_warn "$jarf" MediaDrm.smali "Widevine L3 hook not applied"
	fi

	mvst 'Instrumentation.smali' "$backend_smali_dir" true
	repM 'getMinimumSignatureSchemeVersionForTargetSdk' 1 ApkSignatureVerifier.smali
	remM 'get(Ljava/lang/String;)Ljava/lang/String' 'SystemProperties.smali'
	remM 'get(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String' 'SystemProperties.smali'
	remM 'getInt(Ljava/lang/String;I)I' 'SystemProperties.smali'

	add_method "$patch_prop_config" 'SystemProperties.smali'
	repM 'getMinimumSignatureSchemeVersionForTargetSdk' 1 ApkSignatureVerifier.smali

	if [[ $brand == "ColorOS" ]]; then
		sed -i 's/invoke-interface {v0}, Landroid\/app\/ISearchManager;->getGlobalSearchActivity()Landroid\/content\/ComponentName;/invoke-static {}, Lcom\/android\/vsteam\/Toolbox;->getGlobalSearchActivity()Landroid\/content\/ComponentName;/' $(sudo find -name "SearchManager.smali")

		logcore $jarf "Fix small icon"
		$repS "$(versioned_config "$dir/module/lang/fixsmallicon" ".config")" $(sudo find -name "NotificationManager.smali") "replace"

		logcore $jarf "Support multiple audio output"
		repB 'requestAudioFocus(Landroid/media/AudioFocusRequest;Landroid/media/audiopolicy/AudioPolicy;)I' "vs_multiple_audio_output" 1 'AudioManager.smali' --no-register-bump

		# Redirect addReceiver() instead of replacing whole parse methods: the
		# Android 16 method bodies referenced ParsingPackageUtils.sAconfigFlags,
		# which Android 17 dropped (AconfigFlags.getInstance()), and crashed
		# system_server with NoSuchFieldError while scanning APEXes.
		logcore $jarf "Crack trial theme"
		parsing_utils=$(find . -type f -path '*/com/android/internal/pm/pkg/parsing/ParsingPackageUtils.smali' -print -quit)
		if [[ -z "$parsing_utils" || ! -f "$backend_smali_dir/com/android/vsteam/ThemeTrialStrip.smali" ]]; then
			log_warn "$jarf" ParsingPackageUtils.smali "missing target or ThemeTrialStrip; skip trial theme crack"
		else
			sed -i -E 's#invoke-interface(/range)? \{([^}]*)\}, Lcom/android/internal/pm/pkg/parsing/ParsingPackage;->addReceiver\(Lcom/android/internal/pm/pkg/component/ParsedActivity;\)Lcom/android/internal/pm/pkg/parsing/ParsingPackage;#invoke-static\1 {\2}, Lcom/android/vsteam/ThemeTrialStrip;->addReceiver(Lcom/android/internal/pm/pkg/parsing/ParsingPackage;Lcom/android/internal/pm/pkg/component/ParsedActivity;)Lcom/android/internal/pm/pkg/parsing/ParsingPackage;#g' "$parsing_utils"
			grep -q 'Lcom/android/vsteam/ThemeTrialStrip;->addReceiver' "$parsing_utils" ||
				log_warn "$jarf" ParsingPackageUtils.smali "no addReceiver call patched"
		fi
	fi

	# Widget stacks need the minus screen to declare supportStackWidget; add it
	# to App Vault's <application> meta-data at parse time so Play Store
	# updates of App Vault keep it.
	if [[ $brand == "HyperOS" ]]; then
		logcore $jarf "App Vault widget stack meta-data"
		parsing_utils=$(find . -type f -path '*/com/android/internal/pm/pkg/parsing/ParsingPackageUtils.smali' -print -quit)
		if [[ -z "$parsing_utils" || ! -f "$backend_smali_dir/com/android/vsteam/AppMetaData.smali" ]]; then
			log_warn "$jarf" ParsingPackageUtils.smali "missing target or AppMetaData; skip widget stack meta-data"
		else
			sed -i -E 's#invoke-interface(/range)? \{([^}]*)\}, Lcom/android/internal/pm/pkg/parsing/ParsingPackage;->setMetaData\(Landroid/os/Bundle;\)Lcom/android/internal/pm/pkg/parsing/ParsingPackage;#invoke-static\1 {\2}, Lcom/android/vsteam/AppMetaData;->setMetaData(Lcom/android/internal/pm/pkg/parsing/ParsingPackage;Landroid/os/Bundle;)Lcom/android/internal/pm/pkg/parsing/ParsingPackage;#g' "$parsing_utils"
			grep -q 'Lcom/android/vsteam/AppMetaData;->setMetaData' "$parsing_utils" ||
				log_warn "$jarf" ParsingPackageUtils.smali "no setMetaData call patched"
		fi
	fi

	rebalance_smali_splits

	new_jar_util a $jarf fw
}

settingsprovider() {
	pkg="com.android.providers.settings"
	tempstring=$(check_file_exist $pkg)
	if [[ $tempstring ]]; then
		decp $tempstring || return 1

		log $pkg "Patch SSAID override in SettingsProvider"
		ssaid_config="$dir/module/lang/SettingsProvider_ssaid.config"
		android_version_at_least 17 && ssaid_config="$dir/module/lang/SettingsProvider_ssaid_17.config"
		remM 'getSsaidSettingLocked(Landroid/content/pm/PackageInfo;I)Lcom/android/providers/settings/SettingsState$Setting;' 'SettingsProvider.smali'
		add_method "$ssaid_config" 'SettingsProvider.smali'

		recp $tempstring $1
	fi
}

services() {

	jarf='services.jar'

	lang_dir="$dir/module/lang"

	new_jar_util d $jarf fw
	cd "$dir/jar_temp/$jarf.out"

	logcore $jarf "Disabling signature verification" true

	# Signature-identity gates: "sig"/"sig0" gate the bypass on corepatchBypass()
	# (switch ON AND not the protected package) so com.bhlnk.vsteam always runs
	# the real signature comparison even with CorePatch enabled. The non-signature
	# toggles below (downgrade / apk-verity / verifier) stay unconditional.
	repB "doesSignatureMatchForPermissions" "vs_corepatch_damage" sig InstallPackageHelper.smali
	repB "isDowngradePermitted" "vs_corepatch_damage" 1 PackageManagerServiceUtils.smali
	repB "compareSignatures" "vs_corepatch_damage" sig0 PackageManagerServiceUtils.smali
	repB "isApkVerityEnabled" "vs_corepatch_damage" 0 PackageManagerServiceUtils.smali

	repB "matchSignatureInSystem" "vs_corepatch_damage" sig0 PackageManagerServiceUtils.smali
	repB "matchSignaturesCompat" "vs_corepatch_damage" sig0 PackageManagerServiceUtils.smali
	repB "matchSignaturesRecover" "vs_corepatch_damage" sig0 PackageManagerServiceUtils.smali
	repB "verifySignatures" "vs_corepatch_damage" sig0 PackageManagerServiceUtils.smali
	repB "checkDowngrade" "vs_corepatch_damage" void PackageManagerServiceUtils.smali
	repB "shouldCheckUpgradeKeySetLocked" "vs_corepatch_damage" sig0 KeySetManagerService.smali
	repB "isPersistent" "vs_corepatch_damage" 0 PackageSetting.smali
	repB "isVerificationEnabled" "vs_corepatch_damage" 0 PackageManagerServiceStub.smali
	repB "needSkipDomainVerifier" "vs_corepatch_damage" 1 PackageManagerServiceStub.smali
	ftrue $(sudo find -name 'ReconcilePackageUtils.smali') $debug
	python3 $dir/bin/corepatch_services.py $(pwd)

	# Play Protect rejects Mushrom's overlays/plugins server-side; skip the
	# package verifier when Mushrom is the session's installer.
	logcore $jarf "Skip package verifier for Mushrom installs"
	if ! python3 "$dir/bin/patch_vsteam_skip_verifier.py" "$(pwd)"; then
		log_warn "$jarf" "Skip package verifier for Mushrom installs" "VerifyingSession not matched; skip"
	fi

	# Mushrom passes every permission check (canAccessUnexportedComponents), so
	# isAdoptedShell() took its installs for `adb install` and dropped
	# INSTALL_REPLACE_EXISTING: updates failed and installs were not silent.
	logcore $jarf "Mushrom installs are not shell installs"
	python3 "$dir/bin/patch_vsteam_not_adopted_shell.py" "$(pwd)" || return 1

	# Camera ring (rotary encoder, Xiaomi 17 Ultra by Leica): turning it runs the
	# action picked in Mushrom (vs_focus_ring_cw/ccw) through
	# com.android.vsteam.FocusRing; the camera app keeps its zoom.
	logcore $jarf "Custom camera ring actions"
	if python3 "$dir/bin/patch_focus_ring.py" "$(pwd)"; then
		add_cap focus_ring
	else
		log_warn "$jarf" "Custom camera ring actions" "InputManagerService not matched; skip"
	fi

	# DPI per app from the Mushrom spoof profile ("density" of vs_spoof_<package>),
	# applied by com.android.vsteam.AppDensity as a window manager compat scale.
	logcore $jarf "Custom DPI per app"
	if python3 "$dir/bin/patch_app_density.py" "$(pwd)"; then
		add_cap app_density
	else
		log_warn "$jarf" "Custom DPI per app" "InputManagerService not matched; skip"
	fi

	logcore $jarf "Allow take screenshot secure"
	repM isSecureLocked 0 WindowState.smali
	repM registerScreenCaptureObserver void ActivityTaskManagerService.smali

	logcore $jarf "Allow modify system apk"
	repM 'isPlatformSigned' 1 'PackageManagerService$PackageManagerInternalImpl.smali'
	repM 'isSignedWithPlatformKey' 1 'PackageImpl.smali'
	repM isAccessibilityTargetAllowed 1 AccessibilityManagerService.smali
	repM isGestureLauncherEnabled 0 GestureLauncherService.smali

	logcore $jarf "Allow install overlay apk"
	$repS "$(versioned_config "$dir/module/lang/allow_install_overlay" ".config")" $(sudo find -name "ScanPackageUtils.smali") "replace"
	$repS "$(versioned_config "$dir/module/lang/allow_keep_install_overlay" ".config")" $(sudo find -name "InstallPackageHelper.smali") "replace"
	$repS "$(versioned_config "$dir/module/lang/keep_overlay_state" ".config")" $(sudo find -name "IdmapManager.smali") "replace"

	if [[ $brand == "HyperOS" ]]; then
		logcore $jarf "Disable MIUI Greeze JobScheduler restriction"
		repM isJobRestricted 0 FreezeStatusRestriction.smali

		logcore $jarf "Disable AOSP thermal refresh-rate throttling"
		repM observe void SkinThermalStatusObserver.smali
		repM notifyThrottling void SkinThermalStatusObserver.smali

		logcore $jarf "Keep full-screen intent of call notifications"
		log_command_output Permanent "$jarf" python3 "$dir/bin/patch_fsi_call_permission.py" "$(pwd)" || return 1
	fi

	logcore $jarf "Always hide selected packages from getInstalledApplications/getInstalledPackages"
	patch_pm_always_hidden_packages

	rebalance_smali_splits

	new_jar_util a $jarf fw
}

miui-framework() {
	jarf='miui-framework.jar'
	new_jar_util d "$jarf" fw || return 1
	cd "$dir/jar_temp/miui-framework.jar.out" || return 1

	logcore $jarf "Fix reset theme"
	find "$(pwd)" -type f ! -name 'DrmManager$DrmResult.smali' -exec sed -i 's/DRM_ERROR_UNKNOWN/DRM_SUCCESS/g' {} +
	find "$(pwd)" -type f ! -name 'DrmManager$DrmResult.smali' -exec sed -i 's/DRM_ERROR_ASSET_NOT_MATCH/DRM_SUCCESS/g' {} +

	logcore $jarf "Prevent change font when turn on Optx"
	repM "isMiuiOptimizeEnabled" 1 FontSettings.smali

	fstring '"com.android.permissioncontroller"' '"com.google.android.permissioncontroller"'

	logcore $jarf "Region picker"
	ftrue $(sudo find -name 'CustomizeUtil.smali') $key2
	ftrue $(sudo find -name 'LocaleComparator.smali') $key1
	ftrue $(sudo find -name 'LocalePickerInjector.smali') $key1
	ftrue $(sudo find -name 'DefaultAppsHelper.smali') $key1

	logcore $jarf "Skip auto start restriction"
	repM 'isAutoStartRestriction' 0 AppOpsManagerInjector.smali

	logcore $jarf "Control AOPS ShareSheet" true
	if find . -type f -name 'ResolverActivityStubImpl.smali' -print -quit | grep -q .; then
		repM 'useAospShareSheet' global 'ResolverActivityStubImpl.smali' "vs_aosp_sharesheet"
		repM 'getAlwaysUse' 1 'ResolverActivityStubImpl.smali'
	else
		# Removed from the Android 17 miui-framework ABI, so there is no MIUI
		# resolver stub in this JAR to patch.
		log_warn "$jarf" ResolverActivityStubImpl "not present; skip ShareSheet stub patch"
	fi

	logcore $jarf "Control AOSP ScreenShot" true
	repM 'getServiceComponent' "string" "ScreenshotHelperStubImpl.smali" "get_custom_screenshot"

	fstring "com.baidu.input_mi" "com.google.android.inputmethod.latin"

	logcore $jarf "AOSP Package installer"
	repM 'isXOptMode' 1 AppOpsUtils.smali

	rebalance_smali_splits

	new_jar_util a "$jarf" fw
}

miui-services() {
	jarf='miui-services.jar'
	local extra_smali_dir
	local thermal_observer

	new_jar_util d "$jarf" fw || return 1
	cd "$dir/jar_temp/miui-services.jar.out" || return 1

	extra_smali_dir=$(next_smali_classes_dir)
	mkdir -p "$extra_smali_dir"

	files=$(grep -rlE "$key1|$key2" .)

	for f in $files; do
		ftrue "$f" "$key1"
		ftrue "$f" "$key2"
	done

	logcore $jarf "Use AOSP background policy instead of SmartPower"
	repM isAppHibernationWhiteList 1 SmartPowerPolicyManager.smali
	repM isProcessHibernationWhiteList 1 SmartPowerPolicyManager.smali
	repM shouldInterceptAlarmLocked 0 SmartPowerPolicyManager.smali
	repM shouldInterceptBroadcastLocked 0 SmartPowerPolicyManager.smali
	repM shouldInterceptProviderLocked 0 SmartPowerPolicyManager.smali
	repM shouldInterceptService 0 SmartPowerPolicyManager.smali
	if find . -type f -name 'PowerFrozenManager.smali' -print -quit | grep -q .; then
		repM isEnable 0 PowerFrozenManager.smali
	else
		# PowerFrozenManager was removed on Android 17. Greeze and the remaining
		# freeze entry points below are still patched individually.
		log_warn "$jarf" PowerFrozenManager "not present; use Greeze API 37 patches"
	fi
	repM handleLimitCpuException void SmartCpuPolicyManager.smali

	logcore $jarf "Disable MIUI screen-off process cleaners"
	repM handleScreenOffEvent void ProcessPowerCleaner.smali
	repM handleScreenOff void PeriodicCleanerService.smali
	repM isEnable 0 MemoryStandardProcessControl.smali

	logcore $jarf "Disable MIUI Greeze and Aurogon restrictions"
	repM freezeAction 0 GreezeManagerService.smali
	repM freezePid 0 FreezeUtils.smali
	repM freezeTid 0 FreezeUtils.smali
	repM freezeUid 0 FreezeUtils.smali
	repM isJobRestrict 0 GreezeManagerService.smali
	repM isRestrictReceiver 0 GreezeManagerService.smali
	repM checkAurogonIntentDenyList 0 GreezeManagerService.smali
	repM isNoRestrictApp 1 AurogonImmobulusMode.smali

	repM allowImmobulusMode 0 DomesticPolicyManager.smali
	repM allowLaunchMode 0 DomesticPolicyManager.smali
	repM checkAurogonIntentDenyList 0 DomesticPolicyManager.smali
	repM deferBroadcast 0 DomesticPolicyManager.smali
	repM isAutoStartRestrict 0 DomesticPolicyManager.smali
	repM isJobRestrict 0 DomesticPolicyManager.smali
	repM isRestrictBroadcast 0 DomesticPolicyManager.smali
	repM isRestrictNet 0 DomesticPolicyManager.smali
	repM needCacheBroadcast 0 DomesticPolicyManager.smali
	repM needMonitorNet 0 DomesticPolicyManager.smali
	repM updateAurogonUidRule void DomesticPolicyManager.smali

	repM allowImmobulusMode 0 InternationalPolicyManager.smali
	repM allowLaunchMode 0 InternationalPolicyManager.smali
	repM checkAurogonIntentDenyList 0 InternationalPolicyManager.smali
	repM deferBroadcast 0 InternationalPolicyManager.smali
	repM enableNewStrategy 0 InternationalPolicyManager.smali
	repM isAutoStartRestrict 0 InternationalPolicyManager.smali
	repM isJobRestrict 0 InternationalPolicyManager.smali
	repM isRestrictBroadcast 0 InternationalPolicyManager.smali
	repM isRestrictNet 0 InternationalPolicyManager.smali
	repM needCacheBroadcast 0 InternationalPolicyManager.smali
	repM needMonitorNet 0 InternationalPolicyManager.smali
	repM updateAurogonUidRule void InternationalPolicyManager.smali

	logcore $jarf "Disable MIUI WakePath background interception"
	repM checkServiceWakePath 1 ActivityManagerServiceImpl.smali
	repM checkWakePath 1 ActivityManagerServiceImpl.smali
	repM isRestrictBackgroundAction 1 ActivityManagerServiceImpl.smali
	repM checkBroadcastWakePath 1 'SecurityManagerService$LocalService.smali'
	repM isAllowedByWakePathRule 1 'SecurityManagerService$LocalService.smali'
	repM calleeAliveMatchBlackRule 0 'SecurityManagerService$LocalService.smali'
	repM checkBroadcastWakePath 1 WakePathChecker.smali
	repM isAllowedByWakePathRule 1 WakePathChecker.smali
	repM calleeAliveMatchBlackRule 0 WakePathChecker.smali

	logcore $jarf "Disable toast access"
	repM onShowAccessToast 0 ClipboardServiceStubImpl.smali

	logcore $jarf "Disable MIUI thermal refresh-rate throttling"
	repM getThermalLimitRefreshRange nullz DisplayModeDirectorImpl.smali
	thermal_observer='SmartDisplayPolicyManager$RefreshRatePolicyController$SettingRefreshRateObserver.smali'
	if android_version_at_least 17; then
		thermal_observer='RefreshRatePolicy$RefreshRatePolicyController$SettingRefreshRateObserver.smali'
	fi
	repM updateThermalRefreshRateStatus void "$thermal_observer"
	# OS 17 MIRIM: above persist.sys.smartpower.display_thermal_temp_threshold
	# mIsTempWarning stops every interaction refresh-rate boost; keep it false.
	repM onThermalTempChange void 'RefreshRatePolicy$ThermalTempListenerCallback.smali'

	logcore $jarf "Drop per-app refresh-rate cap (MiuiRefreshRatePolicy scene fps)"
	log_command_output Permanent "$jarf" python3 "$dir/bin/patch_miui_max_fps.py" "$(pwd)" || return 1

	logcore $jarf "Allow app auto-start after reboot"
	repM isAllowStartService 1 AutoStartManagerServiceStubImpl.smali
	repM canRestartServiceLocked 1 AutoStartManagerServiceStubImpl.smali
	repM checkApplicationAutoStart 1 BroadcastQueueModernStubImpl.smali
	repM isAllowAutoStart 1 ProcessManagerService.smali

	logcore $jarf "Prevent SwipeUpClean and OneKeyClean force-stop"
	repM 'isForceStopEnable' 0 'ProcessManagerService.smali'

	logcore $jarf "Prevent remove data-app after reboot"
	repM 'checkGTSSpecAppOptMode' void "PackageManagerServiceImpl.smali"
	fstring '"com.android.permissioncontroller"' '"com.google.android.permissioncontroller"'

	repM 'canBeUpdate' void PackageManagerServiceImpl.smali
	repM 'checkInstallRestriction' void PackageManagerServiceImpl.smali
	repM 'verifyMmsViolation' void PackageManagerServiceImpl.smali
	repM 'verifyIsolationViolation' void PackageManagerServiceImpl.smali

	logcore $jarf "Alway allow to get installed app list"
	repM isAllowedToGetInstalledApps 1 PackageManagerServiceImpl.smali

	logcore $jarf "Dark mode invidual app"
	ftrue $(sudo find -name 'ForceDarkAppListManager.smali') $key1

	logcore $jarf "Fix power menu long time appear"
	ftrue $(sudo find -name 'MiuiShortcutTriggerHelper$ShortcutSettingsObserver.smali') $key2

	logcore $jarf "Fix package installer missing in Second Space"
	ftrue $(sudo find -name 'SettingsImpl.smali') $key1

	logcore $jarf "Disable ask open app when app not running"
	ftrue $(sudo find -name 'ActivitySecurityHelper.smali') $key1

	logcore $jarf "Allow take screenshot secure"
	repM 'notAllowCaptureDisplay' 0 'WindowManagerServiceImpl.smali'

	logcore $jarf "Show full-screen intent activities on lock screen"
	log_command_output Permanent "$jarf" python3 "$dir/bin/patch_show_when_locked.py" "$(pwd)" || return 1

	# Camera ring (Xiaomi 17 Ultra by Leica): outside the camera the sensor hub only
	# reports detents while a Mushrom ring action is set (see services(), FocusRing).
	logcore $jarf "Camera ring detents outside the camera"
	if ! python3 "$dir/bin/patch_focus_ring_mr.py" "$(pwd)"; then
		log_warn "$jarf" "Camera ring detents outside the camera" "CameraMrEventHandler not matched; skip"
	fi

	#fstring 'Landroid/view/animation/DeviceHelper;->isFoldDevice()Z' 'Lcom/android/vsteam/VsProps;->justTrue()Z' 'MiuiMultiFingerGestureManager.smali'

	fstring "com.baidu.input_mi" "com.google.android.inputmethod.latin"

	repM "postTriggerPowerGuide" 1 "PowerKeyRule.smali"
	repB "onMiuiLongPress" "long_press_power_key" action_void PowerKeyRule.smali
	repB "triggerDoubleClick" "double_click_power_key" action_true PowerKeyRule.smali

	repM "enforceVersionPolicy" void "SystemServerImpl.smali"

	mvst 'FolmeFont$FontType.smali' "$extra_smali_dir" true

	rebalance_smali_splits

	new_jar_util a "$jarf" fw
}

miui-wifi-service() {
	local jarf='miui-wifi-service.jar'
	local double_calls
	local float_calls

	new_jar_util d "$jarf" fw || return 1
	cd "$dir/jar_temp/$jarf.out" || return 1

	logcore "$jarf" "Bypass dot and comma"

	double_calls=$(grep -RohF 'Ljava/lang/Double;->parseDouble(Ljava/lang/String;)D' . --include='*.smali' | wc -l)
	float_calls=$(grep -RohF 'Ljava/lang/Float;->parseFloat(Ljava/lang/String;)F' . --include='*.smali' | wc -l)

	find . -type f -name '*.smali' -exec sed -i 's/Ljava\/lang\/Double;->parseDouble(Ljava\/lang\/String;)D/Lcom\/android\/vsteam\/Toolbox;->parseDoubleFromString(Ljava\/lang\/String;)D/g' {} +
	find . -type f -name '*.smali' -exec sed -i 's/Ljava\/lang\/Float;->parseFloat(Ljava\/lang\/String;)F/Lcom\/android\/vsteam\/Toolbox;->parseFloatFromString(Ljava\/lang\/String;)F/g' {} +

	if ((double_calls == 0 && float_calls == 0)); then
		log_warn "$jarf" Parser "no parseDouble/parseFloat calls found"
	else
		logcore "$jarf" "Patched $double_calls double and $float_calls float parsers"
	fi

	rebalance_smali_splits

	new_jar_util a "$jarf" fw
}

oplus-framework() {
	jarf='oplus-framework.jar'
	new_jar_util d $jarf fw
	cd "$dir/jar_temp/oplus-framework.jar.out"

	logcore $jarf "Hook Font for anylang"

	repM 'isCurrentLanguageSupportMediumFont' 1 'OplusFontUtils.smali'
	repM 'isCurrentLanguageSupportVariationFont' 1 'OplusFontUtils.smali'

	logcore $jarf "Hook feature for all brands"
	$repS $dir/module/lang/oplusfeature.config $(sudo find -name "OplusFeatureConfigManager.smali") "replace"

	mvst 'OplusFontUtils.smali' "$dir/module/lang/framework" true
	mvst 'OplusFeatureConfigManager.smali' "$dir/module/lang/framework" true

	rm -rf "$dir/jar_temp/oplus-framework.jar.out"

}

oplus-camera-unit-sdk() {
	local jarf='com.oplus.camera.unit.sdk.jar'
	local decoded="$dir/jar_temp/$jarf.out"
	local target
	local oat_root
	local arch
	local suffix

	if ! find "$dir/img_temp" -type f -name "$jarf" -print -quit | grep -q .; then
		log_warn CameraConfig "$jarf" "not found; skip runtime delta hook"
		return 0
	fi

	new_jar_util d "$jarf" fw || return 1
	cd "$decoded" || {
		log_fail CameraConfig "$jarf" "missing decoded smali root"
		return 1
	}

	logcore "$jarf" "Hook compact Settings.Global camera delta"
	python3 "$dir/bin/patch_oplus_camera_sdk_config.py" "$decoded" || return 1

	rebalance_smali_splits

	new_jar_util a "$jarf" fw || return 1

	# Remove only this JAR's stale preopt files after the shared assembler copied it back.
	target=$(find "$dir/img_temp" -type f -name "$jarf" -print -quit)
	oat_root="$(dirname "$target")/oat"
	if [[ -d "$oat_root" ]]; then
		for arch in "$oat_root"/*; do
			[[ -d "$arch" ]] || continue
			for suffix in odex vdex art; do
				rm -f "$arch/com.oplus.camera.unit.sdk.$suffix"
			done
		done
	fi
}

oplus-services() {
	jarf='oplus-services.jar'
	new_jar_util d $jarf fw
	cd "$dir/jar_temp/oplus-services.jar.out"

	logcore $jarf "MushromTools touch report rate"
	python3 "$dir/bin/patch_oplus_services_touch_rate.py" "$(pwd)" || return 1

	logcore $jarf "Hold Power to call Gemini" true
	$repS "$dir/module/lang/color_gemini.config" $(sudo find -name "PhoneWindowManagerExtImpl.smali") "replace"
	$repS "$dir/module/lang/double_power_key.config" $(sudo find -name "OplusWalletController.smali") "replace"

	logcore $jarf "Fix notification"
	repM "hansEnabled" 0 'OplusHansManager$HansConfig.smali'
	patch_oplus_background_notification_policy

	logcore $jarf "Hook isExpRegion"
	repM 'isDomesticRegion' 1 'FlexibleWindowUtils.smali'
	repM 'isDomestic' 1 'OplusNotificationRUSHelper.smali'
	repM 'setPermissionInterceptEnable' void 'OplusPermissionInterceptPolicy.smali'

	force_put_option "Lcom/android/server/am/OplusPermissionInterceptPolicy;->sIsPermissionInterceptEnabled:Z" "vs_aosp_perm" "OplusPermissionInterceptPolicy.smali"

	fstring oplus.settings.quickstart.flag.provider.open com.bhlnk.vsteam

	fstring "com.finshell.wallet" "com.bhlnk.vsteam" $(sudo find -name "OplusWalletController.smali") r
	fstring "com.coloros.calendar" "com.oplus.calendar" $(sudo find -name "OplusDefaultAppPolicyManager.smali") r

	repM 'isInMediaBlackList' 0 'OplusMediaControlService.smali'

	logcore $jarf "Hidden gesture bar long press"
	repM 'isSupportCircleToSearchNavbarHidden' 1 'OplusContextualNotificationManager.smali'

	repB "handleThreeSwipeUp" "vs_handleThreeSwipeUp" "shortcut" OplusMultiGestureThreeStrategy.smali
	repB "handleThreeSwipeDown" "vs_handleThreeSwipeDown" "shortcut" OplusMultiGestureThreeStrategy.smali
	repB "handleThreeLongPress" "vs_handleThreeLongPress" "shortcut" OplusMultiGestureThreeStrategy.smali
	repB "handleThreeSwipeLeft" "vs_handleThreeSwipeLeft" "shortcut" OplusMultiGestureThreeStrategy.smali
	repB "handleThreeSwipeRight" "vs_handleThreeSwipeRight" "shortcut" OplusMultiGestureThreeStrategy.smali

	logcore $jarf "Hook max refresh rate"
	repB "getRefreshRateById" "vs_handleThreeSwipeRight" "maxrf" OplusRefreshRateUtils.smali

	logcore $jarf "Hook disable flex windows"
	repB 'isSupportFlexibleWindow(Ljava/lang/String;Ljava/lang/String;)Z' "vs_disable_flexwindow" 0 'FlexibleWindowUtils.smali'
	repB 'isSupportFlexibleWindow(Ljava/lang/String;Ljava/lang/String;I)Z' "vs_disable_flexwindow" 0 'FlexibleWindowUtils.smali'

	repM 'allowInterceptAdbInstallInInstallStage' 0 'OplusPackageInstallInterceptManager.smali'
	repM 'getAdbInstallUninstallDisabled' 0 'OplusCustomizePackageManagerService.smali'
	repM 'getAdbInstallUninstallDisabled' 0 'OplusCustomizePmsFeature.smali'
	repM 'getMaxWinNum' 5 'FlexibleWindowManagerService.smali'

	repB "shouldKeepNotifcationWhenForceStop" "vs_keep_noti_when_clear" 1 OplusNotificationCommonPolicy.smali

	rebalance_smali_splits

	new_jar_util a $jarf fw
}

# Oplus doze/alarm/standby customisation lives here, not in oplus-services.jar.
oplus-service-jobscheduler() {
	jarf='oplus-service-jobscheduler.jar'

	if ! find "$dir/img_temp" -type f -name "$jarf" -print -quit | grep -q .; then
		log_warn "$jarf" "Oplus doze policy" "jar not found; skip"
		return 0
	fi

	new_jar_util d "$jarf" fw || return 1
	cd "$dir/jar_temp/$jarf.out" || return 1

	logcore "$jarf" "Disable Google restriction (network/app standby)"
	repM 'getGoogleRestrictSwitch' 0 'OplusDeviceIdleHelper.smali'
	repM 'matchGoogleRestrictRule' 0 'AppStandbyControllerExtImpl.smali'

	logcore "$jarf" "Disable screen-off alarm alignment"
	repM 'onScreenOff' void 'OplusAlarmAdjustment.smali'
	repM 'adjustAlarms' void 'OplusAlarmAdjustment.smali'
	repM 'adjustAlarmLocked' void 'OplusAlarmAdjustment.smali'

	rebalance_smali_splits

	new_jar_util a "$jarf" fw || return 1
}

# Wi-Fi pads have no SIM/cell, so OplusMultiNlpPolicy never clears its default
# mDeviceInRegion=true and pins the CN (Baidu) NLP, which returns nothing
# outside China. Force GMS NLP by never choosing the regional service.
oplus-lbs-services() {
	local jarf='oplus-lbs-services.jar'

	[[ "$isType" == "pad" ]] || return 0

	if ! find "$dir/img_temp" -type f -name "$jarf" -print -quit | grep -q .; then
		log_warn "$jarf" "GMS NLP" "jar not found; skip"
		return 0
	fi

	new_jar_util d "$jarf" fw || return 1
	cd "$dir/jar_temp/$jarf.out" || return 1

	logcore "$jarf" "Pad: use GMS network location instead of regional NLP"
	if ! find . -type f -name 'OplusMultiNlpPolicy.smali' -print -quit | grep -q .; then
		log_warn "$jarf" OplusMultiNlpPolicy "not present; skip"
		rm -rf "$dir/jar_temp/$jarf.out"
		return 0
	fi
	repM 'shouldUseRegionService' 0 'OplusMultiNlpPolicy.smali'

	rebalance_smali_splits

	new_jar_util a "$jarf" fw || return 1
}

subsys-channel-lock-plugin() {
	jarf='subsys-channel-lock-plugin.jar'
	new_jar_util d $jarf fw
	cd "$dir/jar_temp/$jarf.out"

	patch_region_lock_minus1

	logcore "$jarf" "Disable false carrier/device-lock (unblock SMS + calls)"
	repM 'isDeviceLockEffect' 0 'CarrierLockManager.smali'

	rebalance_smali_splits
	new_jar_util a $jarf fw
}

oplus-telephony-common() {
	jarf='oplus-telephony-common.jar'
	new_jar_util d $jarf fw
	cd "$dir/jar_temp/$jarf.out"

	patch_region_lock_minus1
	patch_simlock_unlock

	rebalance_smali_splits

	new_jar_util a $jarf fw
}

oplus-subsystem-service() {
	jarf='oplus-subsystem-service.jar'
	new_jar_util d $jarf fw
	cd "$dir/jar_temp/$jarf.out"

	patch_region_lock_minus1
	patch_subsys_simlock_unlock

	rebalance_smali_splits

	new_jar_util a $jarf fw

}

oplus-subsystem-service-ext() {
	jarf='oplus-subsystem-service-ext.jar'
	new_jar_util d $jarf fw
	cd "$dir/jar_temp/$jarf.out"

	patch_region_lock_minus1
	patch_subsys_simlock_unlock

	rebalance_smali_splits

	new_jar_util a $jarf fw
}


display_config() {
	cd $dir/img_temp

	for id in $(find -type f -name "display_id_*xml"); do
		echo -e "${RED}[DisplayConfig]${RESET}${YELLOW}[$(basename $id)]${RESET}$($pdisplay $id)"
	done
}

thermal_refresh_rate() {
	local image_root="${1:-$dir/img_temp}"

	if [[ $(basename "$image_root") == "odm" ]]; then
		image_root=$(dirname "$image_root")
	fi

	python3 "$dir/bin/patch_thermal_refresh_rate.py" "$image_root"
}

# Every HyperOS device: drop mi_thermald's game scene profiles
# (thermal-*game*.conf: cgame/mgame/tgame/hp-mgame, gl-/demo-ind- variants).
# When the game scene file cannot be opened mi_thermald keeps the config it used
# last ("use config file which last set"), so games run under that profile.
# The same names are removed from /data/vendor/thermal/config (cloud copies)
# before mi_thermald starts; PowerKeeper() stops new cloud downloads.
game_thermal_off() {
	local rc="$dir/img_temp/vendor/etc/init/hw/init.mi_thermald.rc"
	local conf name names=()

	log_start GameThermal "$device"

	while IFS= read -r conf; do
		name=$(basename "$conf")
		names+=("$name")
		sudo rm -f "$conf"
	done < <(sudo find "$dir/img_temp/odm/etc" "$dir/img_temp/vendor/etc" -maxdepth 1 -type f -name 'thermal-*game*.conf' | sort)

	if [[ ${#names[@]} -eq 0 ]]; then
		log_warn GameThermal "$device" "no thermal-*game*.conf found"
		return 0
	fi

	if [[ -f "$rc" ]] && ! sudo grep -q "VS_GAME_THERMAL_OFF" "$rc"; then
		{
			printf '\n# VS_GAME_THERMAL_OFF: drop cloud copies of the removed game profiles\n'
			printf 'on post-fs-data\n'
			# Cloud may also ship scene names this ROM does not carry.
			for name in $(printf '%s\n' "${names[@]}" thermal-{c,m,t,hp-m}game.conf | sort -u); do
				printf '    rm /data/vendor/thermal/config/%s\n' "$name"
			done
		} | sudo tee -a "$rc" >/dev/null
	fi

	log_done GameThermal "$device" "removed ${#names[@]} game thermal profile(s)"
}

# Screen-off "Hey Google" on the DSP: PAL reads the hotword module version only with
# get_module_version="true" (bin/patch_hotword_module_version.py). Every HyperOS device: the
# HyperOS block of bhlnk calls this. Checked on nezha (no ADSP crash on screen off); myron,
# athens and songyuan ship it on. The same switch crash-loops the ADSP of the OPPO Pad
# (opd2515), so ColorOS stays out.
hotword_module_version() {
	log_start HotwordDSP "$device"
	if sudo python3 "$dir/bin/patch_hotword_module_version.py" "$dir/img_temp"; then
		log_done HotwordDSP "$device" "HOTWORD_VOICE_UI get_module_version=true"
	else
		log_warn HotwordDSP "$device" "odm resourcemanager patch failed; skip"
	fi
}

update_xml_element() {
	local file="$1"
	local type="$2"
	local name="$3"
	local value="$4"

	local old_value=$(sudo sed -n "/<$type name=\"$name\">/{s/.*<${type} name=\"$name\">\(.*\)<\/${type}>/\1/p;q}" "$file")

	if [[ -n "$old_value" ]]; then
		sudo sed -i "s|<$type name=\"$name\">$old_value</$type>|<$type name=\"$name\">$value</$type>|g" "$file"
	else
		sudo sed -i "/<\/features>/i <$type name=\"$name\">$value</$type>" "$file"
	fi
}
device_feature() {

	supcup=$(sudo find "$dir/img_temp/" -type f -name "$device".xml)
	all_xml=$(ls $(dirname $supcup)/*xml)

	for supcu in $all_xml; do
		variant=$(basename $supcu)
		checkfilename=$(echo ${variant//.xml/})
		cp -rf $dir/info $dir/$device/firmware-update/$checkfilename.check
		log_start DeviceFeature "$variant"

		cp $supcu $dir/jar_temp
		supcu1=$dir/jar_temp/$variant

		#fix face-unlock
		sudo sed -i "s/CN/ALL/g" $supcu1

		#add 90hz to device with max refresh rate 120hz
		local prop_brand
		local max_refresh_fps
		prop_brand=$(getprop 'Build.BRAND')
		max_refresh_fps=$(sed -n 's/.*<integer name="support_max_fps">\([0-9]\+\)<\/integer>.*/\1/p' "$supcu1" | head -n 1)
		max_refresh_fps=${max_refresh_fps:-120}
		if [[ ! $prop_brand == "MTK" || $device == "chopin" || $device == "corot" || $device == "rothko" ]]; then
			if [[ $max_refresh_fps != "120" ]] && ! grep -q "<item>${max_refresh_fps}</item>" "$supcu1"; then
				log $variant "Add ${max_refresh_fps}Hz"
				sudo sed -i "/^.*<item>120<\\/item>.*/i \\\t<item>${max_refresh_fps}</item>" "$supcu1"
			fi

			if ! grep -q "<item>90</item>" "$supcu1"; then
				log $variant "Add 90Hz"
				sudo sed -i '/^.*<item>120<\/item>.*/a \\t<item>90</item>' "$supcu1"
			fi

		fi

		sudo sed -i '/<integer name="support_max_fps">[0-9]\+<\/integer>/d' "$supcu1"

		update_xml_element $supcu1 bool support_smart_fps true
		update_xml_element $supcu1 bool support_video_dfps true
		update_xml_element $supcu1 bool support_paper_eyecare true
		update_xml_element $supcu1 bool support_screen_enhance_engine true
		update_xml_element $supcu1 bool support_SR_for_image_display true
		update_xml_element $supcu1 bool support_AI_display true
		update_xml_element $supcu1 bool aod_support_keycode_goto_dismiss true
		update_xml_element $supcu1 bool support_dc_backlight true
		update_xml_element $supcu1 bool support_low_flicker_backlight true
		update_xml_element $supcu1 bool hide_flicker_backlight false

		#add fhd for diting
		if [[ $(grep "${device^^}" $dir/fastboot/1.5K.list | head -1) ]]; then
			log $variant "Add FullHD"
			sudo sed -i 's#</features>##' $supcu1
			sudo echo $(cat $dir/module/lang/switchfhq.config.ini) >>$supcu1
		fi

		sudo cp $supcu1 $supcu
		log_success DeviceFeature "$variant"

	done

}

mixer() {
	mixer=$(sudo find $dir/img_temp -type f -name "*mixer*paths*.xml")
	lst=(
		'VA_DEC0 Volume'
		'VA_DEC1 Volume'
		'VA_DEC2 Volume'
		'VA_DEC3 Volume'
		'WSA_RX0 Digital Volume'
		'WSA_RX1 Digital Volume'
		'RX_RX0 Digital Volume'
		'RX_RX1 Digital Volume'
		'RX_RX2 Digital Volume'
		'TX_DEC4 Volume'
		'TX_DEC5 Volume'
		'TX_DEC6 Volume'
		'TX_DEC Volume'
		'RX3 Digital Volume'
		'RX2 Digital Volume'
		'RX1 Digital Volume'
	)

	if [[ $mixer ]]; then
		for i in $mixer; do
			cp $i $dir/jar_temp
			btf=./img_temp/
			mixer_config1=$dir/jar_temp/$(basename $i)
			echo -ne "${RED}[Mixer]${RESET}${YELLOW}[${i#*$btf}]${RESET}"

			for item in "${lst[@]}"; do
				input_name=$item
				input_value=$1

				sudo sed -E -i "s|<ctl name=\"$input_name\" value=\"[^\"]*\" />|<ctl name=\"$input_name\" value=\"$input_value\" />|g" "$mixer_config1"

			done
			echo -e "${GREEN}[Done]${RESET}"
		done
	fi
}

dolby() {
	cd $dir
	dax_config=$(sudo find "$dir/img_temp/" -type f -name "dax-default.xml")
	if [[ $dax_config ]]; then
		for i in $dax_config; do
			btf="$dir/img_temp/"
			dax_config1=$i
			log_start Dolby "${i#*$btf}"

			sudo xmlstarlet ed -L -u "/device_data/profile[@id='0' and @name='Dynamic' and @group='default']/data/volume-modeler-enable/@value" -v true $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='0' and @name='Dynamic' and @group='default']/data/endpoint_type[@id='speaker']/volmax-boost/@value" -v 64 $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='0' and @name='Dynamic' and @group='default']/data/endpoint_type[@id='speaker']/bass-enhancer-enable/@value" -v true $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='0' and @name='Dynamic' and @group='default']/data/endpoint_type[@id='speaker']/bass-enhancer-enable/@value" -v true $dax_config1

			sudo xmlstarlet ed -L -u "/device_data/profile[@id='1' and @name='Movie' and @group='default']/data/volume-modeler-enable/@value" -v true $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='1' and @name='Movie' and @group='default']/data/endpoint_type[@id='speaker']/volmax-boost/@value" -v 64 $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='1' and @name='Movie' and @group='default']/data/endpoint_type[@id='speaker']/bass-enhancer-enable/@value" -v true $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='1' and @name='Movie' and @group='default']/data/endpoint_type[@id='speaker']/bass-enhancer-enable/@value" -v true $dax_config1

			sudo xmlstarlet ed -L -u "/device_data/profile[@id='2' and @name='Music' and @group='default']/data/volume-modeler-enable/@value" -v true $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='2' and @name='Music' and @group='default']/data/endpoint_type[@id='speaker']/volmax-boost/@value" -v 64 $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='2' and @name='Music' and @group='default']/data/endpoint_type[@id='speaker']/bass-enhancer-enable/@value" -v true $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='2' and @name='Music' and @group='default']/data/endpoint_type[@id='speaker']/bass-enhancer-enable/@value" -v true $dax_config1

			sudo xmlstarlet ed -L -u "/device_data/profile[@id='8' and @name='Voice' and @group='default']/data/volume-modeler-enable/@value" -v true $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='8' and @name='Voice' and @group='default']/data/endpoint_type[@id='speaker']/volmax-boost/@value" -v 64 $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='8' and @name='Voice' and @group='default']/data/endpoint_type[@id='speaker']/bass-enhancer-enable/@value" -v true $dax_config1
			sudo xmlstarlet ed -L -u "/device_data/profile[@id='8' and @name='Voice' and @group='default']/data/endpoint_type[@id='speaker']/bass-enhancer-enable/@value" -v true $dax_config1

			log_done Dolby "${i#*$btf}"
		done
	fi
}

enable_usb_debugging_by_default() {
	local usb_config="mtp,adb"

	log_start USBDebugging "Default: $usb_config"

	# Recovery/debug build: allow a host to connect before SystemUI or the setup
	# wizard is available. Keep ro.secure enabled so the ADB shell is not made
	# root implicitly; only host-key authentication is disabled here.
	setprop ro.adb.secure 0
	setprop ro.debuggable 1

	# Android's AdbService enables Settings.Global.adb_enabled during
	# systemReady() when persist.sys.usb.config contains the adb function.
	# Qualcomm's USB init flow reads persist.vendor.usb.config on boot, so keep
	# both sides in sync to prevent the vendor configuration from disabling ADB.
	setprop persist.sys.usb.config "$usb_config"
	setprop persist.vendor.usb.config "$usb_config"

	log_done USBDebugging "Default: $usb_config (RSA authorization disabled)"
}

build() {
	log_start SystemProperties Build.Prop

	local time timestamp ver prop escaped_prop
	local plat_property_contexts="$dir/img_temp/system/system/etc/selinux/plat_property_contexts"
	time="$(date)"
	timestamp="$(date -u +%s)"

	# Mushrom identity for the generated ROM.
	local rom_name="HyperMushrom"
	[[ "$brand" == "ColorOS" ]] && rom_name="ColourMushrom"
	setprop ro.mushrom.name "$rom_name"
	setprop ro.mushrom.version "$patch"
	[[ -n "${iden:-}" ]] && setprop ro.mushrom.build "$iden"

	# Common props
	setprop ro.hypervs.builder "$builder"
	setprop ro.hypervs.version "$patch"
	setprop ro.product.locale "vi-VN"
	setprop ro.hypervs.build.timestamp "$timestamp"
	# The build part of the package name (OS17.0.0.105.VS081026): Mushrom finds
	# the running build in the server's list with it. HyperOS already carries
	# it in ro.mi.os.version.incremental; ColorOS has no such prop of its own.
	[[ -n "${iden:-}" ]] && setprop ro.hypervs.build "$iden"
	setprop ro.hypervs.region "CN"
	# build_ota_package stamps OTA payloads with the same time: the device
	# compares it with ro.build.date.utc and rejects older payloads.
	if grep -q '^build_timestamp=' "$dir/info"; then
		sed -i "s/^build_timestamp=.*/build_timestamp=$timestamp/" "$dir/info"
	else
		echo "build_timestamp=$timestamp" >>"$dir/info"
	fi

	setprop bhlnk.boolean.true true
	setprop bhlnk.boolean.false false

	setprop ro.opa.eligible_device true

	setprop ro.vendor.build.date.utc "$timestamp"
	setprop ro.vendor.build.date "$time"
	setprop ro.odm_dlkm.build.date.utc "$timestamp"
	setprop ro.odm_dlkm.build.date "$time"
	setprop ro.build.date.utc "$timestamp"
	setprop ro.build.date "$time"

	setprop ro.hypervs.paid true

	case "$brand" in
	ColorOS)
		[[ "$device" =~ ^RMX ]] && setprop ro.colourvs.isRealme true

		exbrand=$(getprop 'ro.oplus.image.system_ext.brand')
		[[ $exbrand == "oppo" ]] && setprop ro.colourvs.isOppo true

		setprop persist.sys.oplus.region "VN"
		setprop persist.sys.remove.region.apk "VN"
		setprop sys.region.switch.country "VN"
		setprop sys.region.switch.first "VN"

		setprop ro.colourvs.area gdpr
		setprop ro.colourvs.brand oppo
		setprop ro.colourvs.type "$isType"
		setprop ro.oplus.audio.dolby.equalizer_support true

		setprop ro.secureboot.lockstate "unlocked"
		setprop ro.vipmode.lockstate "locked"

		setprop setupwizard.feature.enable_quick_start_flow true
		setprop setupwizard.feature.enable_quick_enhanced_biometrics_flow true
		setprop setupwizard.feature.baseline_setupwizard_enabled true
		setprop ro.setupwizard.rotation_locked true
		setprop setupwizard.theme "glif_v3_light"

		setprop ro.oplus.radio.global_regionlock.enabled false
		setprop ro.oplus.radio.checkservice false
		setprop persist.sys.radio.global_regionlock.allcheck false
		setprop ro.oplus.radio.global_regionlock.static_enabled false
		setprop ro.oplus.radio.global_regionlock.dynamic false
		setprop ro.oplus.radio.global_regionlock.log false
		setprop ro.oplus.radio.global_regionlockflag 0

		setprop vs.display.lcd_density $(getprop ro.sf.lcd_density)
		setprop vs.ro.radio.noril $(getprop ro.radio.noril)
		setprop ro.vendor.oplus.market.name $(getprop ro.vendor.oplus.market.enname)
		;;

	HyperOS)
		ver="$(echo "$iden" | sed -E "s/(VS|MUSHROM)[0-9]+/MUSHROM$(date "+%d%m%y")/g")"

		sudo sed -i '$a\' "$dir/img_temp/system_ext/etc/cust_prop_white_keys_list"

		setprop ro.product.mod_device "$device"
		setprop ro.mi.os.version.incremental "$ver"
		setprop ro.product.odm.name "$device"
		setprop ro.hypervs.nerversleep "lcd"
		setprop ro.product.marketname '${ro.product.odm.marketname}'

		setprop ro.miui.allow_app_playbackcapture true
		setprop ro.vendor.audio.videobox.switch true
		setprop ro.vendor.audio.spk.clean true

		setprop debug.config.media.video.frc.support true
		setprop debug.config.media.video.aie.support true
		setprop debug.config.media.video.ais.support true

		setprop persist.sys.powmillet.enable false
		setprop persist.sys.spc.powerkill.newpolicy.enable false
		setprop persist.sys.brightmillet.enable false
		setprop persist.sys.millet.handshake false
		setprop persist.sys.millet.newversion false
		setprop persist.sys.smartpower.display.enable false
		setprop persist.sys.smartpower.intercept.enable false
		setprop persist.sys.greeze.oversea false
		setprop persist.sys.millet.newfun false
		setprop persist.sys.sc_allow_conn false

		setprop persist.vendor.tx.speed.control true
		setprop persist.vendor.accelerate.charge true
		setprop persist.vendor.audio.cinema.support true
		setprop persist.vendor.battery.health.optimise true
		setprop persist.sys.bionic_material_supported true

		setprop ro.miui.has_security_keyboard 0

		setprop persist.dbg.vt_avail_ovr 1
		setprop persist.dbg.wfc_avail_ovr 1
		setprop bhlnk.hypervs.overlay true
		setprop ro.hypervs.mod_device "${device}_global"
		setprop ro.hypervs.device_type "$isType"
		setprop ro.miui.google.csp true
		setprop persist.sys.mi_shadow_supported true

		setprop ro.vendor.audio.screenrecorder.bothrecord 1
		setprop ro.vendor.audio.playbackcapture.screen true
		setprop ro.vendor.audio.bose.effect.enable true
		setprop persist.vendor.audio.supereffect.enable true
		setprop ro.vendor.audio.volume_super_index_add 1
		setprop ro.vendor.audio.dolby.dax.support true

		setprop ro.hypervs.paid true
		setprop ro.com.miui.rsa.search tier3
		setprop ro.com.miui.rsa.feature tier3
		setprop ro.com.miui.rsa tier3

		setprop fw.max_users 30
		setprop fw.show_multiuserui 30
		setprop fw.show_hidden_users 30
		setprop fw.power_user_switcher 30

		setprop ro.vendor.fps.switch.thermal false
		;;

	*)
		log_fail SystemProperties Build.Prop "Unknown brand: ${brand}"
		return 1
		;;
	esac

	log_done SystemProperties Build.Prop

	log_start HiddenAPI hiddenapi-package-whitelist.xml
	sudo find -name "hiddenapi-package-whitelist.xml" \
		-exec sed -i '/<\/config>/i\ \ <hidden-api-whitelisted-app package="com.bhlnk.vsteam" />' {} \;
	log_done HiddenAPI hiddenapi-package-whitelist.xml
}

fsetprop() {
	prop=$1
	value=$2
	ini=0

	sudo sed -i '$a\' "$dir/img_temp/system_ext/etc/cust_prop_white_keys_list"
	sudo sed -i "/$prop/d" $dir/img_temp/system/system/etc/selinux/plat_property_contexts
	sudo sed -i "$ a $prop" $dir/img_temp/system_ext/etc/cust_prop_white_keys_list

	if [[ $value ]]; then
		allprop=$(sudo find $dir/img_temp -type f -name "*.prop")
		for i in $allprop; do
			if [[ ! $i == *"cust"* ]]; then
				if sudo grep -q "^$prop=" $i; then
					sudo sed -i "s@^$prop=.*@$prop=$value@" $i
					ini=1
				fi
			fi
		done

		if [[ $ini -eq 0 ]]; then
			rc=$(sudo find $dir/img_temp/vendor -type f -name build.prop)
			for test in $rc; do
				echo "$prop=$value" | sudo tee -a $test
			done
		fi
	fi

}

# Records a ROM feature Mushrom can check: the list ends up in ro.hypervs.caps,
# and Mushrom locks the matching settings on ROMs that lack it. Call it only
# once the patch behind the feature has applied.
add_cap() {
	grep -qx "$1" "$dir/rom_caps" 2>/dev/null || echo "$1" >>"$dir/rom_caps"
}

setprop() {
	local prop="$1"
	shift
	local value="$*"
	local ini=0

	# Xóa dấu ngoặc kép nếu có
	value="${value//\"/}"

	allprop=$(sudo find "$dir/img_temp" -type f -name "*.prop")

	for i in $allprop; do
		if [[ ! $i == *"cust"* ]]; then
			if sudo grep -q "^$prop=" "$i"; then
				sudo sed -i "s@^$prop=.*@$prop=$value@" "$i"
				ini=1
			fi
		fi
	done

	if [[ $brand == "HyperOS" ]]; then
		if [[ $ini -eq 0 ]]; then
			rc=$(sudo find $dir/img_temp -type f -name init.secureboot.rc)
			for test in $rc; do
				# Update an existing line instead of stacking another one:
				# init keeps the first value of a ro.* property. Only lines in
				# the "on init" block count: the same setprop under a trigger
				# such as "on property:ro.miui.customized_clientid=2" may never
				# run (ro.com.miui.rsa.search stayed unset because of that).
				if sudo awk -v p="$prop" '/^on /{s=($0 ~ /^on init[[:space:]]*$/)} s && $1=="setprop" && $2==p {f=1} END{exit !f}' "$test"; then
					# Not in jar_temp: CI_LOW_DISK builds delete it before the
					# image build, and setprop still runs after that.
					local rc_tmp
					rc_tmp="$(mktemp "${TMPDIR:-/tmp}"/vs-setprop-rc.XXXXXX)" || return 1
					sudo awk -v p="$prop" -v v="$value" '/^on /{s=($0 ~ /^on init[[:space:]]*$/)} s && $1=="setprop" && $2==p {sub(/setprop .*/, "setprop " p " " v)} {print}' "$test" >"$rc_tmp" &&
						sudo cp "$rc_tmp" "$test"
					rm -f "$rc_tmp"
				else
					sudo sed -i "/^on init/a \    setprop $prop $value" $test
				fi
			done
		fi
	fi

	if [[ $brand == "ColorOS" ]]; then
		if [[ $ini -eq 0 ]]; then
			build_prop="$dir/img_temp/system/system/build.prop"
			if [[ -f "$build_prop" ]]; then
				echo "$prop=$value" | sudo tee -a "$build_prop" >/dev/null
			fi
		fi
	fi
}

setprop_onboot() {
	prop=$1
	value=$2
	ini=0
	allprop=$(sudo find $dir/img_temp -type f -name *.prop)
	for i in $allprop; do
		if [[ ! $i == *"cust"* ]]; then
			if sudo grep -q $prop $i; then
				sudo sed -i "s@.*$prop.*@$1@" $i
				ini=1
			fi
		fi
	done

	if [[ $ini -eq 0 ]]; then
		rc=$(sudo find $dir/img_temp -type f -name init.secureboot.rc)
		for test in $rc; do
			sudo sed -i "/boot_completed=1/a \    setprop $prop $value" $test
		done
	fi
}

getprop() {
	if [[ ! $2 ]]; then
		[[ -d "$dir/img_temp" ]] || return 0
		allprop=$(sudo find $dir/img_temp -type f -name *.prop)
	else
		[[ -d "$dir/img_temp/$2" ]] || return 0
		allprop=$(sudo find $dir/img_temp/$2 -type f -name *.prop)
	fi
	for i in $allprop; do
		value=$(sudo grep $1 $i | grep -v '^$' | awk -F= '{print $2; exit}')
		if [[ $value ]]; then
			if [[ $value == *" "* ]]; then
				echo "\"$value\""
			else
				echo "$value"
			fi
			break
		fi
	done

}

reprop() {
	if [[ ! $3 ]]; then
		allprop=$(sudo find $dir/img_temp -type f -name *.prop)
	else
		allprop=$(sudo find $dir/img_temp/$3 -type f -name *.prop)
	fi

	for i in $allprop; do
		current_value=$(sudo grep "^$1=" $i | awk -F= '{print $2; exit}')

		if [[ $current_value ]]; then
			sudo sed -i "s/^$1=.*/$1=$2/" $i
			echo "Updated $i: $1=$2"
			break
		fi
	done
}

delprop() {
	prop=$1
	ini=0
	allprop=$(sudo find $dir/img_temp -type f -name *.prop)
	for i in $allprop; do
		if [[ ! $i == *"cust"* ]]; then
			if sudo grep -q $prop $i; then
				sudo sed -i "/$prop/d" $i
				ini=1
			fi
		fi
	done
}
