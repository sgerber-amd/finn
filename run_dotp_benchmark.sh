#!/bin/bash
#
# dotp Benchmark: Test dotp_comp compressor with sensible mode configurations
#
# Mode naming: <counter_type>_<accu_type>
#   lookahead_std     - LOOKAHEAD8 + standard accu (NONSENSICAL: fast compression wasted by slow feedback)
#   lookahead_lowlat  - LOOKAHEAD8 + low-latency accu (SENSIBLE: fast compression + fast feedback)
#   ripple_std        - VersalAtom222 + standard accu (SENSIBLE: LUT-efficient, accepts slow feedback)
#   ripple_lowlat     - VersalAtom222 + low-latency accu (SENSIBLE: LUT-efficient + fast feedback recovery)
#
# This script runs 3 SENSIBLE configs (14 test cases each):
#   lookahead_lowlat - Fast compression + fast feedback (highest Fmax)
#   ripple_std       - LUT-efficient, accepts slower Fmax
#   ripple_lowlat    - LUT-efficient + Fmax recovery (recommended)
#
# Runtime: ~2-3 hours per config (6-9 hours total)
#

set -e

TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "dotp_comp Benchmark (3 sensible RTL configs)"
echo "=========================================="
echo "Started: $(date)"
echo ""

# Define the 3 sensible configs to run (excludes nonsensical lookahead_std)
declare -A CONFIGS
CONFIGS["lookahead_lowlat"]="--low-latency-accu"             # Fast compression + fast feedback
CONFIGS["ripple_std"]="--hw-efficient"                        # LUT-efficient, slow
CONFIGS["ripple_lowlat"]="--hw-efficient --low-latency-accu"  # LUT-efficient + Fmax recovery

for MODE in "ripple_std" "lookahead_lowlat" "ripple_lowlat"; do
    FLAGS="${CONFIGS[$MODE]}"
    WORK_DIR="${FINN_BUILD_DIR:-/tmp}/dotp_${MODE}_${TIMESTAMP}"

    echo "=========================================="
    echo "Mode: $MODE"
    echo "Flags: $FLAGS"
    echo "Work dir: $WORK_DIR"
    echo "=========================================="

    mkdir -p "$WORK_DIR"

    # shellcheck disable=SC2086
    python -m finn.compressor.benchmark_hls_vs_compressor \
        --board vck190 \
        --synth-only \
        --timing-search \
        --keep \
        --synth-clk-period-ns 10.0 \
        --work-dir "$WORK_DIR" \
        $FLAGS \
        2>&1 | tee "$WORK_DIR/run.log"

    echo ""
done

echo "=========================================="
echo "BENCHMARK COMPLETE!"
echo "=========================================="
echo "Finished: $(date)"
echo ""
echo "Results Summary:"
for MODE in "LA8_LL" "222_noLL" "222_LL"; do
    RESULT_DIR="${FINN_BUILD_DIR:-/tmp}/dotp_${MODE}_${TIMESTAMP}"
    echo "  - $MODE: $RESULT_DIR/results.csv"
done
echo ""
