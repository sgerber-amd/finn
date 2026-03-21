/******************************************************************************
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * @brief	Wrapper for add_multi that converts a flat bit-vector bus to the
 *		unpacked arg[] array and ties enable permanently high.
 *		Used by add_multi compressor integration tests.
 *****************************************************************************/

module add_multi_comp_top import mvu_pkg::*; #(
	int unsigned  N,
	int unsigned  ARG_WIDTH,
	int unsigned  DEPTH,

	localparam int unsigned  SUM_WIDTH = sumwidth(N, ARG_WIDTH, 0, 0)
)(
	input	logic  clk,
	input	logic  rst,

	input	logic [N*ARG_WIDTH-1:0]  arg,
	output	logic [SUM_WIDTH  -1:0]  sum
);
	uwire [ARG_WIDTH-1:0]  a[N];
	for(genvar i = 0; i < N; i++)  assign  a[i] = arg[i*ARG_WIDTH +: ARG_WIDTH];

	add_multi #(
		.N(N),
		.DEPTH(DEPTH),
		.ARG_WIDTH(ARG_WIDTH),
		.ARG_LO(0),
		.ARG_HI(0),
		.RESET_ZERO(0)
	) inst (
		.clk, .rst,
		.en(1'b1),
		.arg(a), .sum
	);

endmodule : add_multi_comp_top
