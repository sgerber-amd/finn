# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Turns one Vivado report_utilization report into a RESULT line for data/.

    python3 parse.py REPORT TAG DEPTH WIDTH STYLE

Driven by run.sh. The RESULT lines it prints are what common.read_columns() reads
back. The first name of each row group below that the report carries wins, since
Vivado suffixes the primitive rows per family: RAMB18 on UltraScale+, RAMB18E5 on
Versal.
"""

import argparse
import re
from functools import lru_cache

# reported column -> the report_utilization row names it can appear under
COLUMNS = {
    "lut": ("CLB LUTs*", "CLB LUTs", "Slice LUTs"),
    "lut_logic": ("LUT as Logic",),
    "lut_mem": ("LUT as Memory",),
    "lutram": ("LUT as Distributed RAM",),
    "srl": ("LUT as Shift Register",),
    "ff": ("CLB Registers", "Register as Flip Flop"),
    "ramb36": ("RAMB36/FIFO*", "RAMB36E2 only", "RAMB36E5", "RAMB36"),
    "ramb18": ("RAMB18", "RAMB18E2 only", "RAMB18E5*", "RAMB18E5"),
    "uram": ("URAM", "URAM288"),
}


@lru_cache(maxsize=None)
def _row_pattern(name):
    return re.compile(r"^\|\s*" + re.escape(name) + r"\s*\|\s*(\d+)\s*\|", re.M)


def cell(report, names):
    """The used count of the first of `names` the report has a row for, or 0."""
    for name in names:
        match = _row_pattern(name).search(report)
        if match:
            return int(match.group(1))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("report", help="a report_utilization output file")
    parser.add_argument("tag", help="run identifier, e.g. block_d1024_w32")
    parser.add_argument("depth")
    parser.add_argument("width")
    parser.add_argument("style")
    args = parser.parse_args()

    with open(args.report) as f:
        report = f.read()
    cells = " ".join("%s=%d" % (k, cell(report, names)) for k, names in COLUMNS.items())
    print(
        "RESULT %s depth=%s width=%s style=%s %s"
        % (args.tag, args.depth, args.width, args.style, cells)
    )


if __name__ == "__main__":
    main()
