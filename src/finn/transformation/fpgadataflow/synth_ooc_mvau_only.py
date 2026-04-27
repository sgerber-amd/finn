# Copyright (c) 2020, Xilinx
# Copyright (C) 2024, Advanced Micro Devices, Inc.
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
from qonnx.custom_op.registry import getCustomOp
from qonnx.transformation.base import Transformation
from shutil import copy2

# Import helper from synth_ooc.py
from finn.transformation.fpgadataflow.synth_ooc import generate_unified_add_multi
from finn.util.basic import get_dsp_block, make_build_dir
from finn.util.vivado import out_of_context_synth


class SynthOutOfContextMVAUOnly(Transformation):
    """Run out-of-context synthesis on MVAU core only (no wrappers/FIFOs).

    This transformation synthesizes ONLY the bare MVAU compute core (mvu_vvu_axi.sv)
    without the dataflow infrastructure (AXI wrappers, FIFOs, memstream, etc.).

    Use this for measuring pure MVAU resource usage without overhead.

    For full stitched design synthesis (with wrappers), use SynthOutOfContext instead.
    """

    def __init__(self, part, clk_period_ns, clk_name="ap_clk"):
        super().__init__()
        self.part = part
        self.clk_period_ns = clk_period_ns
        self.clk_name = clk_name

    def apply(self, model):
        # Find MVAU node (assumes single MVAU in model)
        mvau_nodes = [n for n in model.graph.node if n.op_type in ["MVAU_rtl", "MVAU_hls"]]
        assert len(mvau_nodes) == 1, (
            f"SynthOutOfContextMVAUOnly requires exactly 1 MVAU node, found {len(mvau_nodes)}. "
            "For multi-node designs, use SynthOutOfContext instead."
        )
        mvau_node = mvau_nodes[0]
        mvau_inst = getCustomOp(mvau_node)
        is_rtl = mvau_node.op_type == "MVAU_rtl"

        # Get code generation directory (where HDL was generated)
        code_gen_dir = mvau_inst.get_nodeattr("code_gen_dir_ipgen")
        assert code_gen_dir is not None, "MVAU must have generated IP (run step_hw_ipgen first)"

        build_dir = make_build_dir("synth_mvau_only_")
        verilog_extensions = [".v", ".sv", ".vh"]

        # Copy ONLY core MVAU files from finn-rtllib (not wrappers!)
        finn_root = os.environ.get("FINN_ROOT")
        assert finn_root is not None, "FINN_ROOT environment variable not set"

        if is_rtl:
            # RTL MVAU: Copy core SystemVerilog files
            rtllib_mvu_dir = os.path.join(finn_root, "finn-rtllib/mvu")
            core_files = [
                "mvu_vvu_axi.sv",  # Top-level MVAU core (NO wrapper!)
                "mvu.sv",  # MVU implementation
                "mvu_pkg.sv",  # Package definitions
                "replay_buffer.sv",  # Weight replay buffer
            ]

            # Add DSP-specific multiplier based on FPGA part
            dsp_block = get_dsp_block(self.part)
            if dsp_block == "DSP58":
                core_files.append("mvu_vvu_8sx9_dsp58.sv")
            elif dsp_block == "DSP48E2":
                core_files.append("mvu_4sx4u_dsp48e2.sv")
            else:  # DSP48E1 (7-Series) or DSP48E (older)
                core_files.append("mvu_4sx4u_dsp48e1.sv")

            for filename in core_files:
                src_path = os.path.join(rtllib_mvu_dir, filename)
                if os.path.exists(src_path):
                    copy2(src_path, build_dir)

            # Copy compressor files if they exist (dotp_comp, comp_*.sv)
            # These are generated in code_gen_dir
            if os.path.exists(code_gen_dir):
                for filename in os.listdir(code_gen_dir):
                    if filename.startswith("comp_") and filename.endswith(".sv"):
                        copy2(os.path.join(code_gen_dir, filename), build_dir)
                    elif filename == "dotp_comp.sv":
                        copy2(os.path.join(code_gen_dir, filename), build_dir)
                        # dotp_comp.sv requires mul_comp_map.sv (parameter calculation helper)
                        mul_comp_map_src = os.path.join(
                            finn_root, "src/finn/compressor/hdl/mul_comp_map.sv"
                        )
                        if os.path.exists(mul_comp_map_src):
                            copy2(mul_comp_map_src, build_dir)

            # Generate unified add_multi.sv with aggregated CATCH_COMP entries
            # This overwrites any per-node add_multi.sv files that were copied above
            generate_unified_add_multi(model, build_dir)

            top_module_name = "mvu_vvu_axi"

        else:
            # HLS MVAU: Copy generated HLS verilog
            # HLS generates all-in-one verilog, so just copy everything from code_gen_dir
            verilog_dir = os.path.join(code_gen_dir, f"project_{mvau_node.name}/sol1/impl/verilog")
            if os.path.exists(verilog_dir):
                for filename in os.listdir(verilog_dir):
                    if any([filename.endswith(x) for x in verilog_extensions]):
                        copy2(os.path.join(verilog_dir, filename), build_dir)

            # Top module for HLS is the node name
            top_module_name = mvau_node.name

        # Run synthesis on BARE MVAU only
        # Based on finn-rtllib/mvu/tb/mvu_comp_synth_tb_template.tcl (non-project mode)
        ret = self._synthesize_mvau_core(build_dir, top_module_name, is_rtl, mvau_inst)
        model.set_metadata_prop("res_mvau_only_ooc_synth", str(ret))
        return (model, False)

    def _synthesize_mvau_core(self, build_dir, top_module_name, is_rtl, mvau_inst):
        """Synthesize bare MVAU core using Oh-My-Xilinx (project mode).

        Matches the pattern from synth_ooc.py (normal FINN flow):
        - Files already copied to build_dir by apply()
        - Creates optional generics TCL for RTL parameters
        - Calls out_of_context_synth() which handles everything else

        vivadocompile.sh auto-generates sources.tcl, headers.tcl, and .xdc
        by scanning build_dir, so we don't need to create them.
        """
        from finn.util.vivado import out_of_context_synth

        # For RTL MVAU, generate generics TCL file (same pattern as HLS float IPs)
        float_ip_tcl = []
        if is_rtl:
            # Get MVAU parameters from node (same as prepare_codegen_default())
            pe = mvau_inst.get_nodeattr("PE")
            simd = mvau_inst.get_nodeattr("SIMD")
            mw = mvau_inst.get_nodeattr("MW")
            mh = mvau_inst.get_nodeattr("MH")
            aw = mvau_inst.get_input_datatype(0).bitwidth()
            ww = mvau_inst.get_input_datatype(1).bitwidth()
            accu_width = mvau_inst.get_output_datatype().bitwidth()
            signed_act = 1 if mvau_inst.get_input_datatype(0).min() < 0 else 0

            # Get DSP block version
            dsp_block = get_dsp_block(self.part)
            version = 3 if dsp_block == "DSP58" else (2 if dsp_block == "DSP48E2" else 1)

            # Check if compressor is being used
            comp_module_name = mvau_inst.get_nodeattr("comp_module_name")
            use_compressor = 1 if comp_module_name else 0

            # Determine compressor pipeline depth
            comp_depth = 1  # default
            if comp_module_name:
                # comp_module_name format: "comp_16xs2s2_a8_d3" where d3 is depth
                import re

                match = re.search(r"_d(\d+)$", comp_module_name)
                if match:
                    comp_depth = int(match.group(1))

            # Generate generics TCL file (same pattern as synth_ooc.py line 120-125)
            generics_tcl_path = os.path.join(build_dir, "mvau_generics.tcl")
            with open(generics_tcl_path, "w") as f:
                f.write(f"# Set MVAU generics on top module\n")
                f.write(f"set_property generic {{\\\n")
                f.write(f"    IS_MVU=1 \\\n")
                f.write(f"    VERSION={version} \\\n")
                f.write(f"    MW={mw} \\\n")
                f.write(f"    MH={mh} \\\n")
                f.write(f"    PE={pe} \\\n")
                f.write(f"    SIMD={simd} \\\n")
                f.write(f"    ACTIVATION_WIDTH={aw} \\\n")
                f.write(f"    WEIGHT_WIDTH={ww} \\\n")
                f.write(f"    ACCU_WIDTH={accu_width} \\\n")
                f.write(f"    SIGNED_ACTIVATIONS={signed_act} \\\n")
                f.write(f"    USE_COMPRESSOR={use_compressor} \\\n")
                f.write(f"    COMP_PIPELINE_DEPTH={comp_depth} \\\n")
                f.write(f"}} [get_filesets sources_1]\n")
            float_ip_tcl.append(generics_tcl_path)

        # WORKAROUND: Oh-My-Xilinx vivadoprojgen.sh has a bug where it creates dummy
        # files for .h/.vhd/.sv but NOT for .v, causing zsh loops to crash when no
        # .v files exist. Create a dummy .v file to prevent the crash.
        dummy_v_file = os.path.join(build_dir, "dummy_verilog_workaround.v")
        if not os.path.exists(dummy_v_file):
            with open(dummy_v_file, "w") as f:
                f.write("// Dummy file to workaround Oh-My-Xilinx vivadoprojgen.sh bug\n")

        # Call Oh-My-Xilinx (same as synth_ooc.py line 126-128)
        # vivadocompile.sh will:
        #   1. Scan build_dir for .v/.sv/.vh files
        #   2. Generate sources.tcl, headers.tcl, <top>.xdc automatically
        #   3. Source mvau_generics.tcl to set RTL parameters
        #   4. Create results_<top_module_name>/vivadocompile/vivadocompile.xpr
        #   5. Run synthesis and generate res.txt with resource counts
        ret = out_of_context_synth(
            build_dir, top_module_name, float_ip_tcl, self.part, self.clk_name, self.clk_period_ns
        )

        return ret
