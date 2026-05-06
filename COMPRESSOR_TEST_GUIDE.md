# FINN Compressor Test & Benchmark Guide

## Quick Reference

### Already Run in `run_all_compressor_tests.sh`

The main test suite (`./run_all_compressor_tests.sh`) includes:

1. **Core compressor generation** (21 configs × 3 targets)
2. **dotp_comp generation** (8 configs × 3 targets)
3. **add_multi generation** (8 configs × 3 targets)
4. **MVU dotp_comp simulation** (4 configs) - **FIXED PATH BUG**
5. **MVU add_multi simulation** (4 configs)

---

## Additional Tests & Benchmarks NOT in run_all

### 1. MVU Compressor Synthesis Tests
**Location:** `/finn-rtllib/mvu/tb/run_mvu_comp_synth_tests.sh`

**What it does:**
- Synthesizes MVU with dotp_comp (2 configs)
- Reports LUT/FF/DSP utilization and timing
- Full synthesis (not just generation/simulation)

**Run time:** ~1-2 hours

**Usage:**
```bash
cd finn-rtllib/mvu/tb
./run_mvu_comp_synth_tests.sh
```

**Configs tested:**
- mh16_mw8_pe2_simd8_ww2_aw2_sa
- mh16_mw8_pe4_simd8_ww1_aw1

---

### 2. Dotp Compressor Benchmark (HLS vs Compressor)
**Location:** `/run_dotp_benchmark.sh`

**What it does:**
- Compares HLS MVAU vs RTL MVAU with dotp_comp
- Multiple SIMD/PE/bitwidth configurations
- Full synthesis with timing closure search
- Produces comparative results CSV

**Run time:** ~2-3 hours

**Usage:**
```bash
cd /home/sgerber/test_repos/finn
./run_dotp_benchmark.sh
```

**Output:**
- Work dir: `/tmp/quick_pipelined_test_TIMESTAMP/`
- Results: `benchmark_quick_results.csv` (copied to repo root)

**What's compared:**
- HLS MVAU (baseline)
- RTL MVAU with compressor dotp (optimized)
- Metrics: LUT, FF, DSP, fmax, latency

---

### 3. Add_Multi Benchmark (Binary vs Compressor)
**Location:** `/run_add_multi_benchmark.sh`

**What it does:**
- Compares binary adder tree vs compressor adder tree
- Tests lane reduction optimization only
- W10/A10, SIMD 2-32

**Run time:** ~8 hours (already completed!)

**Results available:**
- `/test_repos/finn/results/versal_full_addmulti_w10a10.csv`
- Summary: `/test_repos/add_multi_benchmark_summary.md`

---

### 4. MVAU Compressor Comparison (Versal vs 7-Series)
**Location:** `/run_mvau_compressor_comparison.sh`

**What it does:**
- Compares same MVAU config across different FPGA targets
- Versal (DSP58) vs 7-Series (DSP48E1)
- Shows how compressor performs on different architectures

**Run time:** ~1-2 hours

**Usage:**
```bash
cd /home/sgerber/test_repos/finn
./run_mvau_compressor_comparison.sh
```

---

### 5. Standalone dotp_comp Synthesis
**Location:** `/src/finn/compressor/run_dotp_synth.sh`

**What it does:**
- Synthesizes ONLY the dotp_comp module (no MVU wrapper)
- Useful for analyzing compressor in isolation
- Custom SIMD/PE/bitwidth configuration

**Run time:** ~10-30 minutes (per config)

**Usage:**
```bash
cd src/finn/compressor
./run_dotp_synth.sh --simd 96 --pe 96 --ww 5 --aw 9 [--signed_act] [--part xc7z020clg400-1]
```

**Example configs:**
```bash
# Large config on Versal
./run_dotp_synth.sh --simd 96 --pe 96 --ww 5 --aw 9 --part xcvc1902-vsva2197-2MP-e-S

# Small config on 7-Series
./run_dotp_synth.sh --simd 16 --pe 16 --ww 2 --aw 2 --part xc7z020clg400-1

# Signed activations
./run_dotp_synth.sh --simd 32 --pe 32 --ww 4 --aw 4 --signed_act
```

---

## Recommended Test Order

### Quick Validation (after code changes)
```bash
# 1. Fix the path bug first (already done)
# 2. Run MVU dotp_comp simulation tests (4 configs, ~10 min)
cd finn-rtllib/mvu/tb
./run_mvu_comp_tests.sh

# 3. Run MVU add_multi simulation tests (4 configs, ~10 min)
./run_mvu_add_multi_comp_tests.sh
```

### Medium Validation (~2-3 hours)
```bash
# Synthesis tests for MVU integration
cd finn-rtllib/mvu/tb
./run_mvu_comp_synth_tests.sh
```

### Full Benchmark Suite (~8-10 hours)
```bash
# Option 1: All compressor tests (simulation + generation)
cd /home/sgerber/test_repos/finn
./run_all_compressor_tests.sh

# Option 2: Dotp benchmark (HLS vs Compressor comparison)
./run_dotp_benchmark.sh

# Option 3: Add_multi benchmark (already done!)
# Results at: finn/results/versal_full_addmulti_w10a10.csv
```

### Cross-Platform Comparison
```bash
# Compare Versal vs 7-Series
./run_mvau_compressor_comparison.sh
```

---

## Test Output Locations

### Simulation Tests
- **Logs:** `finn-rtllib/mvu/tb/gen/<config>/*.runner.out`
- **Generated HDL:** `finn-rtllib/mvu/tb/gen/<config>/*.sv`

### Synthesis Tests
- **Work dir:** `/tmp/finn_synth_tests/` or `$FINN_BUILD_DIR/`
- **Reports:** Utilization, timing, resource usage

### Benchmarks
- **dotp_benchmark:** `/tmp/quick_pipelined_test_*/results.csv`
- **add_multi_benchmark:** `/scratch/.../add_multi_benchmark_*/`
  - CSV: `add_multi_results.csv`
  - JSON: `add_multi_results.json`

---

## Known Issues & Fixes

### ✓ FIXED: MVU dotp_comp path bug
**Issue:** `mul_comp_map.sv` path was duplicated
**Fix:** Updated `finn-rtllib/mvu/tb/mvu_comp_tb_template.tcl` line 27
```tcl
# Before: {comp_dir}/finn/compressor/hdl/mul_comp_map.sv
# After:  {comp_dir}/../hdl/mul_comp_map.sv
```

### Unpipelined SIMD=4 compressor
**Issue:** comp_4u24_d0.sv has no pipeline stages (depth=0)
**Impact:** 22% slower than binary adder for SIMD=4
**Status:** Documented in add_multi_benchmark_summary.md
**Fix:** Needs compressor generator update to force minimum pipeline depth

---

## Test Selection Guide

**Choose tests based on your goal:**

| Goal | Run This | Time |
|------|----------|------|
| Quick smoke test after changes | MVU sim tests | 10 min |
| Verify synthesis works | MVU synth tests | 1-2 hrs |
| Compare HLS vs RTL+comp | dotp_benchmark | 2-3 hrs |
| Compare binary vs comp adder | add_multi (done!) | 8 hrs |
| Cross-platform comparison | mvau_comparison | 1-2 hrs |
| Full regression | run_all_compressor_tests | varies |
| Custom config analysis | run_dotp_synth.sh | 10-30 min |

---

## Environment Requirements

All tests need:
- **Vivado** in PATH (2024.2 recommended)
- **Python 3** with FINN dependencies
- **FINN_ROOT** or PYTHONPATH set correctly
- Sufficient disk space for synthesis runs

Optional:
- `FINN_BUILD_DIR` - Override default `/tmp/` for builds
- `MAX_WORKERS` - Parallel workers (default: 12)
- `KEEP_LOG` - Keep Vivado logs (default: 0)
