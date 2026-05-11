/******************************************************************************
 * Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * @brief	Testbench for signed magnitude compressor (2's complement output)
 *****************************************************************************/

module signed_mag_{n}x{m}_tb #(
	localparam int unsigned N = {n},
	localparam int unsigned M = {m},
	localparam int unsigned IN_W = {in_width},
	localparam int unsigned OUT_W = {out_width},
	localparam int unsigned IN_COLS = {in_cols},
	localparam int unsigned SIGN_BIT = {sign_bit}
)();
	logic clk = 0;
	always #5ns clk = !clk;

	logic [N-1:0] signs;
	logic [N-1:0][M-1:0] mags;

	logic [IN_W-1:0] in_a;
	logic [IN_W-1:0] in_b;

	uwire [OUT_W-1:0] out;

	signed_mag_{n}x{m} dut (
		.clk,
		.in(in_a),
		.in_2(in_b),
		.out
	);

	// Pack inputs: in=mag/sign, in_2=sign (gate inputs)
	// Col 0: N XOR(mag[0],sign) + N AND(sign,sign)
	// Col 1 to M-1: N XOR(mag[col],sign)
	// Col M: N XNOR(0,sign) = ~sign for optimized sign extension
	always_comb begin
		automatic int idx = 0;
		// Column 0: mag bits XOR sign
		for (int i = 0; i < N; i++) begin
			in_a[idx] = mags[i][0];
			in_b[idx] = signs[i];
			idx++;
		end
		// Column 0: sign bits AND sign (for +1 injection)
		for (int i = 0; i < N; i++) begin
			in_a[idx] = signs[i];
			in_b[idx] = signs[i];
			idx++;
		end
		// Columns 1 to M-1: mag bits XOR sign
		for (int col = 1; col < M; col++) begin
			for (int i = 0; i < N; i++) begin
				in_a[idx] = mags[i][col];
				in_b[idx] = signs[i];
				idx++;
			end
		end
		// Column M: XNOR(0, sign) = ~sign
		// Sum of ~signs = N - popcount, constant correction gives -popcount
		for (int i = 0; i < N; i++) begin
			in_a[idx] = 1'b0;
			in_b[idx] = signs[i];
			idx++;
		end
	end

	initial begin
		repeat(200) begin
			automatic logic [N-1:0] s_rand;
			automatic logic [N-1:0][M-1:0] m_rand;
			automatic int signed expected = 0;
			automatic int signed got;

			void'(std::randomize(s_rand, m_rand));
			signs <= s_rand;
			mags <= m_rand;

			for (int i = 0; i < N; i++) begin
				automatic int signed val = m_rand[i];
				if (s_rand[i]) val = -val;
				expected += val;
			end

			#10ns;

			// Output is 2's complement - extract relevant bits up to sign bit
			got = $signed(out[SIGN_BIT:0]);

			assert((^out !== 1'bx) && (got == expected)) else begin
				$error("Mismatch: got %0d, expected %0d (out=0x%0x)", got, expected, out);
				$stop;
			end
		end

		$display("Test completed.");
		$finish;
	end

endmodule : signed_mag_{n}x{m}_tb
