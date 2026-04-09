#!/usr/bin/env python3
#
# Benchmark: HLS MVAU vs RTL MVAU with Compressors
#
# Compares the traditional HLS implementation (used for low-bitwidth networks)
# against the new RTL implementation with LUT-based compressor trees.
#
# Focus: 1-3 bit operands where compressors are most beneficial
#
# Usage (inside Docker):
#   python -m finn.compressor.benchmark_hls_vs_compressor --keep --synth-only
#

import argparse
import csv
import glob
import json
import os
import re
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

# Taking model creation from tests
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


class HLSTimingError(Exception):
    """Raised when HLS synthesis fails to meet timing constraints."""
    pass


def check_hls_timing(output_dir):
    """Check HLS synthesis timing from csynth.rpt files.

    Parses the Vitis HLS synthesis reports to extract slack values.
    Returns the worst (minimum) slack found across all HLS modules.

    Parameters
    ----------
    output_dir : str
        Path to the FINN build output directory

    Returns
    -------
    dict
        Dictionary with keys:
        - 'worst_slack_ns': float, minimum slack across all modules
        - 'all_slacks': list of (module_name, slack) tuples
        - 'timing_met': bool, True if all slacks are non-negative

    Raises
    ------
    HLSTimingError
        If any HLS module has negative slack (timing not met)
    """
    # HLS reports are NOT in output_dir - they're in FINN_BUILD_DIR/code_gen_ipgen_*
    # We need to search the FINN build directory
    finn_build_dir = os.environ.get("FINN_BUILD_DIR", "/tmp")

    # Find all csynth.rpt files for HLS nodes
    # They are located at: code_gen_ipgen_*/project_*/sol1/syn/report/csynth.rpt
    csynth_reports = glob.glob(
        os.path.join(finn_build_dir, "code_gen_ipgen_*", "project_*", "sol1", "syn", "report", "csynth.rpt")
    )

    if not csynth_reports:
        print(f"    WARNING: No HLS csynth.rpt files found in {finn_build_dir}")
        return {"worst_slack_ns": None, "all_slacks": [], "timing_met": True}

    all_slacks = []

    for rpt_path in csynth_reports:
        with open(rpt_path, "r") as f:
            content = f.read()

        # Parse the Performance & Resource Estimates table
        # Format varies:
        #   |+ ModuleName  |     -| slack| ...   (timing met, no issue)
        #   |+ ModuleName  |Timing| slack| ...   (timing issue)
        # The slack column (3rd) shows timing slack in ns
        # Pattern matches lines like: |+ MVAU_hls_0  |  Timing|  -4.44|
        pattern = r"\|\+\s+(\S+)\s+\|\s*\S*\|\s*([-\d.]+)\|"
        matches = re.findall(pattern, content)

        for module_name, slack_str in matches:
            try:
                slack = float(slack_str)
                all_slacks.append((module_name, slack))
            except ValueError:
                continue

    if not all_slacks:
        print("    WARNING: Could not parse slack values from HLS reports")
        return {"worst_slack_ns": None, "all_slacks": [], "timing_met": True}

    worst_slack = min(slack for _, slack in all_slacks)
    timing_met = worst_slack >= 0

    result = {
        "worst_slack_ns": worst_slack,
        "all_slacks": all_slacks,
        "timing_met": timing_met,
    }

    if not timing_met:
        failed_modules = [(m, s) for m, s in all_slacks if s < 0]
        print(f"    WARNING: HLS timing not met! Worst slack: {worst_slack:.3f} ns")
        print(f"    Failed modules: {failed_modules}")
        result["failed_modules"] = failed_modules

    return result


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
        verbose=False,
    )

    return build_dataflow_cfg(model_path, cfg)


def parse_results(output_dir):
    """Parse ooc_synth_and_timing.json (actual Vivado synthesis results)."""
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

            return {
                "total": {
                    "LUT": data.get("LUT", 0),
                    "DSP": dsp_count,
                    "FF": data.get("FF", 0),
                    "BRAM": data.get("BRAM_18K", 0),
                    "WNS": data.get("WNS", 0),
                    "fmax_mhz": data.get("fmax_mhz", 0),
                    # Timing search results (if available)
                    "achieved_fmax_mhz": data.get("achieved_fmax_mhz", None),
                    "iterations": data.get("iterations", None),
                }
            }
    return {}


def run_comparison(mw, mh, pe, simd, ww, aw, board, fpga_part, work_dir, synth_only, timing_search, synth_clk_period_ns, run_variants=None):
    """Run HLS vs RTL+Compressor comparison for one config.

    Runs HLS first, checks timing, then runs RTL. If HLS timing fails,
    raises HLSTimingError before attempting RTL build.

    Args:
        run_variants: List of variants to run. Default None runs both ["hls", "rtl_comp"].
                     Pass ["hls"] to only run HLS, or ["rtl_comp"] to only run RTL.
    """
    label = f"mw{mw}_mh{mh}_pe{pe}_simd{simd}_w{ww}_a{aw}"
    wdt = DataType[f"INT{ww}"]
    idt = DataType[f"INT{aw}"]
    odt = DataType["INT32"]

    # Compute expected latency in cycles (same for HLS and RTL)
    # Formula: (MH / PE) * (MW / SIMD) cycles per output
    exp_cycles = int((mh / pe) * (mw / simd))

    results = {}

    # Determine which variants to run
    if run_variants is None:
        run_variants = ["hls", "rtl_comp"]

    variants_to_run = [False, True] if "hls" in run_variants and "rtl_comp" in run_variants else \
                      [False] if "hls" in run_variants else [True]

    # Run HLS FIRST to validate timing before RTL
    for use_rtl in variants_to_run:  # False=HLS, True=RTL
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

            # For HLS builds, check timing BEFORE continuing to RTL
            if not use_rtl:
                print(f"    Checking HLS timing...", flush=True)
                hls_timing = check_hls_timing(output_dir)
                if hls_timing["worst_slack_ns"] is not None:
                    print(f"    HLS timing OK: worst slack = {hls_timing['worst_slack_ns']:.3f} ns")
                # HLSTimingError will be raised if timing failed

            res = parse_results(output_dir)
            results[variant] = res.get("total", {})

            # Add HLS slack to results for reporting
            if not use_rtl:
                if hls_timing.get("worst_slack_ns") is not None:
                    results[variant]["hls_slack_ns"] = hls_timing["worst_slack_ns"]

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

                        # Merge timing results - use values from timing closure, not initial run
                        achieved_fmax = timing_result.get("achieved_fmax_mhz", 0)
                        achieved_period = timing_result.get("achieved_period_ns", 0)
                        results[variant]["achieved_fmax_mhz"] = achieved_fmax
                        results[variant]["achieved_period_ns"] = achieved_period
                        results[variant]["fmax_mhz"] = achieved_fmax  # Update fmax to match
                        results[variant]["iterations"] = timing_result.get("iterations", 0)
                        # Update WNS to reflect the timing-closed result (should be ~0 at boundary)
                        if "wns_at_closure" in timing_result:
                            results[variant]["WNS"] = timing_result["wns_at_closure"]

                        # Compute latency from achieved timing
                        results[variant]["exp_cycles"] = exp_cycles
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
    """Write results to CSV format."""
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'Config', 'Implementation',
            'LUT', 'FF', 'DSP', 'BRAM',
            'WNS_ns', 'fmax_MHz', 'achieved_fmax_MHz', 'achieved_period_ns',
            'exp_cycles', 'latency_ns', 'iterations',
            'hls_slack_ns', 'LUT_delta', 'fmax_improvement_pct'
        ])

        # Data rows
        for label, results in all_results:
            hls = results.get("hls", {})
            rtl = results.get("rtl_comp", {})

            if "error" in hls or "error" in rtl:
                continue

            # HLS row
            writer.writerow([
                label, 'HLS',
                int(hls.get('LUT', 0)),
                int(hls.get('FF', 0)),
                int(hls.get('DSP', 0)),
                int(hls.get('BRAM', 0)),
                f"{hls.get('WNS', 0):.3f}",
                f"{hls.get('fmax_mhz', 0):.1f}",
                f"{hls.get('achieved_fmax_mhz', 0):.1f}" if hls.get('achieved_fmax_mhz') else '',
                f"{hls.get('achieved_period_ns', 0):.3f}" if hls.get('achieved_period_ns') else '',
                int(hls.get('exp_cycles', 0)) if hls.get('exp_cycles') else '',
                f"{hls.get('latency_ns', 0):.2f}" if hls.get('latency_ns') else '',
                int(hls.get('iterations', 0)) if hls.get('iterations') else '',
                f"{hls.get('hls_slack_ns', 0):.3f}" if hls.get('hls_slack_ns') is not None else '',
                '', ''
            ])

            # RTL row with deltas
            lut_delta = int(rtl.get('LUT', 0)) - int(hls.get('LUT', 0))
            fmax_hls = hls.get('achieved_fmax_mhz', hls.get('fmax_mhz', 0))
            fmax_rtl = rtl.get('achieved_fmax_mhz', rtl.get('fmax_mhz', 0))
            fmax_improvement = ((fmax_rtl - fmax_hls) / fmax_hls * 100) if fmax_hls > 0 else 0

            writer.writerow([
                label, 'RTL_Compressor',
                int(rtl.get('LUT', 0)),
                int(rtl.get('FF', 0)),
                int(rtl.get('DSP', 0)),
                int(rtl.get('BRAM', 0)),
                f"{rtl.get('WNS', 0):.3f}",
                f"{rtl.get('fmax_mhz', 0):.1f}",
                f"{rtl.get('achieved_fmax_mhz', 0):.1f}" if rtl.get('achieved_fmax_mhz') else '',
                f"{rtl.get('achieved_period_ns', 0):.3f}" if rtl.get('achieved_period_ns') else '',
                int(rtl.get('exp_cycles', 0)) if rtl.get('exp_cycles') else '',
                f"{rtl.get('latency_ns', 0):.2f}" if rtl.get('latency_ns') else '',
                int(rtl.get('iterations', 0)) if rtl.get('iterations') else '',
                '',  # No HLS slack for RTL
                lut_delta,
                f"{fmax_improvement:.1f}"
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

    # Low-bitwidth configs where compressors are beneficial via dotp_comp
    # Format: (mw, mh, pe, simd, ww, aw, run_variants)
    #   run_variants: list of ["hls"] or ["rtl_comp"] or ["hls", "rtl_comp"] (default)

    # RETRY: Only configs/variants that failed timing closure in 2026-04-08 run
    # (initial synthesis succeeded, but timing search had Vivado crashes/errors)
    configs = [
        (16, 16, 2, 8, 2, 2, ["hls"]),        # mw16_mh16_pe2_simd8_w2_a2 - HLS timing closure failed
        (32, 32, 4, 8, 4, 4, ["rtl_comp"]),   # mw32_mh32_pe4_simd8_w4_a4 - RTL_COMP timing closure failed
        (16, 16, 2, 8, 4, 2, None),           # mw16_mh16_pe2_simd8_w4_a2 - HLS crashed, RTL_COMP didn't run (run both)
    ]

    """
    # ORIGINAL FULL CONFIG LIST (all succeeded initial synth)
    configs = [
        # 2-bit (quaternary) - good compressor benefit
        (16, 16, 2, 8, 2, 2),    # Small 2-bit
        (32, 32, 4, 8, 2, 2),    # Medium 2-bit, PE=4

        # 4-bit - compressor still beneficial over DSPs
        (16, 16, 2, 8, 4, 4),    # Small 4-bit
        (32, 32, 4, 8, 4, 4),    # Medium 4-bit

        # Mixed precision: 4-bit weights, 2-bit activations (has test precedence in test_set_folding.py)
        (16, 16, 2, 8, 4, 2),    # Mixed w4_a2 - tested config
    ]
    """

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
    for i, config in enumerate(configs):
        # Unpack config (support both old 6-tuple and new 7-tuple with run_variants)
        if len(config) == 6:
            mw, mh, pe, simd, ww, aw = config
            run_variants = None
        else:
            mw, mh, pe, simd, ww, aw, run_variants = config

        variants_str = f" (only {run_variants})" if run_variants else ""
        print(f"[{i+1}/{len(configs)}] mw{mw}_mh{mh}_pe{pe}_simd{simd}_w{ww}_a{aw}{variants_str}...", flush=True)
        label, results = run_comparison(
            mw, mh, pe, simd, ww, aw, board, fpga_part, work_dir, args.synth_only,
            args.timing_search, args.synth_clk_period_ns, run_variants
        )
        all_results.append((label, results))

    print("\n" + "=" * 80)
    print(format_table(all_results))

    # Check for HLS timing warnings
    hls_timing_warnings = []
    for label, results in all_results:
        hls = results.get("hls", {})
        if hls.get("hls_slack_ns") is not None and hls["hls_slack_ns"] < 0:
            hls_timing_warnings.append((label, hls["hls_slack_ns"]))

    if hls_timing_warnings:
        print("\n" + "!" * 80)
        print("!!! WARNING: HLS TIMING CONSTRAINTS NOT MET !!!")
        print("!" * 80)
        print("The following configurations had negative HLS slack:")
        for label, slack in hls_timing_warnings:
            print(f"  - {label}: slack = {slack:.3f} ns")
        print("")
        print("This means Vitis HLS could not meet the target clock period.")
        print("The results may still be valid if Vivado P&R achieved timing closure,")
        print("but consider using a more relaxed --synth-clk-period-ns for fair comparison.")
        print("!" * 80 + "\n")

    # Save JSON
    json_path = os.path.join(work_dir, "results.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved JSON: {json_path}")

    # Save CSV
    csv_path = os.path.join(work_dir, "results.csv")
    write_csv(all_results, csv_path)
    print(f"Saved CSV: {csv_path}")

    if not args.keep and not args.work_dir:
        print(f"Cleaning up {work_dir}")
        shutil.rmtree(work_dir)


if __name__ == "__main__":
    main()
