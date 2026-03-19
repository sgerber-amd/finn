# Vivado synthesis flow for MVU compressor integration.
# Runs synthesis (no simulation) to verify area and timing.
#
# Template placeholders expanded by run_mvu_comp_synth_tests.sh:
#   {label}       - Configuration label
#   {mvu_dir}     - Absolute path to finn/finn-rtllib/mvu/
#   {gen_dir}     - Absolute path to gen/<label>/
#   {mh}          - Matrix Height
#   {mw}          - Matrix Width
#   {pe}          - Processing Elements
#   {simd}        - SIMD lanes
#   {ww}          - Weight Width
#   {aw}          - Activation Width
#   {accu_width}  - Accumulator Width
#   {signed_act}  - Signed Activations (0 or 1)
#   {comp_depth}  - Compressor Pipeline Depth

set label {label}
set part xcvc1902-vsva2197-2MP-e-S
create_project -force "mvu_comp_synth_[set label]" "mvu_comp_synth_[set label].vivado" -part $part

# Design sources
read_verilog -sv \
	{mvu_dir}/mvu_pkg.sv \
	{mvu_dir}/mvu_vvu_axi.sv \
	{mvu_dir}/mvu_vvu_axi_wrapper.v \
	{mvu_dir}/replay_buffer.sv \
	{mvu_dir}/mvu_vvu_8sx9_dsp58.sv \
	{mvu_dir}/mvu.sv \
	{mvu_dir}/add_multi.sv \
	{mvu_dir}/tb/mul_comp_map.sv \
	{gen_dir}/dotp_comp.sv \
	{*}[glob {gen_dir}/comp_*.sv]

# Set wrapper as top and configure generic values
set_property top mvu_vvu_axi [current_fileset]
set_property generic [join { \
	IS_MVU=1 \
	VERSION=3 \
	MW={mw} \
	MH={mh} \
	PE={pe} \
	SIMD={simd} \
	ACTIVATION_WIDTH={aw} \
	WEIGHT_WIDTH={ww} \
	ACCU_WIDTH={accu_width} \
	SIGNED_ACTIVATIONS={signed_act} \
	COMP_PIPELINE_DEPTH={comp_depth} \
}] [current_fileset]

# Run Synthesis
launch_runs synth_1 -jobs 4
wait_on_run synth_1

# Report utilization
open_run synth_1
report_utilization -file "mvu_comp_synth_[set label]_util.rpt"
report_timing_summary -file "mvu_comp_synth_[set label]_timing.rpt"

close_project
quit
