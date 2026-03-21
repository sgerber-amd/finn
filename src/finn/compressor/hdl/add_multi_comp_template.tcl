# Vivado batch flow for add_multi compressor integration test.
# Behavioral simulation only — verifies that the generated compressor core
# produces the same results as the behavioral add_multi reference.
#
# Template placeholders expanded by run_add_multi_comp_tests.sh:
#   {label}          - Configuration label (e.g. n8_w4_p2)
#   {hdl_dir}        - Absolute path to compressor-python/hdl/
#   {mvu_pkg_path}   - Absolute path to mvu_pkg.sv
#   {add_multi_path} - Absolute path to the CATCH_COMP-injected add_multi.sv
#   {gen_dir}        - Absolute path to gen/<label>/

set label {label}
set tb add_multi_comp_{label}_tb
set part xcvc1902-vsva2197-2MP-e-S
create_project -force add_multi_comp_$label add_multi_comp_$label.vivado -part $part

# Design sources:
#   mvu_pkg.sv           - package (sumwidth, etc.)
#   add_multi.sv         - local copy with CATCH_COMP entry injected
#   add_multi_comp_top.sv - bus-to-array wrapper (static)
#   comp_*.sv            - generated compressor core(s)
read_verilog -sv {mvu_pkg_path} {add_multi_path} {hdl_dir}/add_multi_comp_top.sv {*}[glob {gen_dir}/comp_*.sv]

# Simulation sources
set simset [current_fileset -simset]
add_files -fileset $simset {gen_dir}/$tb.sv
set_property top $tb $simset
set_property xsim.simulate.runtime all $simset

# Run Simulation
launch_simulation
close_sim

quit
