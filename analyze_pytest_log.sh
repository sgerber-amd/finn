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

# Elaboration proof: check rtlsim dirs for compiled compressor modules (.sdb)
# and dotp modules.  The rtlsim.prj links back to our codegen dirs.
CODEGEN_DIRS=$(grep -oE '/[^ ]*code_gen_ipgen_MVAU_rtl_0_[^/ ]+' "$LOG_FILE" | sort -u)
CODEGEN_COUNT=$(echo "$CODEGEN_DIRS" | grep -c . || true)
TEMP_BASE=$(echo "$CODEGEN_DIRS" | head -1 | sed 's|/code_gen_ipgen_.*||')

RTLSIM_WITH_COMP=0
RTLSIM_WITHOUT_COMP=0
RTLSIM_WITH_DOTP=0
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

        # Check for dotp module compiled
        if [[ -f "$rtlsim_dir/xsim.dir/work/mvu_vvu_8sx9_dsp58.sdb" ]]; then
            (( RTLSIM_WITH_DOTP++ )) || true
        fi
    done
fi

echo "Elaboration proof (XSim compiled modules):"
echo "  codegen dirs in log     : $CODEGEN_COUNT"
echo "  rtlsim dirs matched     : $RTLSIM_TOTAL"
echo "  with comp_*.sdb (COMP)  : $RTLSIM_WITH_COMP"
echo "  without comp_*.sdb      : $RTLSIM_WITHOUT_COMP"
echo "  with dotp .sdb          : $RTLSIM_WITH_DOTP"
echo

echo "Mode-specific check:"
if [[ "$MODE" == "dotp" ]]; then
    if (( USE1_COUNT > 0 )); then
        echo "  OK: dotp path detected (USE_COMPRESSOR=1 present)."
    elif (( USE0_COUNT > 0 )); then
        echo "  ERROR: only USE_COMPRESSOR=0 found (dotp path not active)."
    else
        echo "  WARNING: no USE_COMPRESSOR signal found in this log."
    fi
    if (( RTLSIM_WITH_DOTP > 0 )); then
        echo "  OK: mvu_vvu_8sx9_dsp58 compiled into $RTLSIM_WITH_DOTP simulation(s)."
    else
        echo "  WARNING: no dotp module found in any rtlsim compilation."
    fi
else
    if (( COMP_TREE_COUNT > 0 )); then
        echo "  OK: add_multi compressor tree branch used (as COMP found)."
    elif (( GEN_TREE_COUNT > 0 )); then
        echo "  WARNING: add_multi ran only TREE fallback (no COMP lines)."
    else
        echo "  WARNING: no add_multi COMP/TREE build lines found."
    fi
    if (( RTLSIM_WITH_COMP > 0 )); then
        echo "  OK: comp_*.sdb elaborated in $RTLSIM_WITH_COMP simulation(s) — compressor proven executed."
    elif (( RTLSIM_TOTAL > 0 )); then
        echo "  ERROR: rtlsim dirs found but no comp_*.sdb — compressor not compiled."
    else
        echo "  WARNING: no rtlsim dirs matched (stale or missing build artifacts)."
    fi
fi
echo

echo "Done."