# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Shared plumbing for the FIFO cost-model harness.

Names the data sets, reads the measured runs out of `data/`, scores a model against
them, and loads the model itself from the working tree so that neither `score.py` nor
`ablate.py` holds a transcription that can drift away from what the compiler runs.
"""

import os
import re
from collections import defaultdict, namedtuple
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FIFO_PY = "src/finn/custom_op/fpgadataflow/streamingfifo.py"

STYLES = ("shift", "distributed", "block", "ultra")

# xczu7ev-ffvc1156-2-e
US_SETS = ("results", "holdout_results", "deep_results", "val2_results")
# xcvc1902-vsva2197-2MP-e-S
VERSAL_FIT_SETS = ("versal_results", "versal_fit_results")
# held out, nothing is fitted against it
VERSAL_VAL_SET = "versal_val_results"
# 6 configs x 4 synth_design directives, so re-syntheses of runs already in US_SETS
NOISE_SET = "noise_results"
ALL_SETS = US_SETS + VERSAL_FIT_SETS + (VERSAL_VAL_SET, NOISE_SET)
# every set the model is scored over, excluding the noise re-syntheses
SCORED_SETS = US_SETS + VERSAL_FIT_SETS + (VERSAL_VAL_SET,)

# Vivado reports RAMB18 and RAMB36 separately. One RAMB36 is two RAMB18 of storage.
RAMB18_PER_RAMB36 = 2

# the columns the estimators predict, per measured configuration
Run = namedtuple("Run", "tag depth width style versal ramb18 uram lut")

# The LUT expressions of _fifo_cost, quoted verbatim from streamingfifo.py. score.py
# checks its per-column predictions against these and ablate.py rewrites them, so both
# fail loudly rather than silently working from a stale copy. See assert_exprs().
SHIFT_LUT = "return FifoCost(0, 0, stages * W + mux + 5 * cw - 7)"
DIST_LUT = "return FifoCost(0, 0, _lutram_luts(rows, W) + mux_luts + 8 * cw - 15)"
BLOCK_LUT = "lut = 54 + 3 * groups + 2 * tiles // 5 + (_hi_select_luts(W) if hi else 0)"
ULTRA_LUT = "lut = 6 * cw + (_hi_select_luts(W) if hi else 0)"
ULTRA_READ = "lut += max(1, groups // 4 - 2) * W"
# the two geometry expressions the --columns predictions rebuild from the column data
SHIFT_STAGES = "depth_impl = depth - 1 if depth > 4 else 4"
DIST_ROWS = "rows = 2 ** _clog2(depth - 1)"


@lru_cache(maxsize=None)
def model_source():
    """streamingfifo.py as it stands in the working tree."""
    with open(os.path.join(ROOT, FIFO_PY)) as f:
        return f.read()


def assert_exprs(*exprs):
    """Raises unless every expression still appears verbatim in the model source.

    The caller holds a transcription of each, so a model edit that moves one must
    break the caller rather than leave it scoring against a formula the compiler no
    longer uses.
    """
    src = model_source()
    for expr in exprs:
        if expr not in src:
            raise RuntimeError("%s no longer contains: %s" % (FIFO_PY, expr))


def load_model():
    """The model block of streamingfifo.py, executed in a fresh namespace.

    Everything above `class StreamingFIFO` is self-contained but for the FINN and
    qonnx imports, which only the class below needs.
    """
    src = model_source().split("class StreamingFIFO")[0]
    src = "\n".join(ln for ln in src.split("\n") if not re.match(r"from (finn|qonnx)", ln))
    ns = {}
    exec(compile(src, FIFO_PY, "exec"), ns)
    return ns


def read_columns(*names):
    """Yields every reported column of every run in the named sets, as a dict."""
    for name in names:
        with open(os.path.join(HERE, "data", name + ".txt")) as f:
            for line in f:
                if not line.startswith("RESULT"):
                    continue
                fields = line.split()
                row = dict(kv.split("=") for kv in fields[2:])
                row = {k: (int(v) if v.lstrip("-").isdigit() else v) for k, v in row.items()}
                row["tag"] = fields[1]
                row["versal"] = name.startswith("versal")
                yield row


def read_runs(*names):
    """Yields the runs in the named sets as Run records."""
    for row in read_columns(*names):
        yield Run(
            row["tag"],
            row["depth"],
            row["width"],
            row["style"],
            row["versal"],
            RAMB18_PER_RAMB36 * row["ramb36"] + row["ramb18"],
            row["uram"],
            row["lut"],
        )


class Score:
    """Summed absolute error of one or more models over a set of runs.

    Errors accumulate per style and per predicted column, alongside the measured total
    each is a fraction of, so a report can slice by either. Column indices follow the
    FifoCost order the models return: 0 RAMB18, 1 URAM, 2 LUT.
    """

    def __init__(self, runs, models):
        self.runs = runs
        # (style, column) -> measured total, and (style, model, column) -> summed error
        self.measured = defaultdict(int)
        self.error = defaultdict(int)
        self.n = defaultdict(int)
        # (style, depth, width, versal, measured, predicted) per config a model misses
        self.misses = defaultdict(list)
        for run in runs:
            self.n[run.style] += 1
            predictions = {name: model(run) for name, model in models.items()}
            for i, meas in enumerate((run.ramb18, run.uram, run.lut)):
                self.measured[run.style, i] += meas
                for name, pred in predictions.items():
                    self.error[run.style, name, i] += abs(pred[i] - meas)
            for name, pred in predictions.items():
                memory = tuple(pred[:2])
                if memory != (run.ramb18, run.uram):
                    config = (run.style, run.depth, run.width, run.versal)
                    self.misses[name].append(config + ((run.ramb18, run.uram), memory))

    def styles(self):
        """The styles present, in STYLES order."""
        return [s for s in STYLES if self.n[s]]

    def column(self, style, name, i):
        """(summed error, measured total) for one style, model and column."""
        return self.error[style, name, i], self.measured[style, i]

    def total(self, name, i):
        """(summed error, measured total) for one model and column, over all styles."""
        styles = self.styles()
        return (
            sum(self.error[s, name, i] for s in styles),
            sum(self.measured[s, i] for s in styles),
        )

    def exact(self, name):
        """How many runs the model got both memory columns right on."""
        return len(self.runs) - len(self.misses[name])


def pct(err, total):
    """An error as a percentage of the measured total it is a fraction of."""
    return "%.0f%%" % (100 * err / total) if total else "-"
