# Open Questions & Potential Issues

## 1. Two-Pass Compression Strategy May Generate Extra Hardware for Versal

**Context:** The infinite loop fix in `compressor_constructor.py` changed from a single-pass to a two-pass compression strategy for accumulator mode.

**Original (single-pass, worked for Versal):**
```python
compression_goal = final_adder.compression_goal - 1 - constants
# Compress, add constants → result: height = goal - 1
```

**New (two-pass, fixes 7-Series infinite loop):**
```python
# Pass 1: compress to final_adder.compression_goal - constants
# Add constants → result: height = goal
# Pass 2: compress to final_adder.compression_goal - 1
# Result: height = goal - 1
```

**Functionally identical output** (both reach height = `goal - 1`), but the new two-pass approach may generate **one extra CompressionStage** in the graph between pass 1 and pass 2.

**Impact:**
- ✅ Required for 7-Series (fixes infinite loop with constants)
- ⚠️ May add extra LUTs/pipeline stages for Versal (even though Versal was working before)
- 🔍 **TODO:** Compare Versal synthesis results before/after to quantify overhead

**Mitigation options:**
1. Accept the extra stage (probably negligible)
2. Add platform-specific logic (single-pass for Versal, two-pass for 7-Series)
3. Optimize the compression goal calculation to avoid the second pass when unnecessary

---

## 2. 7-Series Gate Absorption Disabled (Temporary Workaround)

7-Series gate absorption counters (`MuxCYPredAdderCandidate`, `RippleSumPredAdderCandidate`) have critical bugs causing simulation hangs. Currently disabled in `dotp_finn.py` - only `SinglePredCandidate` is used, resulting in suboptimal LUT usage compared to Versal. See `REPORT.md` section 5.8 for details.

**Note**: Extensive VHDL-reference comments added to `counter_candidates.py` (MuxCYAtom classes) during debugging - may remove once stable.
