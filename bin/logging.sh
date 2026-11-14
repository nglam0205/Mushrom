#!/bin/bash

log_color() {
	local name="$1"

	if [[ -n "${NO_COLOR:-}" ]]; then
		echo ""
		return 0
	fi

	case "$name" in
		red) echo "${RED:-\e[31m}" ;;
		green) echo "${GREEN:-\e[32m}" ;;
		yellow) echo "${YELLOW:-\e[33m}" ;;
		cyan) echo "${CYAN:-\e[36m}" ;;
		*) echo "" ;;
	esac
}

log_reset() {
	if [[ -n "${NO_COLOR:-}" ]]; then
		echo ""
	else
		echo "${RESET:-\e[0m}"
	fi
}

declare -gA __LOG_SEEN_EVENTS 2>/dev/null || true

log_should_print_event() {
	local level="$1"
	local scope="$2"
	local target="$3"
	local state="$4"
	local message="$5"
	local key

	[[ "${LOG_DEDUP:-true}" == "true" ]] || return 0

	case "$level:$state" in
		error:*|fail:*|warn:*|*:Error|*:Fail|*:Warn|*:LogBegin|*:LogEnd)
			return 0
			;;
	esac

	key="${level}"$'\034'"${scope}"$'\034'"${target}"$'\034'"${state}"$'\034'"${message}"
	if [[ -n "${__LOG_SEEN_EVENTS[$key]+x}" ]]; then
		return 1
	fi

	__LOG_SEEN_EVENTS[$key]=1
	return 0
}

log_event() {
	local level="$1"
	local scope="$2"
	local target="$3"
	local state="$4"
	local message="$5"
	local state_color
	local reset
	local label
	local detail_scope

	log_should_print_event "$level" "$scope" "$target" "$state" "$message" || return 0

	case "$state" in
		Start) label="[START]" ;;
		Done|Success) label="[DONE]" ;;
		Fail|Error) label="[FAIL]" ;;
		Warn) label="[WARN]" ;;
		Skipped|Skip) label="[SKIP]" ;;
		"") 
			case "$level" in
				error|fail) label="[FAIL]" ;;
				warn) label="[WARN]" ;;
				success|done) label="[DONE]" ;;
				*) label="[INFO]" ;;
			esac
			;;
		*) label="[$(printf "%s" "$state" | tr '[:lower:]' '[:upper:]')]" ;;
	esac

	case "$label" in
		"[DONE]") state_color=$(log_color green) ;;
		"[FAIL]"|"[WARN]") state_color=$(log_color red) ;;
		*) state_color=$(log_color yellow) ;;
	esac
	reset=$(log_reset)

	if [[ -z "$target" && -z "$state" ]]; then
		if [[ "$scope" == "  "* ]]; then
			detail_scope="${scope#"  "}"
			printf "%8s%s" "" "$detail_scope"
			[[ -n "$message" ]] && printf ": %s" "$message"
		else
			printf "%b%-7s%b %s" "$state_color" "$label" "$reset" "$scope"
			[[ -n "$message" ]] && printf ": %s" "$message"
		fi
	else
		printf "%b%-7s%b %s" "$state_color" "$label" "$reset" "$scope"
		[[ -n "$target" ]] && printf " - %s" "$target"
		[[ -n "$message" ]] && printf ": %s" "$message"
	fi
	printf "\n"
}

log_start() {
	log_event info "${1:-}" "${2:-}" Start "${3:-}"
}

log_done() {
	log_event success "${1:-}" "${2:-}" Done "${3:-}"
}

log_success() {
	log_event success "${1:-}" "${2:-}" Success "${3:-}"
}

log_warn() {
	log_event warn "${1:-}" "${2:-}" Warn "${3:-}"
}

log_fail() {
	log_event fail "${1:-}" "${2:-}" Fail "${3:-}"
}

log_block() {
	local scope="$1"
	local target="$2"
	local log_file="$3"
	local status="${4:-0}"
	local lock_dir="${LOG_PRINT_LOCK:-${dir:-$(pwd)}/logs/.print_lock}"

	mkdir -p "$(dirname "$lock_dir")"
	while ! mkdir "$lock_dir" 2>/dev/null; do
		sleep 0.1
	done
	trap 'rmdir "$lock_dir" 2>/dev/null' RETURN

	log_event info "$scope" "$target" LogBegin "${log_file#"$dir/"}"
	if [[ -s "$log_file" ]]; then
		cat "$log_file"
	else
		log_event info "$scope" "$target" Log Empty
	fi
	log_event info "$scope" "$target" LogEnd ""

	if (( status != 0 )); then
		log_warn "$scope" "$target" "exit=$status log=${log_file#"$dir/"}"
	else
		log_done "$scope" "$target"
	fi

	rmdir "$lock_dir" 2>/dev/null
	trap - RETURN
}
