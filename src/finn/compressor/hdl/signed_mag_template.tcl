#############################################################################
# Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#
# @brief    Vivado simulation script for signed magnitude compressor
#############################################################################

set sig {n}x{m}
set top signed_mag_$sig
set part {part}
create_project -force $top $top.vivado -part $part

read_verilog -sv gen/${top}.sv
set simset [current_fileset -simset]
add_files -fileset $simset gen/${top}_tb.sv
set_property top ${top}_tb $simset
set_property xsim.simulate.runtime all $simset

launch_simulation
close_sim

quit
