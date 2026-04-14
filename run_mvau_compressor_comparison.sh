#!/bin/bash
#
# Compare MVAU RTL with dotp_comp between Versal and 7-Series targets
# Runs ONE specific test variant for each target
#

set -e

# Create output directory with timestamp to avoid conflicts
OUTPUT_DIR="${FINN_BUILD_DIR:-/tmp}/mvau_comp_comparison_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

echo "================================================================================"
echo "MVAU Compressor Comparison: Versal vs 7-Series"
echo "================================================================================"
echo "Output directory: $OUTPUT_DIR"
echo ""

# Test file
TEST_FILE="tests/fpgadataflow/test_fpgadataflow_mvau.py"

#------------------------------------------------------------------------------
# Test 1: Versal (uses VersalPredAdder, gate absorption enabled)
#------------------------------------------------------------------------------
echo "================================================================================"
echo "Test 1: Versal Target (xcvc1902)"
echo "  Using: test_fpgadataflow_rtl_mvau with UINT4xINT4, non-pumped"
echo "================================================================================"
echo ""

VERSAL_LOG="${OUTPUT_DIR}/versal_test.log"

# Exact filter to select ONE test variant
# Parameters: mh=18, mw=32, pe=1, simd=16, UINT4×INT4, non-pumped
# Test ID format: [pumpedCompute-pumpedMemory-clk_ns-part-idt_wdt-simd-pe-mw-mh]
pytest -vv -s "$TEST_FILE" \
    -k "test_fpgadataflow_rtl_mvau[False-False-1.66-xcvc1902-vsva2197-2MP-e-S-idt_wdt0-16-1-32-18]" \
    2>&1 | tee "$VERSAL_LOG"

VERSAL_EXIT=${PIPESTATUS[0]}
echo ""
echo "Versal test completed with exit code: $VERSAL_EXIT"
echo "Log saved to: $VERSAL_LOG"
echo ""

#------------------------------------------------------------------------------
# Test 2: 7-Series (uses MuxCY counters, gate absorption currently disabled)
#------------------------------------------------------------------------------
echo "================================================================================"
echo "Test 2: 7-Series Target (xc7z020)"
echo "  Using: test_fpgadataflow_rtl_mvau with UINT4xINT4, non-pumped"
echo "================================================================================"
echo ""

SEVEN_LOG="${OUTPUT_DIR}/7series_test.log"

# Exact filter to select ONE test variant
# Parameters: mh=18, mw=32, pe=1, simd=16, UINT4×INT4, non-pumped
# Test ID format: [pumpedCompute-pumpedMemory-clk_ns-part-idt_wdt-simd-pe-mw-mh]
pytest -vv -s "$TEST_FILE" \
    -k "test_fpgadataflow_rtl_mvau[False-False-1.66-xc7z020clg400-1-idt_wdt0-16-1-32-18]" \
    2>&1 | tee "$SEVEN_LOG"

SEVEN_EXIT=${PIPESTATUS[0]}
echo ""
echo "7-Series test completed with exit code: $SEVEN_EXIT"
echo "Log saved to: $SEVEN_LOG"
echo ""

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
echo "================================================================================"
echo "Test Summary"
echo "================================================================================"
echo ""

if [ $VERSAL_EXIT -eq 0 ]; then
    echo "✓ Versal test: PASSED"
else
    echo "✗ Versal test: FAILED (exit code $VERSAL_EXIT)"
fi

if [ $SEVEN_EXIT -eq 0 ]; then
    echo "✓ 7-Series test: PASSED"
else
    echo "✗ 7-Series test: FAILED (exit code $SEVEN_EXIT)"
fi

echo ""
echo "Output directory: $OUTPUT_DIR"
echo "  - versal_test.log"
echo "  - 7series_test.log"
echo ""

# Count how many tests actually ran
VERSAL_COUNT=$(grep -c "PASSED\|FAILED" "$VERSAL_LOG" 2>/dev/null || echo "0")
SEVEN_COUNT=$(grep -c "PASSED\|FAILED" "$SEVEN_LOG" 2>/dev/null || echo "0")

echo "Tests executed: Versal=$VERSAL_COUNT, 7-Series=$SEVEN_COUNT"
echo ""

if [ $VERSAL_EXIT -eq 0 ] && [ $SEVEN_EXIT -eq 0 ]; then
    echo "All tests PASSED"
    exit 0
else
    echo "Some tests FAILED - check logs for details"
    exit 1
fi
