# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: BSD-3-Clause
#
# Out-of-context synthesis of a single fifo.sv instance, producing the utilization
# report that parse.py turns into one RESULT line. Driven by run.sh through the
# environment; FC_DIRECTIVE is optional and selects a synth_design directive.

set depth  $::env(FC_DEPTH)
set width  $::env(FC_WIDTH)
set style  $::env(FC_STYLE)
set part   $::env(FC_PART)
set outdir $::env(FC_OUT)

# The wrapper must expose count/maxcount. Without them the occupancy monitor is
# unreachable from the top level and synthesis trims it, so the LUT numbers come out
# well below what a FINN build really pays.
set cw [expr {int(ceil(log($depth+1)/log(2)))+1}]
set fh [open $outdir/top.sv w]
puts $fh "module fc_top(
  input  wire clk, input wire rst,
  input  wire \[[expr {$width-1}]:0\] idat, input wire ivld, output wire irdy,
  output wire \[[expr {$width-1}]:0\] odat, output wire ovld, input wire ordy,
  output wire \[$cw:0\] count, output wire \[$cw:0\] maxcount);
  fifo #(.DEPTH($depth), .DATA_WIDTH($width), .RAM_STYLE(\"$style\")) dut (
    .clk(clk), .rst(rst),
    .idat(idat), .ivld(ivld), .irdy(irdy),
    .odat(odat), .ovld(ovld), .ordy(ordy),
    .count(count), .maxcount(maxcount));
endmodule"
close $fh

read_verilog -sv $::env(FC_RTL)/fifo.sv
read_verilog -sv $outdir/top.sv
if {[info exists ::env(FC_DIRECTIVE)]} {
    synth_design -top fc_top -part $part -mode out_of_context -no_iobuf \
        -directive $::env(FC_DIRECTIVE)
} else {
    synth_design -top fc_top -part $part -mode out_of_context -no_iobuf
}
report_utilization -file $outdir/util.rpt
