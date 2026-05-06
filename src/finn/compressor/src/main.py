#############################################################################
# Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#
# @brief    Main compressor tree generation entry point
#############################################################################

import argparse
import time
from typing import List, Optional

from .passes.compressor_constructor import CompressorConstructor
from .passes.cost_estimator import CostEstimator
from .passes.emitter import VerilogGenerator
from .passes.io_annotator import IOAnnotator
from .passes.lut_placer import LUTPlacer
from .passes.printer import CompressorPrinter
from .passes.wire_inserter import WireInserter
from .target import SevenSeries, Target, UltraScale, Versal
from .tests.test_gen import generate_test
from .tests.tester import tester
from .utils.shape import Shape


def parse_cli():
    parser = argparse.ArgumentParser(
        prog="Compressor Generator", description="Generate a Compressor Tree for a given input."
    )
    parser.add_argument(
        "-o", "--output", default="../gen/out.sv", help="Path to store the compressor at."
    )
    parser.add_argument("-s", "--shape", required=True, help="Input shape.")
    parser.add_argument("-a", "--accumulate", action="store_true", help="Enable accumulation.")
    parser.add_argument(
        "-w", "--accumulator_width", help="Accumulator width [default: Reduced input shape]."
    )
    parser.add_argument(
        "-g",
        "--gates",
        default=None,
        help="Inline 2-input gates into the compressor. LSB is left." "Example: 8,3",
    )
    parser.add_argument(
        "-t",
        "--target",
        default="Versal",
        help="Target FPGA generation.",
        choices=["Versal", "7-Series", "UltraScale"],
    )
    parser.add_argument(
        "--test", action="store_true", help="Test the generated compressor using Vivado XSim."
    )
    parser.add_argument(
        "-n", "--name", default="comp", help="Name of the generated Systemverilog module."
    )
    parser.add_argument(
        "-p",
        "--pipeline_every",
        default=None,
        help="Insert Pipeline registers every n stages. Default: " "Purely combinatorial.",
    )
    parser.add_argument(
        "-c", "--constant", default=[], help="Add a constant binary " "number input. Example: 1011"
    )
    parser.add_argument(
        "--low-latency-accu",
        action="store_true",
        help="Use low-latency accumulator (pipelined quad + binary adder). "
        "Faster Fmax, +1 cycle latency.",
    )
    parser.add_argument(
        "--hw-efficient",
        action="store_true",
        help="Use LUT-efficient VersalAtom222 cascade (O52->I4 ripple carry). "
        "Fewer LUTs but slower than LOOKAHEAD8. Versal only, requires --accumulate.",
    )
    args = parser.parse_args()

    # Validate flag combinations
    if args.hw_efficient:
        if args.target != "Versal":
            parser.error("--hw-efficient is only supported on Versal (VersalAtom222 primitive)")

    try:
        shape = Shape(int(el) for el in args.shape.split(","))
    except (ValueError, TypeError):
        print("Improperly defined shape.")
        exit(-1)

    gates = []
    if args.gates:
        assert len(args.gates) == sum(shape), "Length of shape and gate specification do not match."
        gates_lin = list(args.gates)
        for col in shape:
            gates_col = []
            for _ in range(col):
                gates_col.append(gates_lin.pop(0))
            gates.append(gates_col)

    constants = []
    for char in args.constant:
        try:
            constants.append(int(char, 2))
        except ValueError:
            print("Improperly defined constant.")
            exit(-1)
    if args.target == "Versal":
        target = Versal()
    elif args.target == "7-Series":
        target = SevenSeries()
    elif args.target == "UltraScale":
        target = UltraScale()
    else:
        raise ValueError("Target not currently supported.")

    generate_compressor(
        target,
        shape,
        args.name,
        int(args.pipeline_every) if args.pipeline_every else None,
        args.accumulate,
        int(args.accumulator_width) if args.accumulator_width else None,
        gates,
        constants,
        args.output,
        args.test,
        low_latency_accu=args.low_latency_accu,
        hw_efficient=args.hw_efficient,
    )


def generate_compressor(
    target: Target,
    shape: Shape,
    name: str,
    comb_depth: Optional[int],
    accumulate: bool,
    accumulator_width: int,
    gates: List[List[str]],
    constants: List[int],  # Each element is a binary numer digit.
    path: str,
    test: bool,
    enable: bool = False,
    low_latency_accu: bool = False,
    hw_efficient: bool = False,
):
    start_time = time.time()
    constructor = CompressorConstructor()

    # Select counter candidates based on hw_efficient flag:
    # - hw_efficient=True:  VersalAtom222 cascade (O52->I4 ripple, LUT-efficient)
    # - hw_efficient=False: VersalAtomCascade with LOOKAHEAD8 (fast carry)
    #
    # The low_latency_accu flag is orthogonal and controls accumulator pipelining.
    print(f"DEBUG main.py: hw_efficient={hw_efficient}")
    if hw_efficient:
        # Validation: hw_efficient requires Versal target
        if not hasattr(target, "counter_candidates_accumulator"):
            raise ValueError("--hw-efficient requires Versal target with counter_candidates_accumulator")
        counter_candidates = target.counter_candidates_accumulator
        print(f"DEBUG: Using VersalAtom222 path (counter_candidates_accumulator)")
    else:
        counter_candidates = target.counter_candidates
        print(f"DEBUG: Using LOOKAHEAD8 path (counter_candidates)")

    c = constructor(
        counter_candidates,
        target.absorbing_counter_candidates,
        target.final_adder,
        shape,
        name,
        comb_depth=comb_depth,
        accumulate=accumulate,
        accumulator_width=accumulator_width,
        constants=constants,
        gates=gates,
        enable=enable,
        low_latency_accu=low_latency_accu,
    )

    placer = LUTPlacer()
    c.accept(placer)

    wire_inserter = WireInserter()
    c.accept(wire_inserter)

    annotator = IOAnnotator()
    c.accept(annotator)

    cost = CostEstimator()
    c.accept(cost)

    emitter = VerilogGenerator()
    c.accept(emitter)
    with open(path, "w") as f:
        withprefix = (
            f"""// Adder generated by the Python Compressor Generator
// Input shape: {c.input_shape}; Output Shape: {c.output_shape}
// Pipeline stages: {c.delay}
// Target Generation: {target.__class__.__name__}
// Approximate LUTs: {int(cost.luts+0.5)}
// Accumulation: {"yes" if accumulate else "no"} {f"of width {accumulator_width}"
                                                  if accumulator_width else ""}
// Enable mode: {"yes (init values set on accumulator registers)" if enable else "no"}
// Gates: {gates if gates else "None"}
        """
            + emitter.emitter.output
        )
        f.write(withprefix)

    end_time = time.time()
    print("--%s seconds" % (start_time - end_time))

    c.accept(CompressorPrinter())

    if test:
        constant = int("".join(str(c) for c in constants), 2) if constants else 0
        test = generate_test(shape, "comp", c.delay, gates, accumulate, accumulator_width, constant)
        with open("../gen/test.sv", "w") as f:
            f.write(test)
        tester("../gen/test.sv", path)

    return c.delay


if __name__ == "__main__":
    parse_cli()
