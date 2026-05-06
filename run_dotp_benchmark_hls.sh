#!/bin/bash
#
# Quick Benchmark: Test pipelined compressors on a few configs
#
# Runtime: ~2-3 hours
#

set -e

WORK_DIR="${FINN_BUILD_DIR:-/tmp}/quick_pipelined_test_$(date +%Y%m%d_%H%M%S)"

echo "=========================================="
echo "Quick Pipelined Compressor Benchmark"
echo "=========================================="
echo "Work dir: $WORK_DIR"
echo "Started: $(date)"
echo ""

# Create work directory first
mkdir -p "$WORK_DIR"

python -m finn.compressor.benchmark_hls_vs_compressor_hls \
    --board vck190 \
    --synth-only \
    --timing-search \
    --keep \
    --synth-clk-period-ns 10.0 \
    --work-dir "$WORK_DIR" \
    2>&1 | tee "$WORK_DIR/run.log"

echo ""
echo "=========================================="
echo "BENCHMARK COMPLETE!"
echo "=========================================="
echo "Finished: $(date)"
echo ""
echo "Results Summary:"
echo "  - Full results: $WORK_DIR/"
echo "  - Results CSV: $WORK_DIR/results.csv"
echo ""
echo "Quick stats:"
wc -l "$WORK_DIR/results.csv"
echo ""
echo "Top results (by fmax):"
head -1 "$WORK_DIR/results.csv"
tail -n +2 "$WORK_DIR/results.csv" | sort -t, -k9 -rn | head -5
echo ""

# Copy results to repo root for easy access
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$WORK_DIR/results.csv" "$SCRIPT_DIR/benchmark_quick_results.csv"
echo "Results also copied to: $SCRIPT_DIR/benchmark_quick_results.csv"
echo ""
