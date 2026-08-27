# FIFO cost model — measurement harness and data

The resource estimators in `src/finn/custom_op/fpgadataflow/streamingfifo.py` are
partly derived from `fifo.sv` and partly fitted against synthesis. This directory
holds the measurements the fitted terms rest on, the harness that produced them, and
a scorer that checks the estimators against them.

| file | what it is |
|---|---|
| `common.py` | data sets, the `Run` record, `Score`, and loading the model out of the working tree |
| `score.py` | scores the estimators against the measurements |
| `ablate.py` | replaces one term at a time and rescores |
| `run.sh`, `synth_one.tcl`, `parse.py` | one out-of-context synthesis, to one `RESULT` line |
| `data/*.txt` | exactly what those runs emitted |

Everything here is a manual tool. The part worth failing a build over is
`tests/fpgadataflow/test_fifo_cost_model.py`, which drives `common.py` against the
same data and gates memory exactness and the LUT residual. It needs no Vivado and
runs in about a tenth of a second.

## Reproducing the numbers in `SUMMARY.md`

```
python3 score.py            # per-style, legacy vs current
python3 score.py --sets     # per UltraScale+ set, current only
python3 score.py --noise    # LUT spread across synthesis directives
python3 score.py --versal   # Versal, fit set and held-out validation set
python3 score.py --columns  # each term against the column it claims to explain
```

## What the terms actually explain

Scoring against the LUT total lets a term be right for the wrong reason. Vivado
reports that total split — `lut = lut_logic + lut_mem` and `lut_mem = lutram + srl`,
which hold on all 339 runs (the 315 scored ones plus the 24 noise re-syntheses) — so
each term can be checked against its own column. `--columns` does that for all four
styles, and it turns several comments in `streamingfifo.py` from plausible into
measured:

- **`shift` storage is exact, not fitted.** `srl == stages * W` on 49 of 49 runs.
  That isolates the rest: the cascade mux and control terms alone predict
  `lut_logic` to 10%, and almost all of that is depth 2, which costs *more* control
  logic than depth 5 — a genuine non-monotonicity at the degenerate depth, not a
  slope error.
- **The LUTRAM term's product shape is measured.** Dividing the `lutram` column by
  `ceil(rows/32)` gives the same per-bank cost at every depth, for all 12 widths
  over 64–1024 rows. The row factor is therefore exact and the entire fitted part
  is univariate in `W` — but it is not the flat 5/4: the overhead amortizes from
  1.33x storage at W=12 to 1.156x at W=128. No closed form in the obvious families
  gets within two of the 12 widths, and the two widths that break monotonicity rest
  on one run each, so the next useful measurement is more widths at several depths.
- **The LUTRAM term transfers to a style it was not fitted on.** `block` and `ultra`
  reuse it for a `hi` space that lands in LUTRAM, where the `lutram` column measures
  it directly — 61 runs, row counts two orders of magnitude smaller, **7%**. It is
  the cost of a LUTRAM, not the cost of a `distributed` FIFO.
- **The URAM read path's step sequence is measured.** It is the largest fitted term
  in the model. Subtracting every other `ultra` term from `srl + lut_logic` leaves it
  per bit of width: **1 / 1 / 1 / 1 / 2.4 / 6.1 / 14.0** at cascades 1 to 64, against
  the term's 1 / 1 / 1 / 1 / 2 / 6 / 14. Where it lands is measured too — the base is
  the output queue and is `srl` outright, and past cascade eight each further stage
  splits about evenly between `srl` and `lut_logic`, so calling the whole thing a
  shift register overstates it by about half. The `+W` charged for a LUTRAM `hi`
  space under URAM *is* purely a delay line: `srl` reads exactly `2W + 1` wherever
  there is one and `W` where there is not, on all 10 widths measured both ways.
- **The `distributed` and `block` branches have nothing hiding under the sum.** Both
  charge no shift register, and `srl` is 0 on all 34 and all 136 of their runs, so
  their control, mux and tile terms answer for `lut_logic` alone — 9% and 8%, where
  their totals also sit. Only `ultra` shows a compensating pair.
- **`_hi_in_ram` is confirmed on a column it is not fitted to.** It is fitted
  against RAMB18/URAM, but `lutram` answers the same question independently — a hi
  space in LUTRAM appears there, one in RAM does not. The two readings agree on
  **168 of 169** hi spaces, and the exception is the already-documented 6161x1 run.
  That also pins down what the outcome depends on: deduplicated, the 169 are 111
  distinct `(rows, W)` pairs and **no pair ever lands differently** in two runs, across
  either style, either part family or any depth. So the 111 points are a ground truth,
  and `--columns` sweeps every simpler predicate against them at its own best
  threshold: `rows` alone misses 7, `W` alone 42, `rows * W` 6, `rows**3 * W` 3, the
  model 1 — and the sweep recovers `2**20`, so the threshold is not fitted either.

## Is any of the model unnecessary?

```
python3 ablate.py           # every term, replaced by the next simpler form
python3 ablate.py hi_in_ram # only variants whose label matches
```

`score.py` asks whether the model is accurate; this asks whether it is minimal.
Each variant substitutes one term and rescores over all 315 runs, both families at
once, so the cost of dropping it is a number rather than an opinion. Every term
currently in `streamingfifo.py` survives that test — the cheapest one to lose is
the SRL cascade multiplexer's fitted intercept at +0.3pp on `shift`, and the rest
range from +0.8pp to +832pp. Two probes are worth knowing about:

- **`hi_in_ram: always False`** costs +832pp. That is `dev`'s behaviour, and the
  size of the number is the whole reason the predicate exists.
- **`bram18: no word splitting`** costs 22 memory misses. The dynamic program in
  `_bram18_plan` is not decoration; neither a greedy widest-first split (40 misses)
  nor a two-configuration split (531 disagreements over a 2560-point grid) reproduces
  what Vivado does on UltraScale+. The greedy split is the one probe that scores
  the same LUT error as the model, which is a reminder that the LUT column alone
  is not enough to accept a simplification.

One simplification did come out of this and is now in the model: the `lo`/`hi`
selection was fitted once for `block` and once for `ultra`, and is now the single
`_hi_select_luts()`, refitted over both. `hi selector: two separate fits` scores it
— the merge costs 0.2pp, two constants cheaper. Nothing else was free. In
particular the three counter slopes (5 `shift`, 8 `distributed`, 6 `ultra`) look
like one constant asking to be shared: the best single term over all three is
`7 * cw - 16`, which takes `shift` from 3.1% to 7.1%, and the best over `shift` and
`distributed` alone takes `distributed` from 2.9% to 5.7%.

Add a variant by writing one function with the `@variant` decorator. A term reachable
by name is replaced by rebinding it in the namespace; a term living inside
`_fifo_cost` is reached with `patch_cost()`, which rewrites expressions by exact
string match, so a variant whose target has been edited fails loudly instead of
silently passing.

Nothing but Python is needed — the measurements are checked in. `common.load_model()`
executes the model out of the working tree and `score.py` executes the legacy one from
`git show main:`, so neither is a copy that can drift, and neither is retyped.

`--columns` is the one place that must retype the model, since it evaluates a term
apart from the sum it normally lands in. Those transcriptions live in `common.py` next
to the strings `ablate.py` rewrites, and `common.assert_exprs()` checks every one of
them against the model source before `--columns` runs. Edit a term in
`streamingfifo.py` and both harnesses fail loudly rather than scoring a stale formula.

## Re-measuring

```
./run.sh DEPTH WIDTH STYLE [DIRECTIVE] [PART]      # needs Vivado on PATH
```

One out-of-context synthesis of `../hdl/fifo.sv`, printing a `RESULT` line. Append
those to a file under `data/` to extend a set. `data/*.txt` are exactly what the
runs emitted; `parse.py` reads a `report_utilization` report and is what turns one
into the other.

Two details that are easy to get wrong when re-running:

- **The wrapper must expose `count`/`maxcount`.** Otherwise the occupancy monitor
  has no path to the top level, synthesis trims it, and the LUT numbers come out
  well under what a FINN build pays. `synth_one.tcl` generates the wrapper for this
  reason rather than instantiating `fifo` directly.
- **`RAM_STYLE` must be explicit.** FINN resolves the style in Python and never
  passes `"auto"`, so measuring `"auto"` would characterize a configuration the
  compiler does not emit.

## The data

| set | n | what it is |
|---|---|---|
| `results` | 72 | the fit: every style, both memory geometries, widths 1–256, depths 2–262145 |
| `holdout_results` | 27 | never fitted against |
| `deep_results` | 20 | depth >20k, where an earlier model broke (43% LUT, 11% BRAM) |
| `val2_results` | 15 | predictions locked to file before synthesis; 15/15 memory exact |
| `noise_results` | 24 | 6 configs x 4 `synth_design` directives, identical RTL |
| `versal_results` | 33 | second part family; 28 are repeats of the sets above |
| `versal_fit_results` | 105 | the Versal fit, widths 1–256, depths 2–262145 |
| `versal_val_results` | 43 | held out; nothing is fitted against it |

The first five are `xczu7ev-ffvc1156-2-e`, the `versal_*` three
`xcvc1902-vsva2197-2MP-e-S`. Vivado 2026.1 throughout.

The Versal sets exist because resource counts are part-family dependent and one
family cannot show it. All four styles and both memory geometries appear in each,
and `versal_results`' 28 repeats of xczu7ev configs are what isolate the family as
the only variable. Three differences came out of them:

- **Vivado does not split a word across memory configurations on Versal.** On
  UltraScale+ it does, for BRAM: 16385 x 32 is 18b x 2048 + 9b x 4096 + 4b x 8192 +
  1b x 16384, 29 tiles rather than 32. That same config measures 32 on Versal. URAM
  behaves the same way on both parts — the narrowest aspect holding the whole word,
  so 37 bits costs what 72 does.
- **Versal's ladders are shorter.** RAMB18E5 has no 4/2/1-bit SDP configuration, and
  URAM288E5 adds 36/18/9-bit aspects that UltraScale+'s URAM288E2 does not have, so
  narrow `ultra` FIFOs cost half the URAM there.
- **The URAM read path scales with cascade depth, not with `PIPE_DEPTH`.** The two
  are inseparable on UltraScale+, since both are functions of the row count. The
  Versal aspect ladder separates them: at depth 131073 `PIPE_DEPTH` is 18 at every
  width, but crossing 36 bits doubles the cascade and costs 115 LUT for one bit.
  The width sweeps at fixed depth are there to pin this down.

`versal_val_results` is scored but never fitted against, and comes out at the same
5% LUT error as the fit set. Its memory column was inspected once mid-way, while the
BRAM ladder was still being settled, so it is a second look at that column rather
than a single one; the LUT column is untouched.

`noise_results` is the reason the LUT residual is left at ~5%: the same RTL
synthesized under four directives spans 15% on average, while RAMB18 and URAM do not
move at all. The LUT model is already well inside the tool's own spread, so fitting
it harder would mean fitting one arbitrary point in that spread.

## Scope

FIFOs only, and `parse.py` records the utilization summary rather than the primitive
breakdown — enough for the estimators, not enough to attribute LUTs to LUT6-site
packing. The raw `runs/` directories are not checked in.
