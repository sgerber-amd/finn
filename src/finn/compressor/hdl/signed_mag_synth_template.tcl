#############################################################################
# Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#
# @brief    Vivado synthesis script for signed magnitude compressor
#############################################################################

set sig {n}x{m}
set top signed_mag_$sig
set part {part}
create_project -force $top $top.vivado -part $part

read_verilog -sv gen/${top}.sv
set_property top $top [current_fileset]

launch_runs synth_1 -jobs 8
wait_on_runs synth_1

quit
