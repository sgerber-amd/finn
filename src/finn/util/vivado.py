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
import subprocess

from finn.util.basic import launch_process_helper, which


def _parse_utilization_report_fallback(vivado_proj_folder, clk_period_ns):
    """Parse Vivado utilization report directly when res.txt is missing or malformed.

    This fallback handles cases where Oh-My-Xilinx crashes before writing res.txt
    (e.g., TCL piping syntax errors in vivadocompile.tcl).
    """
    import re

    utilization_rpt = (
        vivado_proj_folder
        + "/vivadocompile/vivadocompile.runs/synth_1/finn_design_wrapper_utilization_synth.rpt"
    )

    ret = {"vivado_proj_folder": vivado_proj_folder}

    try:
        with open(utilization_rpt, "r") as urpt:
            urpt_data = urpt.read()

            # Parse LUT count: | Slice LUTs    |  236 |
            match = re.search(r"\|\s*Slice LUTs\s*\|\s*(\d+)\s*\|", urpt_data)
            ret["LUT"] = float(match.group(1)) if match else 0

            # Parse LUTRAM count: | LUT as Memory |   82 |
            match = re.search(r"\|\s*LUT as Memory\s*\|\s*(\d+)\s*\|", urpt_data)
            ret["LUTRAM"] = float(match.group(1)) if match else 0

            # Parse FF count: | FDRE          | 391 |  or total FF line
            match = re.search(r"\|\s*Register as Flip Flop\s*\|\s*(\d+)\s*\|", urpt_data)
            if not match:
                # Fallback: sum FDRE, FDSE, FDCE, FDPE
                ff_matches = re.findall(r"\|\s*FD[RSCP]E\s*\|\s*(\d+)\s*\|", urpt_data)
                ret["FF"] = float(sum(int(x) for x in ff_matches)) if ff_matches else 0
            else:
                ret["FF"] = float(match.group(1))

            # Parse DSP count: | DSPs      |    4 |  or  | DSP48E2 only |    4 |
            match = re.search(r"\|\s*DSPs\s*\|\s*(\d+)\s*\|", urpt_data)
            if match:
                ret["DSP"] = float(match.group(1))
            else:
                # Try specific DSP types (DSP48E1, DSP48E2, DSP58)
                dsp_matches = re.findall(r"\|\s*DSP\d+[^\|]*only\s*\|\s*(\d+)\s*\|", urpt_data)
                ret["DSP"] = float(sum(int(x) for x in dsp_matches)) if dsp_matches else 0

            # Parse BRAM counts
            match = re.search(r"\|\s*RAMB18\s*\|\s*(\d+)\s*\|", urpt_data)
            ret["BRAM_18K"] = float(match.group(1)) if match else 0

            match = re.search(r"\|\s*RAMB36\s*\|\s*(\d+)\s*\|", urpt_data)
            ret["BRAM_36K"] = float(match.group(1)) if match else 0

            ret["BRAM"] = ret["BRAM_18K"] / 2 + ret["BRAM_36K"]

            # Parse URAM count
            match = re.search(r"\|\s*URAM\s*\|\s*(\d+)\s*\|", urpt_data)
            ret["URAM"] = float(match.group(1)) if match else 0

            # Parse CARRY count
            match = re.search(r"\|\s*CARRY\d+\s*\|\s*(\d+)\s*\|", urpt_data)
            ret["Carry"] = float(match.group(1)) if match else 0

    except FileNotFoundError:
        # Utilization report doesn't exist - synthesis failed completely
        ret.update(
            {
                "LUT": 0,
                "LUTRAM": 0,
                "FF": 0,
                "DSP": 0,
                "BRAM": 0,
                "BRAM_18K": 0,
                "BRAM_36K": 0,
                "URAM": 0,
                "Carry": 0,
            }
        )

    # Try to get timing from timing report
    timing_rpt = (
        vivado_proj_folder + "/vivadocompile/vivadocompile.runs/impl_1/"
        "finn_design_wrapper_timing_summary_routed.rpt"
    )
    try:
        with open(timing_rpt, "r") as trpt:
            trpt_data = trpt.read()
            # Parse WNS: | WNS(ns)      | TNS(ns)  |
            # TNS Failing Endpoints | TNS Total Endpoints |
            #            | 3.996        | 0.000    |
            match = re.search(r"WNS\(ns\)[^\n]*\n[^\d]*([-\d.]+)", trpt_data)
            ret["WNS"] = float(match.group(1)) if match else 0
            ret["Delay"] = ret["WNS"]
    except FileNotFoundError:
        ret["WNS"] = 0
        ret["Delay"] = 0

    ret["fmax_mhz"] = 1000.0 / clk_period_ns
    return ret


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

    # Check if res.txt exists (Oh-My-Xilinx may crash before writing it)
    if not os.path.exists(res_counts_path):
        # Fallback: parse utilization report directly
        ret = _parse_utilization_report_fallback(vivado_proj_folder, clk_period_ns)
        return ret

    with open(res_counts_path, "r") as myfile:
        res_data = myfile.read().split("\n")
    ret = {}
    ret["vivado_proj_folder"] = vivado_proj_folder
    for res_line in res_data:
        res_fields = res_line.split("=")
        print(res_fields)
        try:
            val_str = res_fields[1]
            key = res_fields[0]

            # ALWAYS verify DSP count from utilization report (Oh-My-Xilinx has multiple bugs)
            # Known issues: "DSP=X.DSP", empty values, TCL errors embedded in output
            if key == "DSP":
                import re

                utilization_rpt = (
                    vivado_proj_folder + "/vivadocompile/vivadocompile.runs/"
                    "synth_1/finn_design_wrapper_utilization_synth.rpt"
                )
                try:
                    with open(utilization_rpt, "r") as urpt:
                        urpt_data = urpt.read()
                        # Look for DSP table: | DSPs      |    4 |  or  | DSP48E1 only |    4 |
                        match = re.search(r"\|\s*DSPs\s*\|\s*(\d+)\s*\|", urpt_data)
                        if match:
                            ret[key] = float(match.group(1))
                        else:
                            # Try specific DSP types (DSP48E1, DSP48E2, DSP58)
                            dsp_matches = re.findall(
                                r"\|\s*DSP\d+[^\|]*only\s*\|\s*(\d+)\s*\|", urpt_data
                            )
                            ret[key] = float(sum(int(x) for x in dsp_matches)) if dsp_matches else 0
                except FileNotFoundError:
                    # Can't read report - try to parse res.txt value as fallback
                    try:
                        ret[key] = float(val_str)
                    except ValueError:
                        ret[key] = 0
                continue

            # Normal parsing
            try:
                ret[key] = float(val_str)
            except ValueError:
                # Handle other malformed values
                import re

                match = re.match(r"^([0-9.]+)", val_str)
                if match:
                    ret[key] = float(match.group(1))
                else:
                    ret[key] = 0
        except IndexError:
            ret[res_fields[0]] = 0
    # fmax based on passing clock period (not WNS-adjusted critical path)
    ret["fmax_mhz"] = 1000.0 / clk_period_ns
    return ret


def timing_closure_search_from_synth(
    vivado_proj_folder,
    top_name,
    clk_name="ap_clk",
    clk_period_ns_min=1.0,
    clk_period_ns_max=100.0,
):
    """Run binary search for timing closure on an already-synthesized design.
    Based on finnlib rtl/abc.tcl but assumes synthesis.

    Parameters
    ----------
    vivado_proj_folder : str
        Path to existing results_<top_name> directory from out_of_context_synth()
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

    # Create TCL script for binary search (assumes synthesis already done)
    tcl_script = os.path.join(vivado_proj_folder, "timing_search_resume.tcl")

    with open(tcl_script, "w") as f:
        f.write(
            f"""# Resume timing search from existing synthesis
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

# Open synthesized design -> original synth already ran using ooc synth,
# which is generated as part of the finn flow. This means we just open this
# design for timing closure search.
open_run synth_1

# Remove the original clock constraint file from OOC synth
# It contains "create_clock -period 10.000 [get_nets ap_clk]" which conflicts
# with our dynamic timing search
set constr_set [current_fileset -constrset]
set orig_xdc [get_files -of_objects $constr_set *.xdc \\
    -filter {{NAME =~ "*finn_design_wrapper.xdc"}}]
if {{$orig_xdc ne ""}} {{
    puts "INFO: Removing original constraint file: $orig_xdc"
    remove_files -fileset $constr_set $orig_xdc
}}

# Create tmp.xdc for dynamic constraints
close [open tmp.xdc w]
add_files -fileset $constr_set tmp.xdc
set_property target_constrs_file tmp.xdc $constr_set
close_design

# Binary search loop
set timf [open "timing_resume.log" w]
set iteration 0
set best_passing_period $ts
set best_passing_wns 0

# Helper proc to save best result so far (called after each iteration)
proc save_intermediate_result {{period_val wns iteration}} {{
    set fp [open "res_timing_intermediate.txt" w]
    puts $fp "achieved_period_ns=$period_val"
    puts $fp "achieved_fmax_mhz=[expr 1000.0 / $period_val]"
    puts $fp "wns_at_closure=$wns"
    puts $fp "iterations=$iteration"
    close $fp
}}

while {{[expr $ts - $tm] > 0.1}} {{
    incr iteration

    # Write clock constraint to tmp.xdc
    close [open tmp.xdc w]
    open_run synth_1
    create_clock -name clk -period $tt [get_ports $clk_name]
    save_constraints -force
    close_design

    reset_run impl_1

    # Keep pre-route phys_opt (fast, useful) but disable post-route (Phase 13 - very slow)
    #set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED false [get_runs impl_1]

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
        set best_passing_period $ts
        set best_passing_wns $wns
        puts "INFO: Timing PASSED, decreasing period"
    }}

    # Save intermediate best result after each iteration
    save_intermediate_result $best_passing_period $best_passing_wns $iteration

    # Next attempt by splitting the interval [$tm:$ts] on the aggressive side
    # somewhere between 20% and 50% using $tt-$wns as guidance.
    set tt [expr {{max((4*$tm+$ts)/5.0, min($tt-$wns, ($tm+$ts)/2.0))}}]
}}

puts $timf "\\n=== TIMING CLOSURE ACHIEVED ==="
puts $timf "Achieved period: $ts ns"
puts $timf "Achieved fmax: [expr 1000.0 / $ts] MHz"
puts $timf "Total iterations: $iteration"
close $timf

# Extract final WNS from implementation
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
"""
        )

    # Run Vivado with the script with timeout
    cmd = ["vivado", "-mode", "batch", "-source", tcl_script]

    # Timeout: 90 minutes max (typical timing closure takes 20-60 min, hung processes go 90+ min)
    timeout_seconds = 90 * 60

    try:
        subprocess.run(cmd, cwd=vivado_proj_folder, check=True, timeout=timeout_seconds)
        # Normal completion - use intermediate result (has correct best_passing_wns)
        # res_timing.txt has wrong WNS (extracted from last iteration, not best passing)
        res_path = os.path.join(vivado_proj_folder, "res_timing_intermediate.txt")
    except subprocess.TimeoutExpired:
        # Timeout - process hung, kill it and recover intermediate results
        print("WARNING: Timing closure timed out after 90 minutes")
        print("         Process likely hung in Phase 13.2 Critical Path Optimization")
        print("         Attempting to recover best intermediate result...")
        res_path = os.path.join(vivado_proj_folder, "res_timing_intermediate.txt")
        if not os.path.exists(res_path):
            raise Exception("Timing closure timed out and no intermediate results found")
    except subprocess.CalledProcessError:
        # Vivado failed/crashed - try to recover intermediate results
        print("WARNING: Timing closure did not complete successfully")
        print("         Attempting to recover best intermediate result...")
        res_path = os.path.join(vivado_proj_folder, "res_timing_intermediate.txt")
        if not os.path.exists(res_path):
            # No intermediate results available - re-raise the error
            raise

    # Parse results (either final or best intermediate)
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

    # Mark if this was recovered from intermediate results
    if "intermediate" in res_path:
        ret["incomplete"] = True
        ret["timeout"] = True
        print(
            f"         Recovered: period={ret.get('achieved_period_ns')}ns, "
            f"fmax={ret.get('achieved_fmax_mhz'):.1f} MHz, "
            f"iterations={int(ret.get('iterations', 0))} (incomplete)"
        )
        print("         This is the BEST PASSING result before timeout/hang")

    return ret
