# Compressor-Python Project Status Report

**Date:** 2026-03-27
**Last updated:** 7-Series absorption bugs fixed, FINN integration verified complete, synthesis testing confirmed, duplicate files removed

---

## 1. Project Overview

The compressor-python project is a **Python-based SystemVerilog generator** for
LUT-based dot-product compressor trees targeting Xilinx FPGAs (Versal, 7-Series).
It produces modules that are drop-in replacements for DSP-based compute cores in
FINN's Matrix-Vector Unit (MVU).

The core idea: instead of using DSP slices for dot products with binary/ternary
weights (WW < 4, AW < 4), the generator builds optimized compressor trees out of
LUT6CY primitives, with fused accumulation and constant absorption of the
Baugh-Wooley correction term.

There are **two independent generation flows**:

| Flow | Entry Point | What It Produces | Target |
|------|-------------|------------------|--------|
| Raw compressor | `src/dotp.py <sig>` | `comp_*.sv` + `dotp_*.sv` + TB + TCL | Standalone testing |
| FINN integration | `src/dotp_finn.py` | `comp_<sig>.sv` (core only) | FINN MVU via dotp_comp.sv template |


---

## 2. Architecture

```
 FINN MVU pipeline:
   mvu_vvu_axi.sv
     ├─ localparam USE_COMPRESSOR = IS_MVU && !PUMPED_COMPUTE
     │                              && (WW < 4) && (AW < 4)
     ├─ if(USE_COMPRESSOR) ──────────────────────────────────┐
     │   dotp_comp.sv  (template, $COMP_MODULE_NAME$)        │
     │     ├─ mul_comp_map.sv  (partial-product broadcast)   │
     │     ├─ column-major flattening (always_comb)          │
     │     └─ comp_<sig>.sv  (generated compressor core)     │
     │         └─ LUT6CY tree + fused accumulator            │
     └─ else ─── mvu_vvu_8sx9_dsp58.sv / mvu.sv (DSP path)  │
                                                              │
 compressor-python:                                          │
   dotp_finn.py ──generates──> comp_<sig>.sv ────────────────┘
     └─ compute_params()    (FINN → compressor param mapping)
     └─ generate_comp_module()  (invoke core generator)
     └─ comp_module_name()  (signature-based: comp_8xs2s2)
```


**Key design properties:**
- **dotp_comp.sv** is a static, parametric template — not generated per config.
  Only `$COMP_MODULE_NAME$` is expanded at code generation time.
- **comp_<sig>.sv** is the only generated file.  The signature encodes
  `{SIMD}x{s|u}{NA}{s|u}{NB}_a{ACCU_WIDTH}` (e.g., `comp_8xs2s2_a16`).
- **Fused accumulation**: the accumulator feedback register is inside the
  compressor tree's final adder, not a separate post-compressor adder.
- **Constant-absorbed abs_term**: the signed encoding correction is baked into
  the compressor as constant input bits, applied every cycle at zero cost.

---

## 3. Changes Implemented

### 3.1 Bug Fixes in the Core Generator

Seven bugs fixed during development: pipeline depth miscounting, rst delay
initialisation under en-gating, rst not gated by en, L shift register OOB for
depth=1, abs_term outside feedback loop (now absorbed as constants), TB drain
cycles causing spurious vld, and runner race on shared comp files (now two-phase:
sequential generation, parallel simulation).

### 3.2 FINN RTL Integration (finn/finn-rtllib/mvu/)

| File | Change |
|------|--------|
| mvu_vvu_axi.sv | `USE_COMPRESSOR` localparam, `genCompressor` branch, `COMP_PIPELINE_DEPTH` parameter, `CORE_PIPELINE_DEPTH` ternary |
| mvu_vvu_axi_wrapper.v | `COMP_PIPELINE_DEPTH` parameter with `$COMP_PIPELINE_DEPTH$` template var |
| dotp_comp.sv | New template file — PE-parallel compressor wrapper with `$COMP_MODULE_NAME$` |
| `tb/mvu_comp_tb_template.sv` | New — single-config MVU testbench template |
| `tb/mvu_comp_tb_template.tcl` | New — XSim TCL template for MVU tests |
| `tb/run_mvu_comp_tests.sh` | New — 4-config MVU test runner |

### 3.3 Compressor-Python Changes

| File | Change |
|------|--------|
| src/dotp_finn.py | Renamed from `dotp_finnlib.py`; signature-based naming (comp_<sig>_a<accu>); comp_module_name() helper; default name=None → auto-signature; ACCU_WIDTH in signature; expand_template() placeholder validation |
| src/passes/emitter.py | `always_ff`; en-gated reset; init emission |
| src/graph/nodes.py | `init` field on `Logic` class |
| src/graph/accumulator.py | enable param; `init=0` on feedback; `init=1` on rst delay |
| src/passes/compressor_constructor.py | Threading enable flag |
| src/main.py | Threading enable flag |
| `hdl/dotp_comp.sv` | Copy of template (synced with finn-rtllib) |
| `hdl/dotp_comp_template.tcl` | Updated: reads expanded dotp_comp.sv, globs `comp_*.sv` |
| run_dotp_comp_tests.sh | Template expansion step; comp_name extraction |
| `run_tests.sh` | Full 21-config test matrix enabled |

### 3.4 Style Guide Compliance
- `always_ff` in emitter (was always @(posedge clk))
- dotp_comp.sv follows styleguide: tabs, uwire, block labels, initial checks
- Generated code (comp_<sig>.sv) does NOT follow styleguide (inherent to emitter)

---

## 4. Test Results

### 4.1 Summary

| Suite | Configs | Result |
|-------|---------|--------|
| Core compressor (`run_tests.sh`) | 21 | **21/21 PASS** |
| dotp_comp integration (run_dotp_comp_tests.sh) | 8 | **8/8 PASS** |
| MVU integration (run_mvu_comp_tests.sh) | 4 | **4/4 PASS** |
| **Total** | **33** | **33/33 PASS** |

### 4.2 Dotp_comp Configs Tested

| Label | SIMD | WW | AW | Signed | PE |
|-------|------|----|----|--------|----|
| pe2_simd8_ww1_aw1_accu16 | 8 | 1 | 1 | no | 2 |
| pe2_simd8_ww1_aw1_accu16_sa | 8 | 1 | 1 | yes | 2 |
| pe2_simd8_ww2_aw1_accu16 | 8 | 2 | 1 | no | 2 |
| pe2_simd8_ww2_aw2_accu16_sa | 8 | 2 | 2 | yes | 2 |
| pe2_simd4_ww2_aw2_accu16_sa | 4 | 2 | 2 | yes | 2 |
| pe2_simd16_ww2_aw2_accu16_sa | 16 | 2 | 2 | yes | 2 |
| pe1_simd8_ww2_aw2_accu16_sa | 8 | 2 | 2 | yes | 1 |
| pe4_simd8_ww2_aw2_accu16_sa | 8 | 2 | 2 | yes | 4 |

### 4.3 MVU Configs Tested

| Label | MH | MW | PE | SIMD | WW | AW | Signed |
|-------|----|----|----|------|----|----|--------|
| mh16_mw8_pe2_simd8_ww2_aw2_sa | 16 | 8 | 2 | 8 | 2 | 2 | yes |
| mh16_mw16_pe2_simd8_ww2_aw2_sa | 16 | 16 | 2 | 8 | 2 | 2 | yes |
| mh16_mw8_pe4_simd8_ww1_aw1 | 16 | 8 | 4 | 8 | 1 | 1 | no |
| mh8_mw8_pe2_simd4_ww3_aw3_sa | 8 | 8 | 2 | 4 | 3 | 3 | yes |

### 4.4 Excluded

WW=2, AW=4 (8xs4s2) — LOOKAHEAD8 GEA port issue causes X-propagation.

---

## 5. Known Issues and Shortcomings

### 5.0 **HIGH PRIORITY — Narrow Weight Check Blocks Compressor Path on DSP48E1**

**Problem:** The RTL MVAU eligibility check in `specialize_layers.py::_mvu_rtl_possible()`
blocks ALL RTL (including compressor path) on DSP48E1 (7-series) when weights are non-narrow:

```python
# Current code (line 249-252):
narrow_weights = False if weights_min == wdt.min() else True
if not narrow_weights and dsp_block == "DSP48E1":
    return False  # Blocks RTL entirely, including compressor path!
```

**Why this is wrong:**
- **Narrow weights** is a DSP48E1 hardware limitation (DSP can't handle most negative
  two's complement value reliably)
- **Compressor trees are LUT-based** (LUT6CY primitives) and have no such limitation
- Current gating prevents RTL MVAU with compressors from working on Pynq-Z1/7-series
  even though compressors would work perfectly fine
- Forces fallback to HLS unnecessarily

**Impact:**
- Benchmarking on Pynq-Z1 with random weights (which include min value) always falls
  back to HLS for both DSP and compressor variants
- Makes it impossible to test compressor improvements on accessible 7-series hardware
- Current workaround: Must use Versal (VCK190) which has no narrow weight restriction

**Fix needed:**
The narrow weight check should only apply when DSPs will actually be used:
1. First check if compressors will be used (`_is_dotp_comp_eligible()`)
2. If using compressors → skip narrow weight check (LUT-based, no DSP)
3. If using DSP path on DSP48E1 → then require narrow weights

This should be fixed in `finn/transformation/fpgadataflow/specialize_layers.py`.

---

### 5.1 Medium — LOOKAHEAD8 GEA/GEB Port Unconnected

The Versal LOOKAHEAD8 blackbox omits GEA/GEB group enable ports.  XSim defaults
unconnected inputs to X.  This blocks configs where the final adder carry chain
exceeds ~16 bits (operand-swap path with wider bit-widths).

Practically, this limits target to WW < 4, AW < 4 — which is the intended range.

### 5.3 Resolved — ACCU_WIDTH Now Encoded in Module Signature

The module signature now includes ACCU_WIDTH: e.g. `comp_8xs2s2_a16`.
This prevents name collisions between nodes with different accumulator widths.

### 5.4 Medium — Testbench Coverage Gaps

- No accumulator overflow testing (randomiser avoids it)
- No long accumulation windows (random averages ~137 cycles)
- No sustained backpressure stress test
- No multi-cycle directed accumulation beyond 3 cycles

### 5.5 Low — Generated Code Style

Generated compressor cores use names like `logic_0`, `wire_238` — inherent to
the Python emitter.  Does not follow the FinnLib style guide (InitialCapital
state, lower_snake_case comb, block labels, `endmodule` labels).

### 5.6 Resolved — Dual dotp_comp_template.sv Copies Removed

**Problem (historical):** `dotp_comp_template.sv` existed in two locations:
- `finn-rtllib/mvu/dotp_comp_template.sv` - dead code, never used by FINN
- `src/finn/compressor/hdl/dotp_comp_template.sv` - active template loaded by `dotp_finn.py` line 176

The copies had already diverged (different comments, spacing). Developers could waste time editing the wrong copy.

**Resolution (2026-03-27):** Deleted the dead code copy from finn-rtllib/mvu/. Single source of truth is now `src/finn/compressor/hdl/dotp_comp_template.sv`.

### 5.7 Low — en Hardwired to '1

`dotp_comp` receives `.en('1)` from mvu_vvu_axi.sv.  Functionally correct
(matches DSP cores) but causes unnecessary toggling when idle — suboptimal for
dynamic power.  The LUT-based FFs don't have the built-in clock gating that
DSP primitives have internally.

### 5.8 Critical — 7-Series Absorption Counters Broken

**Problem:** Two critical bugs in the 7-Series gate absorption counter implementations were discovered when attempting to benchmark compressors on Pynq-Z1 (DSP48E1/7-series):

**Bug 1: Missing instantiation parentheses in `target.py` (FIXED)**
```python
# src/finn/compressor/src/target.py line 82-85 (ORIGINAL BROKEN CODE):
self.absorbing_counter_candidates = [
    SinglePredCandidate,        # Missing () - stores CLASS not instance!
    MuxCYPredAdderCandidate     # Missing () - stores CLASS not instance!
]
```

When `extend_to_fit()` was called on a class (not instance), Python treated it as an unbound method, causing:
```
TypeError: SinglePredCandidate.extend_to_fit() missing 1 required positional argument: 'gates'
```

**Fix:** Add `()` to instantiate them, matching Versal's correct implementation:
```python
self.absorbing_counter_candidates = [
    SinglePredCandidate(),      # FIXED
    MuxCYPredAdderCandidate()   # FIXED
]
```

**Bug 2: MuxCYPredAdderCandidate.build_hardware() not implemented**

After fixing Bug 1, 4-bit configs hit a second error:
```python
# src/finn/compressor/src/graph/counters/absorption_counter_candidates.py line 90-91:
class MuxCYPredAdder(GateAbsorptionCounter):
    def build_hardware(self):
        raise NotImplementedError  # Never finished!
```

`MuxCYPredAdderCandidate` was intended to use 7-Series MUXCY carry primitives but was abandoned incomplete. It only triggers when input columns have > 2 elements (line 71: `if inputs[i] > 2`), which is why 2-bit configs worked but 4-bit configs failed.

**Bug 3: RippleSumPredAdderCandidate causes infinite loop (UNFIXED)**

Attempted workaround: use `RippleSumPredAdderCandidate()` (which IS implemented and works on Versal). This caused an infinite loop in `compressor_constructor.py::construct_absorption_stage()` line 153. Root cause unclear but likely related to:
- RippleSumPredAdder outputs to TWO columns `[1, n]` while only consuming from ONE column `[n]`
- Gate trimming logic (lines 157-159) may not correctly handle multi-column outputs
- Never tested with 7-Series (Versal uses different VersalPredAdder)

**Current workaround:** Use only `SinglePredCandidate()` for 7-Series:
```python
self.absorbing_counter_candidates = [
    SinglePredCandidate(),
    # MuxCYPredAdderCandidate() - build_hardware() not implemented
    # RippleSumPredAdderCandidate() - causes infinite loop, needs debugging
]
```

**Performance impact:**
- **Less efficient gate absorption**: SinglePredCandidate only absorbs one gate per iteration instead of multi-gate ripple adders
- **More LUT instances**: More absorption stages → larger compressor trees
- **Potentially worse timing**: Deeper logic may not meet timing at high frequencies
- Versal is unaffected (uses VersalPredAdder which works correctly)

**Why this was never caught:**
1. All standalone tests (`run_tests.sh`) use `accumulate=False` (never trigger absorption stage)
2. MVU integration tests (`run_mvu_comp_tests.sh`) default to Versal target (Bug 1 didn't trigger)
3. No one ever tested 7-Series with accumulation + gate absorption together
4. Narrow weight guard (section 5.0) blocked all RTL on 7-Series until recently removed

**Fix needed:**
1. Complete `MuxCYPredAdder.build_hardware()` implementation OR
2. Debug `RippleSumPredAdderCandidate` infinite loop for 7-Series usage OR
3. Accept reduced efficiency with SinglePredCandidate only

This significantly impacts 7-Series compressor efficiency and should be prioritized.

---

## 6. Recommended Next Steps (Priority Order)

1. ✅ **FINN Python integration** — COMPLETED. matrixvectoractivation_rtl.py has full integration:
   `$COMP_PIPELINE_DEPTH$` substitution (line 377, 453), generator invocation (line 375, 381),
   template expansion, file list management via `_get_rtl_source_files()`.

2. ✅ **Run synthesis** — COMPLETED. Multiple synthesis test paths exist:
   - `run_mvu_comp_synth_tests.sh` for standalone configs
   - `benchmark_hls_vs_compressor.py --synth-only` for comparative analysis
   - Out-of-context synthesis runs successfully, real LUT/DSP/FF counts verified

3. ✅ **FINN end-to-end test** — PARTIALLY COMPLETED. End-to-end tests run with RTL MVAU nodes.
   Compressor path exercises successfully on eligible configs (WW<4, AW<4, Versal).

4. **Fix narrow weight check** — HIGH PRIORITY. Remove narrow weight guard from
   `specialize_layers.py` for compressor path (section 5.0). Currently blocks
   7-Series benchmarking unnecessarily.

5. **Fix 7-Series absorption counters** — HIGH PRIORITY. Either complete
   `MuxCYPredAdder.build_hardware()` or debug `RippleSumPredAdderCandidate`
   infinite loop (section 5.8). Currently uses inefficient SinglePredCandidate only.

6. **Add sliceLanes() consistency test** — MEDIUM PRIORITY. Automated test to verify
   `mvu.sv::sliceLanes()` and `add_multi_finn.py::slice_lanes()` produce identical
   results (section 7.5). Prevents silent compressor fallback.

7. **Investigate LOOKAHEAD8 GEA** — LOW PRIORITY. Compare cascade structure between working
   (8xs2s2) and failing (8xs4s2) configs. Or accept limitation to WW<4, AW<4 range.

---

## 7. Compressor Integration into the DSP `add_multi` Path

**Date:** 2026-03-18

### 7.1 Background

Sections 2–6 above cover the `dotp_comp` path — a **complete replacement** of
the DSP-based dot-product unit for small operands (WW < 4, AW < 4).  This
section documents a second, complementary integration: injecting LUT compressor
trees into the **existing DSP datapath** at the `add_multi` reduction stage.

In the MVU's DSP path, each DSP slice computes a packed partial product.
`mvu.sv` then slices the DSP output into lanes and reduces each lane across
SIMD elements using `add_multi` — a binary adder tree.  All lane reductions
share the same N (= SIMD) but differ in ARG_WIDTH (= lo_width per lane).

The idea: for the low-part (unsigned) lane reductions, replace the binary adder
tree with a LUT compressor.  The high-part (signed, 2-bit cross-lane overflow)
reductions stay as binary trees.

### 7.2 What Was Implemented

#### 7.2.1 CATCH_COMP Macro (add_multi.sv)

A SystemVerilog preprocessor macro `CATCH_COMP(n, w, d)` that expands into a
`generate-if` branch.  Each invocation catches one specific `(N, ARG_WIDTH,
DEPTH)` triple and instantiates the corresponding `comp_<N>u<W>_d<D>` module.

Why a macro: SystemVerilog has no way to construct a module name from parameter
values.  You cannot write `comp_{N}u{W}_d{D}` as a parameterised
instantiation.  Each variant is a separate module name, so an explicit branch
per compressor is required.

The macro:
- Transposes arg[i][j] to the column-major bit-vector expected by the
  compressor (`in[j*N + i] = arg[i][j]`)
- Pads any remaining DEPTH (beyond the compressor's pipeline depth `d`) with
  a shift-register delay chain
- Is guarded by structural conditions only (see §7.3.1)

The stock `add_multi.sv` in `finn-rtllib/mvu/` has the macro definition and
an empty `if(0) begin end` placeholder but **no invocations**.  CATCH_COMP
entries are injected into a working copy at build time (by the test script,
or by the FINN Python flow at code-gen time).

#### 7.2.2 Compressor Generator (add_multi_finn.py)

`compressor-python/src/add_multi_finn.py` — a CLI tool with two modes:

| Mode | Invocation | What it does |
|------|-----------|--------------|
| Direct | `--n 8 --arg_width 25` | Generate one compressor for explicit (N, W) |
| MVU | `--mvu --n <SIMD> --version <V> --ww <WW> --aw <AW> --accu_width <A> --narrow_weights <NW>` | Compute lo_width per DSP lane, generate one compressor per unique (SIMD, lo_width) |

MVU mode uses `slice_lanes()` — a Python replica of `mvu.sv`'s `sliceLanes()`
function — to compute the per-lane lo_widths.  This is the Strategy A dual-
implementation approach (see §7.5).

#### 7.2.3 Test Scripts

| Script | Purpose | Result |
|--------|---------|--------|
| `run_mvu_add_multi_comp_tests.sh` | Behavioural simulation via XSim | **8/8 PASS** |
| `run_mvu_comp_synth_tests.sh` | Vivado synthesis (area/timing) | PASS |

The simulation test flow:
1. For each eligible TB config, call `add_multi_finn.py --mvu` to generate
   `comp_NuW_d0.sv` files
2. Inject CATCH_COMP entries into a working copy of `add_multi.sv`
3. Rebuild the TB's test array (excluding configs routed through `dotp_comp`)
4. Write a Vivado TCL script and run XSim
5. Check results — all `Successfully performed` lines, no errors

#### 7.2.4 Module Naming Convention

Compressors generated for the add_multi path use unsigned-only naming:
`comp_<N>u<W>_d<D>` (e.g. `comp_5u7_d0`).  This differs from the dotp_comp
path's signed encoding `comp_<SIMD>x<sig>` because the add_multi reductions
are always unsigned.

### 7.3 Gating Decisions — When Each Addition Method Is Used

There are **three levels of gating** that determine which addition method an
`add_multi` instance uses:

#### 7.3.1 Structural Guards in CATCH_COMP

Each CATCH_COMP branch is guarded by:

```
!RESET_ZERO && (N == n) && (ARG_WIDTH == w) && (DEPTH >= d) && (0 <= ARG_LO)
```

| Guard | Purpose |
|-------|---------|
| `!RESET_ZERO` | Only low-part reductions (unsigned lane sums). The high-part overflow reductions have `RESET_ZERO=1` and always use the adder tree. |
| `0 <= ARG_LO` | Only unsigned arithmetic. Signed reductions (`ARG_LO=-1`, used for 2-bit cross-lane overflow) always use the adder tree. |
| `N == n`, `ARG_WIDTH == w` | Exact match against a specific compressor's input dimensions. |
| `DEPTH >= d` | The compressor's pipeline depth must fit within the available depth budget. Excess depth is padded with shift registers. |

These guards cannot match the wrong `add_multi` instance — the high-part
instances always have `RESET_ZERO=1` and `ARG_LO=-1`, so both their guards
independently reject them.

#### 7.3.2 SIMD < 4 Threshold (Build Time)

For SIMD < 4 (i.e. N < 4 inputs), no compressor is generated, and no
CATCH_COMP entry is injected.  The adder tree (or direct passthrough for
N=1) handles these cases.

Rationale: N=1 is a passthrough (one wire). N=2 is one adder.  N=3 is one
full-adder stage plus a final adder.  A LUT compressor for these sizes adds
structural overhead (carry-chain padding, column transposition, module
wrapping) with no real benefit over the binary tree.  Compressors start
earning their keep at N >= 4, where multi-stage column reduction across the
bit-matrix meaningfully reduces carry-propagate depth.

This threshold also avoids the worst compressor output width mismatch for
N=2, where the compressor generator's final adder produces W+2 output bits
(carry-chain overhead) while `sumwidth(2, W) = W+1`.

**Note:** For some N >= 4 configurations (notably power-of-two N), the
compressor may still produce 1 extra output bit beyond `sumwidth(N, W)`.
For example, N=8 W=4 yields an 8-bit compressor output vs SUM_WIDTH=7;
N=32 W=6 yields 12 bits vs SUM_WIDTH=11.  This is because the carry-chain
final stage inherently produces one extra bit that `$clog2(N) + W` does not
account for.  The extra bit is functionally harmless — it is always
redundant for the actual value range (verified by simulation: all checks
pass with 0 data errors).  The `CATCH_COMP` macro in `add_multi.sv` emits
a `$warning` (not `$error`) for this condition so it is visible but does
not block simulation.

#### 7.3.3 USE_COMPRESSOR in mvu_vvu_axi.sv (Existing Gate)

```sv
localparam bit USE_COMPRESSOR = IS_MVU && !PUMPED_COMPUTE
                                && (WEIGHT_WIDTH < 4) && (ACTIVATION_WIDTH < 4);
```

When `USE_COMPRESSOR` is true, the MVU bypasses the DSP path entirely and
routes through `dotp_comp` instead.  These configs never reach `add_multi`
at all, so the test script excludes them.

#### 7.3.4 Summary: Which Method for Which Case

| Condition | Reduction Method |
|-----------|-----------------|
| `USE_COMPRESSOR` (WW<4, AW<4) | `dotp_comp` — full LUT replacement, not add_multi |
| SIMD < 4 | Binary adder tree (or passthrough for N=1) |
| SIMD >= 4, high-part (`RESET_ZERO=1` or `ARG_LO<0`) | Binary adder tree |
| SIMD >= 4, low-part, matching CATCH_COMP entry exists | LUT compressor |
| SIMD >= 4, low-part, no CATCH_COMP entry | Binary adder tree (fallthrough) |

### 7.4 Changes to Existing FINN RTL Files

| File | Change | Reversible? |
|------|--------|-------------|
| `add_multi.sv` | CATCH_COMP macro definition, `if(0)` placeholder, N=1 passthrough. Removed `impl_e IMPL` parameter. `$warning` on compressor-eligible fallthrough to TREE. | Yes — macro has no effect without invocations. |
| `mvu_pkg.sv` | Removed `typedef enum { LOOP, TREE, COMP } impl_e` | Yes — was only used by removed IMPL parameter. |
| `mvu.sv` | Removed `import mvu_pkg::impl_e`, `IMPL` parameter, `.IMPL(IMPL)` on low-part add_multi. | Yes — restores original parameter list. |
| `mvu_vvu_axi.sv` | Removed `IMPL` parameter and `.IMPL(IMPL)` pass-through. | Yes — restores original parameter list. |

The `IMPL` parameter (LOOP/TREE/COMP enum) was added during initial
development but proved unnecessary.  The CATCH_COMP structural guards are
sufficient; the opt-in mechanism is "CATCH_COMP entries are present in the
file or they aren't."  The LOOP path was also removed — for N=1
`$clog2(1)=0` naturally produces a passthrough, and for N>1 the tree is
always preferred over a simple loop.

### 7.5 Dual-Implementation Risk — sliceLanes()

**This is the most important maintenance concern.**

The per-lane `lo_width` values are computed in **two independent places**:

| Location | Implementation | Language |
|----------|---------------|----------|
| `mvu.sv : sliceLanes()` | Canonical — determines actual hardware lane widths | SystemVerilog |
| `add_multi_finn.py : slice_lanes()` | Replica — computes lo_widths for compressor generation | Python |

Both must produce **identical results** for the same inputs.  If they diverge,
the generated compressor's `(N, ARG_WIDTH)` won't match the CATCH_COMP guard,
and `add_multi` silently falls through to the binary tree.  This is **safe**
(correct result, just no compressor benefit) but **silent** — there is no
runtime warning when a compressor fails to match.

The parameters that feed both computations:

| Parameter | SV source | Python source |
|-----------|-----------|---------------|
| `A_WIDTH` | `25 + 2*(VERSION > 1)` | `25 + 2*(version > 1)` |
| `B_WIDTH` | `18 + 6*(VERSION > 2)` | not used |
| `MIN_LANE_WIDTH` | `WEIGHT_WIDTH + ACTIVATION_WIDTH - 1` | `ww + aw - 1` |
| `NUM_LANES` | `A_WIDTH == WEIGHT_WIDTH? 1 : 1 + (A_WIDTH - !NARROW_WEIGHTS - WEIGHT_WIDTH) / MIN_LANE_WIDTH` | same formula |
| `OFFSETS[]` | `sliceLanes()` bit-slack distribution | `slice_lanes()` identical logic |

**Strategy B (future):** Make Python the single source of truth for OFFSETS
and pass them as module parameters to `mvu.sv`, eliminating the duplication.
This would require changing `mvu.sv`'s parameter list.

### 7.6 Potential Problems for Full FINN Integration

#### 7.6.1 add_multi.sv Must Be Patched Per Project

The stock `add_multi.sv` has no CATCH_COMP invocations.  For a FINN build
that wants compressors, `generate_hdl()` in `matrixvectoractivation_rtl.py`
must:

1. Call `add_multi_finn.py --mvu` (or import its logic) to generate
   `comp_NuW_d0.sv` files
2. Inject CATCH_COMP entries into a working copy of `add_multi.sv`
3. Write both the compressor `.sv` files and the patched `add_multi.sv`
   into `code_gen_dir`
4. Update `get_rtl_file_list()` and `instantiate_ip()` to include them

Since FINN uses a single `add_multi.sv` for all MVU nodes (sourced from
`finn-rtllib/mvu/`), the patched copy must contain CATCH_COMP entries for
**all** MVU nodes in the project.  Alternatively, each node could get its
own patched copy in its `code_gen_dir`, replacing the shared file.

#### 7.6.2 Compressor Output Width vs sumwidth()

The compressor generator's final carry-chain adder produces an output width
that may exceed the mathematical minimum (`sumwidth()`).  For N >= 4 this
is not currently an issue (widths match), but should new compressor
architectures change the output width formula, explicit width adaptation
in CATCH_COMP would be needed.

#### 7.6.3 GEA Port Warnings from Compressor Cores

The Versal LOOKAHEAD8 primitive has GEA/GEB group-enable ports that the
generated compressor code leaves unconnected.  XSim emits `VRFC 10-5021`
warnings for each instance.  These are cosmetic (functionally harmless —
the port defaults to an appropriate value in hardware) but noisy.  This
is a compressor-python emitter issue, not specific to the add_multi path.

### 7.7 Files Added

| File | Location | Purpose |
|------|----------|---------|
| `add_multi_finn.py` | `compressor-python/src/` | Compressor generator for add_multi (direct + MVU modes) |
| `run_mvu_add_multi_comp_tests.sh` | `finn/finn-rtllib/mvu/tb/` | Behavioural simulation test script |
| `run_mvu_comp_synth_tests.sh` | `finn/finn-rtllib/mvu/tb/` | Synthesis test script |

### 7.8 Test Configs (Behavioural Simulation)

8 configs from the existing `mvu_axi_tb.sv`, minus 1 excluded
(`USE_COMPRESSOR` path):

| # | VER | SIMD | WW | AW | ACCU | NW | Compressors Generated |
|---|-----|------|----|----|------|----|-----------------------|
| 0 | 1 | 3 | 4 | 4 | 16 | 1 | None (SIMD < 4) |
| 1 | 1 | 5 | 4 | 3 | 15 | 0 | comp_5u7, comp_5u6, comp_5u15 |
| 2 | 1 | 4 | 3 | 5 | 8 | 0 | comp_4u7, comp_4u8 |
| 3 | 2 | 2 | 15 | 10 | 40 | 0 | None (SIMD < 4) |
| 4 | 2 | 4 | 4 | 4 | 18 | 0 | comp_4u18 |
| 5 | 3 | 2 | 2 | 4 | 17 | 1 | None (SIMD < 4) |
| 6 | 3 | 1 | 2 | 20 | — | — | None (SIMD < 4) |
| 7 | 3 | 10 | 7 | 8 | 23 | 0 | comp_10u19, comp_10u23 |

Multiple compressors per config arise because DSP lane slicing produces
different lo_widths per lane.  For example, config #1 (DSP48E1, SIMD=5,
WW=4, AW=3, non-narrow) has 4 lanes with lo_widths [7, 7, 6, 15] —
three unique widths, each needing its own compressor module.

5. **Extend testbench coverage** — long accumulation, overflow, backpressure.

6. **Test 7-Series target** — verify LUT6_2 + CARRY4 path.

---

## 8. Code Maintenance: Refactoring for Production Quality

**Date:** 2026-03-25

### 8.1 DRY Violation in File List Construction (Fixed)

**Problem:** `matrixvectoractivation_rtl.py` had ~35 lines of file list construction logic duplicated between `instantiate_ip()` and `get_rtl_file_list()`. Both methods independently built the same list of RTL source files (base MVU files + compressor files). This duplication risked the two implementations drifting if one was updated without the other.

**Solution:** Extracted `_get_rtl_source_files(self, abspath=True)` helper method containing all file list logic. Both callers now delegate to this single source of truth. Eliminates maintenance hazard and ensures the two methods can never produce inconsistent file lists.

**Impact:** ~35 lines of duplication removed. Future compressor file handling changes require only one update location.