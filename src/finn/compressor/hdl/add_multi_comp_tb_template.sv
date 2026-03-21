/******************************************************************************
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * @brief	Testbench for add_multi compressor integration.
 *		Exercises the add_multi → CATCH_COMP compressor path via
 *		add_multi_comp_top for one (N, ARG_WIDTH, DEPTH) configuration.
 *
 * Template placeholders expanded by run_add_multi_comp_tests.sh:
 *   {n}         - Number of addends (N / SIMD)
 *   {arg_width} - Bit width of each addend
 *   {depth}     - Pipeline depth of compressor core
 *   {label}     - Configuration label (e.g. n8_w4_p2)
 *****************************************************************************/

module add_multi_comp_{label}_tb import mvu_pkg::*;;

	localparam int unsigned  N         = {n};
	localparam int unsigned  ARG_WIDTH = {arg_width};
	localparam int unsigned  DEPTH     = {depth};
	localparam int unsigned  SUM_WIDTH = sumwidth(N, ARG_WIDTH, 0, 0);
	localparam int unsigned  ROUNDS    = 257;

	typedef logic [N-1:0][ARG_WIDTH-1:0]  args_t;
	typedef logic [SUM_WIDTH-1:0]  sum_t;

	//-----------------------------------------------------------------------
	// Global Control
	logic  clk = 0;
	always #5ns clk = !clk;

	logic  rst = 1;
	initial begin
		repeat(16) @(posedge clk);
		rst <= 0;
	end

	bit  done = 0;
	always_comb begin
		if(done)  $finish;
	end

	//-----------------------------------------------------------------------
	// DUT
	args_t  arg;
	sum_t  sum;
	add_multi_comp_top #(
		.N(N),
		.ARG_WIDTH(ARG_WIDTH),
		.DEPTH(DEPTH)
	) dut (
		.clk, .rst,
		.arg, .sum
	);

	//-----------------------------------------------------------------------
	// Input Feed
	int  Q[$];
	initial begin
		arg = 'x;
		@(posedge clk iff !rst);

		repeat(ROUNDS) begin
			automatic args_t  aa;
			automatic int  exp = 0;
			void'(std::randomize(aa));
			for(int unsigned  i = 0; i < N; i++) begin
				exp += aa[i];
			end

			arg <= aa;
			Q.push_back(exp);
			@(posedge clk);
		end

		arg <= 'x;
		repeat(DEPTH + 10) @(posedge clk);

		assert(Q.size == 0) else begin
			$error("Missing %0d outputs.", Q.size);
		end
		done = 1;
	end

	//-----------------------------------------------------------------------
	// Output Checker
	int unsigned  Checks = 0;
	int unsigned  Errors = 0;
	initial begin
		@(posedge clk iff !rst);
		repeat(DEPTH) @(posedge clk);
		repeat(ROUNDS) @(posedge clk) begin
			automatic int  exp = Q.pop_front();
			automatic int  hav = sum;
			assert(hav == exp) else begin
				$error("Output mismatch %0d instead of %0d.", hav, exp);
				$stop;
				Errors <= Errors + 1;
			end
			Checks <= Checks + 1;
		end
	end

	final begin
		$display("Performed %0d checks with %0d errors.", Checks, Errors);
		assert(Checks == ROUNDS) else  $error("Unexpected number of checks: %0d instead of %0d.", Checks, ROUNDS);
	end

endmodule : add_multi_comp_{label}_tb
