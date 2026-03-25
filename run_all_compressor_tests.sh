#!/bin/bash
# Run all compressor test suites and report results

set -e  # Exit on error (disable with set +e if you want to continue on failures)

# Get repo root
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "======================================================================"
echo "Running All Compressor Test Suites"
echo "======================================================================"
echo "Date: $(date)"
echo "Vivado: $(command -v vivado || echo 'NOT FOUND')"
echo "Repo root: $REPO_ROOT"
echo

# Track overall status
declare -A results

# Test Suite 1: Core compressor (21 configs)
echo "[1/5] Core compressor tests (21 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_tests.sh; then results[core]="PASS"; else results[core]="FAIL"; fi
echo

# Test Suite 2: dotp_comp integration (8 configs)
echo "[2/5] dotp_comp integration tests (8 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_dotp_comp_tests.sh; then results[dotp_comp]="PASS"; else results[dotp_comp]="FAIL"; fi
echo

# Test Suite 3: add_multi standalone (8 configs)
echo "[3/5] add_multi compressor tests (8 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_add_multi_comp_tests.sh; then results[add_multi]="PASS"; else results[add_multi]="FAIL"; fi
echo

# Test Suite 4: MVU dotp_comp integration (4 configs)
echo "[4/5] MVU dotp_comp integration tests (4 configs)..."
cd "$REPO_ROOT/finn-rtllib/mvu/tb"
if ./run_mvu_comp_tests.sh; then results[mvu_comp]="PASS"; else results[mvu_comp]="FAIL"; fi
echo

# Test Suite 5: MVU add_multi integration (4 configs)
echo "[5/5] MVU add_multi integration tests (4 configs)..."
cd "$REPO_ROOT/finn-rtllib/mvu/tb"
if ./run_mvu_add_multi_comp_tests.sh; then results[mvu_add_multi]="PASS"; else results[mvu_add_multi]="FAIL"; fi
echo

# Summary
echo "======================================================================"
echo "FINAL SUMMARY"
echo "======================================================================"
overall="PASS"
for suite in core dotp_comp add_multi mvu_comp mvu_add_multi; do
    status="${results[$suite]}"
    if [ "$status" = "PASS" ]; then
        echo "  $suite: ✓ PASS"
    else
        echo "  $suite: ✗ FAIL"
        overall="FAIL"
    fi
done
echo
echo "Overall: $overall"
echo "======================================================================"

[ "$overall" = "PASS" ]
