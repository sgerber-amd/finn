#!/bin/bash
# Generate dotp compressor netlist
# Edit parameters below, then run: ./gen_dotp_netlist.sh

#=== EDIT THESE PARAMETERS ===
SIMD=128
PE=128
WW=2
AW=2
ACCU_WIDTH=11
SIGNED_WEIGHTS=0      # 0=unsigned, 1=signed weights
SIGNED_ACT=0          # 0=unsigned, 1=signed activations
TARGET="Versal"       # "7-Series" or "Versal"
PART="xcvc1902-vsvd1760-2MP-e-S"  # Vivado part for synthesis
RUN_SYNTH=0           # 0=netlist only, 1=also run Vivado synthesis
#=============================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# PYTHONPATH: go up to FIX/src
export PYTHONPATH="$(cd "$SCRIPT_DIR/../../.." && pwd):${PYTHONPATH:-}"

# Output directory
LABEL="pe${PE}_simd${SIMD}_w${WW}_a${AW}"
[ "$SIGNED_WEIGHTS" -eq 0 ] && LABEL="${LABEL}_uw"
[ "$SIGNED_ACT" -eq 1 ] && LABEL="${LABEL}_sa"
[ "$TARGET" = "Versal" ] && LABEL="${LABEL}_versal"
LABEL="${LABEL}_updatedTiming"
OUT_DIR="$SCRIPT_DIR/gen/$LABEL"
mkdir -p "$OUT_DIR"

echo "=== Generating dotp netlist ==="
echo "  SIMD=$SIMD, PE=$PE, WW=$WW, AW=$AW, ACCU=$ACCU_WIDTH"
echo "  Target=$TARGET, Signed=$SIGNED_ACT"
echo "  Output: $OUT_DIR"

# Generate compressor + dotp_comp.sv
SIGNED_FLAGS=""
[ "$SIGNED_WEIGHTS" -eq 0 ] && SIGNED_FLAGS="--unsigned_weights"
[ "$SIGNED_ACT" -eq 1 ] && SIGNED_FLAGS="$SIGNED_FLAGS --signed_activations"

python3 -m finn.compressor.src.dotp_finn \
    --simd "$SIMD" --ww "$WW" --aw "$AW" \
    --accu_width "$ACCU_WIDTH" $SIGNED_FLAGS \
    --target "$TARGET" \
    --dotp-template "$SCRIPT_DIR/hdl/dotp_comp_template.sv" \
    --dotp-output-name dotp_comp.sv \
    -o "$OUT_DIR" 2>&1 | tee "$OUT_DIR/gen.log"

# Copy mul_comp_map.sv
cp "$SCRIPT_DIR/hdl/mul_comp_map.sv" "$OUT_DIR/"

echo ""
echo "=== Generated files ==="
ls -la "$OUT_DIR"/*.sv

# Optional synthesis
if [ "$RUN_SYNTH" -eq 1 ]; then
    COMP_DEPTH=$(grep -oP 'Pipeline depth:\s*\K\d+' "$OUT_DIR/gen.log" | head -1)
    [ -z "$COMP_DEPTH" ] && COMP_DEPTH=1

    cat > "$OUT_DIR/synth.tcl" <<EOF
read_verilog -sv \\
    $OUT_DIR/mul_comp_map.sv \\
    $OUT_DIR/dotp_comp.sv \\
    [glob $OUT_DIR/comp_*.sv]

synth_design -top dotp_comp -part $PART -generic [join { \\
    PE=$PE \\
    SIMD=$SIMD \\
    WEIGHT_WIDTH=$WW \\
    ACTIVATION_WIDTH=$AW \\
    ACCU_WIDTH=$ACCU_WIDTH \\
    SIGNED_ACTIVATIONS=$SIGNED_ACT \\
    COMP_PIPELINE_DEPTH=$COMP_DEPTH \\
}]

report_utilization -file $OUT_DIR/util.rpt
report_timing_summary -file $OUT_DIR/timing.rpt
quit
EOF

    echo ""
    echo "=== Running Vivado synthesis ==="
    vivado -nolog -nojournal -mode batch -source "$OUT_DIR/synth.tcl" | tee "$OUT_DIR/synth.log"

    echo ""
    echo "=== Results ==="
    grep -E "Slice LUTs|DSPs" "$OUT_DIR/util.rpt" 2>/dev/null || true
    echo "Reports: $OUT_DIR/*.rpt"
fi

echo ""
echo "Done. Netlist in: $OUT_DIR"
