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

# Test Suite 1a: Core compressor - Versal (21 configs)
echo "[1a/6] Core compressor tests - Versal (21 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_tests.sh "" versal; then results[core_versal]="PASS"; else results[core_versal]="FAIL"; fi
echo

# Test Suite 1b: Core compressor - 7-Series (21 configs)
echo "[1b/6] Core compressor tests - 7-Series (21 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_tests.sh "" 7series; then results[core_7series]="PASS"; else results[core_7series]="FAIL"; fi
echo

# Test Suite 2a: dotp_comp integration - Versal (8 configs)
echo "[2a/8] dotp_comp integration tests - Versal (8 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_dotp_comp_tests.sh versal; then results[dotp_comp_versal]="PASS"; else results[dotp_comp_versal]="FAIL"; fi
echo

# Test Suite 2b: dotp_comp integration - 7-Series (8 configs)
echo "[2b/8] dotp_comp integration tests - 7-Series (8 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_dotp_comp_tests.sh 7series; then results[dotp_comp_7series]="PASS"; else results[dotp_comp_7series]="FAIL"; fi
echo

# Test Suite 3a: add_multi standalone - Versal (8 configs)
echo "[3a/8] add_multi compressor tests - Versal (8 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_add_multi_comp_tests.sh versal; then results[add_multi_versal]="PASS"; else results[add_multi_versal]="FAIL"; fi
echo

# Test Suite 3b: add_multi standalone - 7-Series (8 configs)
echo "[3b/8] add_multi compressor tests - 7-Series (8 configs)..."
cd "$REPO_ROOT/src/finn/compressor"
if ./run_add_multi_comp_tests.sh 7series; then results[add_multi_7series]="PASS"; else results[add_multi_7series]="FAIL"; fi
echo

# Test Suite 4: MVU dotp_comp integration (4 configs)
echo "[4/8] MVU dotp_comp integration tests (4 configs)..."
cd "$REPO_ROOT/finn-rtllib/mvu/tb"
if ./run_mvu_comp_tests.sh; then results[mvu_comp]="PASS"; else results[mvu_comp]="FAIL"; fi
echo

# Test Suite 5: MVU add_multi integration (4 configs)
echo "[5/8] MVU add_multi integration tests (4 configs)..."
cd "$REPO_ROOT/finn-rtllib/mvu/tb"
if ./run_mvu_add_multi_comp_tests.sh; then results[mvu_add_multi]="PASS"; else results[mvu_add_multi]="FAIL"; fi
echo

# Summary
echo "======================================================================"
echo "FINAL SUMMARY"
echo "======================================================================"
overall="PASS"
for suite in core_versal core_7series dotp_comp_versal dotp_comp_7series add_multi_versal add_multi_7series mvu_comp mvu_add_multi; do
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
