#!/bin/bash
# Synthesize dotp_comp netlist (no MVU wrapper).
#
# Usage: ./run_dotp_synth.sh --simd 96 --pe 96 --ww 5 --aw 9 [--signed_act] [--part xc7z020clg400-1]

set -e

# Defaults
PART="xc7z020clg400-1"
SIGNED_ACT=0
ACCU_WIDTH=""

# Parse args
while [[ $# -gt 0 ]]; do
	case "$1" in
		--simd) SIMD="$2"; shift 2;;
		--pe) PE="$2"; shift 2;;
		--ww) WW="$2"; shift 2;;
		--aw) AW="$2"; shift 2;;
		--accu_width) ACCU_WIDTH="$2"; shift 2;;
		--signed_act) SIGNED_ACT=1; shift;;
		--part) PART="$2"; shift 2;;
		*) echo "Unknown: $1"; exit 1;;
	esac
done

[ -z "$SIMD" ] || [ -z "$PE" ] || [ -z "$WW" ] || [ -z "$AW" ] && {
	echo "Usage: $0 --simd N --pe N --ww N --aw N [--accu_width N] [--signed_act] [--part PART]"
	exit 1
}

# Auto-compute accu_width if not set (conservative estimate)
if [ -z "$ACCU_WIDTH" ]; then
	ACCU_WIDTH=$((WW + AW + 16))
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FINN_SRC="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export PYTHONPATH="$FINN_SRC${PYTHONPATH:+:$PYTHONPATH}"

LABEL="pe${PE}_simd${SIMD}_w${WW}_a${AW}"
[ "$SIGNED_ACT" -eq 1 ] && LABEL="${LABEL}_sa"
GEN_DIR="$SCRIPT_DIR/gen/$LABEL"
mkdir -p "$GEN_DIR"

echo "Config: PE=$PE SIMD=$SIMD WW=$WW AW=$AW ACCU=$ACCU_WIDTH SIGNED=$SIGNED_ACT"
echo "Part: $PART"
echo "Output: $GEN_DIR"

# Generate compressor
SIGNED_FLAG=""
[ "$SIGNED_ACT" -eq 1 ] && SIGNED_FLAG="--signed_activations"

gen_out=$(python3 -m finn.compressor.src.dotp_finn \
	--simd "$SIMD" --ww "$WW" --aw "$AW" \
	--accu_width "$ACCU_WIDTH" $SIGNED_FLAG \
	--target 7-Series \
	--dotp-template "$SCRIPT_DIR/hdl/dotp_comp_template.sv" \
	--dotp-output-name dotp_comp.sv \
	-o "$GEN_DIR" 2>&1)
echo "$gen_out"

COMP_DEPTH=$(echo "$gen_out" | sed -n 's/^ *Pipeline depth:[[:space:]]*//p' | head -n1 | grep -Eo '[0-9]+')
[ -z "$COMP_DEPTH" ] && COMP_DEPTH=1

# Copy mul_comp_map
cp "$SCRIPT_DIR/hdl/mul_comp_map.sv" "$GEN_DIR/"

# Generate TCL
cat > "$GEN_DIR/synth.tcl" <<EOF
read_verilog -sv \\
	$GEN_DIR/mul_comp_map.sv \\
	$GEN_DIR/dotp_comp.sv \\
	[glob $GEN_DIR/comp_*.sv]

synth_design -top dotp_comp -part $PART -generic [join { \\
	PE=$PE \\
	SIMD=$SIMD \\
	WEIGHT_WIDTH=$WW \\
	ACTIVATION_WIDTH=$AW \\
	ACCU_WIDTH=$ACCU_WIDTH \\
	SIGNED_ACTIVATIONS=$SIGNED_ACT \\
	COMP_PIPELINE_DEPTH=$COMP_DEPTH \\
}]

report_utilization -file $GEN_DIR/util.rpt
report_timing_summary -file $GEN_DIR/timing.rpt
quit
EOF

echo "Running Vivado synthesis..."
vivado -nolog -nojournal -mode batch -source "$GEN_DIR/synth.tcl" | tee "$GEN_DIR/synth.log"

echo ""
echo "=== Results ==="
grep -E "Slice LUTs|DSPs|Timing" "$GEN_DIR/util.rpt" "$GEN_DIR/timing.rpt" 2>/dev/null || true
echo "Reports: $GEN_DIR/*.rpt"
