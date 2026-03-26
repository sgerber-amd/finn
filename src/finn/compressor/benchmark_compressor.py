#!/usr/bin/env python3
#
# Minimal benchmark wrapper for comparing MVAU variants (compressor vs DSP) performance.
# Reuses the FINN build flow to generate estimate reports, then compares results.
#
# Usage (inside Docker):
#   python -m finn.compressor.benchmark_compressor

import argparse
import json
import os
import shutil

from qonnx.core.datatype import DataType
from qonnx.util.basic import gen_finn_dt_tensor

from finn.builder.build_dataflow import build_dataflow_cfg
from finn.builder.build_dataflow_config import (
    DataflowBuildConfig,
    DataflowOutputType,
    ShellFlowType,
    default_build_dataflow_steps,
)

# Taking model creation from tests.
from tests.fpgadataflow.test_fpgadataflow_mvau import make_single_fclayer_modelwrapper


BOARD_CONFIGS = {
    "pynq-z1": {
        "board": "Pynq-Z1",
        "part": "xc7z020clg400-1",
    },
    "ultra96": {
        "board": "Ultra96",
        "part": "xczu3eg-sbva484-1-e",
    },
    "vck190": {
        "board": "VCK190",
        "part": "xcvc1902-vsva2197-2MP-e-S",
    },
}


def run_build(model_path, output_dir, board, fpga_part, no_compressor, synth_only):
    """Run FINN estimate-only build."""
    # Create output_dir if needed
    os.makedirs(output_dir, exist_ok=True)

    # Specialize layers config to force RTL implementation -> many nodes otherwise default to hls.
    specialize_config = {
        "Defaults": {
            "preferred_impl_style": ["rtl", ["MVAU"]],
        }
    }
    specialize_config_path = os.path.join(output_dir, "specialize_layers_config.json")
    with open(specialize_config_path, "w") as f:
        json.dump(specialize_config, f)

    # Folding config for node attributes (resType, mem_mode, noCompressor) -> compressor usage is configured here.
    folding_config = {
        "Defaults": {
            "resType": ["dsp", ["MVAU_rtl"]],
            "mem_mode": ["internal_decoupled", ["MVAU_rtl"]],
            "noCompressor": [1 if no_compressor else 0, ["MVAU_rtl"]],
        }
    }
    folding_config_path = os.path.join(output_dir, "folding_config.json")
    with open(folding_config_path, "w") as f:
        json.dump(folding_config, f)

    # Choose steps based on synth_only flag
    if synth_only:
        # Stop at out-of-context synthesis (no bitfile, much faster)
        steps = default_build_dataflow_steps[:default_build_dataflow_steps.index("step_out_of_context_synthesis") + 1]
        outputs = [
            DataflowOutputType.ESTIMATE_REPORTS,
            DataflowOutputType.STITCHED_IP,  
            DataflowOutputType.OOC_SYNTH,    # Triggers out-of-context synthesis
        ]
        shell_flow = None  
    else:
        # Full bitfile generation
        steps = default_build_dataflow_steps
        outputs = [
            DataflowOutputType.ESTIMATE_REPORTS,
            DataflowOutputType.BITFILE,
            DataflowOutputType.PYNQ_DRIVER,
        ]
        shell_flow = ShellFlowType.VIVADO_ZYNQ

    cfg = DataflowBuildConfig(
        output_dir=output_dir,
        synth_clk_period_ns=5.0,
        board=board,
        shell_flow_type=shell_flow,
        generate_outputs=outputs,
        steps=steps,
        specialize_layers_config_file=specialize_config_path,
        folding_config_file=folding_config_path,
        standalone_thresholds=True,  # Required for RTL MVAU -> HARD requirement, otherwise falls to hls.
        verbose=False,
    )

    return build_dataflow_cfg(model_path, cfg)


def parse_results(output_dir):
    """Parse ooc_synth_and_timing.json (Vivado out-of-context synthesis results)."""
    path = os.path.join(output_dir, "report", "ooc_synth_and_timing.json")
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
            # DSP reporting varies by FPGA family:
            # - Some boards report unified "DSP" count
            # - Others report by type: "DSP48E", "DSP48E1", "DSP48E2", "DSP58"
            # Sum all DSP-related keys to be robust
            dsp_count = data.get("DSP", 0)
            if dsp_count == 0:  # Try specific DSP types if unified count is 0
                dsp_count = (data.get("DSP48E", 0) + data.get("DSP48E1", 0) +
                             data.get("DSP48E2", 0) + data.get("DSP58", 0))

            return {"total": {
                "LUT": data.get("LUT", 0),
                "DSP": dsp_count,
                "FF": data.get("FF", 0),
                "BRAM": data.get("BRAM_18K", 0),
            }}
    return {}


def run_comparison(mw, mh, pe, simd, ww, aw, board, fpga_part, work_dir, synth_only):
    """Run A/B comparison for one config."""
    label = f"mw{mw}_mh{mh}_pe{pe}_simd{simd}_w{ww}_a{aw}"
    wdt = DataType[f"INT{ww}"]
    idt = DataType[f"INT{aw}"]
    odt = DataType["INT32"]

    results = {}
    for no_comp in [True, False]:
        variant = "dsp" if no_comp else "comp"
        run_dir = os.path.join(work_dir, f"{label}_{variant}")
        os.makedirs(run_dir, exist_ok=True)

        # Create and save model
        W = gen_finn_dt_tensor(wdt, (mw, mh))
        model = make_single_fclayer_modelwrapper(W, pe, simd, wdt, idt, odt, T=None, tdt=None)
        model_path = os.path.join(run_dir, "model.onnx")
        model.save(model_path)

        # Run build
        output_dir = os.path.join(run_dir, "output")
        try:
            run_build(model_path, output_dir, board, fpga_part, no_comp, synth_only)
            res = parse_results(output_dir)
            results[variant] = res.get("total", {})
        except Exception as e:
            print(f"    FAILED ({variant}): {e}")
            results[variant] = {"error": str(e)}

    return label, results


def format_table(all_results):
    """Format as markdown table."""
    lines = [
        "## Compressor vs DSP Comparison\n",
        "| Config | LUT (DSP) | LUT (Comp) | DSP (DSP) | DSP (Comp) | LUT Delta | DSP Saved |",
        "|--------|-----------|------------|-----------|------------|-----------|-----------|",
    ]

    for label, res in all_results:
        dsp = res.get("dsp", {})
        comp = res.get("comp", {})

        if "error" in dsp or "error" in comp:
            lines.append(f"| {label} | ERROR | - | - | - | - | - |")
            continue

        lut_dsp = int(dsp.get("LUT", 0))
        lut_comp = int(comp.get("LUT", 0))
        dsp_dsp = int(dsp.get("DSP", 0))
        dsp_comp = int(comp.get("DSP", 0))

        lut_delta = lut_comp - lut_dsp
        dsp_saved = dsp_dsp - dsp_comp

        lines.append(
            f"| {label} | {lut_dsp} | {lut_comp} | "
            f"{dsp_dsp} | {dsp_comp} | "
            f"{'+' if lut_delta >= 0 else ''}{lut_delta} | "
            f"{'+' if dsp_saved >= 0 else ''}{dsp_saved} |"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Benchmark MVAU compressor")
    parser.add_argument("--board", choices=list(BOARD_CONFIGS.keys()), default="pynq-z1")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--keep", action="store_true", help="Keep work directory")
    parser.add_argument("--synth-only", action="store_true", help="Stop at synthesis (faster, no bitfile)")
    args = parser.parse_args()

    board_cfg = BOARD_CONFIGS[args.board]
    board = board_cfg["board"]
    fpga_part = board_cfg["part"]

    # Compressor-eligible configs (ww < 4, aw < 4)
    configs = [
        (16, 16, 2, 8, 2, 2),   # mw, mh, pe, simd, ww, aw
        #(16, 16, 4, 8, 1, 1),
        #(32, 32, 2, 16, 2, 2),
        (16, 16, 2, 8, 4, 4),
    ]

    # Default to FINN_BUILD_DIR/compressor_benchmark
    if args.work_dir:
        work_dir = args.work_dir
    else:
        finn_build_dir = os.environ.get("FINN_BUILD_DIR", "/tmp")
        work_dir = os.path.join(finn_build_dir, "compressor_benchmark")

    print(f"Board: {board}")
    print(f"Part: {fpga_part}")
    print(f"Mode: {'Synthesis only (fast)' if args.synth_only else 'Full bitfile (slow)'}")
    print(f"Work: {work_dir}\n")

    all_results = []
    for mw, mh, pe, simd, ww, aw in configs:
        print(f"  mw{mw}_mh{mh}_pe{pe}_simd{simd}_w{ww}_a{aw}...", flush=True)
        label, results = run_comparison(mw, mh, pe, simd, ww, aw, board, fpga_part, work_dir, args.synth_only)
        all_results.append((label, results))

    print("\n" + "=" * 70)
    print(format_table(all_results))

    # Save JSON
    json_path = os.path.join(work_dir, "results.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved: {json_path}")

    if not args.keep and not args.work_dir:
        print(f"Cleaning up {work_dir}")
        shutil.rmtree(work_dir)


if __name__ == "__main__":
    main()
