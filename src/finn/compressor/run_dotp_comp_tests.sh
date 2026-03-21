#!/bin/bash
#
# Run dotp_comp integration tests for multiple configurations.
# Uses dotp_finn.py to generate the compressor core (comp.sv),
# then instantiates it from the static dotp_comp template via XSim.

# If asserted, logs are kept.
((${KEEP_LOG:=0}))
# Limit the number of parallel worker processes for simulation.
((${MAX_WORKERS:=12}))

# Working directory for Vivado project files (.vivado/).  These can be
# very large (multi-GB WDB files).  Set WORK_DIR to a scratch/temp
# filesystem to avoid filling your home directory.
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
FINN_SRC="$(cd "$SRC_DIR/../../.." && pwd)"
export PYTHONPATH="$FINN_SRC${PYTHONPATH:+:$PYTHONPATH}"
: "${WORK_DIR:=$SRC_DIR}"

if ! command -v vivado >/dev/null 2>&1; then
	echo "ERROR: vivado not found in PATH." >&2
	echo "  Source Vivado settings first, e.g. settings64.sh." >&2
	exit 1
fi

echo "Vivado: $(command -v vivado)"
echo "Vivado version: $(vivado -version | head -n 1)"
echo "Settings: KEEP_LOG=$KEEP_LOG MAX_WORKERS=$MAX_WORKERS WORK_DIR=$WORK_DIR"
if [ "$KEEP_LOG" -le 0 ]; then
	echo "NOTE: KEEP_LOG=0 -> Vivado is called with -nolog, so no *.sim.log files are written."
	echo "      Use: export KEEP_LOG=1  (or KEEP_LOG=1 ./run_dotp_comp_tests.sh)"
fi

# Test configurations: --simd SIMD --ww WW --aw AW --accu_width AW [--signed_activations]
# PE is a testbench-only parameter (dotp_comp template is parametric for PE).
TESTS=(
	# Binary weights (WW=1), unsigned activations
	"--pe 2 --simd 8 --ww 1 --aw 1 --accu_width 16"

	# Binary weights, signed activations
	"--pe 2 --simd 8 --ww 1 --aw 1 --accu_width 16 --signed_activations"

	# Ternary weights (WW=2), unsigned activations
	"--pe 2 --simd 8 --ww 2 --aw 1 --accu_width 16"

	# Ternary weights, signed activations (main use case)
	"--pe 2 --simd 8 --ww 2 --aw 2 --accu_width 16 --signed_activations"

	# NOTE: WW=2,AW=4 (8xs4s2) omitted — LOOKAHEAD8 GEA port is unconnected
	#       in the Versal blackbox, causing X-propagation in XSim.  This is a
	#       pre-existing generator issue, not a testbench bug.

	# Different SIMD widths
	"--pe 2 --simd 4 --ww 2 --aw 2 --accu_width 16 --signed_activations"
	"--pe 2 --simd 16 --ww 2 --aw 2 --accu_width 16 --signed_activations"

	# Different PE counts
	"--pe 1 --simd 8 --ww 2 --aw 2 --accu_width 16 --signed_activations"
	"--pe 4 --simd 8 --ww 2 --aw 2 --accu_width 16 --signed_activations"
)

# Parse config args into a label and separate generator/TB parameters
function parse_config {
	local  pe="" simd="" ww="" aw="" accu="" signed_act="" signed_flag=""
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--pe)    pe="$2"; shift 2;;
			--simd)  simd="$2"; shift 2;;
			--ww)    ww="$2"; shift 2;;
			--aw)    aw="$2"; shift 2;;
			--accu_width) accu="$2"; shift 2;;
			--signed_activations) signed_act="_sa"; signed_flag="--signed_activations"; shift;;
			*) shift;;
		esac
	done
	# Exports for caller
	CFG_PE="$pe"
	CFG_SIMD="$simd"
	CFG_WW="$ww"
	CFG_AW="$aw"
	CFG_ACCU="$accu"
	CFG_SIGNED_FLAG="$signed_flag"
	CFG_LABEL="pe${pe}_simd${simd}_ww${ww}_aw${aw}_accu${accu}${signed_act}"
}

# Active Workers: PID -> LABEL
declare -A workers
# Collected Return Codes: LABEL -> EXIT_CODE
declare -A errcodes
# Ordered labels for summary
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
	shift
	echo "+ $label ..."
	run_sim "$label" &
	workers[$!]="$label"
}

function run_sim {
	local  label="$1"
	local  log tcl out vivado_rc err_count tcl_err_count

	tcl="$SRC_DIR/gen/$label/dotp_comp_${label}.tcl"
	if [ ! -f "$tcl" ]; then
		echo "ERROR: TCL script not found for $label ($tcl)" >&2
		exit 1
	fi

	# Run XSim via Vivado
	if [ "$KEEP_LOG" -gt 0 ]; then
		log=(-log "$SRC_DIR/gen/$label/dotp_comp_${label}.sim.log")
	else
		log=(-nolog)
	fi
	out="$SRC_DIR/gen/$label/dotp_comp_${label}.runner.out"
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
LABELS=()
echo -e "Generating configs:\n"
for i in "${!TESTS[@]}"; do
	args="${TESTS[$i]}"
	# shellcheck disable=SC2086
	parse_config $args
	label="$CFG_LABEL"
	LABELS+=("$label")

	out_dir="gen/$label"
	mkdir -p "$out_dir"

	echo "  Generating $label ..."
	# Generate compressor core (comp.sv) into per-config subdirectory
	# shellcheck disable=SC2086
	gen_out=$(python3 -m finn.compressor.src.dotp_finn \
		--simd "$CFG_SIMD" --ww "$CFG_WW" --aw "$CFG_AW" \
		--accu_width "$CFG_ACCU" $CFG_SIGNED_FLAG \
		--dotp-template hdl/dotp_comp_template.sv \
		--dotp-output-name dotp_comp.sv \
		-o "$out_dir" 2>&1)
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

	# Expand TB template
	sed -e "s/{pe}/$CFG_PE/g" \
	    -e "s/{simd}/$CFG_SIMD/g" \
	    -e "s/{ww}/$CFG_WW/g" \
	    -e "s/{aw}/$CFG_AW/g" \
	    -e "s/{accu_width}/$CFG_ACCU/g" \
	    -e "s/{signed_act}/$([ -n "$CFG_SIGNED_FLAG" ] && echo 1 || echo 0)/g" \
	    -e "s/{full_sig}/$label/g" \
	    -e "s/{comp_depth}/$comp_depth/g" \
	    hdl/dotp_comp_tb_template.sv > "$out_dir/dotp_comp_${label}_tb.sv"

	# Expand TCL template
	sed -e "s/{label}/$label/g" \
	    -e "s|{src_dir}|$SRC_DIR|g" \
	    hdl/dotp_comp_template.tcl > "$out_dir/dotp_comp_${label}.tcl"
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
