# CybSec MLP Build Failure - Scale Absorption Issue

## Problem Statement

Test `test_end2end_cybsec_mlp_build[Pynq-Z1]` failed at Step 4 (step_convert_to_hw) with:

```
AssertionError: MultiThreshold_3: MultiThreshold out_scale must be 1 for HLS conversion.
```

**Build progress:**
- ✅ Step 1: step_qonnx_to_finn
- ✅ Step 2: step_tidy_up  
- ✅ Step 3: step_streamline
- ❌ Step 4: step_convert_to_hw (FAILED)

## Root Cause

### Where the Scale Comes From

The cybersec model export adds a final output quantization (line 68-70 in test):

```python
self.qnt_output = QuantIdentity(
    quant_type=QuantType.BINARY, bit_width=1, min_val=-1.0, max_val=1.0
)
```

This creates a `MultiThreshold` node with a **scale factor** to map the network output to {-1, 1}.

### Why It's Not Absorbed

The streamlining step (`step_streamline` in `build_dataflow_steps.py`, line 392) only includes:

```python
model = model.transform(absorb.AbsorbScalarMulAddIntoTopK())
```

**Problem**: This ONLY absorbs scales into `TopK` nodes (used for multi-class classification).

**CybSec is binary classification** (output=1), so:
- ❌ No TopK node exists
- ❌ Final scale remains in MultiThreshold
- ❌ Conversion to hardware fails because RTL requires `out_scale=1.0`

### Why RTL Requires Scale=1.0

Unlike HLS (which generates custom code per layer), RTL uses **pre-designed, modular blocks**:

```
MVAU_rtl (fixed interface)    Thresholding_rtl (separate fixed block)
    └─ Outputs raw accumulator  └─ Compares: acc > threshold
```

RTL blocks can't dynamically absorb arbitrary scales because:
1. **Architectural separation** - MVAU and Thresholding are separate modules
2. **Fixed interfaces** - AXI-Stream with predefined data formats
3. **Parameterized reusability** - Same block used for all configs
4. **Memory-based thresholds** - Pre-loaded from init files, can't rescale at runtime

**Design philosophy**: Do graph transformations in software (streamlining), then map to clean RTL blocks.

## Why Error Message is Misleading

The error says "for HLS conversion" but it's actually a **generic hardware conversion** check:

```python
# Line 215 in convert_to_hw_layers.py
assert scale == 1.0, (
    node.name + ": MultiThreshold out_scale must be 1 for HLS conversion."
)
```

This check happens at Step 4, **BEFORE** RTL vs HLS is chosen (Step 7). Both RTL and HLS require normalized scales, but:
- **HLS**: Can absorb scales during C++ code generation if needed
- **RTL**: Absolutely requires `scale=1.0` due to fixed architecture

The `specialize_layers_config_cybsec_rtl.json` config is applied **later** at Step 7.

## Incorrect "Fix" That Was Attempted

### ❌ Removing `standalone_thresholds=True`

Setting `standalone_thresholds=False` made the error go away, but:

**Before (correct):**
```
MatMul (separate) → MultiThreshold (separate)
         ↓                    ↓
    MVAU_rtl           Thresholding_rtl
   (compressors!)
```

**After (wrong):**
```
MatMul + MultiThreshold (merged)
         ↓
    MVAU_hls (absorbs scale internally)
   (NO compressors - HLS only!)
```

**Problems created:**
1. ❌ Switched from RTL to HLS backend
2. ❌ No compressors (compressors only work with RTL)
3. ❌ Not testing what was intended
4. ❌ `specialize_layers_config_cybsec_rtl.json` ignored

**Why it "worked":** HLS can absorb the threshold scale during C++ code generation, so `out_scale != 1.0` is tolerated. But you're no longer using RTL or compressors!

## Correct Fix

### Solution: Add Missing Transformations to Streamlining

**Edit**: `/home/sgerber/test_repos/finn/src/finn/builder/build_dataflow_steps.py`

**Around line 390-392**, change:

```python
# BEFORE:
model = model.transform(ConvertBipolarMatMulToXnorPopcount())
model = model.transform(Streamline())
# absorb final add-mul nodes into TopK
model = model.transform(absorb.AbsorbScalarMulAddIntoTopK())
model = model.transform(InferDataLayouts())
```

**TO:**

```python
# AFTER:
model = model.transform(ConvertBipolarMatMulToXnorPopcount())
model = model.transform(Streamline())
# absorb final add-mul nodes into MultiThreshold AND TopK
model = model.transform(absorb.AbsorbMulIntoMultiThreshold())  # ADD THIS
model = model.transform(absorb.AbsorbAddIntoMultiThreshold())  # ADD THIS
model = model.transform(absorb.AbsorbScalarMulAddIntoTopK())
model = model.transform(InferDataLayouts())
```

**Why this works:**
- `AbsorbMulIntoMultiThreshold()` absorbs multiplication scales into threshold values
- `AbsorbAddIntoMultiThreshold()` absorbs addition offsets into threshold values
- These transformations already exist in `absorb.py` but weren't being called
- Works for both binary classification (MultiThreshold) and multi-class (TopK)

### Verification After Fix

After applying the fix, run:

```bash
pytest tests/end2end/test_end2end_cybsec_mlp.py::test_end2end_cybsec_mlp_build[Pynq-Z1] -v
```

Should now proceed past Step 4 and use **RTL MVAUs with compressors**.

## Alternative Solutions (Not Recommended)

### Option 2: Fix the Export
Remove output quantization from the test:

```python
# In test_end2end_cybsec_mlp.py, line 78:
# BEFORE:
out_final = self.qnt_output(out_original)
return out_final

# AFTER:
return out_original  # No output quantization
```

**Problem**: Changes model semantics, may affect accuracy.

### Option 3: Manual Workaround
Add transformations before build:

```python
# In test_end2end_cybsec_mlp_build, before line 185:
from finn.transformation.streamline import absorb
model = ModelWrapper(model_file)
model = model.transform(absorb.AbsorbMulIntoMultiThreshold())
model = model.transform(absorb.AbsorbAddIntoMultiThreshold())
model.save(model_file)
```

**Problem**: Only fixes this one test, not the general issue.

## Impact Assessment

**This is NOT a compressor bug** - it's a missing transformation in the standard FINN build flow.

**Scope:**
- Affects models with output quantization but no TopK layer
- Binary/multi-label classification (vs multi-class with TopK)
- Only visible when using RTL backend with `standalone_thresholds=True`
- HLS backend masks the issue by absorbing scales during code generation

**Recommended action:**
- Fix the streamlining step (Solution 1)
- Benefits all similar models, not just cybersec
- Maintains RTL+compressor test coverage

## Files Referenced

- `/home/sgerber/test_repos/finn/tests/end2end/test_end2end_cybsec_mlp.py` - Test file
- `/home/sgerber/test_repos/finn/src/finn/builder/build_dataflow_steps.py` - Build steps (FIX HERE)
- `/home/sgerber/test_repos/finn/src/finn/transformation/streamline/absorb.py` - Absorption transformations
- `/home/sgerber/test_repos/finn/src/finn/transformation/fpgadataflow/convert_to_hw_layers.py` - Where assertion fails
- `/home/sgerber/test_repos/finn/specialize_layers_config_cybsec_rtl.json` - RTL config (correct)

## Timeline

- Test failed at Step 4/20
- Workaround applied: removed `standalone_thresholds=True`
- Test "passed" but switched to HLS (no compressors)
- Root cause identified: missing scale absorption transformations
- Proper fix: add transformations to streamlining step
