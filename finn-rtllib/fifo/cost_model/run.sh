#!/bin/bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: BSD-3-Clause
#
# Synthesizes one fifo.sv configuration out of context and prints its RESULT line.
# Needs Vivado on PATH.
set -e

usage() {
	cat <<-EOF
		usage: run.sh DEPTH WIDTH STYLE [DIRECTIVE] [PART]

		  STYLE      shift, distributed, block or ultra; passed straight to RAM_STYLE
		  DIRECTIVE  optional synth_design directive; "-" to use the tool default
		  PART       defaults to $DEFAULT_PART

		Runs land in runs/<tag>/ next to this script, or under \$FC_RUNS if set.
		\$FC_RTL overrides where fifo.sv is read from.
	EOF
	exit 1
}

DEFAULT_PART=xczu7ev-ffvc1156-2-e
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

depth=$1
width=$2
style=$3
directive=${4:--}
part=${5:-$DEFAULT_PART}
[ -n "$style" ] || usage

tag="${style}_d${depth}_w${width}"
[ "$directive" = "-" ] || tag="${tag}_${directive}"
out="${FC_RUNS:-$here/runs}/$tag"
mkdir -p "$out"

export FC_DEPTH=$depth FC_WIDTH=$width FC_STYLE=$style FC_PART=$part FC_OUT=$out
export FC_RTL="${FC_RTL:-$here/../hdl}"
[ "$directive" = "-" ] || export FC_DIRECTIVE=$directive

# a failing synthesis is reported through the missing report rather than the exit code,
# which Vivado also sets on warnings
(cd "$out" && vivado -mode batch -nojournal -nolog -notrace -source "$here/synth_one.tcl" \
	> "$out/vivado.log" 2>&1) || true
[ -f "$out/util.rpt" ] || { echo "$tag FAIL, see $out/vivado.log" >&2; exit 1; }

python3 "$here/parse.py" "$out/util.rpt" "$tag" "$depth" "$width" "$style"
