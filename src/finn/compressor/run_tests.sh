#!/bin/bash

# If asserted, logs are kept.
((${KEEP_LOG:=0}))
# Limit the number of parallel worker processes for simulation.
((${MAX_WORKERS:=12}))
# Constant Absorption Option
ca="$1"

# PYTHONPATH so python -m finn.compressor.src.* resolves
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FINN_SRC="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export PYTHONPATH="$FINN_SRC${PYTHONPATH:+:$PYTHONPATH}"

TESTS=(
	1xu1u1 1xu1s1 1xs1u1 1xs1s1
	7xu1s1
	8xs1u1
	9xu1u1

	1xu2u1 1xu2s1 1xs2u1 1xs2s1
	2xu2s1

	1xu2u2 1xu2s2 1xs2u2 1xs2s2
	2xs2u2

	1xs3u3
	3xs5u4
	3xu5u4
	7xs7s6
)
IFS=$'\n' TESTS=($(sort -r <<<"${TESTS[*]}"))

# Active Workers: PID -> TEST_ID
declare -A workers
# Collected Return Codes: TEST_ID -> EXIT_CODE
declare -A errcodes

# Collect finishing Workers until no more than the passed Limit are active
function collect_workers {
	local  pid test code
	while :; do
		# Collect finished workers
		for pid in "${!workers[@]}"; do
			if ! kill -0 "$pid" 2>/dev/null; then
				test=${workers["$pid"]}
				wait "$pid"
				code=$?
				errcodes["$test"]="$code"
				unset  "workers[$pid]"

				echo "- $test -> $code"
			fi
		done
		# Return when done
		if [ "${#workers[@]}" -le "$1" ]; then return; fi
		# Pause before going for another sweep
		sleep 5
	done
}

# Start the specified test in a forked worker
function start_test {
	echo "+ $1 ..."
	run_test "$1" &
	workers[$!]="$1"
}

function run_test {
	local  sig=$1
	local  log

	if [ "$KEEP_LOG" -gt 0 ]; then log="comp_$sig.log"; else log="/dev/null"; fi
	if ! python3 -m finn.compressor.src.dotp "$sig" "$ca" >"$log" 2>&1; then exit 1; fi

	# Use "^Error: " lines to determine error count
	if [ "$KEEP_LOG" -gt 0 ]; then log=(-log "dotp_$sig.log"); else log=(-nolog); fi
	exit "$(vivado "${log[@]}" -nojournal -mode batch -source "gen/dotp_$sig.tcl" 2>&1 | grep -ic '^Error: ')"
}

# Run all available tests and record encountered error counts
echo -e "Running tests with $MAX_WORKERS parallel workers:\n"
for test in "${TESTS[@]}"; do
	# Wait if two many workers already active
	collect_workers $((MAX_WORKERS - 1))
	start_test "$test"
done
# Wait for all workers to terminate
collect_workers 0
echo

# Print error summary and derive overall exit code
overall=0
echo -e "Summary:\n"
for test in "${TESTS[@]}"; do
	code="${errcodes[$test]}"
	if [ "$code" -eq 0 ]; then  msg=$'\e[92;1mPASS\e[0m'
	else
		if [ "$code" -gt 1 ]; then  msg='s'; else msg=''; fi
		msg="$(printf $'\e[91;1mFAIL\e[0m [%u error%s]' "$code" "$msg")"
		overall=1
	fi
	printf ' %-28s - %s\n' "$msg" "$test"
done
echo

exit "$overall"
