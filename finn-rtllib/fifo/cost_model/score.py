# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Scores FINN's FIFO resource estimators against measured synthesis results.

Reproduces the before/after table in SUMMARY.md. The current model is loaded from
the working tree and the legacy one is executed straight out of `git show main:`, so
neither is transcribed and neither can drift from what the compiler actually runs.

`--columns` is the exception: it rebuilds each term from the column data, so it does
hold a transcription. assert_exprs() pins every one of them to the model source.

    python3 score.py              # per-style before/after
    python3 score.py --sets       # per data set, current model only
    python3 score.py --noise      # synthesis-directive spread on identical RTL
    python3 score.py --versal     # Versal, fit set and held-out validation set
    python3 score.py --columns    # each term against the column it claims to explain
"""

import argparse
import math
import re
import subprocess
from collections import defaultdict
from common import (
    ALL_SETS,
    BLOCK_LUT,
    DIST_LUT,
    DIST_ROWS,
    FIFO_PY,
    NOISE_SET,
    ROOT,
    SHIFT_LUT,
    SHIFT_STAGES,
    ULTRA_LUT,
    ULTRA_READ,
    US_SETS,
    VERSAL_FIT_SETS,
    VERSAL_VAL_SET,
    Score,
    assert_exprs,
    load_model,
    pct,
    read_columns,
    read_runs,
)
from types import SimpleNamespace

# --------------------------------------------------------------- model loading


def load_current():
    """_fifo_cost() from the working tree, as (bram, uram, lut) of a Run."""
    cost = load_model()["_fifo_cost"]
    return lambda run: tuple(cost(run.depth, run.width, run.style, run.versal))[:3]


def load_legacy(ref="main"):
    """The estimators as they stand on `ref`, run against a stub node.

    Only three methods are lifted out. Everything they touch is a node attribute or
    one of the shape helpers, so a stub is enough to drive the real code. The dispatch
    around them is reconstructed here: StreamingFIFO carried two implementations and
    insert_fifo/set_fifo_depths sent anything past max_qsrl_depth=256 to the Vivado
    IP, which additionally rounded depth up to a power of two.
    """
    src = subprocess.run(
        ["git", "-C", ROOT, "show", "%s:%s" % (ref, FIFO_PY)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    wanted = ("bram_estimation", "uram_estimation", "lut_estimation")
    body, keep = [], False
    for line in src.split("\n"):
        method = re.match(r"    def (\w+)", line)
        if method:
            keep = method.group(1) in wanted
        if keep:
            body.append(line)
    if not body:
        raise RuntimeError("no estimators found in %s:%s" % (ref, FIFO_PY))
    ns = {"math": math}
    exec("class Legacy:\n" + "\n".join(body), ns)

    class Stub(ns["Legacy"]):
        def __init__(self, run):
            self.attrs = {
                "depth": run.depth,
                "ram_style": run.style,
                # the max_qsrl_depth=256 dispatch: Q_srl below, axis_data_fifo above
                "impl_style": "rtl" if run.depth <= 256 else "vivado",
            }
            self.W = run.width

        def get_nodeattr(self, key):
            return self.attrs[key]

        def get_adjusted_depth(self):
            depth = self.attrs["depth"]
            if self.attrs["impl_style"] == "vivado":
                depth = 1 << (depth - 1).bit_length()
            return depth

        def get_instream_width(self, ind=0):
            return self.W

    def cost(run):
        node = Stub(run)
        # bram_estimation is already denominated in RAMB18, see the 36x512 branch and
        # bram_efficiency_estimation's "* 36 * 512"
        return node.bram_estimation(), node.uram_estimation(), node.lut_estimation()

    return cost


# ------------------------------------------------------------- default: old/new


def report_overall(cur, legacy):
    runs = list(read_runs(*US_SETS))
    acc = Score(runs, {"old": legacy, "new": cur})
    header = ("style", "n", "RAMB18 old/new", "URAM old/new", "LUT old/new")
    print("%-13s %4s | %14s | %14s | %14s" % header)

    def pair(old, new):
        """Both models against the one measured total they share."""
        (old_err, tot), (new_err, _) = old, new
        return "%6s %6s" % (pct(old_err, tot), pct(new_err, tot))

    for style in acc.styles():
        cols = (pair(acc.column(style, "old", i), acc.column(style, "new", i)) for i in range(3))
        print("%-13s %4d | %s | %s | %s" % (style, acc.n[style], *cols))
    cols = [pair(acc.total("old", i), acc.total("new", i)) for i in range(3)]
    print("%-13s %4d | %s | %s | %s" % ("ALL", len(runs), *cols))

    lut_err, lut_tot = acc.total("new", 2)
    print(
        "\nmemory exact on %d/%d configs; LUT error %.1f%% of %d measured"
        % (acc.exact("new"), len(runs), 100 * lut_err / lut_tot, lut_tot)
    )


# ------------------------------------------------------------------- --sets


def report_sets(cur):
    print("%-16s %5s %8s %8s %8s" % ("set", "n", "RAMB18", "URAM", "LUT"))
    for name in US_SETS:
        runs = list(read_runs(name))
        acc = Score(runs, {"cur": cur})
        cols = (pct(*acc.total("cur", i)) for i in range(3))
        print("%-16s %5d %8s %8s %8s" % (name, len(runs), *cols))


# ------------------------------------------------------------------- --versal


def report_versal(cur):
    """Versal fit set, then the held-out validation set.

    Nothing was fitted against versal_val_results, so the gap between the two blocks
    is what the LUT terms cost off their fit set.
    """
    ref = {(r.depth, r.width, r.style): (r.ramb18, r.uram) for r in read_runs(*US_SETS)}
    for title, names in (("fit", VERSAL_FIT_SETS), ("validation", (VERSAL_VAL_SET,))):
        runs = sorted(read_runs(*names), key=lambda r: (r.depth, r.width))
        acc = Score(runs, {"cur": cur})
        print("%-13s %4s | %8s %8s | %s" % (title, "n", "RAMB18", "URAM", "LUT"))
        for style in acc.styles():
            cols = (pct(*acc.column(style, "cur", i)) for i in range(3))
            print("%-13s %4d | %8s %8s | %6s" % (style, acc.n[style], *cols))
        cols = [pct(*acc.total("cur", i)) for i in range(3)]
        print(
            "%-13s %4d | %8s %8s | %6s   memory exact %d/%d\n"
            % ("ALL", len(runs), *cols, acc.exact("cur"), len(runs))
        )
        for style, depth, width, _, measured, predicted in acc.misses["cur"]:
            print(
                "  memory miss: %-9s %dx%d  measured %d/%d  model %d/%d"
                % (style, depth, width, *measured, *predicted)
            )
        # where a config was also run on xczu7ev, the part family is the only variable,
        # so a difference there is a real family effect and not measurement noise
        paired = [
            (r.ramb18, r.uram) == ref[(r.depth, r.width, r.style)]
            for r in runs
            if (r.depth, r.width, r.style) in ref
        ]
        print(
            "  memory identical to xczu7ev on %d/%d paired configs\n" % (sum(paired), len(paired))
        )


# -------------------------------------------------------------------- --noise


def report_noise(cur):
    """Identical RTL and part under four synth_design directives.

    Establishes how much of the LUT residual is a property of the tool rather than
    of the model.
    """
    by_config = defaultdict(dict)
    for run in read_runs(NOISE_SET):
        directive = run.tag.split("_w%d_" % run.width, 1)[1]
        by_config[(run.depth, run.width, run.style)][directive] = run
    directives = sorted({d for v in by_config.values() for d in v})
    print(
        "%-22s %s %8s %7s %7s"
        % ("config", " ".join("%6s" % d[:6] for d in directives), "spread", "model", "memory")
    )
    spread = base = 0
    for (depth, width, style), measured in sorted(by_config.items()):
        runs = [measured[d] for d in directives]
        luts = [r.lut for r in runs]
        spread += max(luts) - min(luts)
        base += min(luts)
        memory = {(r.ramb18, r.uram) for r in runs}
        print(
            "%-22s %s %7.0f%% %7d %7s"
            % (
                "%s %dx%d" % (style, depth, width),
                " ".join("%6d" % x for x in luts),
                100 * (max(luts) - min(luts)) / min(luts),
                cur(runs[0])[2],
                "same" if len(memory) == 1 else "DIFFERS",
            )
        )
    print("\nmean LUT spread from the directive alone: %.1f%%" % (100 * spread / base))


# ------------------------------------------------------------------ --columns


def report_columns():
    """Each term against the Vivado column it claims to explain.

    Score() checks the model against the total LUT count, which a term can match for
    the wrong reason. Vivado also reports that total split into lut_logic + lut_mem
    and lut_mem into lutram + srl, so each term can be checked against its own column.

    The predictions below are transcribed from _fifo_cost rather than called, since a
    term has to be evaluated apart from the sum it normally lands in. assert_exprs()
    is what keeps those transcriptions honest.
    """
    assert_exprs(SHIFT_LUT, SHIFT_STAGES, DIST_LUT, DIST_ROWS, BLOCK_LUT, ULTRA_LUT, ULTRA_READ)
    m = SimpleNamespace(**load_model())
    runs = list(read_columns(*ALL_SETS))
    broken = sum(
        1
        for r in runs
        if r["lut"] != r["lut_logic"] + r["lut_mem"] or r["lut_mem"] != r["lutram"] + r["srl"]
    )
    # the scored sets plus the noise re-syntheses, which are usable here but not there
    print("%d runs; the two column identities hold on %d\n" % (len(runs), len(runs) - broken))
    _columns_shift(m, runs)
    _columns_lutram_shape(m, runs)
    _columns_distributed(m, runs)
    _columns_block(m, runs)
    _columns_lutram_transfer(m, runs)
    _columns_ultra(m, runs)
    _columns_hi_in_ram(m, runs)


def _shift_stages(depth):
    return math.ceil((depth - 1 if depth > 4 else 4) / 32)


def _columns_shift(m, runs):
    """The srl column measures the storage term outright, isolating the rest."""
    shift = [r for r in runs if r["style"] == "shift"]
    exact = sum(1 for r in shift if r["srl"] == _shift_stages(r["depth"]) * r["width"])
    print("shift storage       srl == stages * W          exact on %d/%d" % (exact, len(shift)))

    err = tot = 0
    residuals = []
    for r in shift:
        mux = m._cascade_mux_luts(_shift_stages(r["depth"]), r["width"], r["versal"])
        pred = mux + 5 * (m._clog2(r["depth"] + 1) + 1) - 7
        err += abs(r["lut_logic"] - pred)
        tot += r["lut_logic"]
        residuals.append((r["lut_logic"] - pred, r["depth"], r["width"]))
    print(
        "shift control+mux   vs lut_logic alone        %.0f%% of %d measured"
        % (100 * err / tot, tot)
    )
    residuals.sort(key=lambda x: -abs(x[0]))
    print("  largest residuals: %s" % ", ".join("%+d at %dx%d" % w for w in residuals[:4]))


def _columns_lutram_shape(m, runs):
    """The LUTRAM term is a row factor times a width factor.

    Dividing the measured column by the row factor isolates the width factor, and it
    comes out the same at every depth. So the shape is right and everything
    unexplained is univariate in W.
    """
    per_width = defaultdict(set)
    for r in runs:
        if r["style"] != "distributed":
            continue
        banks = math.ceil(2 ** m._clog2(r["depth"] - 1) / 32)
        per_width[r["width"]].add(r["lutram"] / banks)
    varying = [w for w, v in per_width.items() if len(v) > 1]
    print(
        "\ndistributed LUTRAM  measured / ceil(rows/32)  same at every depth for %d/%d widths"
        % (len(per_width) - len(varying), len(per_width))
    )
    print("     W   per bank   ceil(W/2)   ratio      model")
    for w in sorted(per_width):
        factor = list(per_width[w])[0]
        print(
            "  %4d %10.0f %11d %7.3f %10d"
            % (w, factor, math.ceil(w / 2), factor / math.ceil(w / 2), m._lutram_luts(32, w))
        )


def _columns_distributed(m, runs):
    """The LUTRAM is its own column, so mux and counter answer for lut_logic alone."""
    dist = [r for r in runs if r["style"] == "distributed"]
    err = tot = 0
    for r in dist:
        rows = 2 ** m._clog2(r["depth"] - 1)
        pred = rows // 128 * (r["width"] // 2) + 8 * (m._clog2(r["depth"] + 1) + 1) - 15
        err += abs(r["lut_logic"] - pred)
        tot += r["lut_logic"]
    print(
        "\ndistributed mux+ctl vs lut_logic alone       %.0f%% of %d, and srl == 0 on %d/%d"
        % (100 * err / tot, tot, sum(1 for r in dist if r["srl"] == 0), len(dist))
    )


def _columns_block(m, runs):
    """The block branch charges no shift register and the srl column agrees.

    So its terms are testable against lut_logic with only the LUTRAM hi space taken
    out. It comes out where the total does, no pair of compensating errors hiding
    under the sum, unlike the ultra branch.
    """
    block = [r for r in runs if r["style"] == "block"]
    err = tot = 0
    for r in block:
        hi = m._geometry(r["depth"], False)[1]
        tiles, groups = m._bram_plan(r["depth"], r["width"], r["versal"])
        pred = 54 + 3 * groups + 2 * tiles // 5 + (m._hi_select_luts(r["width"]) if hi else 0)
        err += abs(r["lut_logic"] - pred)
        tot += r["lut_logic"]
    print(
        "\nblock control+tiles vs lut_logic alone      %.0f%% of %d, and srl == 0 on %d/%d"
        % (100 * err / tot, tot, sum(1 for r in block if r["srl"] == 0), len(block))
    )


def _columns_lutram_transfer(m, runs):
    """_lutram_luts is fitted on distributed runs, but block/ultra reuse it.

    They reuse it for a hi space that lands in LUTRAM, where the lutram column measures
    it directly. That is an out-of-style prediction.
    """
    hi_spaces = []
    for r in runs:
        if r["style"] not in ("block", "ultra"):
            continue
        hi = m._geometry(r["depth"], r["style"] == "ultra")[1]
        if hi and not m._hi_in_ram(2**hi, r["width"]):
            hi_spaces.append((r, 2**hi))
    err = sum(abs(r["lutram"] - m._lutram_luts(rows, r["width"])) for r, rows in hi_spaces)
    tot = sum(r["lutram"] for r, _ in hi_spaces)
    print(
        "\nlutram term         on block/ultra hi spaces  %.0f%% of %d over %d runs, "
        "never fitted there" % (100 * err / tot, tot, len(hi_spaces))
    )


def _columns_ultra(m, runs):
    """The URAM read path is the largest fitted term in the model.

    srl and lut_logic between them measure it: subtract every other ultra term from
    their sum and what is left is the read path, per bit of width.
    """
    ultra = [r for r in runs if r["style"] == "ultra"]
    err = tot = 0
    by_cascade = defaultdict(lambda: [0, 0])
    delay = defaultdict(set)
    for r in ultra:
        W = r["width"]
        lo, hi = m._geometry(r["depth"], True)
        hi_lutram = bool(hi) and not m._hi_in_ram(2**hi, W)
        other = 6 * (m._clog2(r["depth"] + 1) + 1) + (m._hi_select_luts(W) if hi else 0)
        other += m.VERSAL_URAM_LUTS if r["versal"] else 0
        other += W if hi_lutram else 0  # the SRL16E delaying LUTRAM to URAM latency
        meas = r["srl"] + r["lut_logic"] - other
        cascade = m._uram_plan(2**lo, W, r["versal"])[1]
        err += abs(meas - max(1, cascade // 4 - 2) * W)
        # the residual can go negative, so the measured column is not itself a total
        tot += abs(meas)
        by_cascade[cascade][0] += meas
        by_cascade[cascade][1] += W
        delay[(hi_lutram, W)].add(r["srl"])
    print(
        "\nultra read path     vs srl + lut_logic        %.0f%% of %d over %d runs"
        % (100 * err / tot, tot, len(ultra))
    )
    print("    cascade   measured/W   model/W")
    for cascade in sorted(by_cascade):
        meas, width = by_cascade[cascade]
        print("  %9d %12.2f %9d" % (cascade, meas / width, max(1, cascade // 4 - 2)))

    # The +W the model charges a LUTRAM hi space under URAM is a delay line, so it is
    # srl, and it is the whole difference between the two populations.
    pure = {W: v for (lutram, W), v in delay.items() if not lutram}
    paired = sorted((W, v) for (lutram, W), v in delay.items() if lutram and W in pure)
    exact = sum(1 for W, v in paired if v == {2 * W + 1} and min(pure[W]) in (W, W + 1))
    print(
        "  the LUTRAM delay line   srl == 2W + 1 wherever the hi space is in LUTRAM, "
        "exactly W over the runs that have none: %d/%d widths" % (exact, len(paired))
    )


def _columns_hi_in_ram(m, runs):
    """_hi_in_ram is fitted against the RAMB18/URAM columns.

    lutram is a column it is never fitted to, and it answers the same question
    independently: a hi space in LUTRAM shows up there, a hi space in RAM does not.
    """
    ok = bad = 0
    misses = []
    truth = defaultdict(set)
    for r in runs:
        if r["style"] not in ("block", "ultra"):
            continue
        hi = m._geometry(r["depth"], r["style"] == "ultra")[1]
        if not hi:
            continue
        truth[(2**hi, r["width"])].add(r["lutram"] == 0)
        if m._hi_in_ram(2**hi, r["width"]) == (r["lutram"] == 0):
            ok += 1
        else:
            bad += 1
            misses.append("%s %dx%d" % (r["style"], r["depth"], r["width"]))
    print(
        "\n_hi_in_ram          vs the lutram column      agrees on %d/%d hi spaces" % (ok, ok + bad)
    )
    if misses:
        print("  disagrees on: %s" % ", ".join(sorted(set(misses))))

    # Deduplicated, the same hi space never lands differently in two runs, so the
    # outcome is a function of (rows, W) alone, not of the style that asked for it,
    # the part family or the depth that produced the geometry. That makes the point set
    # a ground truth a candidate predicate can be swept against, at its own best
    # threshold rather than an inherited one.
    clash = sum(1 for v in truth.values() if len(v) > 1)
    y = {k: next(iter(v)) for k, v in truth.items()}
    print(
        "\n%d distinct (rows, W) hi spaces, %d disagreeing across style/part/depth"
        % (len(y), clash)
    )
    print("  candidate                 best threshold   misclassified")
    candidates = (
        ("W alone", lambda rows, W: W),
        ("rows alone", lambda rows, W: rows),
        ("rows ** 2", lambda rows, W: rows * rows),
        ("rows * W", lambda rows, W: rows * W),
        ("rows ** 3 * W", lambda rows, W: rows**3 * W),
        ("rows ** 2 * W  (model)", lambda rows, W: rows * rows * W),
    )
    for name, f in candidates:
        wrong, threshold = min(
            (sum(1 for (rows, W), t in y.items() if (f(rows, W) >= c) != t), c)
            for c in {f(rows, W) for rows, W in y}
        )
        print("  %-22s %18d %15d" % (name, threshold, wrong))


# ----------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sets", action="store_true", help="per UltraScale+ set, current model only")
    mode.add_argument("--noise", action="store_true", help="LUT spread across synth directives")
    mode.add_argument("--versal", action="store_true", help="Versal fit and held-out sets")
    mode.add_argument("--columns", action="store_true", help="each term against its own column")
    args = parser.parse_args()

    if args.columns:
        report_columns()
        return
    cur = load_current()
    if args.sets:
        report_sets(cur)
    elif args.noise:
        report_noise(cur)
    elif args.versal:
        report_versal(cur)
    else:
        report_overall(cur, load_legacy())


if __name__ == "__main__":
    main()
