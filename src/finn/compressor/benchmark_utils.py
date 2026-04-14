#!/usr/bin/env python
# Copyright (C) 2024, Advanced Micro Devices, Inc.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Shared utilities for FINN compressor benchmarks."""

# Standardized board configurations
BOARD_CONFIGS = {
    "pynq-z1": {
        "board": "Pynq-Z1",
        "part": "xc7z020clg400-1",
    },
    "ultra96": {
        "board": "Ultra96",
        "part": "xczu3eg-sbva484-1-e",
    },
    "zcu104": {
        "board": "ZCU104",
        "part": "xczu7ev-ffvc1156-2-e",
    },
    "u250": {
        "board": "U250",
        "part": "xcu250-figd2104-2L-e",
    },
    "vck190": {
        "board": "VCK190",
        "part": "xcvc1902-vsva2197-2MP-e-S",
    },
}


def parse_dsp_counts(data):
    """Parse DSP counts from synthesis report JSON.

    DSP reporting varies by FPGA family:
    - Some boards report unified "DSP" count
    - Others report by type: "DSP48E", "DSP48E1", "DSP48E2", "DSP58"

    Returns the total DSP count across all types.
    """
    dsp_count = data.get("DSP", 0)
    if dsp_count == 0:  # Try specific DSP types if unified count is 0
        dsp_count = (
            data.get("DSP48E", 0)
            + data.get("DSP48E1", 0)
            + data.get("DSP48E2", 0)
            + data.get("DSP58", 0)
        )
    return dsp_count


def format_config_label(mw, mh, pe, simd, ww, aw):
    """Create standardized config label."""
    return f"mw{mw}_mh{mh}_pe{pe}_simd{simd}_w{ww}_a{aw}"


def compute_latency_cycles(mh, pe, mw, simd):
    """Compute expected latency in cycles for MVAU.

    Formula: (MH / PE) * (MW / SIMD) cycles per output
    """
    return int((mh / pe) * (mw / simd))
