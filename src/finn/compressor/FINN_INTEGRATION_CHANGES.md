# FINN Compressor Integration - Changes Summary

This document tracks all new and modified files in the FINN compressor integration compared to the original `compressor-python` repository.

**Baseline**: Original upstream `test_repos/compressor-python` clone (4609 lines total)
**Current**: FINN integrated version `finn/src/finn/compressor` (5912 lines total, +28% net addition including all integration work)
**Status**: Integration complete as of 2026-04-09
**Cleanup**: Code review and debloat completed 2026-04-09
**Test fixes**: Path issues fixed, dual-platform support added 2026-04-09

---

## Recent Changes

### Code Quality Cleanup (2026-04-10)

**dotp_finn.py cleaned up:**
- **Removed**: ~200 lines of misleading bug fix code and comments
  - Deleted `compute_natural_output_width()` function (47 lines) - was unnecessary
  - Deleted 150+ lines of sign-extension "fix" comments - fix was mathematically equivalent to original
  - Root cause was gate absorption bug, not constants handling
- **Removed**: Unused imports (`sys`, `typing.Optional`)
- **Removed**: Unused imports from target module (`Target`, `Versal`, `SevenSeries`)
- **Fixed**: PEP 8 violations (blank lines, comment spacing)
- **Simplified**: Verbose docstring (17 lines → 2 lines)
- **Result**: Clean, production-ready integration file (259 → 245 lines)

**REPORT.md updated:**
- Section 5.01: Clarified -256 offset bug was fixed by gate absorption disable, not sign-extension
- Section 5.1: Changed status from "HIGH PRIORITY" → "RESOLVED" (narrow weight check commented out)
- Section 5.9: Changed status from "CRITICAL" → "NOT AN ISSUE" (NARROW_WEIGHTS is DSP-specific, compressor doesn't need it)

### Test Infrastructure Fixes (2026-04-09)

**Path Issues Fixed:**
- `dotp.py` now uses absolute paths for output files (was relative `gen/`)
- Template processing replaces relative paths with absolute paths in TCL
- Tests work regardless of CWD or Vivado work directory
- Fixes: "FileNotFoundError: gen/comp_*.sv" and "hdl/mul_comp_map.sv does not exist"

**Dual-Platform Support Added:**
- All 3 standalone test suites now support both Versal and 7-Series
- Suite 1 (core): `./run_tests.sh "" [versal|7series]`
- Suite 2 (dotp_comp): `./run_dotp_comp_tests.sh [versal|7series]`
- Suite 3 (add_multi): `./run_add_multi_comp_tests.sh [versal|7series]`
- `run_all_compressor_tests.sh` now runs 8 test suites (was 5): 3 suites × 2 platforms + 2 MVU suites
- Versal: Full gate absorption (VersalPredAdder + RippleSumPredAdder)
- 7-Series: Gate absorption disabled (SinglePredCandidate only)

### Cleanup Summary (2026-04-09)

**Net reduction**: -793 lines (-49% bloat removed)

### Additional Cleanup (2026-04-10)

**Net reduction**: -214 lines from dotp_finn.py (misleading bug fixes + import bloat)

**add_multi_finn.py cleaned up:**
- **Removed**: Standalone script hack (sys.path.insert block, 3 lines) - now consistent with dotp_finn.py
- **Removed**: TODO comment (4 lines) about slice_lanes() strategy (documented in hacky patterns instead)
- **Fixed**: Import organization (moved shutil to top)
- **Fixed**: Typos and spacing (3 comment spacing fixes, 2 typos)
- **Net reduction**: -10 lines (420 → 410 lines)
- **Result**: Consistent with dotp_finn.py coding style

**Category 1 Review Complete (2026-04-10):**
- ✅ Reviewed all 3 core integration files (dotp_finn.py, add_multi_finn.py, __init__.py)
- ✅ Identified 3 hacky patterns (documented below)
- ✅ Code quality cleanup completed

**Category 2 Review Complete (2026-04-10):**

**compressor_constructor.py (198 → 174 lines):**
- **Removed**: Verbose docstring, redundant comments, infinite loop guard (non-fix)
- **Net reduction**: -24 lines
- **Result**: Clean infinite loop fix without defensive bloat
- **Note**: See OPEN_QUESTIONS.md for potential Versal hardware impact

**counter_candidates.py (669 → 724 lines):**
- **No cleanup**: All +55 lines are critical bug fixes and VHDL-reference documentation
- **Fixes**: MuxCYAtom14 O5 predicate, MuxCYAtom2 O5/O6, MuxCYAtom06 O5
- **Result**: Extensive comments explain predicates vs VHDL reference (may remove once stable)

**target.py (86 → 69 lines):**
- **Removed**: is_versal_part() helper (inlined), _TARGET_NAMES dict (over-engineered)
- **Net reduction**: -17 lines
- **Result**: Two simple functions (resolve_target, resolve_target_name) without abstraction bloat

**absorption_counter_candidates.py (322 → 263 lines):**
- **Removed**: 59 lines of commented-out experimental code in MuxCYPredAdder.build_hardware()
- **Kept**: MuxCYRippleSum implementation (buggy but closer to working)
- **Fixed**: MuxCYPredAdderCandidate early break logic
- **Net reduction**: -59 lines
- **Result**: Clean NotImplementedError for MuxCYPredAdder, MuxCYRippleSum has CARRY4.O wiring bugs (disabled in target.py)

**Category 3 Review Started (2026-04-10):**

**emitter.py (305 → 318 → 305 lines):**
- **Removed**: visit_constant() method (7 lines) - never called, defensive bloat
- **Removed**: Redundant Constant special-case in visit_wire() (6 lines) - get_name() already handles Constants
- **Net reduction**: -13 lines
- **Result**: Identical to original - no changes needed for integration

**nodes.py (384 → 386 → 384 lines):**
- **Removed**: Constant.accept() method (2 lines) - never called, defensive bloat
- **Net reduction**: -2 lines
- **Result**: Identical to original - no changes needed for integration

### Files Deleted (3 files, -621 lines)
- `benchmark_compressor.py` - Redundant, superseded by benchmark_hls_vs_compressor.py
- `benchmark_hls_vs_compressor_legacy.py` - Broken code (missing function call line 205)
- `test_enable.py` - One-off debug script

### Files Modified (2 benchmarks)
- **benchmark_hls_vs_compressor.py** (620→434 lines, -30% bloat)
  - **Removed**: HLS timing check (80 lines), run_variants feature, duplicate DSP parsing, duplicate board configs
  - **Added**: BSD license, shared utilities, simplified CSV (9 columns with latency)
  - **Changed**: Now uses `make_single_fclayer_modelwrapper()` from test infrastructure

- **benchmark_add_multi.py** (420→396 lines, -6% bloat)
  - **Removed**: Custom `create_model()`, manual ONNX transformations, unused imports
  - **Added**: BSD license, latency calculation, shared utilities
  - **Changed**: Now uses `make_single_fclayer_modelwrapper()`, standardized board configs

### Files Added (1 file, +64 lines)
- **benchmark_utils.py** - Shared utilities
  - `BOARD_CONFIGS` - Standardized 5 boards (pynq-z1, ultra96, zcu104, u250, vck190)
  - `parse_dsp_counts()` - Handle all DSP types across FPGA families
  - `compute_latency_cycles()` - (MH/PE) × (MW/SIMD) formula
  - `format_config_label()` - Standardized naming

### Files Enhanced (2 files)
- **src/tests/tester.py** - Added BSD license, docstring
- **src/tests/__init__.py** - Added BSD license header

---

## Cleanup Learnings & Style Guide

### Principles Applied
1. **Remove bloat first, ask questions later** - Delete unused/broken code immediately
2. **Verify actual usage** - Check shell scripts, not assumptions
3. **DRY (Don't Repeat Yourself)** - Extract shared utilities
4. **Use existing infrastructure** - Prefer test helpers over custom builders
5. **Simplify outputs** - Focus on metrics actually needed

### Red Flags to Remove
- **Half-implemented features** - Code that checks but never acts (HLSTimingError never raised)
- **Unused parameters** - Arguments passed but never used (fpga_part in run_comparison)
- **Dead imports** - Libraries imported but never called (glob, re, numpy)
- **Duplicate logic** - Same parsing/formatting code in multiple files
- **Debug artifacts** - One-off test scripts, commented-out code
- **Bloated docstrings** - 20-line docstrings for 5-line functions
- **CSV kitchen sink** - 15 columns when 9 suffice

### Future Cleanup Style
- **Start with usage audit** - `grep` for function calls, check shell script invocations
- **Delete first, refactor second** - Remove broken/redundant files before optimizing
- **Shared utilities over copy-paste** - Create `utils.py` for 2+ uses
- **Consistency over features** - Same test infrastructure across all benchmarks
- **Licenses everywhere** - BSD-3-Clause on all new files
- **Focus on core functionality** - Remove features not used by shell scripts/tests

---

## New Files vs Original compressor-python (11 total)

### Benchmarking (3 files)
1. **benchmark_add_multi.py** (396 lines)
   - Benchmarks add_multi compressor (8-bit, SIMD≥4, DSP+optimized adder tree)
   - Compares: RTL binary adder vs RTL compressor adder

2. **benchmark_hls_vs_compressor.py** (434 lines)
   - Benchmarks HLS vs RTL+Compressor (ww≤4, aw≤4, dotp_comp path)
   - Compares: HLS MVAU vs RTL MVAU with LUT compressors

3. **benchmark_utils.py** (64 lines)
   - Shared utilities for benchmarks

### Test Configs (2 files)
4. **configs/dotp_add_multi_7sieries_config.py** (46 lines)
   - Mixed test: dotp_comp eligible + add_multi eligible configs

5. **configs/dotp_standard_7sieries_config.py** (52 lines)
   - Standard dotp_comp test configs (2-bit, 4-bit operands)

### Test Infrastructure (6 files)
6. **lib/test_common.sh** (28 lines) - Shared bash utilities
7. **run_add_multi_comp_tests.sh** (65 lines) - Test add_multi path
8. **run_dotp_comp_tests.sh** (92 lines) - Test dotp_comp path
9. **run_tests.sh** (135 lines) - Main test runner (unit + integration)
10. **src/tests/__init__.py** (4 lines) - Python package marker
11. **src/tests/tester.py** (43 lines) - XSim wrapper for CLI --test flag

---

## Modified Files vs Original (33 total)

### Core Integration (3 files) - **REVIEWED 2026-04-10 ✓**
- `__init__.py` (5 lines, new) - Package exports, clean minimal design
- `src/dotp_finn.py` (245 lines) - FINN integration wrapper for dotp compressors
  - **Cleaned 2026-04-10**: Removed ~200 lines of unnecessary "bug fix" code
  - Reverted to original simple constants handling (mathematically correct)
  - Removed misleading comments about sign-extension (bug was in gate absorption, not here)
- `src/add_multi_finn.py` (410 lines) - FINN integration wrapper for add_multi compressors
  - **Cleaned 2026-04-10**: Removed standalone script hack, TODO, import bloat
  - **Hacky patterns identified**: See "Known Hacky Patterns" section below

### Other Integration Files (4 files)

- `src/main.py` - Enhanced CLI with enable mode support
- `src/passes/compressor_constructor.py` - Added enable mode, accumulation fixes
- `src/passes/emitter.py` - Enhanced Verilog generation
- `src/utils/shape.py` - Added Shape utility class

### Templates (14 files)
- 7-Series templates: 6 files (dotp, add_multi, variants)
- Versal templates: 8 files (dotp, add_multi, genINT8 variants)

### Test Files (12 files)
- `src/tests/test_gen.py` - SystemVerilog testbench generator
- Various test configs and utilities

---

## Key Architecture

### Two Compressor Paths
1. **dotp_comp** (ww≤4, aw≤4) - Replaces DSP entirely with LUT compressor tree
2. **add_multi** (SIMD≥4) - DSP compute + optimized LUT adder tree for lane reduction

### Board Support
- **7-Series** (DSP48E1): Versal and 7-Series compressors
- **Versal** (DSP58): Versal compressors
- **UltraScale+** (DSP48E2): Not supported (no CARRY8 target)

### Test Strategy
- **Unit tests**: Individual compressor generation (run_tests.sh)
- **Integration tests**: Full FINN builds with XSim verification (run_dotp_comp_tests.sh, run_add_multi_comp_tests.sh)
- **Benchmarks**: LUT/DSP/timing comparison (benchmark_*.py scripts)

---

## Known Hacky Patterns (Identified 2026-04-10)

### 1. Generate-then-rename hack (add_multi_finn.py:171-203)
**Severity**: Low
**Pattern**: Creates temp module, generates, reads back, replaces module name, writes final file
**Why**: Delay is unknown until generation completes (depends on tree structure)
**Mitigation**: Well-documented, safe, unavoidable given architecture
**Action**: None needed

### 2. String injection patching (add_multi_finn.py:269-284)
**Severity**: Medium
**Pattern**: Reads add_multi.sv template, string replaces marker with CATCH_COMP macro calls
```python
marker = "\t// FINN_GENERATED_COMP_ENTRIES\n"
add_multi_src = add_multi_src.replace(marker, catch_lines + marker)
```
**Why**: Injects generated compressor entries into SystemVerilog template
**Mitigation**: Runtime error if marker not found (fails fast)
**Fragility**: Requires exact tab/newline match
**Action**: Works fine, but consider template engine if complexity grows

### 3. Dual slice_lanes() implementation (add_multi_finn.py:62-112)
**Severity**: HIGH
**Pattern**: Python function duplicates mvu.sv::sliceLanes() SystemVerilog logic (48 lines)
**Why**: Lo_width values determine compressor Shape, needed at generation time
**Risk**: Manual sync required - divergence causes silent fallback to binary tree
**Current check**: $warning in add_multi.sv catches divergence at simulation time only
**Impact if diverges**: Functional correctness maintained, but compressor benefit lost
**Recommended action**: Add Python test that verifies slice_lanes() matches known SV outputs for standard configs
**Example test cases**:
- VERSION=1, WW=2, AW=2, ACCU_WIDTH=16, NARROW_WEIGHTS=0 → verify lo_widths match SV
- VERSION=3, WW=4, AW=4, ACCU_WIDTH=32, NARROW_WEIGHTS=1 → verify lo_widths match SV
- Add to unit test suite to catch regressions
