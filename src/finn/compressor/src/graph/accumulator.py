#############################################################################
# Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#
# @brief    Accumulator stage implementation for compressor
#############################################################################

from collections.abc import Iterable

from .nodes import Bitmatrix, Constant, Logic, Shape, Stage, Wire


class AccumulatorStage(Stage):
    def __init__(
        self,
        shape: Shape,
        final_adder,
        preceeding_pipeline_stages,
        accumulator_width=None,
        enable=False,
        low_latency=False,
        binary_adder=None,
    ):
        super().__init__()
        self.input_shape = shape
        self.output_shape = Shape([1 for _ in range(self.get_accumulator_width(accumulator_width))])
        self.instances = []
        self.input_wires = Bitmatrix(shape)
        self.output_wires = Bitmatrix(self.output_shape)
        self.accumulator_width = self.get_accumulator_width(accumulator_width)
        self.final_adder_gen = final_adder
        self.preceeding_pipeline_stages = preceeding_pipeline_stages
        self.enable = enable
        self.low_latency = low_latency
        self.binary_adder_gen = binary_adder

        if low_latency:
            self._build_low_latency()
        else:
            self._build_normal()

    def _build_normal(self):
        acc_input_shape = self.input_shape + self.output_shape
        final_adder = self.final_adder_gen(acc_input_shape)

        en_neg = Wire(desired_name="en_neg")
        en_neg.set_to_module_input()
        rst = Wire(desired_name="rst")
        rst.set_to_module_input()
        self.instances.append(en_neg)
        self.instances.append(rst)

        # Optional clock enable signal (for finnlib integration)
        en_wire = None
        if self.enable:
            en_wire = Wire(desired_name="en")
            en_wire.set_to_module_input()
            self.instances.append(en_wire)

        # Create shifted enable and reset signal.
        # init=1 on rst delay chain: when enable mode is active, en-gating
        # prevents these registers from capturing the initial rst=1 pulse if
        # en=0 during global reset.  Initialising to 1 ensures the accumulator
        # feedback is properly zeroed from power-up.  In the current finn(lib)
        # integration en is hardwired to '1 making this technically redundant,
        # but the FPGA INIT attribute is free and keeps the design robust
        # against future uses where en may be gated.
        rst_del = self.delay_signal(
            rst, self.preceeding_pipeline_stages + 1, en=en_wire, init=1 if self.enable else None
        )
        en_neg_del = self.delay_signal(en_neg, self.preceeding_pipeline_stages, en=en_wire)

        # Connect inputs to final adder
        loop = self.delay_signal(
            final_adder.output_wires, cycles=1, rst=rst_del, en=en_wire, init=0
        )
        in_ = self.delay_signal(self.input_wires, cycles=1, rst=en_neg_del, en=en_wire, init=0)
        for col_loop, col_fa in zip(loop, final_adder.input_wires):
            col_loop[0].connect_to(col_fa[0])

        for col_in, col_fa in zip(in_, final_adder.input_wires):
            for el_in, el_fa in zip(col_in, col_fa[1:]):
                el_in.connect_to(el_fa)

        # Connect final adder output to stage output
        for col_t, col_s in zip(self.output_wires, final_adder.output_wires):
            for t, s in zip(col_t, col_s):
                s.connect_to(t)
        self.instances.append(final_adder)

    def _build_low_latency(self):
        """Low-latency mode: pipelined QuaternaryAdder + fast BinaryAdder feedback.

        Architecture:
        1. QuaternaryAdder (pipelined) reduces compressor outputs to single binary sum
        2. BinaryAdder handles accumulation with simple 2-input feedback loop

        This isolates the complex compression from the feedback path, allowing
        higher Fmax at the cost of +1 cycle latency.
        """
        # Control signals
        en_neg = Wire(desired_name="en_neg")
        en_neg.set_to_module_input()
        rst = Wire(desired_name="rst")
        rst.set_to_module_input()
        self.instances.append(en_neg)
        self.instances.append(rst)

        # Optional clock enable signal
        en_wire = None
        if self.enable:
            en_wire = Wire(desired_name="en")
            en_wire.set_to_module_input()
            self.instances.append(en_wire)

        # Stage 1: QuaternaryAdder (outside feedback loop)
        # MUST be pipelined to break the critical path and achieve higher Fmax.
        # Without pipelining, the entire QuaternaryAdder logic sits in the
        # critical path, defeating the purpose of low-latency mode.
        try:
            quad_adder = self.final_adder_gen(self.input_shape, pipelined=True)
        except TypeError:
            # Fallback for final adders that don't support pipelining (e.g., MuxCYTernaryAdder)
            quad_adder = self.final_adder_gen(self.input_shape)

        # Store as attribute so nodes.py can access quad_adder.delay for total delay calculation
        self.quad_adder = quad_adder
        self.instances.append(quad_adder)

        # Connect compressor inputs to quad adder
        in_ = self.delay_signal(
            self.input_wires,
            cycles=1,
            rst=self.delay_signal(en_neg, self.preceeding_pipeline_stages, en=en_wire),
            en=en_wire,
            init=0,
        )
        for col_in, col_fa in zip(in_, quad_adder.input_wires):
            for el_in, el_fa in zip(col_in, col_fa):
                el_in.connect_to(el_fa)

        # Pipeline register after quad adder output
        quad_output_regs = []
        for col in quad_adder.output_wires:
            reg = Logic(en=en_wire)
            col[0].connect_to(reg)
            quad_output_regs.append(reg)
            self.instances.append(reg)

        # Stage 2: BinaryAdder (inside feedback loop)
        binary_adder = self.binary_adder_gen(self.accumulator_width)
        self.instances.append(binary_adder)

        # Reset delay must match the data path delay to the feedback registers.
        # Data path stages before feedback:
        #   +1: input register (line 131-138)
        #   +quad_adder.delay: QuaternaryAdder internal pipeline (1 if pipelined, 0 if not)
        #   +1: quad output register (line 145-149)
        # Total: preceeding_pipeline_stages + 2 + quad_adder.delay
        rst_del = self.delay_signal(
            rst,
            self.preceeding_pipeline_stages + 2 + quad_adder.delay,
            en=en_wire,
            init=1 if self.enable else None,
        )

        # Connect quad output to binary adder input[1] (new data)
        # Zero-extend if quad output is narrower than accumulator width
        for i in range(self.accumulator_width):
            if i < len(quad_output_regs):
                quad_output_regs[i].connect_to(binary_adder.input_wires[i][1])
            else:
                # Zero-extend upper bits
                Constant("1'b0").connect_to(binary_adder.input_wires[i][1])

        # Feedback loop: binary output -> register -> binary input[0]
        for i in range(self.accumulator_width):
            fb_reg = Logic(rst=rst_del, en=en_wire, init=0)
            binary_adder.output_wires[i][0].connect_to(fb_reg)
            fb_reg.connect_to(binary_adder.input_wires[i][0])
            self.instances.append(fb_reg)

        # Connect binary adder output to stage output
        for col_t, col_s in zip(self.output_wires, binary_adder.output_wires):
            for t, s in zip(col_t, col_s):
                s.connect_to(t)

    def delay_signal(self, signal, /, cycles=1, rst=None, en=None, init=None):
        if isinstance(signal, Iterable):
            return [self.delay_signal(el, cycles, rst, en, init) for el in signal]
        for i in range(cycles):
            lgc = Logic(rst=rst, en=en, init=init)
            signal.connect_to(lgc)
            self.instances.append(lgc)
            signal = lgc
        return signal

    def get_accumulator_width(self, input=None):
        if input:
            return input
        else:
            return sum([(el << idx) for idx, el in enumerate(self.input_shape)]).bit_length()

    def accept(self, visitor):
        visitor.visit_accumulator_stage(self)
