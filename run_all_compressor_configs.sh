#!/bin/bash
#
# Automated benchmark runner for all compressor configurations
#
# Runs add_multi benchmark for all combinations of:
#   - USE_LOOKAHEAD8_FOR_CASCADE: True/False
#   - USE_VERSAL_ATOM_222: True/False
#
# Each run creates a uniquely named output directory.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COUNTER_FILE="$SCRIPT_DIR/src/finn/compressor/src/graph/counters/counter_candidates.py"
BASE_WORK_DIR="${FINN_BUILD_DIR:-/tmp}"

# Timestamp for this batch run
BATCH_TS=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "Compressor Configuration Benchmark Suite"
echo "=========================================="
echo "Started: $(date)"
echo "Config file: $COUNTER_FILE"
echo "Output base: $BASE_WORK_DIR"
echo ""

# Function to set USE_LOOKAHEAD8_FOR_CASCADE
set_lookahead8() {
    local value="$1"  # "True" or "False"
    sed -i "s/^USE_LOOKAHEAD8_FOR_CASCADE = .*/USE_LOOKAHEAD8_FOR_CASCADE = ${value}/" "$COUNTER_FILE"
}

# Function to set USE_VERSAL_ATOM_222
set_222() {
    local value="$1"  # "True" or "False"
    sed -i "s/^USE_VERSAL_ATOM_222 = .*/USE_VERSAL_ATOM_222 = ${value}/" "$COUNTER_FILE"
}

# Function to run a single benchmark
run_benchmark() {
    local la8="$1"      # "True" or "False"
    local v222="$2"     # "True" or "False"

    # Create meaningful directory name
    local la8_tag=$([ "$la8" == "True" ] && echo "LA8on" || echo "LA8off")
    local v222_tag=$([ "$v222" == "True" ] && echo "222on" || echo "222off")
    local run_name="${la8_tag}_${v222_tag}_${BATCH_TS}"

    echo ""
    echo "=========================================="
    echo "Config: LOOKAHEAD8=$la8, VERSAL_ATOM_222=$v222"
    echo "Run:    $run_name"
    echo "=========================================="

    # Apply configuration
    set_lookahead8 "$la8"
    set_222 "$v222"

    # Verify configuration
    echo ""
    grep "^USE_LOOKAHEAD8_FOR_CASCADE" "$COUNTER_FILE"
    grep "^USE_VERSAL_ATOM_222" "$COUNTER_FILE"
    echo ""

    # Set work dir name via env var and run the benchmark script
    export FINN_BUILD_DIR="$BASE_WORK_DIR"
    export FINN_BENCHMARK_RUN_NAME="$run_name"

    # Run the existing benchmark script
    "$SCRIPT_DIR/run_add_multi_benchmark.sh"

    echo ""
    echo "Completed: $run_name"
}

# ============================================================================
# Run all 4 configurations
# ============================================================================

echo ""
echo "Will run 4 configurations:"
echo "  1. LOOKAHEAD8=True,  VERSAL_ATOM_222=True   (expected best)"
echo "  2. LOOKAHEAD8=True,  VERSAL_ATOM_222=False"
echo "  3. LOOKAHEAD8=False, VERSAL_ATOM_222=True   (baseline)"
echo "  4. LOOKAHEAD8=False, VERSAL_ATOM_222=False"
echo ""

# Config 1: LOOKAHEAD8=True, VERSAL_ATOM_222=True
# run_benchmark "True" "True"

# Config 2: LOOKAHEAD8=True, VERSAL_ATOM_222=False
# run_benchmark "True" "False"

# Config 3: LOOKAHEAD8=False, VERSAL_ATOM_222=True
# run_benchmark "False" "True"

# Config 4: LOOKAHEAD8=False, VERSAL_ATOM_222=False
run_benchmark "False" "False"

# ============================================================================
# Restore to best config
# ============================================================================
echo ""
echo "=========================================="
echo "Restoring to best configuration"
echo "=========================================="
set_lookahead8 "True"
set_222 "True"
grep "^USE_LOOKAHEAD8" "$COUNTER_FILE"
grep "^USE_VERSAL_ATOM_222" "$COUNTER_FILE"

echo ""
echo "=========================================="
echo "ALL BENCHMARKS COMPLETE!"
echo "=========================================="
echo "Finished: $(date)"
echo ""
echo "Results in: $BASE_WORK_DIR/"
ls -d "$BASE_WORK_DIR"/add_multi_*_"${BATCH_TS}"*/ 2>/dev/null || echo "(check for directories with timestamp $BATCH_TS)"
