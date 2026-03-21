#!/bin/bash
#
# Run add_multi compressor integration tests.
# For each (N, ARG_WIDTH) configuration:
#   1. Generate comp_NuW_dD.sv via add_multi_finn.py
#   2. Inject CATCH_COMP entry into a local copy of add_multi.sv
#   3. Expand TB and TCL templates
#   4. Run XSim via Vivado
#
# Prerequisites:
#   - Vivado on PATH
#   - FINN MVU sources (for mvu_pkg.sv)

# If asserted, logs are kept.
((${KEEP_LOG:=0}))
# Limit the number of parallel worker processes for simulation.
((${MAX_WORKERS:=12}))

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
SRC_DIR="$SCRIPT_DIR/src"
HDL_DIR="$SCRIPT_DIR/hdl"
GEN_BASE="$SCRIPT_DIR/gen"
FINN_SRC="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export PYTHONPATH="$FINN_SRC${PYTHONPATH:+:$PYTHONPATH}"

# FINN MVU directory (for mvu_pkg.sv, add_multi.sv)
FINN_MVU="${FINN_MVU:-$(cd "$SCRIPT_DIR/../../../../finn-rtllib/mvu" && pwd)}"
if [ ! -f "$FINN_MVU/mvu_pkg.sv" ]; then
	echo "ERROR: FINN MVU not found at $FINN_MVU" >&2
	echo "  Set FINN_MVU to point to finn/finn-rtllib/mvu/." >&2
	exit 1
fi

# Test configurations:  --n N --arg_width W [-p pipeline_every]
#
# These cover production-relevant (N, ARG_WIDTH) values from mvu.sv:
#   N = SIMD (typical: 4, 8, 16, 32)
#   ARG_WIDTH = lo_width(i) (typical: 3-17 bits, depends on DSP version/lane)
TESTS=(
	# Small configs (fast to generate and simulate)
	"--n 8  --arg_width 4"
	"--n 8  --arg_width 4  -p 2"
	"--n 16 --arg_width 3"
	"--n 16 --arg_width 6  -p 2"

	# Medium configs matching common FINN parameters
	"--n 32 --arg_width 6  -p 2"
	"--n 32 --arg_width 16 -p 2"

	# Large configs
	"--n 47 --arg_width 5  -p 2"
	"--n 56 --arg_width 8  -p 2"
)

function parse_config {
	local  n="" w="" p="" p_flag=""
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--n)         n="$2"; shift 2;;
			--arg_width) w="$2"; shift 2;;
			-p)          p="$2"; p_flag="-p $2"; shift 2;;
			*)           shift;;
		esac
	done
	CFG_N="$n"
	CFG_W="$w"
	CFG_P_FLAG="$p_flag"
	CFG_LABEL="n${n}_w${w}"
	if [ -n "$p" ]; then CFG_LABEL="${CFG_LABEL}_p${p}"; fi
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

	tcl="$GEN_BASE/$label/add_multi_comp_${label}.tcl"
	if [ ! -f "$tcl" ]; then
		echo "ERROR: TCL script not found: $tcl" >&2
		exit 1
	fi

	if [ "$KEEP_LOG" -gt 0 ]; then log=(-log "$GEN_BASE/$label/add_multi_comp_${label}.sim.log"); else log=(-nolog); fi
	out="$GEN_BASE/$label/add_multi_comp_${label}.runner.out"
	if ! vivado "${log[@]}" -nojournal -mode batch -source "$tcl" >"$out" 2>&1; then
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

# Phase 1: Generate compressor cores and expand templates
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

	# Generate compressor core
	# shellcheck disable=SC2086
	if ! gen_out=$(python3 -m finn.compressor.src.add_multi_finn \
		--n "$CFG_N" --arg_width "$CFG_W" $CFG_P_FLAG \
		-o "$gen_dir" 2>&1); then
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

	# For DEPTH parameter in TB: use max(comp_depth, 1) so there's at least
	# one pipeline stage for meaningful testing.  If comp_depth is 0 (purely
	# combinational), use DEPTH=0 and the CATCH_COMP macro delay chain handles it.
	tb_depth="$comp_depth"

	# Create a local copy of add_multi.sv with CATCH_COMP entry injected.
	# The macro is appended after the "if(0) begin end" anchor so that its
	# "else if(...)" clause chains into the generate-if cascade.
	sed 's|if(0) begin end|if(0) begin end\n\t`CATCH_COMP('"$CFG_N,$CFG_W,$comp_depth"')|' \
		"$FINN_MVU/add_multi.sv" > "$gen_dir/add_multi.sv"

	# Expand TB template
	sed -e "s/{n}/$CFG_N/g" \
	    -e "s/{arg_width}/$CFG_W/g" \
	    -e "s/{depth}/$tb_depth/g" \
	    -e "s/{label}/$label/g" \
	    "$HDL_DIR/add_multi_comp_tb_template.sv" > "$gen_dir/add_multi_comp_${label}_tb.sv"

	# Expand TCL template
	sed -e "s|{label}|$label|g" \
	    -e "s|{hdl_dir}|$HDL_DIR|g" \
	    -e "s|{mvu_pkg_path}|$FINN_MVU/mvu_pkg.sv|g" \
	    -e "s|{add_multi_path}|$gen_dir/add_multi.sv|g" \
	    -e "s|{gen_dir}|$gen_dir|g" \
	    "$HDL_DIR/add_multi_comp_template.tcl" > "$gen_dir/add_multi_comp_${label}.tcl"
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
