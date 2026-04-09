# FINN Compressor Integration - Changes Summary

This document tracks all new and modified files in the FINN compressor integration compared to the original `compressor-python` repository.

**Baseline**: Original upstream `test_repos/compressor-python` clone (4609 lines total)
**Current**: FINN integrated version `finn/src/finn/compressor` (5912 lines total, +28% net addition including all integration work)
**Status**: Integration complete as of 2026-04-09
**Cleanup**: Code review and debloat completed 2026-04-09

---

## Cleanup Summary (2026-04-09)

**Net reduction**: -793 lines (-49% bloat removed)

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

### Core Integration (7 files)
- `__init__.py` - Added `generate_add_multi_comps()` export
- `src/dotp_finn.py` - FINN integration wrapper for dotp compressors
- `src/add_multi_finn.py` - FINN integration wrapper for add_multi compressors
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
