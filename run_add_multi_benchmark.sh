#!/bin/bash
#
# Quick add_multi Benchmark: Test add_multi compressor optimization
#
# Runtime: ~30-60 minutes (2 configs × 2 variants = 4 builds)
#

set -e

WORK_DIR="${FINN_BUILD_DIR:-/tmp}/add_multi_benchmark_$(date +%Y%m%d_%H%M%S)"

echo "=========================================="
echo "add_multi Compressor Benchmark"
echo "=========================================="
echo "Work dir: $WORK_DIR"
echo "Started: $(date)"
echo ""

# Create work directory first
mkdir -p "$WORK_DIR"

python -m finn.compressor.benchmark_add_multi \
    --board vck190 \
    --synth-only \
    --timing-search \
    --synth-clk-period-ns 10.0 \
    --work-dir "$WORK_DIR" \
    | tee "$WORK_DIR/run.log"

echo ""
echo "=========================================="
echo "BENCHMARK COMPLETE!"
echo "=========================================="
echo "Finished: $(date)"
echo ""
echo "Results Summary:"
echo "  - Full results: $WORK_DIR/"
echo "  - Results CSV: $WORK_DIR/add_multi_results.csv"
echo ""
