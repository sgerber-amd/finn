#!/bin/bash
#
# Run MVU-level add_multi compressor integration tests.
# For each (MH, MW, PE, SIMD, WW, AW, ACCU_WIDTH) configuration:
#   1. Compute lo_width per DSP lane via add_multi_finn.py --mvu
#   2. Generate comp_NuW_dD.sv compressors for each unique (SIMD, lo_width)
#   3. Inject CATCH_COMP entries into a local copy of add_multi.sv
#   4. Expand TB and TCL templates
#   5. Run full mvu_vvu_axi simulation via XSim
#
# This tests the DSP lane path (genSoftVec in mvu.sv) with compressor-
# replaced adder trees, verifying end-to-end MVU correctness.
#
# Prerequisites:
#   - Vivado on PATH
#   - compressor-python source (COMP_SRC_DIR)

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
MVU_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GEN_BASE="$SCRIPT_DIR/gen"

# Compressor source directory and PYTHONPATH
FINN_SRC="$(cd "$MVU_DIR/../../src" && pwd)"
export PYTHONPATH="$FINN_SRC${PYTHONPATH:+:$PYTHONPATH}"

COMP_SRC_DIR="$FINN_SRC/finn/compressor/src"
if [ ! -f "$COMP_SRC_DIR/add_multi_finn.py" ]; then
	echo "ERROR: Cannot find compressor source." >&2
	echo "  Expected at $COMP_SRC_DIR/" >&2
	exit 1
fi
echo "Compressor source: $COMP_SRC_DIR"

# Working directory for Vivado project files
: "${WORK_DIR:=$SCRIPT_DIR}"

# Test configurations: --mh MH --mw MW --pe PE --simd SIMD --ww WW --aw AW --accu_width ACCU [--signed_activations] [--narrow_weights]
#
# These must hit the genSoftVec path in mvu.sv (not genINT8), which requires
# either NUM_LANES > 3 (for VERSION=3) or WW > 8 or AW > 9.
# Also, USE_COMPRESSOR must be 0, so WW >= 4 or AW >= 4.
TESTS=(
	# 4x4 weights/activations, 4 lanes → 3 unique compressors (lo_widths: 8, 7, 16)
	"--mh 16 --mw 16 --pe 4 --simd 8 --ww 4 --aw 4 --accu_width 16"
	# Same with signed activations
	"--mh 16 --mw 16 --pe 4 --simd 8 --ww 4 --aw 4 --accu_width 16 --signed_activations"
	# 4x10 (AW > 9 bypasses genINT8), 2 lanes → 2 unique compressors (lo_widths: 22, 24)
	"--mh  8 --mw 16 --pe 2 --simd 8 --ww 4 --aw 10 --accu_width 24 --signed_activations"
	# 4x4 narrow weights, 4 lanes → 3 unique compressors (lo_widths: 8, 8, 7, 16)
	"--mh 16 --mw 16 --pe 4 --simd 8 --ww 4 --aw 4 --accu_width 16 --narrow_weights"
)

function parse_config {
	local  mh="" mw="" pe="" simd="" ww="" aw="" accu="" signed_act="" signed_flag="" narrow="" narrow_flag=""
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
			--narrow_weights) narrow="_nw"; narrow_flag="--narrow_weights"; shift;;
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
	CFG_NARROW_FLAG="$narrow_flag"
	CFG_NARROW="$([ -n "$narrow_flag" ] && echo 1 || echo 0)"
	CFG_LABEL="mh${mh}_mw${mw}_pe${pe}_simd${simd}_ww${ww}_aw${aw}${signed_act}${narrow}"
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

	tcl="$GEN_BASE/$label/mvu_add_multi_comp_${label}.tcl"
	if [ ! -f "$tcl" ]; then
		echo "ERROR: TCL script not found: $tcl" >&2
		exit 1
	fi

	if [ "$KEEP_LOG" -gt 0 ]; then
		log=(-log "$GEN_BASE/$label/mvu_add_multi_comp_${label}.sim.log")
	else
		log=(-nolog)
	fi
	out="$GEN_BASE/$label/mvu_add_multi_comp_${label}.runner.out"
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

	# Generate compressor cores via add_multi_finn.py --mvu
	# This computes lo_width per DSP lane and generates one comp_NuW_dD.sv
	# per unique (SIMD, lo_width).
	if ! gen_out=$(python3 -m finn.compressor.src.add_multi_finn \
		--mvu --n "$CFG_SIMD" --version 3 \
		--ww "$CFG_WW" --aw "$CFG_AW" --accu_width "$CFG_ACCU" \
		--narrow_weights "$CFG_NARROW" \
		-o "$gen_dir" 2>&1); then
		echo "GENERATION FAILED for $label:" >&2
		echo "$gen_out" >&2
		exit 1
	fi

	# Display generation summary
	echo "$gen_out" | grep -E '(NUM_LANES|LO_WIDTH|Lane|Module|Delay|reuses)' | sed 's/^/    /'

	# Extract all generated module names for CATCH_COMP injection
	# Format from generator: "    Module:    comp_8u7_d0"
	comp_modules=()
	while IFS= read -r line; do
		comp_modules+=("$line")
	done < <(echo "$gen_out" | sed -n 's/^ *Module:[[:space:]]*//p')

	if [ "${#comp_modules[@]}" -eq 0 ]; then
		echo "    No compressors generated (SIMD too small or single lane)."
		echo "    Skipping — behavioral add_multi will be used."
	fi

	# Build CATCH_COMP entries from the module names.
	# Module names follow the pattern comp_NuW_dD (e.g. comp_8u7_d0).
	catch_entries=""
	for mod in "${comp_modules[@]}"; do
		if [[ "$mod" =~ comp_([0-9]+)u([0-9]+)_d([0-9]+) ]]; then
			cn="${BASH_REMATCH[1]}"
			cw="${BASH_REMATCH[2]}"
			cd_val="${BASH_REMATCH[3]}"
			catch_entries="${catch_entries}\t\`CATCH_COMP(${cn},${cw},${cd_val})\n"
		else
			echo "WARNING: Could not parse module name '$mod'" >&2
		fi
	done

	# Create a local copy of add_multi.sv with CATCH_COMP entries injected
	if [ -n "$catch_entries" ]; then
		sed "s|if(0) begin end|if(0) begin end\n${catch_entries}|" \
			"$MVU_DIR/add_multi.sv" > "$gen_dir/add_multi.sv"
	else
		cp "$MVU_DIR/add_multi.sv" "$gen_dir/add_multi.sv"
	fi

	# Expand TB template
	sed -e "s/{mh}/$CFG_MH/g" \
	    -e "s/{mw}/$CFG_MW/g" \
	    -e "s/{pe}/$CFG_PE/g" \
	    -e "s/{simd}/$CFG_SIMD/g" \
	    -e "s/{ww}/$CFG_WW/g" \
	    -e "s/{aw}/$CFG_AW/g" \
	    -e "s/{accu_width}/$CFG_ACCU/g" \
	    -e "s/{signed_act}/$CFG_SIGNED_ACT/g" \
	    -e "s/{narrow}/$CFG_NARROW/g" \
	    -e "s/{label}/$label/g" \
	    "$SCRIPT_DIR/mvu_add_multi_comp_tb_template.sv" > "$gen_dir/mvu_add_multi_comp_${label}_tb.sv"

	# Expand TCL template
	sed -e "s|{label}|$label|g" \
	    -e "s|{mvu_dir}|$MVU_DIR|g" \
	    -e "s|{gen_dir}|$gen_dir|g" \
	    "$SCRIPT_DIR/mvu_add_multi_comp_tb_template.tcl" > "$gen_dir/mvu_add_multi_comp_${label}.tcl"
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
	printf '  %-50s %s\n' "$label:" "$msg"
done
echo
exit "$overall"
