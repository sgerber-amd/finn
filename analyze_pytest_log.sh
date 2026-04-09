#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <pytest-log-file> [--mode dotp|addmulti]"
    echo "Example: $0 8bit_config_long.log --mode addmulti"
    exit 1
fi

LOG_FILE="$1"
MODE="addmulti"

if [[ $# -ge 3 ]]; then
    if [[ "$2" != "--mode" ]]; then
        echo "Error: unknown argument: $2"
        echo "Usage: $0 <pytest-log-file> [--mode dotp|addmulti]"
        exit 1
    fi
    MODE="$3"
fi

if [[ "$MODE" != "dotp" && "$MODE" != "addmulti" ]]; then
    echo "Error: invalid mode '$MODE' (must be dotp or addmulti)"
    exit 1
fi

if [[ ! -f "$LOG_FILE" ]]; then
    echo "Error: file not found: $LOG_FILE"
    exit 1
fi

extract_prev_token_count() {
    local line="$1"
    local word="$2"
    awk -v w="$word" '
        {
            for (i = 1; i <= NF; i++) {
                if ($i == w && i > 1) {
                    gsub(/,/, "", $(i - 1));
                    print $(i - 1);
                    exit;
                }
            }
        }
    ' <<< "$line"
}

COLLECTED_LINE=$(grep -m1 -E "collected [0-9]+ items" "$LOG_FILE" || true)
SUMMARY_LINE=$(grep -E "^=+ .* in .*s =+$" "$LOG_FILE" | tail -n1 || true)

echo "Log file: $LOG_FILE"
echo "Mode    : $MODE"
echo

if [[ -n "$COLLECTED_LINE" ]]; then
    echo "Collection:"
    echo "  $COLLECTED_LINE"

    COLLECTED=$(extract_prev_token_count "$COLLECTED_LINE" "items")
    DESELECTED=$(extract_prev_token_count "$COLLECTED_LINE" "deselected")
    SELECTED=$(extract_prev_token_count "$COLLECTED_LINE" "selected")

    [[ -n "$COLLECTED" ]] && echo "  collected : $COLLECTED"
    [[ -n "$DESELECTED" ]] && echo "  deselected: $DESELECTED"
    [[ -n "$SELECTED" ]] && echo "  selected  : $SELECTED"
    echo
else
    echo "Collection: not found"
    echo
fi

if [[ -n "$SUMMARY_LINE" ]]; then
    echo "Final summary line:"
    echo "  $SUMMARY_LINE"
    echo
fi

# Count per-test status lines (robust even when final summary line is missing).
PASSED_COUNT=$(grep -cE "^tests/.* PASSED$" "$LOG_FILE" || true)
FAILED_COUNT=$(grep -cE "^tests/.* FAILED$" "$LOG_FILE" || true)
SKIPPED_COUNT=$(grep -cE "^tests/.* SKIPPED" "$LOG_FILE" || true)
ERROR_COUNT=$(grep -cE "^tests/.* ERROR$" "$LOG_FILE" || true)
XFAILED_COUNT=$(grep -cE "^tests/.* XFAIL" "$LOG_FILE" || true)
XPASSED_COUNT=$(grep -cE "^tests/.* XPASS" "$LOG_FILE" || true)

# Some pytest formats print standalone status tokens on separate lines.
if (( PASSED_COUNT == 0 )); then
    PASSED_COUNT=$(grep -cE "^PASSED$" "$LOG_FILE" || true)
fi
if (( FAILED_COUNT == 0 )); then
    FAILED_COUNT=$(grep -cE "^FAILED$" "$LOG_FILE" || true)
fi
if (( SKIPPED_COUNT == 0 )); then
    SKIPPED_COUNT=$(grep -cE "^SKIPPED( .*)?$" "$LOG_FILE" || true)
fi
if (( ERROR_COUNT == 0 )); then
    ERROR_COUNT=$(grep -cE "^ERROR$" "$LOG_FILE" || true)
fi
if (( XFAILED_COUNT == 0 )); then
    XFAILED_COUNT=$(grep -cE "^XFAIL(ED)?( .*)?$" "$LOG_FILE" || true)
fi
if (( XPASSED_COUNT == 0 )); then
    XPASSED_COUNT=$(grep -cE "^XPASS(ED)?( .*)?$" "$LOG_FILE" || true)
fi

echo "Per-test status counts (from test lines):"
echo "  passed : $PASSED_COUNT"
echo "  failed : $FAILED_COUNT"
echo "  skipped: $SKIPPED_COUNT"
echo "  error  : $ERROR_COUNT"
echo "  xfailed: $XFAILED_COUNT"
echo "  xpassed: $XPASSED_COUNT"
echo

if (( FAILED_COUNT > 0 || ERROR_COUNT > 0 )); then
    echo "Failed/Error tests:"
    grep -E "^tests/.* (FAILED|ERROR)$" "$LOG_FILE" || true
    echo
fi

USE0_COUNT=$(grep -cE "USE_COMPRESSOR=1'b0|USE_COMPRESSOR=32'b0+" "$LOG_FILE" || true)
USE1_COUNT=$(grep -cE "USE_COMPRESSOR=1'b1|USE_COMPRESSOR=32'b0*1" "$LOG_FILE" || true)

# Build run-scoped file list from paths referenced in this specific pytest log.
RUN_FILE_LIST=$(mktemp)
cleanup() { rm -f "$RUN_FILE_LIST"; }
trap cleanup EXIT

grep -oE '/scratch/users/[^ ]*code_gen_ipgen_MVAU_rtl_0_[^/ ]+' "$LOG_FILE" | sort -u | while read -r d; do
    [[ -f "$d/add_multi.sv" ]] && echo "$d/add_multi.sv"
    [[ -f "$d/MVAU_rtl_0_wrapper.v" ]] && echo "$d/MVAU_rtl_0_wrapper.v"
    ls "$d"/comp_*.sv 2>/dev/null || true
done >> "$RUN_FILE_LIST"

grep -oE '/scratch/users/[^ ]*vivado_stitch_proj_[^/ ]+' "$LOG_FILE" | sort -u | while read -r d; do
    [[ -f "$d/vivado.log" ]] && echo "$d/vivado.log"
done >> "$RUN_FILE_LIST"

sort -u -o "$RUN_FILE_LIST" "$RUN_FILE_LIST"
RUN_FILE_COUNT=$(wc -l < "$RUN_FILE_LIST" | tr -d ' ')

if (( RUN_FILE_COUNT > 0 )); then
    COMP_TREE_COUNT=$( (xargs -r grep -hE "\[ADD_MULTI_PATH\] COMP|Building add_multi\(.*as COMP\." < "$RUN_FILE_LIST" || true) | wc -l | tr -d ' ')
    GEN_TREE_COUNT=$( (xargs -r grep -hE "\[ADD_MULTI_PATH\] TREE|Building add_multi\(.*as TREE\." < "$RUN_FILE_LIST" || true) | wc -l | tr -d ' ')
else
    COMP_TREE_COUNT=0
    GEN_TREE_COUNT=0
fi

echo "Compressor usage signals:"
echo "  USE_COMPRESSOR=1 hits: $USE1_COUNT"
echo "  USE_COMPRESSOR=0 hits: $USE0_COUNT"
echo "  run-scoped files scanned: $RUN_FILE_COUNT"
echo "  add_multi as COMP   : $COMP_TREE_COUNT"
echo "  add_multi as TREE   : $GEN_TREE_COUNT"
echo

# Elaboration proof: check rtlsim dirs for compiled compressor modules (.sdb),
# DSP modules, and HLS modules. The rtlsim.prj links back to our codegen dirs.
CODEGEN_DIRS=$(grep -oE '/[^ ]*code_gen_ipgen_MVAU_rtl_0_[^/ ]+' "$LOG_FILE" | sort -u)
CODEGEN_COUNT=$(echo "$CODEGEN_DIRS" | grep -c . || true)
TEMP_BASE=$(echo "$CODEGEN_DIRS" | head -1 | sed 's|/code_gen_ipgen_.*||')

# Also check for HLS MVAU (should NOT be present if RTL is used)
HLS_CODEGEN_DIRS=$(grep -oE '/[^ ]*code_gen_ipgen_MVAU_hls_0_[^/ ]+' "$LOG_FILE" | sort -u)
HLS_CODEGEN_COUNT=$(echo "$HLS_CODEGEN_DIRS" | grep -c . || true)

RTLSIM_WITH_COMP=0
RTLSIM_WITHOUT_COMP=0
RTLSIM_WITH_DSP_MODULE=0
RTLSIM_WITH_HLS=0
RTLSIM_TOTAL=0

if [[ -n "$TEMP_BASE" && -d "$TEMP_BASE" ]]; then
    for cg in $CODEGEN_DIRS; do
        cg_hash=$(basename "$cg")
        # Find the rtlsim dir whose rtlsim.prj references this codegen dir
        prj=$(grep -rl "$cg_hash" "$TEMP_BASE"/rtlsim_MVAU_rtl_0_*/rtlsim.prj 2>/dev/null | head -1)
        if [[ -z "$prj" ]]; then continue; fi
        rtlsim_dir=$(dirname "$prj")
        (( RTLSIM_TOTAL++ )) || true

        # Check for compiled compressor .sdb files
        if compgen -G "$rtlsim_dir/xsim.dir/work/comp_*.sdb" >/dev/null 2>&1; then
            (( RTLSIM_WITH_COMP++ )) || true
        else
            (( RTLSIM_WITHOUT_COMP++ )) || true
        fi

        # Check for DSP modules compiled (mvu_vvu_8sx9_dsp58, or mvu.sv with DSP lanes)
        if [[ -f "$rtlsim_dir/xsim.dir/work/mvu_vvu_8sx9_dsp58.sdb" ]] || \
           compgen -G "$rtlsim_dir/xsim.dir/work/mvu@*.sdb" >/dev/null 2>&1; then
            (( RTLSIM_WITH_DSP_MODULE++ )) || true
        fi

        # Check for HLS modules (Matrix_Vector_Activate)
        if compgen -G "$rtlsim_dir/xsim.dir/work/*matrix_vector_activate*.sdb" >/dev/null 2>&1; then
            (( RTLSIM_WITH_HLS++ )) || true
        fi
    done
fi

echo "Elaboration proof (XSim compiled modules):"
echo "  RTL codegen dirs in log : $CODEGEN_COUNT"
echo "  HLS codegen dirs in log : $HLS_CODEGEN_COUNT"
echo "  rtlsim dirs matched     : $RTLSIM_TOTAL"
echo "  with comp_*.sdb (COMP)  : $RTLSIM_WITH_COMP"
echo "  without comp_*.sdb      : $RTLSIM_WITHOUT_COMP"
echo "  with DSP modules        : $RTLSIM_WITH_DSP_MODULE"
echo "  with HLS modules        : $RTLSIM_WITH_HLS"
echo

echo "Mode-specific validation:"
echo ""

# Check for HLS contamination (should NEVER be present)
if (( HLS_CODEGEN_COUNT > 0 )); then
    echo "  ❌ FAIL: HLS codegen dirs found - RTL test contaminated with HLS!"
    echo "          Found $HLS_CODEGEN_COUNT HLS directories"
elif (( RTLSIM_WITH_HLS > 0 )); then
    echo "  ❌ FAIL: HLS modules compiled in $RTLSIM_WITH_HLS simulation(s)"
    echo "          Expected RTL-only implementation!"
else
    echo "  ✓ PASS: No HLS contamination (RTL-only as expected)"
fi
echo ""

if [[ "$MODE" == "dotp" ]]; then
    echo "dotp_comp mode checks (compressors replace DSPs entirely):"
    echo ""

    # Check 1: USE_COMPRESSOR signal
    if (( USE1_COUNT > 0 )); then
        echo "  ✓ PASS: USE_COMPRESSOR=1 detected ($USE1_COUNT instances)"
    elif (( USE0_COUNT > 0 )); then
        echo "  ❌ FAIL: USE_COMPRESSOR=0 found - dotp_comp path NOT active"
        echo "          Expected USE_COMPRESSOR=1 for WW<=4 AND AW<=4"
    else
        echo "  ⚠ WARN: No USE_COMPRESSOR signal found in log"
    fi

    # Check 2: Compressor modules present
    if (( RTLSIM_WITH_COMP > 0 )); then
        echo "  ✓ PASS: comp_*.sdb found in $RTLSIM_WITH_COMP simulation(s)"
        echo "          Compressor dot-product modules successfully compiled"
    elif (( RTLSIM_TOTAL > 0 )); then
        echo "  ❌ FAIL: No comp_*.sdb found - compressors not compiled!"
    else
        echo "  ⚠ WARN: No rtlsim dirs matched (stale/missing artifacts)"
    fi

    # Check 3: NO DSP modules (critical!)
    if (( RTLSIM_WITH_DSP_MODULE > 0 )); then
        echo "  ❌ FAIL: DSP modules found in $RTLSIM_WITH_DSP_MODULE simulation(s)"
        echo "          dotp_comp should use ZERO DSPs - found DSP modules instead!"
        echo "          This indicates compressor path failed to activate properly."
    else
        echo "  ✓ PASS: No DSP modules compiled (compressor-only as expected)"
    fi

else
    echo "add_multi mode checks (DSP multiply + compressor lane reduction):"
    echo ""

    # Check 1: USE_COMPRESSOR should be 0
    if (( USE0_COUNT > 0 )); then
        echo "  ✓ PASS: USE_COMPRESSOR=0 detected ($USE0_COUNT instances)"
        echo "          DSP path active as expected"
    elif (( USE1_COUNT > 0 )); then
        echo "  ❌ FAIL: USE_COMPRESSOR=1 found - switched to dotp_comp instead!"
        echo "          Expected DSP path for WW>4 OR AW>4"
    else
        echo "  ⚠ WARN: No USE_COMPRESSOR signal found in log"
    fi

    # Check 2: DSP modules must be present
    if (( RTLSIM_WITH_DSP_MODULE > 0 )); then
        echo "  ✓ PASS: DSP modules found in $RTLSIM_WITH_DSP_MODULE simulation(s)"
        echo "          Using DSPs for multiply operations as expected"
    else
        echo "  ❌ FAIL: No DSP modules found - DSP path not working!"
    fi

    # Check 3: add_multi compressor usage
    if (( COMP_TREE_COUNT > 0 )); then
        echo "  ✓ PASS: add_multi COMP branch used ($COMP_TREE_COUNT instances)"
    elif (( GEN_TREE_COUNT > 0 )); then
        echo "  ⚠ WARN: add_multi TREE fallback used ($GEN_TREE_COUNT instances)"
        echo "          Expected COMP for SIMD>=4, got binary tree instead"
    else
        echo "  ⚠ WARN: No add_multi build lines found in logs"
    fi

    # Check 4: Compressor modules present for lane reduction
    if (( RTLSIM_WITH_COMP > 0 )); then
        echo "  ✓ PASS: comp_*.sdb found in $RTLSIM_WITH_COMP simulation(s)"
        echo "          Compressor lane reduction successfully compiled"
    elif (( RTLSIM_TOTAL > 0 )); then
        echo "  ⚠ WARN: No comp_*.sdb - add_multi may have fallen back to trees"
        echo "          (Could be OK if SIMD<4 or compressor not beneficial)"
    else
        echo "  ⚠ WARN: No rtlsim dirs matched (stale/missing artifacts)"
    fi
fi
echo

echo "Done."