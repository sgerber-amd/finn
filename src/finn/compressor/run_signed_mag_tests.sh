#!/bin/bash
#############################################################################
# Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#
# @brief    Test runner for signed magnitude compressor tests
# @author    Simon Gerber <simon.gerber@amd.com>
#############################################################################

# If asserted, logs are kept.
((${KEEP_LOG:=0}))
# Limit the number of parallel worker processes for simulation.
((${MAX_WORKERS:=12}))

# Target platform (versal or 7series)
target="${1:-versal}"

# PYTHONPATH so python -m finn.compressor.src.* resolves
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FINN_SRC="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$FINN_SRC${PYTHONPATH:+:$PYTHONPATH}"
: "${WORK_DIR:=${FINN_HOST_BUILD_DIR:-/tmp/finn_compressor_tests}}"

if ! command -v vivado >/dev/null 2>&1; then
	echo "ERROR: vivado not found in PATH." >&2
	exit 1
fi

echo "Vivado: $(command -v vivado)"
echo "Settings: KEEP_LOG=$KEEP_LOG MAX_WORKERS=$MAX_WORKERS WORK_DIR=$WORK_DIR"

source "$SCRIPT_DIR/lib/test_common.sh"

# Tests: NxM where N=num_inputs, M=magnitude_width
TESTS=(
	2x4
	2x8
	3x4
	4x4
	4x8
)
IFS=$'\n' TESTS=($(sort -r <<<"${TESTS[*]}"))

function run_test {
	local sig="$1"
	local gen_log comp_log sim_out

	if [ "$KEEP_LOG" -gt 0 ]; then
		gen_log="$SCRIPT_DIR/signed_mag_$sig.log"
		comp_log=(-log "$SCRIPT_DIR/signed_mag_$sig.vivado.log")
	else
		gen_log="/dev/null"
		comp_log=(-nolog)
	fi

	# Phase 1: Generate compressor
	if ! python3 -m finn.compressor.src.signed_mag "$sig" "$target" >"$gen_log" 2>&1; then
		echo "ERROR: Generation failed for $sig" >&2
		return 1
	fi

	# Phase 2: Run simulation
	sim_out="$SCRIPT_DIR/gen/signed_mag_$sig.runner.out"
	mkdir -p "$WORK_DIR"
	(cd "$WORK_DIR" && vivado "${comp_log[@]}" -nojournal -mode batch -source "$SCRIPT_DIR/gen/signed_mag_$sig.tcl" >"$sim_out" 2>&1)

	check_vivado_errors "$sim_out" "$sig"
	return $?
}

# Phase 1: Sequential generation
LABELS=()
mkdir -p "$SCRIPT_DIR/gen"
echo -e "Generating configs:\n"
for test in "${TESTS[@]}"; do
	echo "  $test ..."
	LABELS+=("$test")
	if ! python3 -m finn.compressor.src.signed_mag "$test" "$target" >/dev/null 2>&1; then
		echo "ERROR: Generation failed for $test" >&2
		exit 1
	fi
done
echo

# Phase 2: Parallel simulation
echo -e "Running simulations with $MAX_WORKERS parallel workers:\n"
for label in "${LABELS[@]}"; do
	collect_workers $((MAX_WORKERS - 1))
	start_worker "$label" run_test
done
collect_workers 0
echo

print_summary
exit $?
