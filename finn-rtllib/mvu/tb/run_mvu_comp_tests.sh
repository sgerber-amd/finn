#!/bin/bash
#
# Run MVU compressor integration tests.
# For each (MH, MW, PE, SIMD, WW, AW) configuration:
#   1. Generate comp_<sig>.sv via dotp_finn.py
#   2. Expand dotp_comp template with $COMP_MODULE_NAME$
#   3. Expand TB and TCL templates
#   4. Run full mvu_vvu_axi simulation via XSim
#
# Prerequisites:
#   - Vivado on PATH
#   - compressor-python source (COMP_SRC_DIR)

set -euo pipefail

# If asserted, logs are kept.
: "${KEEP_LOG:=0}"
# Limit the number of parallel worker processes for simulation.
: "${MAX_WORKERS:=12}"

if ! command -v vivado >/dev/null 2>&1; then
	echo "ERROR: vivado not found in PATH." >&2
	echo "  Source Vivado settings first, e.g. settings64.sh." >&2
	exit 1
fi

echo "Vivado: $(command -v vivado)"
echo "Vivado version: $(vivado -version | head -n 1)"
echo "Settings: KEEP_LOG=$KEEP_LOG MAX_WORKERS=$MAX_WORKERS"

# Resolve directories
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MVU_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GEN_BASE="$SCRIPT_DIR/gen"

# Compressor-python source directory
# First check for the in-tree copy (finn/finn-rtllib/mvu/compressor/)
if [ -d "$MVU_DIR/compressor" ]; then
	COMP_SRC_DIR="$MVU_DIR/compressor"
elif [ -d "$MVU_DIR/../../deps/compressor-python/src" ]; then
	COMP_SRC_DIR="$(cd "$MVU_DIR/../../deps/compressor-python/src" && pwd)"
else
	COMP_SRC_DIR="${COMP_SRC_DIR:-}"
fi

if [ -z "$COMP_SRC_DIR" ] || [ ! -f "$COMP_SRC_DIR/dotp_finn.py" ]; then
	echo "ERROR: Cannot find compressor-python source." >&2
	echo "  Expected at $MVU_DIR/compressor/ or set COMP_SRC_DIR." >&2
	exit 1
fi
echo "Compressor source: $COMP_SRC_DIR"

# Working directory for Vivado project files
: "${WORK_DIR:=$SCRIPT_DIR}"

# Test configurations: MH MW PE SIMD WW AW ACCU_WIDTH SIGNED_ACTIVATIONS
# These are the 4 configs from the original test matrix.
TESTS=(
	"--mh 16 --mw  8 --pe 2 --simd 8 --ww 2 --aw 2 --accu_width 16 --signed_activations"
	"--mh 16 --mw 16 --pe 2 --simd 8 --ww 2 --aw 2 --accu_width 16 --signed_activations"
	"--mh 16 --mw  8 --pe 4 --simd 8 --ww 1 --aw 1 --accu_width 16"
	"--mh  8 --mw  8 --pe 2 --simd 4 --ww 3 --aw 3 --accu_width 16 --signed_activations"
)

function parse_config {
	local  mh="" mw="" pe="" simd="" ww="" aw="" accu="" signed_act="" signed_flag=""
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--mh)    mh="$2"; shift 2;;
			--mw)    mw="$2"; shift 2;;
			--pe)    pe="$2"; shift 2;;
			--simd)  simd="$2"; shift 2;;
			--ww)    ww="$2"; shift 2;;
			--aw)    aw="$2"; shift 2;;
			--accu_width) accu="$2"; shift 2;;
			--signed_activations) signed_act="_sa"; signed_flag="--signed_activations"; shift;;
			*) shift;;
		esac
	done
	CFG_MH="$mh"
	CFG_MW="$mw"
	CFG_PE="$pe"
	CFG_SIMD="$simd"
	CFG_WW="$ww"
	CFG_AW="$aw"
	CFG_ACCU="$accu"
	CFG_SIGNED_FLAG="$signed_flag"
	CFG_SIGNED_ACT="$([ -n "$signed_flag" ] && echo 1 || echo 0)"
	CFG_LABEL="mh${mh}_mw${mw}_pe${pe}_simd${simd}_ww${ww}_aw${aw}${signed_act}"
}

# Active Workers: PID -> LABEL
declare -A workers=()
declare -A errcodes=()
LABELS=()

function collect_workers {
	local  pid label code
	while :; do
		for pid in "${!workers[@]}"; do
			if ! kill -0 "$pid" 2>/dev/null; then
				label=${workers["$pid"]}
				wait "$pid"
				code=$?
				errcodes["$label"]="$code"
				unset  "workers[$pid]"
				echo "- $label -> $code"
			fi
		done
		if [ "${#workers[@]}" -le "$1" ]; then return; fi
		sleep 5
	done
}

function start_test {
	local  label="$1"
	echo "+ $label ..."
	run_sim "$label" &
	workers[$!]="$label"
}

function run_sim {
	local  label="$1"
	local  log tcl out vivado_rc err_count tcl_err_count

	tcl="$GEN_BASE/$label/mvu_comp_${label}.tcl"
	if [ ! -f "$tcl" ]; then
		echo "ERROR: TCL script not found: $tcl" >&2
		exit 1
	fi

	if [ "$KEEP_LOG" -gt 0 ]; then
		log=(-log "$GEN_BASE/$label/mvu_comp_${label}.sim.log")
	else
		log=(-nolog)
	fi
	out="$GEN_BASE/$label/mvu_comp_${label}.runner.out"
	mkdir -p "$WORK_DIR"
	if ! (cd "$WORK_DIR" && vivado "${log[@]}" -nojournal -mode batch -source "$tcl" >"$out" 2>&1); then
		vivado_rc=$?
	else
		vivado_rc=0
	fi
	err_count=$(grep -ic '^Error: ' "$out" || true)
	tcl_err_count=$(grep -Eic "can't read \"|invalid command name|no such variable|^ERROR: \[Common" "$out" || true)
	if [ "$vivado_rc" -ne 0 ] || [ "$tcl_err_count" -gt 0 ]; then
		echo "ERROR: Vivado/Tcl failed for $label (vivado_rc=$vivado_rc, error_lines=$err_count, tcl_errors=$tcl_err_count)." >&2
		exit 1
	fi
	exit "$err_count"
}

# Phase 1: Generate compressor cores and expand templates per config
echo -e "Generating configs:\n"
for i in "${!TESTS[@]}"; do
	args="${TESTS[$i]}"
	# shellcheck disable=SC2086
	parse_config $args
	label="$CFG_LABEL"
	LABELS+=("$label")

	gen_dir="$GEN_BASE/$label"
	mkdir -p "$gen_dir"

	echo "  Generating $label ..."

	# Generate compressor core via dotp_finn.py
	# Run from compressor source dir so bare imports resolve correctly.
	# shellcheck disable=SC2086
	gen_out=$(cd "$COMP_SRC_DIR" && python3 dotp_finn.py \
		--simd "$CFG_SIMD" --ww "$CFG_WW" --aw "$CFG_AW" \
		--accu_width "$CFG_ACCU" $CFG_SIGNED_FLAG \
		--dotp-template "$MVU_DIR/dotp_comp_template.sv" \
		--dotp-output-name dotp_comp.sv \
		-o "$gen_dir" 2>&1)
	if [ $? -ne 0 ]; then
		echo "GENERATION FAILED for $label:" >&2
		echo "$gen_out" >&2
		exit 1
	fi

	# Extract module name and pipeline depth from generator output
	comp_name=$(echo "$gen_out" | sed -n 's/^ *Module name:[[:space:]]*//p' | head -n 1)
	comp_depth=$(echo "$gen_out" | sed -n 's/^ *Pipeline depth:[[:space:]]*//p' | head -n 1 | grep -Eo '[0-9]+' || true)
	if [ -z "$comp_name" ]; then
		echo "ERROR: Could not extract comp_name from generator output for $label" >&2
		exit 1
	fi
	if [ -z "$comp_depth" ]; then
		echo "ERROR: Could not extract comp_depth from generator output for $label" >&2
		exit 1
	fi

	echo "    comp_name=$comp_name  comp_depth=$comp_depth"

	# Expand TB template
	sed -e "s/{mh}/$CFG_MH/g" \
	    -e "s/{mw}/$CFG_MW/g" \
	    -e "s/{pe}/$CFG_PE/g" \
	    -e "s/{simd}/$CFG_SIMD/g" \
	    -e "s/{ww}/$CFG_WW/g" \
	    -e "s/{aw}/$CFG_AW/g" \
	    -e "s/{accu_width}/$CFG_ACCU/g" \
	    -e "s/{signed_act}/$CFG_SIGNED_ACT/g" \
	    -e "s/{comp_depth}/$comp_depth/g" \
	    -e "s/{label}/$label/g" \
	    "$SCRIPT_DIR/mvu_comp_tb_template.sv" > "$gen_dir/mvu_comp_${label}_tb.sv"

	# Expand TCL template
	sed -e "s|{label}|$label|g" \
	    -e "s|{mvu_dir}|$MVU_DIR|g" \
	    -e "s|{comp_dir}|$COMP_SRC_DIR|g" \
	    -e "s|{gen_dir}|$gen_dir|g" \
	    "$SCRIPT_DIR/mvu_comp_tb_template.tcl" > "$gen_dir/mvu_comp_${label}.tcl"
done
echo

# Phase 2: Run simulations in parallel
echo -e "Running simulations with $MAX_WORKERS parallel workers:\n"
for label in "${LABELS[@]}"; do
	collect_workers $((MAX_WORKERS - 1))
	start_test "$label"
done
collect_workers 0
echo

# Print summary
overall=0
echo -e "Summary:\n"
for label in "${LABELS[@]}"; do
	code="${errcodes[$label]}"
	if [ "$code" -eq 0 ]; then  msg=$'\e[92;1mPASS\e[0m'
	else
		msg=$'\e[91;1mFAIL\e[0m'" (errors: $code)"
		overall=1
	fi
	echo "  $label: $msg"
done
echo
exit "$overall"
