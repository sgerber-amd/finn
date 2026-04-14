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
from qonnx.core.modelwrapper import ModelWrapper
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


def run_build(model_path, output_dir, board, use_rtl, synth_only, synth_clk_period_ns, mvau_only=False):
    """Run FINN build - either HLS or RTL with compressors.

    Args:
        mvau_only: If True, stop at IP generation (no stitched IP) for MVAU-only synthesis
    """
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
                # NOTE: internal_decoupled is FINN default (matches test suite). May produce
                # DRC warnings (driverless net) on AXI padding bits for large SIMD configs.
                # Harmless for synth-only benchmarking; use external mem_mode for deployment.
                "mem_mode": ["internal_decoupled", ["MVAU_rtl"]],
                "noCompressor": [0, ["MVAU_rtl"]],  # Enable compressors
            }
        }
        folding_config_path = os.path.join(output_dir, "folding_config.json")
        with open(folding_config_path, "w") as f:
            json.dump(folding_config, f)

        standalone_thresholds = True  # Required for RTL
    else:
        # HLS - use internal_decoupled to match RTL and FINN test defaults
        specialize_config = {
            "Defaults": {
                "preferred_impl_style": ["hls", ["MVAU"]],
            }
        }
        specialize_config_path = os.path.join(output_dir, "specialize_layers_config.json")
        with open(specialize_config_path, "w") as f:
            json.dump(specialize_config, f)

        folding_config = {
            "Defaults": {
                "mem_mode": ["internal_decoupled", ["MVAU_hls"]],
            }
        }
        folding_config_path = os.path.join(output_dir, "folding_config.json")
        with open(folding_config_path, "w") as f:
            json.dump(folding_config, f)

        standalone_thresholds = False  # HLS can merge with thresholds

    # Choose steps based on synth_only and mvau_only flags
    if synth_only:
        if mvau_only:
            # Stop at IP generation (no stitched IP, we'll synthesize MVAU core directly)
            steps = default_build_dataflow_steps[
                : default_build_dataflow_steps.index("step_hw_ipgen") + 1
            ]
            outputs = [
                DataflowOutputType.ESTIMATE_REPORTS,
            ]
        else:
            # Normal synthesis: include stitched IP and OOC synthesis
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

    # Run build (returns 0 on success, -1 on error)
    ret = build_dataflow_cfg(model_path, cfg)
    if ret != 0:
        raise RuntimeError(f"Build failed with return code {ret}")

    # Only load model if needed for mvau_only synthesis
    if mvau_only:
        final_model_path = os.path.join(output_dir, "intermediate_models", "step_hw_ipgen.onnx")
        if not os.path.exists(final_model_path):
            raise RuntimeError(f"Model not found: {final_model_path}")
        return ModelWrapper(final_model_path)
    else:
        return None  # Not needed for normal mode (uses parse_results instead)


def parse_results(output_dir, mvau_only=False):
    """Parse ooc_synth_and_timing.json (Vivado synthesis results).

    Args:
        output_dir: FINN build output directory
        mvau_only: If True, parse MVAU-only results (from model metadata)
    """
    if mvau_only:
        # MVAU-only results are stored in model metadata, not in report files
        # Caller must extract from model.get_metadata_prop("res_mvau_only_ooc_synth")
        return {}

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


def run_comparison(mw, mh, pe, simd, ww, aw, board, work_dir, synth_only, timing_search, synth_clk_period_ns, mvau_only=False):
    """Run HLS vs RTL+Compressor comparison for one config.

    Args:
        mvau_only: If True, synthesize bare MVAU core without wrappers/FIFOs
    """
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
            result = run_build(model_path, output_dir, board, use_rtl, synth_only, synth_clk_period_ns, mvau_only)

            if mvau_only:
                # Run MVAU-only synthesis (no stitched IP was created)
                # result is a ModelWrapper in this mode
                from finn.transformation.fpgadataflow.synth_ooc_mvau_only import SynthOutOfContextMVAUOnly

                # board is the uppercase name (e.g. "VCK190"), need to get part from BOARD_CONFIGS
                # Find the matching config by checking board names (case-insensitive)
                part = None
                for key, cfg in BOARD_CONFIGS.items():
                    if cfg["board"].upper() == board.upper():
                        part = cfg["part"]
                        break
                if part is None:
                    # Fallback: assume board is already a part number
                    part = board

                model = result.transform(
                    SynthOutOfContextMVAUOnly(
                        part=part,
                        clk_period_ns=synth_clk_period_ns
                    )
                )

                # Parse results from model metadata
                mvau_res_str = model.get_metadata_prop("res_mvau_only_ooc_synth")
                mvau_res = eval(mvau_res_str)

                res = {
                    "LUT": mvau_res.get("LUT", 0),
                    "DSP": mvau_res.get("DSP", 0),
                    "FF": mvau_res.get("FF", 0),
                    "BRAM": mvau_res.get("BRAM_18K", 0),
                    "WNS": mvau_res.get("WNS", 0),
                    "fmax_mhz": mvau_res.get("fmax_mhz", 0),
                    "vivado_proj_folder": mvau_res.get("vivado_proj_folder"),  # For timing search
                }
            else:
                # result is None in normal mode, parse from output_dir
                res = parse_results(output_dir)

            results[variant] = res
            results[variant]["exp_cycles"] = exp_cycles

            # Optionally run timing search on synthesized design
            if timing_search and synth_only:
                print(f"      Running timing search for {variant}...", flush=True)
                from finn.util.vivado import timing_closure_search_from_synth

                # Get vivado_proj_folder and top_name (different paths for mvau_only vs normal)
                vivado_proj_folder = None
                top_name = None

                if mvau_only:
                    # mvau_only: Get from mvau metadata (stored by SynthOutOfContextMVAUOnly)
                    vivado_proj_folder = res.get("vivado_proj_folder")
                    # Top module is the bare MVAU core (not finn_design_wrapper)
                    top_name = "mvu_vvu_axi" if use_rtl else model.graph.node[0].name  # HLS uses node name
                else:
                    # Normal mode: Get from ooc_synth_and_timing.json
                    synth_report_path = os.path.join(output_dir, "report", "ooc_synth_and_timing.json")
                    if os.path.exists(synth_report_path):
                        with open(synth_report_path) as f:
                            synth_report = json.load(f)
                        vivado_proj_folder = synth_report.get("vivado_proj_folder")
                        top_name = "finn_design_wrapper"
                    else:
                        print(f"      WARNING: Synthesis report not found at {synth_report_path}")

                if vivado_proj_folder and os.path.exists(vivado_proj_folder):
                    print(f"      Using vivado_proj_folder: {vivado_proj_folder}")

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
                    wns_at_closure = timing_result.get("wns_at_closure", 0)
                    results[variant]["achieved_fmax_mhz"] = achieved_fmax
                    results[variant]["fmax_mhz"] = achieved_fmax
                    results[variant]["iterations"] = timing_result.get("iterations", 0)
                    results[variant]["WNS"] = wns_at_closure  # Override initial synth WNS with timing closure WNS

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
            'LUT', 'FF', 'DSP', 'BRAM', 'fmax_MHz', 'cycles', 'latency_ns',
            'LUT_delta', 'latency_delta_ns', 'WNS'
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
            wns_hls = hls.get('WNS', 0)
            writer.writerow([
                label, 'HLS',
                int(hls.get('LUT', 0)),
                int(hls.get('FF', 0)),
                int(hls.get('DSP', 0)),
                int(hls.get('BRAM', 0)),
                f"{fmax_hls:.1f}",
                int(hls.get('exp_cycles', 0)),
                f"{latency_hls:.2f}" if latency_hls else '',
                '', '',
                f"{wns_hls:.3f}" if wns_hls else ''
            ])

            # RTL row with deltas
            lut_delta = int(rtl.get('LUT', 0)) - int(hls.get('LUT', 0))
            latency_delta = latency_rtl - latency_hls if (latency_rtl and latency_hls) else 0
            wns_rtl = rtl.get('WNS', 0)

            writer.writerow([
                label, 'RTL_Compressor',
                int(rtl.get('LUT', 0)),
                int(rtl.get('FF', 0)),
                int(rtl.get('DSP', 0)),
                int(rtl.get('BRAM', 0)),
                f"{fmax_rtl:.1f}",
                int(rtl.get('exp_cycles', 0)),
                f"{latency_rtl:.2f}" if latency_rtl else '',
                lut_delta,
                f"{latency_delta:.2f}" if latency_delta else '',
                f"{wns_rtl:.3f}" if wns_rtl else ''
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
    parser.add_argument(
        "--mvau-only",
        action="store_true",
        help="Synthesize bare MVAU core only (no wrappers/FIFOs) for pure resource measurement",
    )
    args = parser.parse_args()

    board_cfg = BOARD_CONFIGS[args.board]
    board = board_cfg["board"]
    fpga_part = board_cfg["part"]

    # 7-Series configs (MW, MH, PE, SIMD, WW, AW)
    configs = [
        (32, 18, 1, 1, 4, 4),    # Minimal parallelism (PE=1, SIMD=1)
        (32, 18, 1, 16, 4, 4),   # SIMD only, 4-bit
        (32, 18, 9, 1, 4, 4),    # PE only (PE=9, SIMD=1)
        (32, 18, 9, 16, 2, 2),   # Balanced, 2-bit
        (32, 18, 9, 16, 4, 4),   # Balanced, 4-bit
        (32, 18, 9, 16, 8, 8),   # DSP-based reference (8-bit)
        (32, 18, 9, 32, 4, 4),   # Balanced, SIMD-max, 4-bit
        (32, 18, 18, 16, 4, 4),  # PE-max, 4-bit
    ]


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
    print(f"Synthesis target: {'MVAU core only (no wrappers)' if args.mvau_only else 'Full stitched design (with wrappers/FIFOs)'}")
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
            args.timing_search, args.synth_clk_period_ns, args.mvau_only
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
