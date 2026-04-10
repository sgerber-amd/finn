#!/usr/bin/env python3
# Copyright (C) 2024, Advanced Micro Devices, Inc.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Benchmark HLS MVAU vs RTL MVAU with Compressors.

Compares the traditional HLS implementation (used for low-bitwidth networks)
against the new RTL implementation with LUT-based compressor trees.

Focus: 1-4 bit operands where compressors replace DSPs entirely (dotp_comp path).

Usage (inside Docker):
    python -m finn.compressor.benchmark_hls_vs_compressor --synth-only --keep
"""

import argparse
import csv
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
from finn.compressor.benchmark_utils import (
    BOARD_CONFIGS,
    compute_latency_cycles,
    format_config_label,
    parse_dsp_counts,
)
from tests.fpgadataflow.test_fpgadataflow_mvau import make_single_fclayer_modelwrapper


def run_build(model_path, output_dir, board, use_rtl, synth_only, synth_clk_period_ns):
    """Run FINN build - either HLS or RTL with compressors."""
    os.makedirs(output_dir, exist_ok=True)

    if use_rtl:
        # RTL with compressors enabled
        specialize_config = {
            "Defaults": {
                "preferred_impl_style": ["rtl", ["MVAU"]],
            }
        }
        specialize_config_path = os.path.join(output_dir, "specialize_layers_config.json")
        with open(specialize_config_path, "w") as f:
            json.dump(specialize_config, f)

        folding_config = {
            "Defaults": {
                "resType": ["dsp", ["MVAU_rtl"]],
                # NOTE: Testing with internal_decoupled (FINN default) to investigate
                # DRC errors. This matches end2end tests and should work, but we've seen
                # multiple driver net errors with specific configs (w2_a4).
                "mem_mode": ["internal_decoupled", ["MVAU_rtl"]],
                "noCompressor": [0, ["MVAU_rtl"]],  # Enable compressors
            }
        }
        folding_config_path = os.path.join(output_dir, "folding_config.json")
        with open(folding_config_path, "w") as f:
            json.dump(folding_config, f)

        standalone_thresholds = True  # Required for RTL
    else:
        # HLS - use external mem_mode for fair comparison with RTL
        specialize_config = {
            "Defaults": {
                "preferred_impl_style": ["hls", ["MVAU"]],
            }
        }
        specialize_config_path = os.path.join(output_dir, "specialize_layers_config.json")
        with open(specialize_config_path, "w") as f:
            json.dump(specialize_config, f)

        # Use internal_decoupled to match RTL default and investigate DRC issue
        folding_config = {
            "Defaults": {
                "mem_mode": ["internal_decoupled", ["MVAU_hls"]],
            }
        }
        folding_config_path = os.path.join(output_dir, "folding_config.json")
        with open(folding_config_path, "w") as f:
            json.dump(folding_config, f)

        standalone_thresholds = False  # HLS can merge with thresholds

    # Choose steps based on synth_only flag
    if synth_only:
        steps = default_build_dataflow_steps[
            : default_build_dataflow_steps.index("step_out_of_context_synthesis") + 1
        ]
        outputs = [
            DataflowOutputType.ESTIMATE_REPORTS,
            DataflowOutputType.STITCHED_IP,
            DataflowOutputType.OOC_SYNTH,
        ]
        shell_flow = None
    else:
        steps = default_build_dataflow_steps
        outputs = [
            DataflowOutputType.ESTIMATE_REPORTS,
            DataflowOutputType.BITFILE,
            DataflowOutputType.PYNQ_DRIVER,
        ]
        shell_flow = ShellFlowType.VIVADO_ZYNQ

    cfg = DataflowBuildConfig(
        output_dir=output_dir,
        synth_clk_period_ns=synth_clk_period_ns,
        board=board,
        shell_flow_type=shell_flow,
        generate_outputs=outputs,
        steps=steps,
        specialize_layers_config_file=specialize_config_path,
        folding_config_file=folding_config_path,
        standalone_thresholds=standalone_thresholds,
        verbose=True,
    )

    return build_dataflow_cfg(model_path, cfg)


def parse_results(output_dir):
    """Parse ooc_synth_and_timing.json (Vivado synthesis results)."""
    path = os.path.join(output_dir, "report", "ooc_synth_and_timing.json")
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
            return {
                "LUT": data.get("LUT", 0),
                "DSP": parse_dsp_counts(data),
                "FF": data.get("FF", 0),
                "BRAM": data.get("BRAM_18K", 0),
                "WNS": data.get("WNS", 0),
                "fmax_mhz": data.get("fmax_mhz", 0),
            }
    return {}


def run_comparison(mw, mh, pe, simd, ww, aw, board, work_dir, synth_only, timing_search, synth_clk_period_ns):
    """Run HLS vs RTL+Compressor comparison for one config."""
    label = format_config_label(mw, mh, pe, simd, ww, aw)
    wdt = DataType[f"INT{ww}"]
    idt = DataType[f"INT{aw}"]
    odt = DataType["INT32"]

    exp_cycles = compute_latency_cycles(mh, pe, mw, simd)

    results = {}

    # Run both HLS and RTL variants
    for use_rtl in [False, True]:  # False=HLS, True=RTL
        variant = "rtl_comp" if use_rtl else "hls"
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
            run_build(model_path, output_dir, board, use_rtl, synth_only, synth_clk_period_ns)

            res = parse_results(output_dir)
            results[variant] = res
            results[variant]["exp_cycles"] = exp_cycles

            # Optionally run timing search on synthesized design
            if timing_search and synth_only:
                print(f"      Running timing search for {variant}...", flush=True)
                from finn.util.vivado import timing_closure_search_from_synth

                # Get vivado_proj_folder from ooc_synth_and_timing.json
                # The synthesis project is in FINN_BUILD_DIR (scratch), not in output dir
                synth_report_path = os.path.join(output_dir, "report", "ooc_synth_and_timing.json")

                if os.path.exists(synth_report_path):
                    with open(synth_report_path) as f:
                        synth_report = json.load(f)

                    vivado_proj_folder = synth_report.get("vivado_proj_folder")

                    if vivado_proj_folder and os.path.exists(vivado_proj_folder):
                        print(f"      Using vivado_proj_folder: {vivado_proj_folder}")

                        # Top module name (not actually used by _from_synth)
                        top_name = "finn_design_wrapper"

                        timing_result = timing_closure_search_from_synth(
                            vivado_proj_folder=vivado_proj_folder,
                            top_name=top_name,
                            clk_name="ap_clk",
                            clk_period_ns_min=2.0,  # Aggressive (500 MHz) - will likely fail
                            clk_period_ns_max=20.0,  # Conservative (50 MHz) - should pass
                        )

                        # Merge timing results
                        achieved_fmax = timing_result.get("achieved_fmax_mhz", 0)
                        achieved_period = timing_result.get("achieved_period_ns", 0)
                        results[variant]["achieved_fmax_mhz"] = achieved_fmax
                        results[variant]["fmax_mhz"] = achieved_fmax
                        results[variant]["iterations"] = timing_result.get("iterations", 0)

                        # Compute latency from achieved timing
                        if achieved_period > 0:
                            latency_ns = exp_cycles * achieved_period
                            results[variant]["latency_ns"] = latency_ns
                            print(f"      Achieved fmax: {achieved_fmax:.1f} MHz in {timing_result['iterations']} iterations")
                            print(f"      Latency: {exp_cycles} cycles = {latency_ns:.2f} ns")
                        else:
                            print(f"      Achieved fmax: {achieved_fmax:.1f} MHz in {timing_result['iterations']} iterations")
                    else:
                        print(f"      WARNING: vivado_proj_folder not found or doesn't exist: {vivado_proj_folder}")
                else:
                    print(f"      WARNING: Synthesis report not found at {synth_report_path}")

        except Exception as e:
            print(f"    FAILED ({variant}): {e}")
            results[variant] = {"error": str(e)}

    return label, results


def format_table(all_results):
    """Format comparison results as markdown table."""
    # Check if any result has timing search data
    has_timing = any(
        results.get("hls", {}).get("achieved_fmax_mhz") is not None or
        results.get("rtl_comp", {}).get("achieved_fmax_mhz") is not None
        for _, results in all_results
    )

    if has_timing:
        lines = [
            "## HLS vs RTL+Compressor Comparison (with Timing Search)",
            "",
            "| Config | LUT (HLS) | LUT (RTL) | DSP (HLS) | DSP (RTL) | fmax (HLS) MHz | fmax (RTL) MHz | LUT Δ | DSP Δ |",
            "|--------|-----------|-----------|-----------|-----------|----------------|----------------|-------|-------|",
        ]
    else:
        lines = [
            "## HLS vs RTL+Compressor Comparison",
            "",
            "| Config | LUT (HLS) | LUT (RTL) | DSP (HLS) | DSP (RTL) | LUT Δ | DSP Δ |",
            "|--------|-----------|-----------|-----------|-----------|-------|-------|",
        ]

    for label, results in all_results:
        hls = results.get("hls", {})
        rtl = results.get("rtl_comp", {})

        if "error" in hls or "error" in rtl:
            if has_timing:
                lines.append(f"| {label} | ERROR | - | - | - | - | - | - | - |")
            else:
                lines.append(f"| {label} | ERROR | - | - | - | - | - |")
            continue

        lut_hls = int(hls.get("LUT", 0))
        lut_rtl = int(rtl.get("LUT", 0))
        dsp_hls = int(hls.get("DSP", 0))
        dsp_rtl = int(rtl.get("DSP", 0))

        lut_delta = lut_rtl - lut_hls
        dsp_delta = dsp_rtl - dsp_hls

        if has_timing:
            fmax_hls = hls.get("achieved_fmax_mhz", hls.get("fmax_mhz", 0))
            fmax_rtl = rtl.get("achieved_fmax_mhz", rtl.get("fmax_mhz", 0))
            lines.append(
                f"| {label} | {lut_hls} | {lut_rtl} | "
                f"{dsp_hls} | {dsp_rtl} | "
                f"{fmax_hls:.1f} | {fmax_rtl:.1f} | "
                f"{'+' if lut_delta >= 0 else ''}{lut_delta} | "
                f"{'+' if dsp_delta >= 0 else ''}{dsp_delta} |"
            )
        else:
            lines.append(
                f"| {label} | {lut_hls} | {lut_rtl} | "
                f"{dsp_hls} | {dsp_rtl} | "
                f"{'+' if lut_delta >= 0 else ''}{lut_delta} | "
                f"{'+' if dsp_delta >= 0 else ''}{dsp_delta} |"
            )

    return "\n".join(lines)


def write_csv(all_results, csv_path):
    """Write results to CSV format (Option B: with latency)."""
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'Config', 'Implementation',
            'LUT', 'DSP', 'fmax_MHz', 'cycles', 'latency_ns',
            'LUT_delta', 'latency_delta_ns'
        ])

        # Data rows
        for label, results in all_results:
            hls = results.get("hls", {})
            rtl = results.get("rtl_comp", {})

            if "error" in hls or "error" in rtl:
                continue

            # Use achieved_fmax if available (from timing search), otherwise use initial fmax
            fmax_hls = hls.get('achieved_fmax_mhz') or hls.get('fmax_mhz', 0)
            fmax_rtl = rtl.get('achieved_fmax_mhz') or rtl.get('fmax_mhz', 0)
            latency_hls = hls.get('latency_ns', 0)
            latency_rtl = rtl.get('latency_ns', 0)

            # HLS row
            writer.writerow([
                label, 'HLS',
                int(hls.get('LUT', 0)),
                int(hls.get('DSP', 0)),
                f"{fmax_hls:.1f}",
                int(hls.get('exp_cycles', 0)),
                f"{latency_hls:.2f}" if latency_hls else '',
                '', ''
            ])

            # RTL row with deltas
            lut_delta = int(rtl.get('LUT', 0)) - int(hls.get('LUT', 0))
            latency_delta = latency_rtl - latency_hls if (latency_rtl and latency_hls) else 0

            writer.writerow([
                label, 'RTL_Compressor',
                int(rtl.get('LUT', 0)),
                int(rtl.get('DSP', 0)),
                f"{fmax_rtl:.1f}",
                int(rtl.get('exp_cycles', 0)),
                f"{latency_rtl:.2f}" if latency_rtl else '',
                lut_delta,
                f"{latency_delta:.2f}" if latency_delta else ''
            ])


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark HLS MVAU vs RTL MVAU with Compressors"
    )
    parser.add_argument("--board", choices=list(BOARD_CONFIGS.keys()), default="pynq-z1")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--keep", action="store_true", help="Keep work directory")
    parser.add_argument(
        "--synth-only", action="store_true", help="Stop at synthesis (faster, no bitfile)"
    )
    parser.add_argument(
        "--timing-search",
        action="store_true",
        help="Run binary search for timing closure (SLOW: 50-150 min per config)",
    )
    parser.add_argument(
        "--synth-clk-period-ns",
        type=float,
        default=1.5,
        help="Target clock period for synthesis in ns (default: 1.5 = 666 MHz)",
    )
    args = parser.parse_args()

    board_cfg = BOARD_CONFIGS[args.board]
    board = board_cfg["board"]
    fpga_part = board_cfg["part"]

    # 7-Series configs (MW, MH, PE, SIMD, WW, AW)
    configs = [
        (32, 18, 1, 1, 4, 4),   # Minimal parallelism
        (32, 18, 9, 1, 4, 4),   # PE only
        (32, 18, 1, 16, 2, 2),  # SIMD only, 2-bit
        (32, 18, 1, 16, 4, 4),  # SIMD only, 4-bit
        (32, 18, 9, 16, 2, 2),  # Balanced, 2-bit
        (32, 18, 9, 16, 4, 4),  # Balanced, 4-bit
        (32, 18, 18, 16, 4, 4), # PE-max, 4-bit
        (32, 18, 1, 32, 2, 2),  # SIMD-max, 2-bit
        (32, 18, 1, 32, 4, 4),  # SIMD-max, 4-bit
        (32, 18, 9, 32, 4, 4),  # Balanced, 4-bit
        (32, 18, 9, 16, 8, 8),  # DSP-based reference
    ]

    # Versal configs (for VCK190 board)
    # configs = [
    #     (600, 64, 8, 8, 2, 2),     # Cybsec layer 1
    #     (64, 64, 8, 8, 2, 2),      # Cybsec layers 2-3
    #     (64, 1, 1, 8, 2, 2),       # Cybsec layer 4
    #     (128, 128, 128, 128, 4, 4),# Large depth (UltraRAM)
    #     (32, 18, 1, 16, 4, 4),     # Cross-platform comparison
    #     (32, 18, 9, 16, 2, 2),     # Cross-platform comparison
    #     (32, 18, 9, 32, 4, 4),     # SIMD=32, 4-bit
    #     (32, 18, 9, 16, 8, 8),     # DSP58 reference
    # ]

    # Default to FINN_BUILD_DIR/hls_vs_compressor_benchmark
    if args.work_dir:
        work_dir = args.work_dir
    else:
        finn_build_dir = os.environ.get("FINN_BUILD_DIR", "/tmp")
        work_dir = os.path.join(finn_build_dir, "hls_vs_compressor_benchmark")

    print(f"Board: {board}")
    print(f"Part: {fpga_part}")
    print(f"Target clock: {args.synth_clk_period_ns} ns ({1000/args.synth_clk_period_ns:.1f} MHz)")
    print(f"Mode: {'Synthesis only (fast)' if args.synth_only else 'Full bitfile (slow)'}")
    print(f"Timing search: {'Enabled' if args.timing_search else 'Disabled'}")
    print(f"Configs: {len(configs)}")
    print(f"Work: {work_dir}\n")

    all_results = []
    json_path = os.path.join(work_dir, "results.json")
    csv_path = os.path.join(work_dir, "results.csv")

    for i, (mw, mh, pe, simd, ww, aw) in enumerate(configs):
        print(f"[{i+1}/{len(configs)}] mw{mw}_mh{mh}_pe{pe}_simd{simd}_w{ww}_a{aw}...", flush=True)
        label, results = run_comparison(
            mw, mh, pe, simd, ww, aw, board, work_dir, args.synth_only,
            args.timing_search, args.synth_clk_period_ns
        )
        all_results.append((label, results))

        # Save incremental results after each config
        with open(json_path, "w") as f:
            json.dump(all_results, f, indent=2)
        write_csv(all_results, csv_path)

    print("\n" + "=" * 80)
    print(format_table(all_results))
    print(f"\nFinal results saved to:")
    print(f"  JSON: {json_path}")
    print(f"  CSV: {csv_path}")

    if not args.keep and not args.work_dir:
        print(f"Cleaning up {work_dir}")
        shutil.rmtree(work_dir)


if __name__ == "__main__":
    main()
