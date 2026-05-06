#!/bin/bash
#
# add_multi Benchmark: Test add_multi compressor timing for all 4 mode combinations
#
# 2x2 matrix:
#   --hw-efficient     : VersalAtom222 cascade (LUT-efficient, slower)
#   --low-latency-accu : Pipelined quad + binary accumulator (faster, +1 cycle)
#
# Usage: ./run_add_multi_benchmark.sh [mode] [target]
#   mode: "", "hw", "ll", "hwll", "all" (default: all)
#   target: vck190, pynq-z1, zcu102 (default: vck190)
#
# Runtime: ~30-60 minutes per mode (4 configs × 1 variant = 4 builds per mode)
#

set -e

# Parse arguments
MODE_ARG="${1:-all}"
BOARD="${2:-vck190}"

# Define modes to test
if [[ "$MODE_ARG" == "all" ]]; then
    MODES=("" "hw" "ll" "hwll")
else
    MODES=("$MODE_ARG")
fi

# Build mode flags
function get_mode_flags {
    local mode="$1"
    local flags=""
    [[ "$mode" == *"hw"* ]] && flags="$flags --hw-efficient"
    [[ "$mode" == *"ll"* ]] && flags="$flags --low-latency-accu"
    echo "$flags"
}

# Mode label for directory naming
function get_mode_label {
    local mode="$1"
    if [ -z "$mode" ]; then
        echo "LA8_noLL"
    elif [ "$mode" == "hw" ]; then
        echo "222_noLL"
    elif [ "$mode" == "ll" ]; then
        echo "LA8_LL"
    elif [ "$mode" == "hwll" ]; then
        echo "222_LL"
    else
        echo "$mode"
    fi
}

TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "add_multi Compressor Benchmark (2x2 Matrix)"
echo "=========================================="
echo "Board: $BOARD"
echo "Modes: ${MODES[*]}"
echo "Started: $(date)"
echo ""

# Run each mode
for mode in "${MODES[@]}"; do
    MODE_LABEL=$(get_mode_label "$mode")
    MODE_FLAGS=$(get_mode_flags "$mode")

    # Support custom run name from env (used by run_all_compressor_configs.sh)
    if [ -n "$FINN_BENCHMARK_RUN_NAME" ]; then
        WORK_DIR="${FINN_BUILD_DIR:-/tmp}/add_multi_${MODE_LABEL}_${FINN_BENCHMARK_RUN_NAME}"
    else
        WORK_DIR="${FINN_BUILD_DIR:-/tmp}/add_multi_${MODE_LABEL}_${TIMESTAMP}"
    fi

    echo "=========================================="
    echo "Mode: $MODE_LABEL"
    echo "Flags: ${MODE_FLAGS:-none}"
    echo "Work dir: $WORK_DIR"
    echo "=========================================="

    mkdir -p "$WORK_DIR"

    # shellcheck disable=SC2086
    python -m finn.compressor.benchmark_add_multi \
        --board "$BOARD" \
        --synth-only \
        --timing-search \
        --synth-clk-period-ns 10.0 \
        --work-dir "$WORK_DIR" \
        $MODE_FLAGS \
        | tee "$WORK_DIR/run.log"

    echo ""
done

echo "=========================================="
echo "BENCHMARK COMPLETE!"
echo "=========================================="
echo "Finished: $(date)"
echo ""
echo "Results Summary:"
for mode in "${MODES[@]}"; do
    MODE_LABEL=$(get_mode_label "$mode")
    if [ -n "$FINN_BENCHMARK_RUN_NAME" ]; then
        RESULT_DIR="${FINN_BUILD_DIR:-/tmp}/add_multi_${MODE_LABEL}_${FINN_BENCHMARK_RUN_NAME}"
    else
        RESULT_DIR="${FINN_BUILD_DIR:-/tmp}/add_multi_${MODE_LABEL}_${TIMESTAMP}"
    fi
    echo "  - $MODE_LABEL: $RESULT_DIR/add_multi_results.csv"
done
echo ""
