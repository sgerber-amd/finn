#!/bin/bash
#
# Quick Benchmark: Test pipelined compressors on a few configs
#
# Runtime: ~2-3 hours
#

set -e

# Get actual username (Docker may not set $USER)
ACTUAL_USER="${USER:-$(whoami)}"
WORK_DIR="/scratch/users/$ACTUAL_USER/finn_temp_files/quick_pipelined_test_$(date +%Y%m%d_%H%M%S)"

echo "=========================================="
echo "Quick Pipelined Compressor Benchmark"
echo "=========================================="
echo "Work dir: $WORK_DIR"
echo "Started: $(date)"
echo ""

# Create work directory first
mkdir -p "$WORK_DIR"

# Ensure we're using pipelined compressor
sed -i "s/pipeline_every=.*/pipeline_every=1,  # Max pipelining/" \
    src/finn/compressor/src/dotp_finn.py

python -m finn.compressor.benchmark_hls_vs_compressor \
    --board pynq-z1 \
    --synth-only \
    --timing-search \
    --keep \
    --synth-clk-period-ns 1.5 \
    --work-dir "$WORK_DIR" \
    2>&1 | tee "$WORK_DIR/run.log"

echo ""
echo "=========================================="
echo "COMPLETE!"
echo "=========================================="
echo "Finished: $(date)"
echo "Results: $WORK_DIR/results.csv"
echo ""
cat "$WORK_DIR/results.csv"
