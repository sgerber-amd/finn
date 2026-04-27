#!/usr/bin/env python3
"""
Generate a dotp compressor netlist (PE-parallel, no MVAU/AXI wrappers).

Outputs:
  - comp_<sig>.sv      : Generated compressor core
  - dotp_comp.sv       : PE-parallel wrapper (expanded template)
  - mul_comp_map.sv    : Partial product broadcast (copied from hdl/)

Usage:
  python generate_dotp_netlist.py \
      --vector-length 64 --simd 8 --pe 4 \
      --ww 4 --aw 4 --signed-act \
      --target xc7z020clg400-1 \
      -o gen/
"""

import argparse
import math
import os
import shutil
import sys

from finn.compressor.src.dotp_finn import generate_dotp_comp
from finn.compressor.src.target import resolve_target

# Add src/ to path for imports when running outside installed environment
script_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(script_dir, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)


def compute_accu_width(vector_length, simd, ww, aw, signed_act):
    """Compute minimum accumulator width for the given config."""
    num_acc_cycles = vector_length // simd

    # Max magnitude of single product
    if signed_act:
        # Signed × Signed: range is roughly -(2^(ww-1)) * (2^(aw-1)) to +(2^(ww-1)-1) * (2^(aw-1)-1)
        max_product = (2 ** (ww - 1)) * (2 ** (aw - 1))
    else:
        # Signed × Unsigned: range is -(2^(ww-1)) * (2^aw - 1) to +(2^(ww-1)-1) * (2^aw - 1)
        max_product = (2 ** (ww - 1)) * (2**aw - 1)

    # Max accumulated value (worst case: all products at max magnitude, same sign)
    max_accum = num_acc_cycles * simd * max_product

    # Bits needed + 1 for sign
    if max_accum > 0:
        accu_width = math.ceil(math.log2(max_accum + 1)) + 1
    else:
        accu_width = ww + aw + math.ceil(math.log2(vector_length))

    # Ensure minimum width
    accu_width = max(accu_width, ww + aw)

    return accu_width


def main():
    parser = argparse.ArgumentParser(
        description="Generate a dotp compressor netlist (PE-parallel, no MVAU wrappers)"
    )
    parser.add_argument(
        "--vector-length",
        type=int,
        required=True,
        help="Dot product vector length (must be divisible by SIMD)",
    )
    parser.add_argument(
        "--simd", type=int, required=True, help="SIMD parallelism (products per cycle)"
    )
    parser.add_argument(
        "--pe", type=int, required=True, help="PE parallelism (parallel dot products)"
    )
    parser.add_argument(
        "--ww", type=int, required=True, help="Weight bit width (weights are always signed)"
    )
    parser.add_argument("--aw", type=int, required=True, help="Activation bit width")
    parser.add_argument(
        "--signed-act", action="store_true", help="Activations are signed (default: unsigned)"
    )
    parser.add_argument(
        "--target",
        type=str,
        default="xc7z020clg400-1",
        help="FPGA part (e.g., xc7z020clg400-1, xcvc1902-...)",
    )
    parser.add_argument(
        "--accu-width",
        type=int,
        default=None,
        help="Override accumulator width (auto-computed if not set)",
    )
    parser.add_argument(
        "--pipeline-depth", type=int, default=None, help="Pipeline depth (auto-selected if not set)"
    )
    parser.add_argument("-o", "--output-dir", type=str, default="gen/", help="Output directory")
    args = parser.parse_args()

    # Validate
    if args.vector_length % args.simd != 0:
        raise ValueError(
            f"vector_length ({args.vector_length}) must be divisible by simd ({args.simd})"
        )
    if args.ww > 4 or args.aw > 4:
        print(
            f"WARNING: Compressor path is designed for WW <= 4 and AW <= 4. "
            f"Got WW={args.ww}, AW={args.aw}. DSP path may be more efficient."
        )

    # Compute accumulator width if not specified
    if args.accu_width:
        accu_width = args.accu_width
    else:
        accu_width = compute_accu_width(
            args.vector_length, args.simd, args.ww, args.aw, args.signed_act
        )
        print(f"Auto-computed accu_width: {accu_width}")

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Generate compressor core + dotp_comp.sv
    result = generate_dotp_comp(
        fpgapart=args.target,
        simd=args.simd,
        ww=args.ww,
        aw=args.aw,
        accu_width=accu_width,
        signed_act=args.signed_act,
        output_dir=args.output_dir,
    )

    # Copy mul_comp_map.sv (static file)
    compressor_hdl_dir = os.path.join(script_dir, "src", "finn", "compressor", "hdl")
    mul_comp_map_src = os.path.join(compressor_hdl_dir, "mul_comp_map.sv")
    mul_comp_map_dst = os.path.join(args.output_dir, "mul_comp_map.sv")
    shutil.copy(mul_comp_map_src, mul_comp_map_dst)
    result["files"].append(mul_comp_map_dst)

    # Summary
    num_cycles = args.vector_length // args.simd
    target = resolve_target(args.target)

    print()
    print("=" * 60)
    print("DOTP NETLIST GENERATED")
    print("=" * 60)
    print(f"  Target:           {target.__class__.__name__} ({args.target})")
    print(f"  Vector length:    {args.vector_length}")
    print(f"  SIMD:             {args.simd}")
    print(f"  PE:               {args.pe}")
    print(f"  Weight bits:      {args.ww} (signed)")
    print(f"  Activation bits:  {args.aw} ({'signed' if args.signed_act else 'unsigned'})")
    print(f"  Accumulator:      {accu_width} bits")
    print(f"  Cycles/vector:    {num_cycles}")
    print(f"  Pipeline depth:   {result['comp_delay']}")
    print(f"  Compressor:       {result['comp_name']}")
    print()
    print("Generated files:")
    for f in result["files"]:
        print(f"  {f}")
    print()
    print("Note: PE parameter is passed to dotp_comp module at instantiation.")
    print("The generated netlist supports any PE value; set PE when instantiating.")


if __name__ == "__main__":
    main()
