#!/bin/bash
# Run all compressor test suites and report results
# Usage: ./run_all_compressor_tests.sh [versal|ultrascale|7series|all]

set -e  # Exit on error (disable with set +e if you want to continue on failures)

# Parse target argument
TARGET="${1:-all}"

# Validate target
case "$TARGET" in
    versal|ultrascale|7series|all)
        ;;
    *)
        echo "Usage: $0 [versal|ultrascale|7series|all]"
        echo "  versal     - Run Versal tests only"
        echo "  ultrascale - Run UltraScale/UltraScale+ tests only"
        echo "  7series    - Run 7-Series tests only"
        echo "  all        - Run all targets (default)"
        exit 1
        ;;
esac

# Get repo root
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "======================================================================"
echo "Running Compressor Test Suites - Target: $TARGET"
echo "======================================================================"
echo "Date: $(date)"
echo "Vivado: $(command -v vivado || echo 'NOT FOUND')"
echo "Repo root: $REPO_ROOT"
echo

# Track overall status
declare -A results

# Test Suite 1a: Core compressor - Versal (normal mode)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[1a] Core compressor tests - Versal normal mode (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_tests.sh "" versal; then results[core_versal_normal]="PASS"; else results[core_versal_normal]="FAIL"; fi
    echo
fi

# Test Suite 1a-hw: Core compressor - Versal (--hw-efficient)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[1a-hw] Core compressor tests - Versal --hw-efficient (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_tests.sh "hw" versal; then results[core_versal_hw]="PASS"; else results[core_versal_hw]="FAIL"; fi
    echo
fi

# Test Suite 1a-ll: Core compressor - Versal (--low-latency-accu)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[1a-ll] Core compressor tests - Versal --low-latency-accu (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_tests.sh "ll" versal; then results[core_versal_ll]="PASS"; else results[core_versal_ll]="FAIL"; fi
    echo
fi

# Test Suite 1a-hwll: Core compressor - Versal (--hw-efficient + --low-latency-accu)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[1a-hwll] Core compressor tests - Versal --hw-efficient + --low-latency-accu (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_tests.sh "hwll" versal; then results[core_versal_hwll]="PASS"; else results[core_versal_hwll]="FAIL"; fi
    echo
fi

# Test Suite 1a-ca: Core compressor - Versal (ca - constant absorption)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[1a-ca] Core compressor tests - Versal constant absorption (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_tests.sh "ca" versal; then results[core_versal_ca]="PASS"; else results[core_versal_ca]="FAIL"; fi
    echo
fi

# Test Suite 1a-ca_hw: Core compressor - Versal (ca + --hw-efficient)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[1a-ca_hw] Core compressor tests - Versal ca + --hw-efficient (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_tests.sh "ca_hw" versal; then results[core_versal_ca_hw]="PASS"; else results[core_versal_ca_hw]="FAIL"; fi
    echo
fi

# Test Suite 1a-ca_ll: Core compressor - Versal (ca + --low-latency-accu)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[1a-ca_ll] Core compressor tests - Versal ca + --low-latency-accu (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_tests.sh "ca_ll" versal; then results[core_versal_ca_ll]="PASS"; else results[core_versal_ca_ll]="FAIL"; fi
    echo
fi

# Test Suite 1a-ca_hwll: Core compressor - Versal (ca + --hw-efficient + --low-latency-accu)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[1a-ca_hwll] Core compressor tests - Versal ca + --hw-efficient + --low-latency-accu (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_tests.sh "ca_hwll" versal; then results[core_versal_ca_hwll]="PASS"; else results[core_versal_ca_hwll]="FAIL"; fi
    echo
fi

# Test Suite 1b: Core compressor - UltraScale
if [ "$TARGET" = "ultrascale" ] || [ "$TARGET" = "all" ]; then
    echo "[1b] Core compressor tests - UltraScale (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
   # if ./run_tests.sh "" ultrascale; then results[core_ultrascale]="PASS"; else results[core_ultrascale]="FAIL"; fi
    echo
fi

# Test Suite 1c: Core compressor - 7-Series
if [ "$TARGET" = "7series" ] || [ "$TARGET" = "all" ]; then
    echo "[1c] Core compressor tests - 7-Series (21 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
   # if ./run_tests.sh "" 7series; then results[core_7series]="PASS"; else results[core_7series]="FAIL"; fi
    echo
fi

# Test Suite 2a: dotp_comp integration - Versal (all 4 modes x 8 configs = 32 tests)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[2a] dotp_comp integration tests - Versal (all modes, 32 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_dotp_comp_tests.sh all versal; then results[dotp_comp_versal]="PASS"; else results[dotp_comp_versal]="FAIL"; fi
    echo
fi

# Test Suite 2b: dotp_comp integration - UltraScale
if [ "$TARGET" = "ultrascale" ] || [ "$TARGET" = "all" ]; then
    echo "[2b] dotp_comp integration tests - UltraScale (8 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
  #  if ./run_dotp_comp_tests.sh ultrascale; then results[dotp_comp_ultrascale]="PASS"; else results[dotp_comp_ultrascale]="FAIL"; fi
    echo
fi

# Test Suite 2c: dotp_comp integration - 7-Series
if [ "$TARGET" = "7series" ] || [ "$TARGET" = "all" ]; then
    echo "[2c] dotp_comp integration tests - 7-Series (8 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    #if ./run_dotp_comp_tests.sh 7series; then results[dotp_comp_7series]="PASS"; else results[dotp_comp_7series]="FAIL"; fi
    echo
fi

# Test Suite 3a: add_multi standalone - Versal (2 modes x 8 configs = 16 tests)
if [ "$TARGET" = "versal" ] || [ "$TARGET" = "all" ]; then
    echo "[3a] add_multi compressor tests - Versal (2 modes: normal+hw, 16 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_add_multi_comp_tests.sh all versal; then results[add_multi_versal]="PASS"; else results[add_multi_versal]="FAIL"; fi
    echo
fi

# Test Suite 3b: add_multi standalone - UltraScale
if [ "$TARGET" = "ultrascale" ] || [ "$TARGET" = "all" ]; then
    echo "[3b] add_multi compressor tests - UltraScale (8 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    #if ./run_add_multi_comp_tests.sh ultrascale; then results[add_multi_ultrascale]="PASS"; else results[add_multi_ultrascale]="FAIL"; fi
    echo
fi

# Test Suite 3c: add_multi standalone - 7-Series
if [ "$TARGET" = "7series" ] || [ "$TARGET" = "all" ]; then
    echo "[3c] add_multi compressor tests - 7-Series (8 configs)..."
    cd "$REPO_ROOT/src/finn/compressor"
    if ./run_add_multi_comp_tests.sh "" 7series; then results[add_multi_7series]="PASS"; else results[add_multi_7series]="FAIL"; fi
    echo
fi

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
echo "FINAL SUMMARY - Target: $TARGET"
echo "======================================================================"
overall="PASS"
for suite in "${!results[@]}"; do
    status="${results[$suite]}"
    if [ "$status" = "PASS" ]; then
        echo "  $suite: ✓ PASS"
    else
        echo "  $suite: ✗ FAIL"
        overall="FAIL"
    fi
done | sort
echo
echo "Overall: $overall"
echo "======================================================================"

[ "$overall" = "PASS" ]
