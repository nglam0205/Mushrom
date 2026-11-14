#!/bin/bash

dir=$(pwd)
#binary setup
fspatch="python3 $dir/bin/fspatch.py"
repS="python3 $dir/bin/strRep.py"
repM="python3 $dir/bin/repM.py"
avbclear="python3 $dir/bin/avbclear.py"
xmlset="python3 $dir/bin/xmlset.py"
erofsup="$dir/bin/extract.erofs"
erofsrp="$dir/bin/mkfs.erofs"
erofs_update="python3 $dir/bin/erofsup.py"

if ! declare -F log_event >/dev/null 2>&1 && [[ -f "$dir/bin/logging.sh" ]]; then
	source "$dir/bin/logging.sh"
fi


# this iss functon for partion utils 

p_sanitize_ascii() {
	# Remove non-ASCII characters from a file (keeps tabs/newlines/spaces)
	# Creates a backup with suffix .bak_nonascii
	local target_file="$1"
	if [[ -f "$target_file" ]]; then
		LC_ALL=C awk '{ gsub(/[^\t\n\r\x20-\x7E]/, ""); print }' "$target_file" > "${target_file}.clean" && mv -f "${target_file}.clean" "$target_file"
	fi
}

p_detect() {

    path="$1"
    if file $1 | grep -q 'data'; then
    	echo "erofs"
    elif file $1 | grep -q 'EROFS'; then
    	echo "erofs"
    else
    	echo false
    fi

}

# detect() writes whatever raw string it was called with (e.g. "local") into
# $dir/info as builder=..., but every cleanup check below only ever compared
# against the literal "LocalBuild". That mismatch meant "local" builds were
# never recognized as local, so p_erofs() deleted img_temp/<partition> right
# after `flash <partition>` even on a LocalBuild-intended run.
is_local_builder() {
	local b="${builder,,}"
	[[ "$b" == "localbuild" || "$b" == "local" ]]
}

# The extract tools used to run with their output discarded, so a failed farm
# job left nothing to diagnose. Print the tool's last real lines (progress
# redraws dropped) plus the free space and memory at the moment of failure.
p_extract_fail_report() {
	local log_file="$1"
	[[ -s "$log_file" ]] && tr '\r' '\n' <"$log_file" |
		sed 's/\x1b\[[0-9;]*m//g' | grep -v -E '^[[:space:]]*$|\[ *[0-9.]+% *\]' | tail -n 15
	df -h "$dir" | tail -n 1
	free -m | sed -n 2p
}

p_erofs() {
	if [[ $1 == "e" ]]; then
		local img="$2"
		local str
		local p

		str=$(basename "$img")
		p="${str%.img}"
		local rc=0 log_file="$dir/logs/extract_erofs_${p}.log"
		mkdir -p "$dir/logs"
		# extract.erofs is single-threaded by default and takes at most 40 threads.
		local threads
		threads=$(nproc)
		(( threads > 40 )) && threads=40
		"$erofsup" -i "$img" -o "$dir/img_temp/" -x -T"$threads" >"$log_file" 2>&1 || rc=$?
		if (( rc != 0 )); then
			log_fail EROFS "$p" "Extract failed (exit $rc, see logs/${log_file##*/})"
			p_extract_fail_report "$log_file"
			return 1
		fi
		rm -f "$log_file"
		log_event info EROFS "$p" "" "Extracted"
		elif [[ $1 == "b" ]]; then
		p=$2
		if [[ -d "$dir/img_temp/${p}" ]]; then
				[[ "${LOG_EROFS_DETAIL:-false}" == "true" ]] && log_start EROFS "Make ${p}"
				# Sanitize file_contexts to avoid mkfs.erofs failure on non-ASCII
				ctx_file="$dir/img_temp/config/${p}_file_contexts"
				if [[ -f "$ctx_file" ]]; then
					if grep -qP "[^\x00-\x7F]" "$ctx_file" 2>/dev/null; then
						p_sanitize_ascii "$ctx_file"
					fi
				fi

				# Match the OPlus EROFS images used by the stock backup.  The
				# 12 KiB physical cluster is important here: with this mkfs.erofs
				# it enables 0padding, compr_cfgs and big_pcluster.  Using only
				# -zlz4hc,9 falls back to the legacy layout and makes system/vendor
				# images substantially larger.
				compress_opt="${EROFS_COMPRESS_OPT:--zlz4hc,1}"
				pcluster_opt="${EROFS_PCLUSTER_OPT:--C12288}"
				fs_options_file="$dir/img_temp/config/${p}_fs_options"
				build_time=""
				build_uuid=""
				if [[ -f "$fs_options_file" ]]; then
					build_time=$(sed -n 's/.*-T[[:space:]=]*\([0-9][0-9]*\).*/\1/p' "$fs_options_file" | head -n 1)
					build_uuid=$(sed -n 's/.*-U[[:space:]=]*\([0-9A-Fa-f-]\{36\}\).*/\1/p' "$fs_options_file" | head -n 1)
				fi
				# OTA builds pass a fresh time: -T also stamps every file's mtime,
				# and PackageManager only re-reads certs of system APKs whose mtime
				# changed, so a dirty-flashed OTA would keep stale signatures.
				[[ "${EROFS_BUILD_TIME:-}" =~ ^[0-9]+$ ]] && build_time="$EROFS_BUILD_TIME"
				[[ "$build_time" =~ ^[0-9]+$ ]] || build_time="$(date -u +%s)"
				[[ "$build_uuid" =~ ^[0-9A-Fa-f-]{36}$ ]] || build_uuid=""
				erofs_metadata_opts=( -T "$build_time" )
				[[ -n "$build_uuid" ]] && erofs_metadata_opts+=( -U "$build_uuid" )
	
				if ! nice -n 10 $erofsrp $compress_opt $pcluster_opt \
					"${erofs_metadata_opts[@]}" \
					--mount-point "/${p}" \
					--fs-config-file "$dir/img_temp/config/${p}_fs_config" \
					--file-contexts "$dir/img_temp/config/${p}_file_contexts" \
					"$dir/$device/firmware-update/${p}.img" \
					"$dir/img_temp/${p}" >/dev/null 2>&1; then
					log_fail EROFS "Make ${p}" "mkfs.erofs failed"
					return 1
				fi

			[[ "${LOG_EROFS_DETAIL:-false}" == "true" ]] && log_done EROFS "Make ${p}"
			if ! is_local_builder; then
				sudo rm -rf "$dir/img_temp/${p}"
			fi
		fi
	fi
}

# Backend modules kept on this machine (the farm: /data/MODULE, set by farm.sh
# as VS_MODULE_DIR). Modules are copied from it and Google Drive is not asked;
# without a store they are synced from Drive.
module_store_has() {
	[[ -n "${VS_MODULE_DIR:-}" && -d "$VS_MODULE_DIR/$1" ]]
}

sync() {
	log_start Sync "$1"

	local remote_path="teamdrive:VS_BackEnd/$1"
	local local_path="$dir/module/update/$1"

	if module_store_has "$1"; then
		mkdir -p "$dir/module/update"
		sudo rm -rf "$local_path"
		if ! cp -a --reflink=auto "$VS_MODULE_DIR/$1" "$local_path"; then
			log_fail Sync "$1" "failed: $VS_MODULE_DIR/$1"
			return 1
		fi
		log_done Sync "$1"
		return 0
	fi

	mkdir -p "$local_path"

	if $rclone check "$remote_path" "$local_path" \
		--size-only \
		--fast-list \
		--checkers 16 >/dev/null 2>&1; then
		log_event warn Sync "$1" Skip "already up to date"
		return 0
	fi

	if ! $rclone sync "$remote_path" "$local_path" \
		--size-only \
		--fast-list \
		--use-server-modtime \
		--checkers 16 \
		--transfers 8; then
		log_fail Sync "$1" "failed: $remote_path"
		return 1
	fi

	log_done Sync "$1"
}

should_prune_update_cache() {
	[[ "${GITHUB_ACTIONS:-false}" == "true" || "${CLEAN_SYNC_CACHE:-false}" == "true" ]]
}






set_att() {
	
	echo -e "\n" >> "$dir/img_temp/config/product_file_contexts"

	se1=$(echo "$1" | sed 's/[+.]/\\&/g')
	if [[ ${se1} ]]; then
		echo "/${se1} u:object_r:system_file:s0" >> "$dir/img_temp/config/product_file_contexts"
	fi   
	#correct fs config
	$fspatch "$dir/img_temp/product" "$dir/img_temp/config/product_fs_config" >/dev/null 2>&1
}

manual_add() {
	efs=$1
	
	if [[ $efs == "true" ]]; then
		if [ -d $dir/module/update/$2 ]; then 
			[[ ! $2 == "temp_"* ]] && log_start AddConfig "$2"
		  	for i in \
			    mi_ext\
			    my_bigball \
			    my_carrier \
			    my_engineering \
			    my_heytap \
			    my_manifest \
			    my_product \
			    my_region \
			    my_stock \
			    odm \
			    product \
			    system \
			    system_dlkm \
			    system_ext \
			    vendor \
			    vendor_dlkm
	     	do
				if [[ -d $dir/img_temp/$i ]]; then
				  	#correct se label
				  	cd $dir/module/update/$2
					if [[ ! $i == "vendor" ]]; then
						contest=system
					else
						contest=vendor
					fi

					echo -e "\n" >> "$dir/img_temp/config/${i}_file_contexts"

					# Modules may ship an explicit _config/<partition>_file_contexts
					# with exact SELinux labels (custom exec domains, etc). Honor
					# those verbatim and skip the generic guess for those paths.
					explicit_fc="$dir/module/update/$2/_config/${i}_file_contexts"
					explicit_paths=""
					if [[ -f $explicit_fc ]]; then
						cat "$explicit_fc" >> "$dir/img_temp/config/${i}_file_contexts"
						echo -e "\n" >> "$dir/img_temp/config/${i}_file_contexts"
						explicit_paths=$(awk '{print $1}' "$explicit_fc" | sed -e 's/\\\([.+]\)/\1/g' -e 's#^/##')
					fi

					if [[ -d $i ]]; then
						for se in $(find "$i"); do
							se1=$(echo "$se" | sed 's/[+.]/\\&/g')
							if [[ ${se1} ]]; then
								if grep -qxF "$se" <<< "$explicit_paths"; then
									continue
								fi
								if [[ ! -d $dir/img_temp/$se1 ]]; then
									if [[ ! -f $dir/img_temp/$se1 ]]; then
									    if [[ $se1 == *"lib"* ]]; then
									        echo "/${se1} u:object_r:${contest}_lib_file:s0" >> "$dir/img_temp/config/${i}_file_contexts"
									    else
									        echo "/${se1} u:object_r:${contest}_file:s0" >> "$dir/img_temp/config/${i}_file_contexts"
									    fi
									fi
								fi
							fi
						done
					fi
		        fi
	     	done

	     	cp -rf $dir/module/update/$2/. $dir/img_temp/
	     	# _config/ carries per-partition overrides only, it is not a partition itself
	     	[[ -d $dir/img_temp/_config ]] && rm -rf "$dir/img_temp/_config"

	     	# apk_paths was scanned before this module landed; index its APKs so
	     	# a later auto_add can replace them by package name.
	     	if [[ -f $dir/apk_paths ]]; then
	     		while IFS= read -r added_apk; do
	     			added_dest="$dir/img_temp/${added_apk#./}"
	     			grep -qF "$added_dest:" "$dir/apk_paths" && continue
	     			added_pkg=$(sudo $aapt2_b dump badging "$added_dest" 2>/dev/null | grep package | head -1 | awk '{print $2}' | sed s/name=//g | sed s/\'//g)
	     			echo "$added_dest:$added_pkg" >> "$dir/apk_paths"
	     		done < <(cd "$dir/module/update/$2" && find . -type f -name "*.apk")
	     	fi

		  	for i in \
			    mi_ext\
			    my_bigball \
			    my_carrier \
			    my_engineering \
			    my_heytap \
			    my_manifest \
			    my_product \
			    my_region \
			    my_stock \
			    odm \
			    product \
			    system \
			    system_dlkm \
			    system_ext \
			    vendor \
			    vendor_dlkm
	     	do
	     		explicit_fs="$dir/module/update/$2/_config/${i}_fs_config"
	     		[[ -f $explicit_fs ]] && cat "$explicit_fs" >> "$dir/img_temp/config/${i}_fs_config"
	     		$fspatch "$dir/img_temp/$i" "$dir/img_temp/config/${i}_fs_config" >/dev/null 2>&1
	     	done

		  
		      [[ ! $2 == "temp_"* ]] && log_done AddConfig "$2"
		  fi 
	else
		typed="$dir/module/update/$2"
  		se_sys=$dir/img_temp/system/system/build.prop
		se_ven=$dir/img_temp/vendor/build.prop

	  	if [[ -d $typed ]]; then
	  		[[ ! $2 == "temp_"* ]] && log_start AddConfig "$2"
	    	cd $typed
	    	if [[ -d vendor ]]; then
		      sudo cp -rf vendor/. $dir/img_temp/vendor/
		      cd vendor
		      crrt=$(find *)
		      for si in $crrt; do
		        permis "vendor" $si $se_ven
		      done
	    	fi

		    for p in 'system/system' product system_ext; do
		      cd $typed
		      if [[ -d $p ]]; then
		        sudo cp -rf $p/. $dir/img_temp/$p/
		        cd $p
		        crrt=$(find *)
		        for si in $crrt; do
		          permis $p $si $se_sys
		        done
		      fi
		    done
			
			    [[ ! $2 == "temp_"* ]] &&  log_done AddConfig "$2"
  		fi

  	fi

  	if [[ $2 == "temp_"* ]]; then
  		sudo rm -rf "$dir/module/update/$2"
  	fi

	if ! is_local_builder && should_prune_update_cache; then
		sudo rm -rf "$dir/module/update/$2"
	fi
 
}


auto_add() {
	local copy_count=0

	if [[ ! -f $dir/apk_paths ]]; then
		log_warn APKIndex apk_paths "missing, scan before auto add: $1"
		apk_scan_w
	fi

	cd $dir/module/update
	if [[ -d $1 ]]; then
		log_start AutoAdd "$1"
		cd $dir/module/update/$1
		btf=$dir/img_temp/
	    for file in $(ls | cat); do
	      app_version=""
	      if [[ "${file##*.}" == "apk" ]]; then
	        app_version=$(sudo $aapt2_b dump badging "$file" 2>/dev/null | sed -n "1s/.*versionName='\([^']*\)'.*/\1/p")
	      fi
	      copy_log="$(basename "$file")"
	      if [[ $app_version ]]; then
	        copy_log="$copy_log (app version: $app_version)"
	      fi
	      if [[ ! "${file##*.}" == "apk" || "$2" == *"$file"* ]]; then
			input=$(get_file_dir $file auto)
			if [[ $input ]]; then
				for i in $input; do
					log_event info AutoAdd "$1" Copy "$copy_log -> ${i#*$btf}"
					sudo cp -rf $file $i
					copy_count=$((copy_count + 1))
				done
			fi
		  else
			package_name=$(sudo $aapt2_b dump badging $file 2>/dev/null | grep package | head -1 | awk '{print $2}' | sed s/name=//g | sed s/\'//g  )
			apk_path1=$(grep ":$package_name\$" $dir/apk_paths | cut -d':' -f1)

			if [ "$apk_path1" ]; then
				for i in $apk_path1; do
					log_event info AutoAdd "$1" Copy "$copy_log -> ${i#*$btf}"

					if [[ "$i" =~ "product/priv-app" ]]; then
					    $dir/bin/fix_permission.sh $file $dir/module/update/temp_perm/product/etc/permissions
					fi
					
					sudo cp -rf $file $i
					copy_count=$((copy_count + 1))
				done
			fi
		  fi 
		    done
			log_done AutoAdd "$1" "$copy_count item(s)"
			
	fi


	if ! is_local_builder && should_prune_update_cache; then
		sudo rm -rf "$dir/module/update/$1"
	fi
	
}


permis() {
	if [[ -e $2 ]]; then
		if [[ -d $2 ]]; then
			sudo chmod 755 "$dir/img_temp/$1/$2"
		else
			sudo chmod 644 "$dir/img_temp/$1/$2"
		fi
		sudo chcon --reference=$3 "$dir/img_temp/$1/$2"
	fi
}

apk_scan() {
	for apk_file in $(sudo find $dir/img_temp -type f -name *.apk) ; do
		package_name=$(sudo aapt2 dump badging $apk_file 2>/dev/null | grep package | head -1 | awk '{print $2}' | sed s/name=//g | sed s/\'//g  )
		if [[ $package_name = $1 ]]; then
			echo $(basename $apk_file)	
		fi
	done

	return 0
}


dat2sys_pkg() {

	exist=$(check_file_exist $1)
	package_name=$(echo $exist | cut -d ':' -f2 | cut -d '=' -f2)
	file_path=$(echo $exist | cut -d ':' -f1 | cut -d '=' -f1)
	file_name=$(basename $file_path)

	if [[ $file_path == *"data-app"* ]]; then
	    dest=$(echo $file_path | sed 's/data-app/app/')
	    
	    sudo mv $(dirname $file_path) $(dirname $(dirname $dest))

	    if [[ -f $dest ]]; then

		    set_att ${dest#*"$dir/img_temp/"}
		    set_att $(dirname ${dest#*"$dir/img_temp/"})
		    echo "====> Data2System $package_name/$file_name: $dest"

	    fi


	fi
}

mv2priv() {
	if [[ $1 ]]; then
		exist=$(check_file_exist "$1")
		if [[ $exist ]]; then
			package_name=$(echo "$exist" | cut -d ':' -f2 | cut -d '=' -f2)
			file_path=$(echo "$exist" | cut -d ':' -f1 | cut -d '=' -f1)
			file_name=$(basename "$file_path")
			dest=""

			if [[ "$file_path" == *"/data-app/"* ]]; then
				dest="${file_path/\/data-app\//\/priv-app\/}"
			elif [[ "$file_path" == *"/app/"* ]]; then
				dest="${file_path/\/app\//\/priv-app\/}"
			elif [[ "$file_path" == *"/priv-app/"* ]]; then
				dest="$file_path"
			fi

			if [[ $dest ]]; then
				if [[ "$file_path" != "$dest" ]]; then
					mkdir -p "$(dirname "$(dirname "$dest")")"
					mv "$(dirname "$file_path")" "$(dirname "$(dirname "$dest")")"
				fi

				if [[ -f "$dest" ]]; then
					perm_dir="$dir/img_temp/product/etc/permissions"
					mkdir -p "$perm_dir"
					env "PATH=$dir/bin:$PATH" "$dir/bin/fix_permission.sh" "$dest" "$perm_dir"

					set_att "${dest#*"$dir/img_temp/"}"
					set_att "$(dirname "${dest#*"$dir/img_temp/"}")"
					set_att "product/etc/permissions"
					set_att "product/etc/permissions/vs.per.$package_name.xml"

					if [[ -f "$dir/apk_paths" ]]; then
						sed -i "s|^$file_path|$dest|g" "$dir/apk_paths"
					fi

					log_success Move2PrivApp "$package_name" "$file_name -> ${dest#"$dir/img_temp/"}"
				fi
			fi
		fi
	fi
}


add_method() {
	for i in $(find -type f -name "$2"); do
		cat "$1" >> "$i"
	done
}
