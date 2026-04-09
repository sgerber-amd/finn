#!/usr/bin/env python
# Copyright (C) 2024, Advanced Micro Devices, Inc.
# All rights reserved.
#
# Benchmark add_multi compressor optimization in RTL MVAU layers.
#
# This benchmark compares:
# - RTL with DSP + binary adder tree (noCompressor=1)
# - RTL with DSP + compressor adder tree (noCompressor=0)
#
# Both use higher bitwidths (8-bit) where DSPs are mandatory.
# The compressor only optimizes the lane reduction adder tree, not the DSP multiply.

import argparse
import csv
import json
import os
import shutil

from qonnx.core.datatype import DataType
from qonnx.transformation.general import GiveUniqueNodeNames

import finn.builder.build_dataflow as build
import finn.builder.build_dataflow_config as build_cfg
from finn.builder.build_dataflow_config import default_build_dataflow_steps
from finn.util.basic import make_build_dir
from finn.util.test import get_test_model_trained

# Board configurations
BOARD_CONFIGS = {
    "Pynq-Z1": "xc7z020clg400-1",
    "ZCU104": "xczu7ev-ffvc1156-2-e",
    "U250": "xcu250-figd2104-2L-e",
    "VCK190": "xcvc1902-vsva2197-2MP-e-S",
}


def create_model(ww, aw, mw, mh):
    """Create a simple single-layer MVAU model for benchmarking."""
    import numpy as np
    from onnx import TensorProto, helper
    from qonnx.core.modelwrapper import ModelWrapper
    from qonnx.util.basic import gen_finn_dt_tensor, qonnx_make_model

    wdt = DataType[f"INT{ww}"]
    idt = DataType[f"UINT{aw}"]
    odt = DataType["INT32"]  # Wide enough for accumulation

    inp = helper.make_tensor_value_info("inp", TensorProto.FLOAT, [1, mw])
    outp = helper.make_tensor_value_info("outp", TensorProto.FLOAT, [1, mh])

    matmul = helper.make_node("MatMul", ["inp", "weights"], ["outp"])
    graph = helper.make_graph(
        nodes=[matmul], name="single_matmul", inputs=[inp], outputs=[outp]
    )

    model = qonnx_make_model(graph, producer_name="add_multi_benchmark")
    model = ModelWrapper(model)

    model.set_tensor_datatype("inp", idt)
    model.set_tensor_datatype("outp", odt)
    model.set_tensor_datatype("weights", wdt)

    # Generate random weights
    W = gen_finn_dt_tensor(wdt, (mw, mh))
    model.set_initializer("weights", W)

    return model


def run_build(model, output_dir, board, use_compressor, synth_only, synth_clk_period_ns, pe, simd):
    """Run FINN build for RTL MVAU with or without add_multi compressors."""
    os.makedirs(output_dir, exist_ok=True)

    # RTL specialization
    specialize_config = {
        "Defaults": {
            "preferred_impl_style": ["rtl", ["MVAU"]],
        }
    }
    specialize_config_path = os.path.join(output_dir, "specialize_layers_config.json")
    with open(specialize_config_path, "w") as f:
        json.dump(specialize_config, f)

    # Folding config - noCompressor controls add_multi compressor generation
    folding_config = {
        "Defaults": {
            "PE": [pe, ["MVAU_rtl"]],
            "SIMD": [simd, ["MVAU_rtl"]],
            "resType": ["dsp", ["MVAU_rtl"]],  # Force DSP path (8-bit needs DSPs)
            "mem_mode": ["internal_decoupled", ["MVAU_rtl"]],  # Include MVAU in synthesis
            "noCompressor": [1 if not use_compressor else 0, ["MVAU_rtl"]],
        }
    }
    folding_config_path = os.path.join(output_dir, "folding_config.json")
    with open(folding_config_path, "w") as f:
        json.dump(folding_config, f)

    standalone_thresholds = True  # Required for RTL

    # Choose steps based on synth_only flag
    if synth_only:
        steps = default_build_dataflow_steps[
            : default_build_dataflow_steps.index("step_out_of_context_synthesis") + 1
        ]
        outputs = [
            build_cfg.DataflowOutputType.ESTIMATE_REPORTS,
            build_cfg.DataflowOutputType.STITCHED_IP,
            build_cfg.DataflowOutputType.OOC_SYNTH,
        ]
        shell_flow = None
    else:
        steps = default_build_dataflow_steps
        outputs = [
            build_cfg.DataflowOutputType.ESTIMATE_REPORTS,
            build_cfg.DataflowOutputType.BITFILE,
            build_cfg.DataflowOutputType.PYNQ_DRIVER,
            build_cfg.DataflowOutputType.DEPLOYMENT_PACKAGE,
        ]
        shell_flow = build_cfg.ShellFlowType.VIVADO_ZYNQ

    # Determine fpga_part
    fpga_part = BOARD_CONFIGS.get(board)
    if fpga_part is None:
        raise ValueError(f"Unknown board: {board}")

    cfg = build.DataflowBuildConfig(
        output_dir=output_dir,
        synth_clk_period_ns=synth_clk_period_ns,
        board=board if not synth_only else None,
        fpga_part=fpga_part if synth_only else None,
        shell_flow_type=shell_flow,
        steps=steps,
        generate_outputs=outputs,
        specialize_layers_config_file=specialize_config_path,
        folding_config_file=folding_config_path,
        standalone_thresholds=standalone_thresholds,
        save_intermediate_models=True,
    )

    build.build_dataflow_cfg(model, cfg)

    # Read synthesis results
    synth_report_path = os.path.join(output_dir, "report", "ooc_synth_and_timing.json")
    if os.path.exists(synth_report_path):
        with open(synth_report_path) as f:
            return json.load(f)
    else:
        return None


def run_comparison(mw, mh, pe, simd, ww, aw, board, fpga_part, work_dir, synth_only, timing_search, synth_clk_period_ns):
    """Run both RTL variants (with and without add_multi compressor) and compare."""
    label = f"mw{mw}_mh{mh}_pe{pe}_simd{simd}_w{ww}_a{aw}"
    print(f"  Config: {label}")

    results = {}

    # Create model
    model = create_model(ww, aw, mw, mh)

    # Convert to MVAU - just basic transformations
    # Let the build flow handle folding via config files
    from qonnx.transformation.infer_data_layouts import InferDataLayouts
    from qonnx.transformation.infer_datatypes import InferDataTypes
    from qonnx.transformation.infer_shapes import InferShapes
    import finn.transformation.fpgadataflow.convert_to_hw_layers as to_hw

    model = model.transform(InferShapes())
    model = model.transform(InferDataTypes())
    model = model.transform(InferDataLayouts())
    model = model.transform(to_hw.InferQuantizedMatrixVectorActivation())
    model = model.transform(GiveUniqueNodeNames())

    model_path = os.path.join(work_dir, f"{label}_base.onnx")
    model.save(model_path)

    for variant in ["rtl_binary_adder", "rtl_compressor_adder"]:
        try:
            use_compressor = variant == "rtl_compressor_adder"
            output_dir = os.path.join(work_dir, f"{label}_{variant}")

            print(f"    Building {variant}...", flush=True)
            synth_result = run_build(
                model_path, output_dir, board, use_compressor, synth_only, synth_clk_period_ns, pe, simd
            )

            if synth_result:
                results[variant] = {
                    "LUT": synth_result.get("LUT", 0),
                    "FF": synth_result.get("FF", 0),
                    "DSP": synth_result.get("DSP", 0),
                    "BRAM": synth_result.get("BRAM", 0),
                    "WNS": synth_result.get("WNS", 0),
                    "fmax_mhz": synth_result.get("fmax_mhz", 0),
                }

                # Optionally run timing search on synthesized design
                if timing_search and synth_only:
                    print(f"      Running timing search for {variant}...", flush=True)
                    from finn.util.vivado import timing_closure_search_from_synth

                    synth_report_path = os.path.join(output_dir, "report", "ooc_synth_and_timing.json")

                    if os.path.exists(synth_report_path):
                        with open(synth_report_path) as f:
                            synth_report = json.load(f)

                        vivado_proj_folder = synth_report.get("vivado_proj_folder")

                        if vivado_proj_folder and os.path.exists(vivado_proj_folder):
                            print(f"      Using vivado_proj_folder: {vivado_proj_folder}")

                            top_name = "finn_design_wrapper"

                            timing_result = timing_closure_search_from_synth(
                                vivado_proj_folder=vivado_proj_folder,
                                top_name=top_name,
                                clk_name="ap_clk",
                                clk_period_ns_min=2.0,  # Aggressive (500 MHz)
                                clk_period_ns_max=20.0,  # Conservative (50 MHz)
                            )

                            # Merge timing results
                            achieved_fmax = timing_result.get("achieved_fmax_mhz", 0)
                            achieved_period = timing_result.get("achieved_period_ns", 0)
                            results[variant]["achieved_fmax_mhz"] = achieved_fmax
                            results[variant]["achieved_period_ns"] = achieved_period
                            results[variant]["fmax_mhz"] = achieved_fmax  # Update fmax to match
                            results[variant]["iterations"] = timing_result.get("iterations", 0)
                            if "wns_at_closure" in timing_result:
                                results[variant]["WNS"] = timing_result["wns_at_closure"]

                            print(f"      Achieved fmax: {achieved_fmax:.1f} MHz in {timing_result['iterations']} iterations")
                        else:
                            print(f"      WARNING: vivado_proj_folder not found: {vivado_proj_folder}")
                    else:
                        print(f"      WARNING: Synthesis report not found at {synth_report_path}")

            else:
                print(f"      WARNING: No synthesis results for {variant}")
                results[variant] = {"error": "No synthesis results"}

        except Exception as e:
            print(f"    FAILED ({variant}): {e}")
            results[variant] = {"error": str(e)}

    return label, results


def format_table(all_results):
    """Format comparison results as markdown table."""
    # Check if any result has timing search data
    has_timing = any(
        results.get("rtl_binary_adder", {}).get("achieved_fmax_mhz") is not None or
        results.get("rtl_compressor_adder", {}).get("achieved_fmax_mhz") is not None
        for _, results in all_results
    )

    if has_timing:
        lines = [
            "## RTL Binary Adder vs RTL Compressor Adder (with Timing Search)",
            "",
            "| Config | LUT (Binary) | LUT (Compressor) | DSP (Binary) | DSP (Compressor) | fmax (Binary) MHz | fmax (Compressor) MHz | LUT Δ | fmax Δ % |",
            "|--------|--------------|------------------|--------------|------------------|-------------------|-----------------------|-------|----------|",
        ]
    else:
        lines = [
            "## RTL Binary Adder vs RTL Compressor Adder (add_multi optimization)",
            "",
            "| Config | LUT (Binary) | LUT (Compressor) | DSP (Binary) | DSP (Compressor) | fmax (Binary) MHz | fmax (Compressor) MHz | LUT Δ | fmax Δ % |",
            "|--------|--------------|------------------|--------------|------------------|-------------------|-----------------------|-------|----------|",
        ]

    for label, results in all_results:
        binary = results.get("rtl_binary_adder", {})
        compressor = results.get("rtl_compressor_adder", {})

        if "error" in binary or "error" in compressor:
            lines.append(f"| {label} | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR |")
            continue

        lut_b = binary.get("LUT", 0)
        lut_c = compressor.get("LUT", 0)
        dsp_b = binary.get("DSP", 0)
        dsp_c = compressor.get("DSP", 0)

        # Use achieved_fmax if available (from timing search), otherwise use initial fmax
        fmax_b = binary.get("achieved_fmax_mhz") or binary.get("fmax_mhz", 0)
        fmax_c = compressor.get("achieved_fmax_mhz") or compressor.get("fmax_mhz", 0)

        lut_delta = lut_c - lut_b
        fmax_delta_pct = ((fmax_c - fmax_b) / fmax_b * 100) if fmax_b > 0 else 0

        lines.append(
            f"| {label} | {lut_b:.0f} | {lut_c:.0f} | {dsp_b:.0f} | {dsp_c:.0f} | "
            f"{fmax_b:.1f} | {fmax_c:.1f} | {lut_delta:+.0f} | {fmax_delta_pct:+.1f} |"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark add_multi compressor optimization")
    parser.add_argument("--board", type=str, default="VCK190", choices=BOARD_CONFIGS.keys())
    parser.add_argument("--synth-only", action="store_true", help="Synthesis only (no bitfile)")
    parser.add_argument("--timing-search", action="store_true", help="Run timing closure search")
    parser.add_argument("--synth-clk-period-ns", type=float, default=10.0, help="Target clock period")
    parser.add_argument("--work-dir", type=str, help="Work directory for builds")
    parser.add_argument("--keep", action="store_true", help="Keep intermediate files")

    args = parser.parse_args()

    board = args.board
    fpga_part = BOARD_CONFIGS[board]

    # Test configurations - 10-bit operands to force genSoftVec path on Versal
    # (Exceeds genINT8 threshold of WW<=8 && AW<=9, forcing mvu.sv with add_multi)
    # add_multi compressors only activate when SIMD >= 4
    configs = [
        # Format: (mw, mh, pe, simd, ww, aw)

        # 10-bit × 10-bit (forces genSoftVec, uses add_multi for lane reduction)
        (8, 8, 1, 4, 10, 10),    # Smallest that triggers genSoftVec + add_multi

        # Mixed precision (10-bit weight, 9-bit activation - still exceeds threshold)
        (8, 8, 1, 4, 10, 9),     # Mixed precision variant
    ]

    # Default work directory
    if args.work_dir:
        work_dir = args.work_dir
    else:
        finn_build_dir = os.environ.get("FINN_BUILD_DIR", "/tmp")
        work_dir = os.path.join(finn_build_dir, "add_multi_benchmark")

    print("=" * 80)
    print("add_multi Compressor Benchmark")
    print("=" * 80)
    print(f"Board: {board}")
    print(f"Part: {fpga_part}")
    print(f"Target clock: {args.synth_clk_period_ns} ns ({1000/args.synth_clk_period_ns:.1f} MHz)")
    print(f"Mode: {'Synthesis only' if args.synth_only else 'Full bitfile'}")
    print(f"Timing search: {'Enabled' if args.timing_search else 'Disabled'}")
    print(f"Configs: {len(configs)}")
    print(f"Work: {work_dir}\n")

    all_results = []
    for i, (mw, mh, pe, simd, ww, aw) in enumerate(configs):
        print(f"[{i+1}/{len(configs)}] mw{mw}_mh{mh}_pe{pe}_simd{simd}_w{ww}_a{aw}...", flush=True)
        label, results = run_comparison(
            mw, mh, pe, simd, ww, aw, board, fpga_part, work_dir, args.synth_only,
            args.timing_search, args.synth_clk_period_ns
        )
        all_results.append((label, results))

    print("\n" + "=" * 80)
    print(format_table(all_results))

    # Save CSV
    csv_path = os.path.join(work_dir, "add_multi_results.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Config", "Implementation", "LUT", "FF", "DSP", "BRAM",
            "WNS_ns", "fmax_MHz", "achieved_fmax_MHz", "achieved_period_ns",
            "iterations", "LUT_delta", "fmax_delta_pct"
        ])

        for label, results in all_results:
            binary = results.get("rtl_binary_adder", {})
            compressor = results.get("rtl_compressor_adder", {})

            if "error" not in binary:
                writer.writerow([
                    label, "RTL_Binary_Adder",
                    binary.get("LUT", 0), binary.get("FF", 0),
                    binary.get("DSP", 0), binary.get("BRAM", 0),
                    binary.get("WNS", 0), binary.get("fmax_mhz", 0),
                    binary.get("achieved_fmax_mhz", ""), binary.get("achieved_period_ns", ""),
                    binary.get("iterations", ""),
                    "", ""
                ])

            if "error" not in compressor:
                lut_delta = compressor.get("LUT", 0) - binary.get("LUT", 0)
                # Use achieved_fmax if available, otherwise use initial fmax
                fmax_b = binary.get("achieved_fmax_mhz") or binary.get("fmax_mhz", 0)
                fmax_c = compressor.get("achieved_fmax_mhz") or compressor.get("fmax_mhz", 0)
                fmax_delta_pct = ((fmax_c - fmax_b) / fmax_b * 100) if fmax_b > 0 else 0

                writer.writerow([
                    label, "RTL_Compressor_Adder",
                    compressor.get("LUT", 0), compressor.get("FF", 0),
                    compressor.get("DSP", 0), compressor.get("BRAM", 0),
                    compressor.get("WNS", 0), compressor.get("fmax_mhz", 0),
                    compressor.get("achieved_fmax_mhz", ""), compressor.get("achieved_period_ns", ""),
                    compressor.get("iterations", ""),
                    lut_delta, f"{fmax_delta_pct:.1f}"
                ])

    print(f"\nResults saved to: {csv_path}")
