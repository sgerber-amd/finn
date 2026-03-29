# Compressor Benchmark Guide

## Quick Reference

### CSV Output Format
```csv
Config,Implementation,LUT,FF,DSP,BRAM,WNS_ns,fmax_MHz,achieved_fmax_MHz,iterations,LUT_delta,fmax_improvement_pct
mw16_mh16_pe2_simd8_w2_a2,HLS,696,663,0,0,1.990,332.2,480.8,4,,
mw16_mh16_pe2_simd8_w2_a2,RTL_Compressor,271,292,0,0,1.700,303.0,850.3,6,-425,76.9
```

**Key Columns:**
- `LUT_delta`: RTL - HLS (negative = compressor wins)
- `fmax_improvement_pct`: % faster than HLS
- `achieved_fmax_MHz`: From binary search (real max frequency)
- `fmax_MHz`: From initial synthesis @ target clock

## Test Configurations

### 9 Configurations Tested

| # | Config | MW×MH | PE | SIMD | W/A bits | Why This Config? |
|---|--------|-------|----|----- |----------|------------------|
| 1 | Small binary | 16×16 | 2 | 8 | 1/1 | **Maximum** compressor benefit |
| 2 | Medium binary | 32×32 | 4 | 8 | 1/1 | Larger binary network |
| 3 | Small 2-bit | 16×16 | 2 | 8 | 2/2 | Good compressor benefit |
| 4 | Medium 2-bit | 32×32 | 2 | 16 | 2/2 | Higher SIMD parallelism |
| 5 | Large 2-bit | 64×64 | 4 | 16 | 2/2 | Stress test |
| 6 | Small 3-bit | 16×16 | 2 | 8 | 3/3 | Moderate benefit |
| 7 | Medium 3-bit | 32×32 | 2 | 8 | 3/3 | Boundary case (WW<4, AW<4) |
| 8 | Mixed 2×3 | 16×16 | 2 | 8 | 2/3 | Asymmetric precision |
| 9 | Mixed 1×2 | 32×32 | 4 | 8 | 1/2 | Binary weights, low act |

**Why these configs?**
- **1-3 bit range**: Where compressors are eligible (WW<4, AW<4)
- **Varying sizes**: Show scaling properties
- **Different PE/SIMD**: Show impact of parallelism
- **Mixed precision**: Real-world scenarios

## Running Benchmarks

### Option 1: Quick Test (2-3 hours, fully pipelined)
```bash
./run-docker.sh ./run_quick_benchmark.sh
```
**Tests:** All 9 configs with pipeline_every=1
**Output:** `/scratch/users/$USER/finn_temp_files/quick_pipelined_test_*/results.csv`

### Option 2: Overnight Comprehensive (8-12 hours)
```bash
./run-docker.sh ./run_overnight_benchmark.sh
```
**Tests:** All 9 configs × 3 pipeline strategies:
1. **Unpipelined** (pipeline_every=None, 5.0ns target) - Baseline
2. **Fully pipelined** (pipeline_every=1, 1.5ns target) - Maximum fmax
3. **Moderate** (pipeline_every=2, 2.5ns target) - Balanced

**Output:**
- Individual results: `/scratch/.../overnight_benchmark_*/*/results.csv`
- **Consolidated:** `/scratch/.../overnight_benchmark_*/SUMMARY.csv`

### Option 3: Custom Run
```bash
./run-docker.sh python -m finn.compressor.benchmark_hls_vs_compressor \
    --board pynq-z1 \
    --synth-only \
    --timing-search \
    --synth-clk-period-ns 1.5 \
    --keep \
    --work-dir /scratch/users/$USER/my_custom_test
```

## Expected Results (Pipelined vs Unpipelined)

### Unpipelined Baseline (current)
```
Config: mw16_mh16_pe2_simd8_w2_a2
HLS:           696 LUT, 480 MHz (hit limit)
RTL Compressor: 271 LUT, 480 MHz (hit limit)
Benefit: 61% less LUTs, same fmax (limited by search range)
```

### Fully Pipelined (after changes)
```
Config: mw16_mh16_pe2_simd8_w2_a2
HLS:           720 LUT, 650 MHz (estimated)
RTL Compressor: 310 LUT, 850 MHz (estimated)
Benefit: 57% less LUTs, 31% higher fmax, 2.7× better LUT efficiency!
```

## What to Look For

### 1. Resource Efficiency (LUT/fmax ratio)
Lower is better: `Efficiency = LUT / achieved_fmax_MHz`

**Expected:**
- HLS: ~1.0-1.5 LUT per MHz
- RTL Compressor: ~0.3-0.4 LUT per MHz ← **2-3× better!**

### 2. Fmax Scaling
How well does fmax improve with pipelining?

**Expected:**
- Unpipelined: 300-500 MHz
- Moderate (pipe=2): 500-700 MHz
- Aggressive (pipe=1): 700-1000 MHz

### 3. Throughput per LUT
`Throughput = fmax / cycles_per_vector / LUT_count`

This shows how efficiently each LUT is being used.

## Interpreting the Timing Search

### Good Result (found the limit):
```
Iteration 1: 6.0 ns → WNS = +1.5 ns (PASS)
Iteration 2: 4.0 ns → WNS = +0.8 ns (PASS)
Iteration 3: 2.5 ns → WNS = +0.2 ns (PASS)
Iteration 4: 1.8 ns → WNS = -0.3 ns (FAIL) ← Found it!
Iteration 5: 2.15 ns → WNS = +0.05 ns (PASS)
Final: 2.15 ns = 465 MHz
```

### Bad Result (hit limit):
```
Iteration 1: 6.0 ns → WNS = +1.99 ns (PASS)
Iteration 2: 4.0 ns → WNS = +1.99 ns (PASS)
Iteration 3: 2.4 ns → WNS = +1.99 ns (PASS)
Iteration 4: 2.08 ns → WNS = +1.99 ns (PASS)
Final: 2.08 ns = 480 MHz
^ WNS stayed constant → hit lower bound, design can go faster!
```

## Post-Processing Tips

### Load CSV in Python
```python
import pandas as pd

df = pd.read_csv('results.csv')

# Calculate efficiency
df['lut_per_mhz'] = df['LUT'] / df['achieved_fmax_MHz']

# Filter to RTL only
rtl = df[df['Implementation'] == 'RTL_Compressor']

# Best configs by efficiency
print(rtl.nsmallest(5, 'lut_per_mhz'))
```

### Compare Strategies
```python
# Load consolidated overnight results
df = pd.read_csv('SUMMARY.csv')

# Pivot to compare strategies
pivot = df.pivot_table(
    index='Config',
    columns='Pipeline_Strategy',
    values='achieved_fmax_MHz'
)
print(pivot)
```

## Troubleshooting

### Synthesis fails with timing errors
→ Increase `--synth-clk-period-ns` (less aggressive target)

### All configs hit 500 MHz limit
→ Decrease timing search `clk_period_ns_min` (currently 0.5 ns)

### Out of memory during synthesis
→ Reduce config sizes or run fewer configs at once

### Results show no improvement
→ Check that pipelining was actually enabled:
```bash
grep "pipeline_every" src/finn/compressor/src/dotp_finn.py
# Should show: pipeline_every=1, not None
```

## Time Estimates

Per config (HLS + RTL):
- Synthesis only: ~5-10 min
- Synthesis + timing search: ~30-60 min (depends on how many iterations)

Total for 9 configs:
- **Quick (no timing search):** ~1 hour
- **With timing search:** ~4-6 hours
- **Overnight (3 strategies):** ~12-18 hours

## Files Generated

```
work_dir/
├── results.json          # Raw JSON results
├── results.csv           # CSV format (importable)
├── mw16_mh16_.../        # Per-config directories
│   ├── hls/
│   │   └── output/
│   │       ├── report/ooc_synth_and_timing.json
│   │       └── ...
│   └── rtl_comp/
│       └── output/
│           └── ...
└── run.log               # Full build log
```

For overnight run, also:
```
SUMMARY.csv              # All strategies combined
unpipelined_baseline/
pipelined_aggressive/
pipelined_moderate/
```
