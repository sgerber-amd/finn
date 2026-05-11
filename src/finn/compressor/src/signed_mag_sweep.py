#############################################################################
# Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#
# @brief    Sweep signed magnitude compressor configurations and collect LUT counts
#############################################################################

import os
import re
import subprocess
import sys
from pathlib import Path

from .main import generate_compressor
from .passes.compressor_constructor import CompressorConstructor
from .target import Versal
from .utils.shape import Shape


def parse_utilization_report(rpt_path: Path) -> dict:
    """Parse Vivado utilization report and extract LUT counts."""
    result = {"CLB LUTs": 0, "LUT as Logic": 0, "LUT as Memory": 0, "LOOKAHEAD8": 0}
    if not rpt_path.exists():
        return result

    with open(rpt_path) as f:
        content = f.read()

    # Format: | CLB LUTs*  |   58 |     0 | ...
    for line in content.split("\n"):
        if "CLB LUTs" in line or "Slice LUTs" in line:
            match = re.search(r"\|\s*(?:CLB|Slice) LUTs\*?\s*\|\s*(\d+)", line)
            if match:
                result["CLB LUTs"] = int(match.group(1))
        elif "LUT as Logic" in line:
            match = re.search(r"\|\s*LUT as Logic\s*\|\s*(\d+)", line)
            if match:
                result["LUT as Logic"] = int(match.group(1))
        elif "LUT as Memory" in line:
            match = re.search(r"\|\s*LUT as Memory\s*\|\s*(\d+)", line)
            if match:
                result["LUT as Memory"] = int(match.group(1))
        elif "LOOKAHEAD8" in line:
            match = re.search(r"\|\s*LOOKAHEAD8\s*\|\s*(\d+)", line)
            if match:
                result["LOOKAHEAD8"] = int(match.group(1))

    return result


def generate_config(num_inputs: int, mag_width: int, script_dir: Path, gen_dir: Path, hdl_dir: Path):
    """Generate compressor and TCL files for one configuration."""
    target = Versal()
    fpga_part = "xcvc1902-vsva2197-2MP-e-S"

    constructor = CompressorConstructor()
    input_shape, _ = constructor.configure_signed_magnitude_inputs(num_inputs, mag_width)
    sign_bit = constructor.signed_magnitude_sign_bit(num_inputs, mag_width)
    output_width = sign_bit + 1

    name = f"signed_mag_{num_inputs}x{mag_width}"
    output_path = gen_dir / f"{name}.sv"

    generate_compressor(
        target=target,
        shape=Shape([1]),
        name=name,
        comb_depth=None,
        accumulate=False,
        accumulator_width=None,
        gates=[],
        constants=[],
        path=str(output_path),
        test=False,
        signed_magnitude=(num_inputs, mag_width),
    )

    # Generate synthesis TCL
    src = hdl_dir / "signed_mag_synth_template.tcl"
    dst = gen_dir / f"{name}_synth.tcl"
    with open(src) as fsrc:
        with open(dst, "w") as fdst:
            for line in fsrc:
                fdst.write(
                    line.replace("{n}", str(num_inputs))
                    .replace("{m}", str(mag_width))
                    .replace("{part}", fpga_part)
                    .replace("gen/", str(gen_dir) + "/")
                )

    return name


def run_synthesis(name: str, script_dir: Path, gen_dir: Path) -> dict:
    """Run Vivado synthesis and return utilization."""
    tcl_path = gen_dir / f"{name}_synth.tcl"

    # Run Vivado in batch mode
    cmd = ["vivado", "-mode", "batch", "-source", str(tcl_path)]
    print(f"  Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(script_dir),
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )
        if result.returncode != 0:
            print(f"  WARNING: Vivado returned {result.returncode}")
            if result.stderr:
                print(f"  stderr: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        print(f"  ERROR: Synthesis timed out for {name}")
        return {"CLB LUTs": -1, "LUT as Logic": -1, "LUT as Memory": -1}
    except FileNotFoundError:
        print("  ERROR: vivado not found in PATH. Source Vivado environment first.")
        sys.exit(1)

    # Parse utilization report
    rpt_path = script_dir / f"{name}.vivado" / f"{name}.runs" / "synth_1" / f"{name}_utilization_synth.rpt"
    return parse_utilization_report(rpt_path)


def main():
    # Configuration sweep ranges
    num_inputs_range = [2, 4, 8, 16]
    mag_width_range = [4, 8, 16]

    script_dir = Path(__file__).resolve().parent.parent
    gen_dir = script_dir / "gen"
    hdl_dir = script_dir / "hdl"

    gen_dir.mkdir(exist_ok=True)

    results = []

    print("Signed Magnitude Compressor LUT Sweep")
    print("=" * 60)

    for num_inputs in num_inputs_range:
        for mag_width in mag_width_range:
            print(f"\n[{num_inputs}x{mag_width}] Generating...")
            name = generate_config(num_inputs, mag_width, script_dir, gen_dir, hdl_dir)

            print(f"[{num_inputs}x{mag_width}] Synthesizing...")
            util = run_synthesis(name, script_dir, gen_dir)

            results.append({
                "num_inputs": num_inputs,
                "mag_width": mag_width,
                "name": name,
                **util,
            })
            print(f"[{num_inputs}x{mag_width}] LUTs: {util['CLB LUTs']}, L8: {util['LOOKAHEAD8']}")

    # Print summary table
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'N':>4} {'M':>4} {'CLB LUTs':>10} {'Logic':>10} {'LOOKAHEAD8':>12}")
    print("-" * 48)
    for r in results:
        print(f"{r['num_inputs']:>4} {r['mag_width']:>4} {r['CLB LUTs']:>10} {r['LUT as Logic']:>10} {r['LOOKAHEAD8']:>12}")

    # Write CSV
    csv_path = gen_dir / "signed_mag_sweep.csv"
    with open(csv_path, "w") as f:
        f.write("num_inputs,mag_width,clb_luts,lut_logic,lookahead8\n")
        for r in results:
            f.write(f"{r['num_inputs']},{r['mag_width']},{r['CLB LUTs']},{r['LUT as Logic']},{r['LOOKAHEAD8']}\n")
    print(f"\nResults written to: {csv_path}")


if __name__ == "__main__":
    main()
