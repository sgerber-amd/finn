#############################################################################
# Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#
# @brief    Compressor tree constructor with two-pass accumulator handling
#############################################################################

from math import ceil, log2
from typing import List, Tuple, Optional

from ..graph.accumulator import AccumulatorStage
from ..graph.counters.absorption_counter_candidates import (
    GateAbsorptionCounterCandidate,
)
from ..graph.counters.counter_candidates import ConstantOne
from ..graph.final_adder import SignedMagnitudeConverter
from ..graph.nodes import (
    CompressionStage,
    Compressor,
    Counter,
    GateAbsorbedStage,
    InputStage,
    Passthrough,
)
from ..utils.shape import Shape
from .compressor_pipeliner import CompressorPipeliner


class CompressorConstructor:
    @staticmethod
    def signed_magnitude_sign_bit(num_inputs: int, mag_width: int) -> int:
        """Return the sign bit position (0-indexed) for signed magnitude output."""
        return (ceil(log2(num_inputs)) if num_inputs > 1 else 0) + mag_width

    def configure_signed_magnitude_inputs(
        self, num_inputs: int, mag_width: int
    ) -> Tuple[Shape, List[List[str]]]:
        """Configure inputs for signed magnitude addition.

        Optimized sign extension using XNOR + constant:
        - Columns 0 to M-1: magnitude XOR sign (absorbed)
        - Column M: XNOR(0, sign) = ~sign, sum gives (N - popcount)
        - Constant correction makes it -popcount for proper sign extension

        This uses only M+1 columns instead of sign_bit+1 columns!
        """
        shape_list = []
        gates = []

        for col in range(mag_width):
            if col == 0:
                # Column 0: XOR(mag[0], sign) + AND(sign, sign) for +1 injection
                col_height = num_inputs * 2
                col_gates = ["6"] * num_inputs + ["8"] * num_inputs
            else:
                # Columns 1 to M-1: XOR(mag[col], sign)
                col_height = num_inputs
                col_gates = ["6"] * num_inputs
            shape_list.append(col_height)
            gates.append(col_gates)

        # Column M: XNOR(0, sign) = ~sign for optimized sign extension
        # Gate "9" = XNOR (truth table 1001)
        shape_list.append(num_inputs)
        gates.append(["9"] * num_inputs)

        return Shape(shape_list), gates

    def signed_magnitude_constants(self, num_inputs: int, mag_width: int) -> Tuple[int, ...]:
        """Return constants for signed magnitude sign extension correction.

        The XNOR approach gives sum of ~signs = N - popcount.
        We need to add (2^k - N) to get -popcount, where k = ceil(log2(N)) + 1.

        For power-of-2 N, this is a single bit at column M + k - 1.
        """
        k = (ceil(log2(num_inputs)) if num_inputs > 1 else 1) + 1
        correction = (1 << k) - num_inputs

        # Convert correction to list of constant bits starting at column M
        constants = [0] * mag_width
        for bit in range(k):
            if correction & (1 << bit):
                constants.append(1)
            else:
                constants.append(0)

        return tuple(constants)

    def adjust_compression_goal_for_constants(self, compression_goal, constants):
        # Subtract constants, but never go below 2 (minimum achievable by compressor)
        return lambda x: max(2, compression_goal(x) - (constants[x] if x < len(constants) else 0))

    def get_compression_goal(self, final_adder, accumulate, constants):
        # Two-pass strategy for accumulate: compress to goal, add constants, then post-check
        compression_goal = final_adder.compression_goal
        return self.adjust_compression_goal_for_constants(compression_goal, constants)

    def add_constants_to_stage(self, s: CompressionStage, constants):
        """Add constant bits to the compression stage."""
        for idx, el in enumerate(constants):
            if el:
                c = ConstantOne()
                s.append_counter(c, idx)

    def __call__(
        self,
        counter_candidates,
        absorption_counter_candidates,
        final_adder,
        input_shape: Shape,
        name: str,
        comb_depth: int = None,
        accumulate=False,
        accumulator_width: int = None,
        constants: Tuple[bool] = tuple(),
        gates: Tuple[Tuple[str]] = tuple(),
        enable: bool = False,
        low_latency_accu: bool = False,
        signed_magnitude: Optional[Tuple[int, int]] = None,
    ) -> Compressor:
        # Handle signed magnitude mode: override shape and gates
        if signed_magnitude is not None:
            num_inputs, mag_width = signed_magnitude
            input_shape, gates = self.configure_signed_magnitude_inputs(num_inputs, mag_width)

        compression_goal = self.get_compression_goal(final_adder, accumulate, constants)

        c = Compressor(name)
        c.stages.append(InputStage(input_shape, gates))

        if gates:
            s = self.construct_absorption_stage(
                c.stages[-1].output_shape, gates, absorption_counter_candidates
            )
            c.stages[-1].connect_to(s)
            c.stages.append(s)

        # CRITICAL: This loop can hang if compression_goal is unreachable
        # add_compression_stage cannot compress height-1 or height-2 columns (requires >= 3)
        # Therefore compression_goal must be achievable given this constraint
        # See get_compression_goal() for how this is ensured in accumulate configurations
        while not self.compression_goal_reached(c.stages[-1].output_shape, compression_goal):
            self.add_compression_stage(c, compression_goal, counter_candidates)

        # Add constants to the graph.
        if not isinstance(c.stages[-1], CompressionStage) and constants:
            self.add_compression_stage(c, compression_goal, counter_candidates)
        self.add_constants_to_stage(c.stages[-1], constants)

        # After constants, check if we need additional compression for accumulator mode.
        # The ternary adder receives: compressor_output + feedback (height 1).
        # If any column exceeds final_adder capacity, we need more compression.
        if accumulate:

            def post_const_goal(x):
                # Leave room for feedback (height 1) within ternary adder capacity
                return max(2, final_adder.compression_goal(x) - 1)

            while not self.compression_goal_reached(c.stages[-1].output_shape, post_const_goal):
                self.add_compression_stage(c, post_const_goal, counter_candidates)

        if comb_depth:
            pipeliner = CompressorPipeliner()
            pipeline_stages = pipeliner.pipeline(c, comb_depth)
        else:
            pipeline_stages = 0

        if accumulate:
            from ..graph.final_adder import BinaryAdder

            acc = AccumulatorStage(
                c.stages[-1].output_shape,
                final_adder,
                pipeline_stages,
                accumulator_width=accumulator_width,
                enable=enable,
                low_latency=low_latency_accu,
                binary_adder=BinaryAdder if low_latency_accu else None,
            )
            c.stages.append(acc)
        elif max(c.stages[-1].output_shape) > 1:
            final_stage = CompressionStage()
            # Try to create pipelined final adder for non-accumulator mode
            try:
                fa = final_adder(c.stages[-1].output_shape, pipelined=True)
            except TypeError:
                # Final adder doesn't support pipelining
                fa = final_adder(c.stages[-1].output_shape)
            final_stage.append_counter(fa, 0)
            c.stages.append(final_stage)

        # if signed_magnitude is not None and not accumulate:
        #     num_inputs, mag_width = signed_magnitude
        #     sign_bit = self.signed_magnitude_sign_bit(num_inputs, mag_width)
        #     output_width = len(c.stages[-1].output_shape)
        #     converter_stage = CompressionStage()
        #     converter = SignedMagnitudeConverter(output_width, sign_bit=sign_bit)
        #     converter_stage.append_counter(converter, 0)
        #     c.stages.append(converter_stage)

        for s_p, s_n in zip(c.stages, c.stages[1:]):
            s_p.connect_to(s_n)
        return c

    def add_compression_stage(self, compressor: Compressor, compression_goal, counter_candidates):
        """Add a compression stage. Cannot compress columns with height < 3 (Full Adder = 3:2)."""
        new_stage = CompressionStage()
        stage_inputs = compressor.stages[-1].output_shape
        stage_outputs = Shape()

        i = 0
        while i < max(len(stage_inputs), len(stage_outputs)):

            def cur_output_height():
                return (stage_inputs + stage_outputs)[i]

            def cur_input_height():
                return stage_inputs[i] if len(stage_inputs) > i else 0

            while cur_input_height() >= 3 and cur_output_height() > compression_goal(i):
                counter = self.schedule_counter(
                    stage_inputs[i:],
                    stage_outputs[i:],
                    lambda x: compression_goal(x + i),
                    counter_candidates,
                )
                stage_inputs = stage_inputs - (counter.input_shape << i)
                stage_outputs = stage_outputs + (counter.output_shape << i)
                new_stage.append_counter(counter, i)
            i += 1

        # pass through all leftover inputs:
        for i in range(len(stage_inputs)):
            for j in range(stage_inputs[i]):
                new_stage.append_counter(Passthrough(), i)

        compressor.stages.append(new_stage)

    def schedule_counter(
        self, stage_inputs, stage_outputs, compression_goal, counter_candidates
    ) -> Counter:
        counters = []
        for counter_candid in counter_candidates:
            counter = counter_candid.extend_to_fit(stage_inputs, stage_outputs, compression_goal)
            counters.append(counter)

        try:
            return max(
                (c for c in counters if c is not None), key=lambda x: (x.efficiency, x.strength)
            )
        except ValueError:
            raise ValueError(
                f"Could not schedule counter for input shape"
                f"{stage_inputs}; output shape {stage_outputs}; "
                "compression goal {compression_goal(0)}"
            )

    def compression_goal_reached(self, shape, compression_goal):
        return all([col <= compression_goal(idx) for idx, col in enumerate(shape)])

    def get_best_inlined_counter(self, input_shape, gates, absorption_counters):
        candidates = []
        for counter in absorption_counters:
            candidate = counter.extend_to_fit(input_shape, gates)
            if candidate:
                candidates.append(candidate)
        return max(candidates, key=lambda x: (x.efficiency, x.strength))

    def construct_absorption_stage(
        self,
        input_shape: Shape,
        gates: List[str],
        absorption_counters: GateAbsorptionCounterCandidate,
    ):
        s = GateAbsorbedStage()
        cur_shape = input_shape
        cur_gates = gates[:]
        for idx in range(len(input_shape)):
            while cur_shape[idx] > 0:
                best = self.get_best_inlined_counter(
                    cur_shape[idx:], cur_gates[idx:], absorption_counters
                )
                cur_shape = cur_shape - (best.input_shape << idx)
                for i in range(len(cur_shape)):
                    new = list(reversed(list(reversed(cur_gates[i]))[: cur_shape[i]]))
                    cur_gates[i] = new
                s.append_counter(best, idx)
        return s
