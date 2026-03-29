#!/bin/bash
#
# Overnight Benchmark: Compare Pipelined vs Unpipelined Compressors
#
# This script runs comprehensive benchmarks comparing HLS vs RTL+Compressor
# implementations with different pipelining strategies.
#
# Expected runtime: 8-12 hours for all configurations
#

set -e  # Exit on error

# Get actual username (Docker may not set $USER)
ACTUAL_USER="${USER:-$(whoami)}"
WORK_BASE="/scratch/users/$ACTUAL_USER/finn_temp_files/overnight_benchmark_$(date +%Y%m%d_%H%M%S)"

# Create base directory
mkdir -p "$WORK_BASE"

echo "=========================================="
echo "FINN Overnight Benchmark Suite"
echo "=========================================="
echo "Started: $(date)"
echo "Results will be saved to: $WORK_BASE"
echo ""

# Function to run benchmark with specific pipelining config
run_benchmark() {
    local pipeline_level=$1
    local synth_clk_ns=$2
    local label=$3
    local work_dir="$WORK_BASE/$label"

    echo ""
    echo "=========================================="
    echo "Running: $label"
    echo "Pipeline level: $pipeline_level"
    echo "Synth clock target: $synth_clk_ns ns"
    echo "=========================================="
    echo ""

    # Temporarily modify dotp_finn.py for this run
    sed -i "s/pipeline_every=.*/pipeline_every=$pipeline_level,  # $label/" \
        src/finn/compressor/src/dotp_finn.py

    # Run the benchmark
    python -m finn.compressor.benchmark_hls_vs_compressor \
        --board pynq-z1 \
        --synth-only \
        --timing-search \
        --keep \
        --synth-clk-period-ns "$synth_clk_ns" \
        --work-dir "$work_dir" \
        2>&1 | tee "$work_dir/run.log"

    echo ""
    echo "Completed: $label at $(date)"
    echo "Results: $work_dir/results.csv"
}

# ========================================
# BENCHMARK 1: Unpipelined (baseline)
# ========================================
run_benchmark "None" "5.0" "unpipelined_baseline"

# ========================================
# BENCHMARK 2: Fully pipelined (aggressive)
# ========================================
run_benchmark "1" "1.5" "pipelined_aggressive"

# ========================================
# BENCHMARK 3: Moderate pipelining
# ========================================
run_benchmark "2" "2.5" "pipelined_moderate"

# ========================================
# Consolidate Results
# ========================================
echo ""
echo "=========================================="
echo "Consolidating Results..."
echo "=========================================="

# Create summary CSV with all results
SUMMARY_CSV="$WORK_BASE/SUMMARY.csv"
echo "Creating: $SUMMARY_CSV"

# Header
echo "Pipeline_Strategy,Config,Implementation,LUT,FF,DSP,BRAM,WNS_ns,fmax_MHz,achieved_fmax_MHz,iterations,LUT_delta,fmax_improvement_pct" > "$SUMMARY_CSV"

# Append all results with pipeline strategy label
for strategy in unpipelined_baseline pipelined_aggressive pipelined_moderate; do
    if [ -f "$WORK_BASE/$strategy/results.csv" ]; then
        # Skip header, add strategy column
        tail -n +2 "$WORK_BASE/$strategy/results.csv" | while IFS=, read -r line; do
            echo "$strategy,$line" >> "$SUMMARY_CSV"
        done
    fi
done

echo ""
echo "=========================================="
echo "BENCHMARK COMPLETE!"
echo "=========================================="
echo "Finished: $(date)"
echo ""
echo "Results Summary:"
echo "  - Full results: $WORK_BASE/"
echo "  - Consolidated: $SUMMARY_CSV"
echo ""
echo "Quick stats:"
wc -l "$SUMMARY_CSV"
echo ""
echo "Top results (by fmax):"
head -1 "$SUMMARY_CSV"
tail -n +2 "$SUMMARY_CSV" | sort -t, -k9 -rn | head -5
echo ""
