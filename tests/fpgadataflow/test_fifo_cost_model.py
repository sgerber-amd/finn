# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Gates the FIFO resource estimators against the measured synthesis results.

`finn-rtllib/fifo/cost_model/` holds out-of-context synthesis runs of fifo.sv over
both part families, and a harness that scores _fifo_cost() against them. That harness
is a manual tool. This is the part of it worth failing a build over: a model edit that
loses memory exactness or moves the LUT residual well outside the tool's own spread.

The thresholds are deliberately loose. noise_results measures the same RTL under four
synth_design directives at a 15% LUT spread, so the LUT model is already inside the
noise and tightening the bound here would be fitting one point of it.
"""

import pytest

import os
import sys

from finn.custom_op.fpgadataflow.streamingfifo import _fifo_cost

COST_MODEL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "finn-rtllib", "fifo", "cost_model")
)
sys.path.insert(0, COST_MODEL_DIR)

from common import SCORED_SETS, STYLES, Score, read_runs  # noqa: E402

# The one config the model is known to get wrong, documented in the cost model README:
# a 6161x1 ultra FIFO whose hi space _hi_in_ram puts in RAM and Vivado puts in LUTRAM.
KNOWN_MEMORY_MISS = ("ultra", 6161, 1, True, (0, 1), (1, 1))

# Well clear of the 4.6% the model currently scores, and still far inside the 15% spread
# the synthesis directive alone produces on identical RTL.
MAX_LUT_ERROR_PCT = 8.0
MAX_STYLE_LUT_ERROR_PCT = 12.0


def _model(run):
    return _fifo_cost(run.depth, run.width, run.style, run.versal)


@pytest.fixture(scope="module")
def scored():
    runs = list(read_runs(*SCORED_SETS))
    assert runs, "no measured runs found under %s/data" % COST_MODEL_DIR
    return Score(runs, {"model": _model})


@pytest.mark.fpgadataflow
def test_fifo_cost_model_memory_is_exact(scored):
    """RAMB18 and URAM are counted, not fitted, so every run but the known one is exact."""
    assert scored.misses["model"] == [KNOWN_MEMORY_MISS]


@pytest.mark.fpgadataflow
def test_fifo_cost_model_lut_error(scored):
    err, tot = scored.total("model", 2)
    assert 100 * err / tot < MAX_LUT_ERROR_PCT


@pytest.mark.fpgadataflow
@pytest.mark.parametrize("style", STYLES)
def test_fifo_cost_model_lut_error_per_style(scored, style):
    """A per-style bound, so one style cannot regress under a passing total."""
    err, tot = scored.column(style, "model", 2)
    assert tot, "no measured runs for style %s" % style
    assert 100 * err / tot < MAX_STYLE_LUT_ERROR_PCT


@pytest.mark.fpgadataflow
def test_fifo_cost_model_zero_depth():
    """set_fifo_depths makes depth-0 FIFOs for MLO parameter inputs."""
    assert _fifo_cost(0, 32, "block") == (0, 0, 0)
    assert _fifo_cost(1, 32, "block") == (0, 0, 0)
