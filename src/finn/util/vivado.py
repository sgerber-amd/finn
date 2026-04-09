# Copyright (c) 2020, Xilinx
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of FINN nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import os

from finn.util.basic import launch_process_helper, which


def out_of_context_synth(
    verilog_dir,
    top_name,
    float_ip_tcl,
    fpga_part="xczu3eg-sbva484-1-e",
    clk_name="ap_clk_0",
    clk_period_ns=5.0,
):
    "Run out-of-context Vivado synthesis, return resources and slack."

    # ensure that the OH_MY_XILINX envvar is set
    if "OHMYXILINX" not in os.environ:
        raise Exception("The environment variable OHMYXILINX is not defined.")
    # ensure that vivado is in PATH: source $VIVADO_PATH/settings64.sh
    if which("vivado") is None:
        raise Exception("vivado is not in PATH, ensure settings64.sh is sourced.")
    omx_path = os.environ["OHMYXILINX"]
    script = "vivadocompile.sh"
    # vivadocompile.sh <top-level-entity> <fp0.tcl#fp1.tcl> <clk-name (opt)> <fpga-part (opt)>
    call_omx = "zsh %s/%s %s %s %s %s %f" % (
        omx_path,
        script,
        top_name,
        '"%s"' % "#".join(float_ip_tcl),
        clk_name,
        fpga_part,
        float(clk_period_ns),
    )
    call_omx = call_omx.split()
    launch_process_helper(call_omx, proc_env=os.environ.copy(), cwd=verilog_dir)

    vivado_proj_folder = "%s/results_%s" % (verilog_dir, top_name)
    res_counts_path = vivado_proj_folder + "/res.txt"

    with open(res_counts_path, "r") as myfile:
        res_data = myfile.read().split("\n")
    ret = {}
    ret["vivado_proj_folder"] = vivado_proj_folder
    for res_line in res_data:
        res_fields = res_line.split("=")
        print(res_fields)
        try:
            ret[res_fields[0]] = float(res_fields[1])
        except ValueError:
            ret[res_fields[0]] = 0
        except IndexError:
            ret[res_fields[0]] = 0
    if ret["WNS"] == 0:
        ret["fmax_mhz"] = 0
    else:
        ret["fmax_mhz"] = 1000.0 / (clk_period_ns - ret["WNS"])
    return ret


def timing_closure_search_from_synth(
    vivado_proj_folder,
    top_name,
    clk_name="ap_clk",
    clk_period_ns_min=1.0,
    clk_period_ns_max=100.0,
):
    """Run binary search for timing closure on an already-synthesized design.

    This is more efficient than timing_closure_search() when synthesis was
    already performed (e.g., by out_of_context_synth()). Reuses the existing
    synthesized netlist and only iterates implementation.

    Parameters
    ----------
    vivado_proj_folder : str
        Path to existing results_<top_name> directory from out_of_context_synth()
    top_name : str
        Top-level module name (not used, kept for API consistency)
    clk_name : str, optional
        Clock port name (default: ap_clk)
    clk_period_ns_min : float, optional
        Minimum (aggressive) clock period to test in ns (default: 1.0)
    clk_period_ns_max : float, optional
        Maximum (conservative) clock period to test in ns (default: 100.0)

    Returns
    -------
    dict
        Same as timing_closure_search() - includes achieved_fmax_mhz, etc.

    Notes
    -----
    - Expects vivado_proj_folder to contain vivadocompile/ project directory
    - Synthesis must have already completed (synth_1 run must exist)
    - Much faster than timing_closure_search() - skips ~10-20 min synthesis step
    """
    import subprocess

    # ensure that vivado is in PATH
    if which("vivado") is None:
        raise Exception("vivado is not in PATH, ensure settings64.sh is sourced.")

    # Create TCL script for binary search (assumes synthesis already done)
    tcl_script = os.path.join(vivado_proj_folder, "timing_search_resume.tcl")

    with open(tcl_script, "w") as f:
        f.write(f"""# Resume timing search from existing synthesis
# Open existing project
open_project {vivado_proj_folder}/vivadocompile/vivadocompile.xpr

# Verify synthesis run exists
if {{[get_runs synth_1] == ""}} {{
    puts "ERROR: synth_1 run not found. Run synthesis first."
    exit 1
}}

# Binary search parameters
set clk_name "{clk_name}"
set tm {clk_period_ns_min}
set ts {clk_period_ns_max}
set tt [expr ($tm + $ts) / 2.0]

puts "INFO: Resuming timing search from existing synthesis"
puts "INFO: Clock name: $clk_name"
puts "INFO: Initial bounds: \\[$tm ns : $ts ns\\]"

# NOTE: Timing relaxation parameters (route.timingRelaxation, route.maxIterations,
# route.timingRelaxationRatio) do not exist in Vivado 2024.2.
# The Default routing directive has built-in timeouts and won't hang indefinitely.
# If using older Vivado versions with hanging issues, uncomment and test:
# set_param route.timingRelaxation true
# set_param route.maxIterations 100
# set_param route.timingRelaxationRatio 0.95

puts "INFO: Using Default routing directive (no timing relaxation params in Vivado 2024.2)"

# Open synthesized design
open_run synth_1

# CRITICAL: Remove the original clock constraint file from OOC synth
# It contains "create_clock -period 10.000 [get_nets ap_clk]" which conflicts
# with our dynamic timing search
set constr_set [current_fileset -constrset]
set orig_xdc [get_files -of_objects $constr_set *.xdc -filter {{NAME =~ "*finn_design_wrapper.xdc"}}]
if {{$orig_xdc ne ""}} {{
    puts "INFO: Removing original constraint file: $orig_xdc"
    remove_files -fileset $constr_set $orig_xdc
}}

# Create tmp.xdc for dynamic constraints (like reference script)
close [open tmp.xdc w]
add_files -fileset $constr_set tmp.xdc
set_property target_constrs_file tmp.xdc $constr_set
close_design

# Binary search loop
set timf [open "timing_resume.log" w]
set iteration 0

while {{[expr $ts - $tm] > 0.1}} {{
    incr iteration

    # Write clock constraint to tmp.xdc (matching reference script approach)
    close [open tmp.xdc w]
    open_run synth_1
    create_clock -name clk -period $tt [get_ports $clk_name]
    save_constraints -force
    close_design

    reset_run impl_1

    # Keep pre-route phys_opt (fast, useful) but disable post-route (Phase 13 - very slow)
    set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED false [get_runs impl_1]

    # Use Default directive (no directive set) for better timing quality
    # Combined with timing relaxation params above, this gives close to OOC synth quality
    # without risk of infinite hanging (router gives up after 100 iterations)
    # NOTE: Quick directive line removed - Default routing is now used

    puts -nonewline $timf "# Iteration $iteration: Testing period $tt ns (\\[$tm : $ts\\]) -> "
    flush $timf

    puts "INFO: Iteration $iteration: Testing period $tt ns"
    launch_runs impl_1 -jobs 8
    wait_on_runs impl_1
    open_run impl_1

    set wns [get_property SLACK [get_timing_paths]]
    close_design

    puts $timf "WNS = $wns ns"
    flush $timf
    puts "INFO: WNS = $wns ns"

    if {{ $wns < 0 }} {{
        set tm $tt
        puts "INFO: Timing FAILED, increasing period"
    }} else {{
        set ts $tt
        puts "INFO: Timing PASSED, decreasing period"
    }}

    set tt [expr {{max((4*$tm+$ts)/5.0, min($tt-$wns, ($tm+$ts)/2.0))}}]
}}

puts $timf "\\n=== TIMING CLOSURE ACHIEVED ==="
puts $timf "Achieved period: $ts ns"
puts $timf "Achieved fmax: [expr 1000.0 / $ts] MHz"
puts $timf "Total iterations: $iteration"
close $timf

# Extract final WNS from implementation
# Note: Resource counts (LUT/FF/DSP) don't change during timing iterations,
# so we reuse them from the initial synthesis report (ooc_synth_and_timing.json)
open_run impl_1
set time_wns [get_property SLACK [get_timing_paths]]
close_design

# Write timing results only
set fp [open "res_timing.txt" w]
puts $fp "achieved_period_ns=$ts"
puts $fp "achieved_fmax_mhz=[expr 1000.0 / $ts]"
puts $fp "wns_at_closure=$time_wns"
puts $fp "iterations=$iteration"
close $fp

puts "INFO: Results written to res_timing.txt"
exit
""")

    # Run Vivado with the script
    cmd = ["vivado", "-mode", "batch", "-source", tcl_script]
    subprocess.run(cmd, cwd=vivado_proj_folder, check=True)

    # Parse results
    res_path = os.path.join(vivado_proj_folder, "res_timing.txt")
    with open(res_path, "r") as f:
        res_data = f.read().split("\n")

    ret = {}
    for res_line in res_data:
        if "=" in res_line:
            key, val = res_line.split("=")
            try:
                ret[key] = float(val)
            except ValueError:
                ret[key] = 0

    ret["vivado_proj_folder"] = vivado_proj_folder
    return ret
