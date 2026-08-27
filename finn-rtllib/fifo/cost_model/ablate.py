# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Ablation harness: replaces one term of the model at a time and rescores.

`score.py` answers "is the model accurate". This answers "is any part of it
unnecessary": for each term, substitute a simpler form and report what the accuracy
costs.

    python3 ablate.py             # every variant
    python3 ablate.py hi_in_ram   # only variants whose label matches

A variant is a function decorated with @variant("label") that mutates a fresh model
namespace in place. Simple terms can be replaced by rebinding the name. Terms living
inside _fifo_cost are reached with patch_cost(), which rewrites an expression by exact
string match, so a variant whose target has been edited fails loudly.
"""

import argparse
import math
from common import (
    BLOCK_LUT,
    DIST_LUT,
    SCORED_SETS,
    SHIFT_LUT,
    STYLES,
    ULTRA_LUT,
    ULTRA_READ,
    Score,
    load_model,
    model_source,
    read_runs,
)

VARIANTS = {}


def variant(label):
    """Registers a namespace mutation under a label, for the report table."""

    def register(fn):
        assert label not in VARIANTS, label
        VARIANTS[label] = fn
        return fn

    return register


def patch_cost(ns, *replacements):
    """Rebuilds _fifo_cost in ns with each (old, new) expression substituted."""
    body = model_source().split("def _fifo_cost")[1].split("\nclass ")[0]
    for old, new in replacements:
        assert old in body, old
        body = body.replace(old, new)
    exec(compile("def _fifo_cost" + body, "<variant>", "exec"), ns)


# The per-style LUT expressions live in common.py, shared with score.py --columns. The
# hi-space promotion branch is only ever rewritten here.
HI_URAM = "if hi >= URAM_MIN_ABITS or hi_bram >= HI_URAM_RATIO * hi_uram:"


# --------------------------------------------------------- _hi_in_ram predicate


@variant("hi_in_ram: rows*W >= 2**14")
def hi_in_ram_area(ns):
    ns["_hi_in_ram"] = lambda rows, W: rows * W >= 2**14


@variant("hi_in_ram: rows >= 512")
def hi_in_ram_rows_512(ns):
    ns["_hi_in_ram"] = lambda rows, W: rows >= 512


@variant("hi_in_ram: rows >= 1024")
def hi_in_ram_rows_1024(ns):
    ns["_hi_in_ram"] = lambda rows, W: rows >= 1024


@variant("hi_in_ram: rows**2 * W >= 2**20 (current)")
def hi_in_ram_current(ns):
    pass


@variant("hi_in_ram: rows**1.5 * W >= 2**16")
def hi_in_ram_rows_1p5(ns):
    ns["_hi_in_ram"] = lambda rows, W: rows**1.5 * W >= 2**16


@variant("hi_in_ram: rows*_lutram_luts(rows,W) >= 2**14")
def hi_in_ram_lutram_cost(ns):
    ns["_hi_in_ram"] = lambda rows, W: rows * ns["_lutram_luts"](rows, W) >= 2**14


@variant("hi_in_ram: always True")
def hi_in_ram_always(ns):
    ns["_hi_in_ram"] = lambda rows, W: True


@variant("hi_in_ram: always False")
def hi_in_ram_never(ns):
    ns["_hi_in_ram"] = lambda rows, W: False


# ------------------------------------------------ memory plans and aspect ladders


@variant("bram18: no word splitting on either family")
def bram18_no_split(ns):
    def plan(rows, W, versal=False):
        ladder = ns["RAMB18E5_SDP"] if versal else ns["RAMB18_SDP"]
        return min(
            (math.ceil(rows / cfg_rows) * math.ceil(W / cfg_w), math.ceil(rows / cfg_rows))
            for cfg_w, cfg_rows in ladder
        )

    ns["_bram18_plan"] = plan


@variant("bram18: greedy widest-first split (US+)")
def bram18_greedy_split(ns):
    exhaustive = ns["_bram18_plan"]

    def plan(rows, W, versal=False):
        if versal:
            return exhaustive(rows, W, True)
        tiles = groups = 0
        rest = W
        for cfg_w, cfg_rows in ns["RAMB18_SDP"]:
            n = rest // cfg_w if cfg_w > 1 else rest
            if n:
                t = math.ceil(rows / cfg_rows)
                tiles += n * t
                groups = max(groups, t)
                rest -= n * cfg_w
        if rest:
            t = math.ceil(rows / 512)
            tiles += t
            groups = max(groups, t)
        return tiles, groups

    ns["_bram18_plan"] = plan


@variant("bram18: US+ ladder truncated to 36/18/9")
def bram18_ladder_9(ns):
    ns["RAMB18_SDP"] = ((36, 512), (18, 1024), (9, 2048))
    ns["_bram18_plan"].cache_clear()


@variant("bram18: US+ ladder truncated to 36/18/9/4")
def bram18_ladder_4(ns):
    ns["RAMB18_SDP"] = ((36, 512), (18, 1024), (9, 2048), (4, 4096))
    ns["_bram18_plan"].cache_clear()


@variant("uram: Versal ladder truncated to 72/36")
def uram_ladder_36(ns):
    ns["URAM288_SDP"] = ((72, 4096), (36, 8192))


@variant("block: groups -> ceil(rows/512), no DP cascade")
def bram18_flat_groups(ns):
    exhaustive = ns["_bram18_plan"]
    ns["_bram18_plan"] = lambda rows, W, versal=False: (
        exhaustive(rows, W, versal)[0],
        math.ceil(rows / 512),
    )


# ------------------------------------------------------------------ shift style


@variant("shift: drop cascade mux (Versal)")
def shift_no_mux(ns):
    ns["_cascade_mux_luts"] = lambda stages, W, versal: 0


@variant("shift: cascade mux without the -2 intercept")
def shift_mux_no_intercept(ns):
    def mux(stages, W, versal):
        if not versal or stages < 2:
            return 0
        per_bit = 1 if stages > 2 else 0.5
        return math.ceil(W * per_bit * math.ceil((stages - 1) / 3))

    ns["_cascade_mux_luts"] = mux


@variant("shift: cascade mux W*ceil((stages-1)/3), no half step")
def shift_mux_no_half_step(ns):
    def mux(stages, W, versal):
        if not versal or stages < 2:
            return 0
        return W * math.ceil((stages - 1) / 3) - 2

    ns["_cascade_mux_luts"] = mux


@variant("shift: cascade mux, no -2 AND no half step")
def shift_mux_bare(ns):
    def mux(stages, W, versal):
        if not versal or stages < 2:
            return 0
        return W * math.ceil((stages - 1) / 3)

    ns["_cascade_mux_luts"] = mux


# ------------------------------------------------------------ distributed style


@variant("distributed: drop the 128-row mux term")
def dist_no_mux(ns):
    patch_cost(ns, (DIST_LUT, DIST_LUT.replace("mux_luts + ", "")))


# ------------------------------------------------------------------ block style


@variant("block: drop the 3*groups cascade term")
def block_no_cascade(ns):
    patch_cost(ns, (BLOCK_LUT, BLOCK_LUT.replace("3 * groups + ", "")))


@variant("block: drop the 2*tiles/5 enable term")
def block_no_enable(ns):
    patch_cost(ns, (BLOCK_LUT, BLOCK_LUT.replace("2 * tiles // 5 + ", "")))


@variant("block: constant only (54 + hi flag)")
def block_constant_only(ns):
    patch_cost(ns, (BLOCK_LUT, "lut = 54 + (19 if hi else 0)"))


@variant("block: 2*tiles/5 -> tiles/2")
def block_enable_half(ns):
    patch_cost(ns, (BLOCK_LUT, BLOCK_LUT.replace("2 * tiles // 5", "tiles // 2")))


@variant("block: 3*groups -> 2*groups")
def block_cascade_two(ns):
    patch_cost(ns, (BLOCK_LUT, BLOCK_LUT.replace("3 * groups", "2 * groups")))


# ------------------------------------------------------------------ ultra style


@variant("ultra: drop the 6*cw counter term")
def ultra_no_counter(ns):
    patch_cost(ns, (ULTRA_LUT, ULTRA_LUT.replace("6 * cw + ", "")))


@variant("ultra: drop the hi selector term")
def ultra_no_hi_select(ns):
    patch_cost(ns, (ULTRA_LUT, "lut = 6 * cw"))


@variant("ultra: read path flat at 1*W (no cascade scaling)")
def ultra_read_flat(ns):
    patch_cost(ns, (ULTRA_READ, "lut += W"))


@variant("ultra read path: ceil(cascade/4)-1 instead of max(1,c//4-2)")
def ultra_read_ceil(ns):
    patch_cost(ns, (ULTRA_READ, "lut += max(1, math.ceil(groups / 4) - 1) * W"))


@variant("ultra: drop the Versal flat 20 LUT")
def ultra_no_versal_flat(ns):
    patch_cost(ns, ("lut += VERSAL_URAM_LUTS", "lut += 0"))


# ----------------------------------------- hi space: selector and URAM promotion


@variant("hi selector: drop its W term (constant only)")
def hi_select_constant(ns):
    patch_cost(ns, (BLOCK_LUT, BLOCK_LUT.replace("_hi_select_luts(W)", "18")))


@variant("hi selector: shared, refitted as 15 + W//2")
def hi_select_15_half(ns):
    patch_cost(
        ns,
        (BLOCK_LUT, BLOCK_LUT.replace("_hi_select_luts(W)", "15 + W // 2")),
        (ULTRA_LUT, ULTRA_LUT.replace("_hi_select_luts(W)", "15 + W // 2")),
    )


@variant("hi selector: shared, W//2 with no constant")
def hi_select_half(ns):
    patch_cost(
        ns,
        (BLOCK_LUT, BLOCK_LUT.replace("_hi_select_luts(W)", "W // 2")),
        (ULTRA_LUT, ULTRA_LUT.replace("_hi_select_luts(W)", "W // 2")),
    )


@variant("hi selector: shared, refitted as 15 + 2*W//5")
def hi_select_15_two_fifths(ns):
    patch_cost(
        ns,
        (BLOCK_LUT, BLOCK_LUT.replace("_hi_select_luts(W)", "15 + 2 * W // 5")),
        (ULTRA_LUT, ULTRA_LUT.replace("_hi_select_luts(W)", "15 + 2 * W // 5")),
    )


@variant("hi selector: two separate fits, one per style (pre-merge)")
def hi_select_split_fits(ns):
    patch_cost(
        ns,
        (BLOCK_LUT, BLOCK_LUT.replace("_hi_select_luts(W)", "19 + 2 * W // 5")),
        (ULTRA_LUT, ULTRA_LUT.replace("_hi_select_luts(W)", "11 + W // 2")),
    )


@variant("ultra/hi: drop the >= 4096 rows promotion branch")
def hi_uram_no_rows_branch(ns):
    patch_cost(ns, (HI_URAM, "if hi_bram >= HI_URAM_RATIO * hi_uram:"))


@variant("ultra/hi: drop the HI_URAM_RATIO branch")
def hi_uram_no_ratio_branch(ns):
    patch_cost(ns, (HI_URAM, "if hi >= URAM_MIN_ABITS:"))


@variant("ultra/hi: always promote hi into URAM")
def hi_uram_always(ns):
    patch_cost(ns, (HI_URAM, "if True:"))


@variant("ultra/hi: never promote hi into URAM")
def hi_uram_never(ns):
    patch_cost(ns, (HI_URAM, "if False:"))


# ------------------------------------------------------------- _lutram_luts term


@variant("lutram: drop the 5/4 overhead factor")
def lutram_no_overhead(ns):
    ns["_lutram_luts"] = lambda rows, W: math.ceil(rows / 32) * math.ceil(W / 2)


@variant("lutram: pure storage + ceil(5/32 * storage)")
def lutram_overhead_5_32(ns):
    def luts(rows, W):
        storage = math.ceil(rows / 32) * math.ceil(W / 2)
        return storage + math.ceil(5 * storage / 32)

    ns["_lutram_luts"] = luts


@variant("lutram: 9/8 instead of 5/4")
def lutram_overhead_9_8(ns):
    ns["_lutram_luts"] = lambda rows, W: math.ceil(rows / 32) * math.ceil(W / 2) * 9 // 8


@variant("lutram: 6/5 instead of 5/4")
def lutram_overhead_6_5(ns):
    ns["_lutram_luts"] = lambda rows, W: math.ceil(rows / 32) * math.ceil(W / 2) * 6 // 5


# ----------------------------------------------------------- occupancy counters


@variant("shift: drop the 5*cw counter term")
def shift_no_counter(ns):
    patch_cost(ns, (SHIFT_LUT, SHIFT_LUT.replace("5 * cw - 7", "0")))


@variant("distributed: drop the 8*cw counter term")
def dist_no_counter(ns):
    patch_cost(ns, (DIST_LUT, DIST_LUT.replace("8 * cw - 15", "0")))


@variant("counters: one shared 6*cw-10 for all styles")
def counters_shared(ns):
    patch_cost(
        ns,
        (SHIFT_LUT, SHIFT_LUT.replace("5 * cw - 7", "6 * cw - 10")),
        (DIST_LUT, DIST_LUT.replace("8 * cw - 15", "6 * cw - 10")),
    )


@variant("counters: drop both intercepts (-7, -15)")
def counters_no_intercepts(ns):
    patch_cost(
        ns,
        (SHIFT_LUT, SHIFT_LUT.replace(" - 7", "")),
        (DIST_LUT, DIST_LUT.replace(" - 15", "")),
    )


@variant("counters: cw = clog2(depth) instead of clog2(depth+1)+1")
def counters_narrow(ns):
    patch_cost(ns, ("cw = _clog2(depth + 1) + 1", "cw = _clog2(depth)"))


# --------------------------------------------------------------------- scoring


def report(label, ns, runs, base=None):
    """Scores one namespace over both part families and prints its row of the table."""
    cost = ns["_fifo_cost"]
    acc = Score(runs, {"v": lambda run: cost(run.depth, run.width, run.style, run.versal)})
    lut_err, lut_tot = acc.total("v", 2)
    lut = 100 * lut_err / lut_tot
    misses = acc.misses["v"]

    def style_pct(style):
        err, tot = acc.column(style, "v", 2)
        return 100 * err / tot if tot else 0

    cols = " ".join("%s %4.1f%%" % (s[:4], style_pct(s)) for s in STYLES)
    delta = "" if base is None else "  (%+.1f)" % (lut - base)
    print("%-59s LUT %5.1f%%%s  mem-miss %2d | %s" % (label, lut, delta, len(misses), cols))
    return lut, misses


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("filter", nargs="?", help="only run variants whose label contains this")
    args = parser.parse_args()

    runs = list(read_runs(*SCORED_SETS))
    base, base_misses = report("BASELINE (working tree)", load_model(), runs)
    print()
    for label, mutate in VARIANTS.items():
        if args.filter and args.filter not in label:
            continue
        ns = load_model()
        mutate(ns)
        report(label, ns, runs, base)
    if base_misses:
        print("\nbaseline memory misses:", base_misses)


if __name__ == "__main__":
    main()
