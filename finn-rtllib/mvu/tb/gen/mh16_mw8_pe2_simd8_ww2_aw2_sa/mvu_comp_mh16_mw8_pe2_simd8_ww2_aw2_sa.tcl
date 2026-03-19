# Vivado batch flow for MVU compressor integration test.
# Behavioral simulation — verifies the full mvu_vvu_axi pipeline with
# USE_COMPRESSOR=1 through AXI-Stream interfaces.
#
# Template placeholders expanded by run_mvu_comp_tests.sh:
#   mh16_mw8_pe2_simd8_ww2_aw2_sa       - Configuration label (e.g. mh16_mw8_pe2_simd8_ww2_aw2_sa)
#   /home/sgerber/test_repos/finn/finn-rtllib/mvu     - Absolute path to finn/finn-rtllib/mvu/
#   /home/sgerber/test_repos/finn/finn-rtllib/mvu/compressor    - Absolute path to compressor-python/src/ or compressor copy
#   /home/sgerber/test_repos/finn/finn-rtllib/mvu/tb/gen/mh16_mw8_pe2_simd8_ww2_aw2_sa     - Absolute path to gen/<label>/

set label mh16_mw8_pe2_simd8_ww2_aw2_sa
set tb mvu_comp_$mh16_mw8_pe2_simd8_ww2_aw2_sa_tb
set part xcvc1902-vsva2197-2MP-e-S
create_project -force mvu_comp_$label mvu_comp_$label.vivado -part $part

# Design sources:
#   MVU pipeline:  mvu_pkg.sv, mvu_vvu_axi.sv, replay_buffer.sv,
#                  mvu_vvu_8sx9_dsp58.sv, mvu.sv, add_multi.sv
#   Compressor:    dotp_comp.sv (expanded), mul_comp_map.sv, comp_<sig>.sv
read_verilog -sv \
	/home/sgerber/test_repos/finn/finn-rtllib/mvu/mvu_pkg.sv \
	/home/sgerber/test_repos/finn/finn-rtllib/mvu/mvu_vvu_axi.sv \
	/home/sgerber/test_repos/finn/finn-rtllib/mvu/replay_buffer.sv \
	/home/sgerber/test_repos/finn/finn-rtllib/mvu/mvu_vvu_8sx9_dsp58.sv \
	/home/sgerber/test_repos/finn/finn-rtllib/mvu/mvu.sv \
	/home/sgerber/test_repos/finn/finn-rtllib/mvu/add_multi.sv \
	/home/sgerber/test_repos/finn/finn-rtllib/mvu/tb/mul_comp_map.sv \
	/home/sgerber/test_repos/finn/finn-rtllib/mvu/tb/gen/mh16_mw8_pe2_simd8_ww2_aw2_sa/dotp_comp.sv \
	{*}[glob /home/sgerber/test_repos/finn/finn-rtllib/mvu/tb/gen/mh16_mw8_pe2_simd8_ww2_aw2_sa/comp_*.sv]

# Simulation sources
set simset [current_fileset -simset]
add_files -fileset $simset /home/sgerber/test_repos/finn/finn-rtllib/mvu/tb/gen/mh16_mw8_pe2_simd8_ww2_aw2_sa/$tb.sv
set_property top $tb $simset
set_property xsim.simulate.runtime all $simset

# Defines for simulation-only features
set_property verilog_define {FINN_SIMULATION=1} $simset

# Run Simulation
launch_simulation
close_sim

quit
