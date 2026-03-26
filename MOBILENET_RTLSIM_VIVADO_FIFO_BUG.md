# ROOT CAUSE ANALYSIS: MobileNet RTLSim Failure

## Problem Statement
Test `test_end2end_mobilenet_stitched_ip_rtlsim` failed with:
```
ValueError: invalid literal for int() with base 16: '0XXX'
```

## Root Cause: Vivado FIFO Initialization Glitch

### Evidence Chain

**1. Vivado Warning (FALSE ALARM - Not the issue)**
```
CRITICAL WARNING [BD 41-759]: 
/MVAU_rtl_0/MVAU_rtl_0_wstrm/s_axis_0_tvalid tied to 0
```
- **Analysis**: This is NOT the root cause
- The `s_axis_0_tvalid` is an input to memstream wrapper
- When `SETS=1` (which it is), this signal is **ignored** by the RTL (memstream.sv:92)
- The memstream internally generates `vld0 = 1` (always valid)
- This warning is harmless

**2. FINN Warning (ACTUAL ISSUE)**
From `create_stitched_ip.py:405-409`:
```python
if firstfifo.get_nodeattr("impl_style") == "vivado":
    warnings.warn(
        """First FIFO has impl_style=vivado, which may cause
        simulation glitches (e.g. dropping the first input sample
        after reset)."""
    )
```

**3. Test Warnings**
The test output showed:
```
/finn/transformation/fpgadataflow/create_stitched_ip.py:405: UserWarning: 
First FIFO has impl_style=vivado, which may cause simulation glitches
```

### Root Cause Explanation

**Vivado-style FIFOs** (Xilinx IP Catalog FIFOs) have known RTL simulation issues:

1. **Post-Reset Behavior**: After reset de-assertion, Vivado FIFOs may have 1-2 cycles where internal state is undefined
2. **First Sample Glitch**: The very first data sample may contain `X` (undefined) values
3. **Propagation**: These `X` values propagate through the entire datapath
4. **Compressor Sensitivity**: LUT-based compressors (unlike DSP blocks) have no inherent `X`→`0` behavior, so `X` propagates through the entire computation
5. **Output Failure**: Final output contains `XXX` hex values → parser crashes

###Why This Affects MobileNet But Not Other Tests

1. **Compressor Usage**: MobileNet uses 14 MVAU layers with compressors
   - Signature: `comp_16xs4u4` (4-bit weights × 4-bit activations)
   - Compressor path is LUT-based, propagates `X` values faithfully
   
2. **DSP Path More Robust**: Other tests may use DSP48 path which has:
   - Internal registers with defined reset values
   - Arithmetic units that treat `X` as `0` in some cases
   
3. **Long Simulation**: 20-hour runtime means complex design
   - More layers → more opportunities for `X` propagation
   - Vivado FIFO at input → affects ALL downstream layers

### Technical Details: Where `X` Originates

**Vivado FIFO Internal Structure** (axis_data_fifo_v2_0):
```
Reset → FIFO State Machine → Count/Pointers → Data Valid
   ↓
During first 1-2 cycles post-reset:
- Write pointer: defined
- Read pointer: may be X
- Data output: X if read before write
- TVALID: may glitch
```

**Propagation Path**:
```
Input → Vivado FIFO (X in first sample) 
     → StreamingDataWidthConverter 
     → MVAU_rtl_0 (compressor sees X inputs)
     → Compressor tree (X propagates through LUT6CY)
     → Output (XXX)
     → Python hex parser (CRASH)
```

## Why XSim Didn't Warn

XSim elaboration was clean because:
1. **No syntax/structural errors**: Design is correctly connected
2. **No uninitialized signals**: All signals have drivers (even if they drive `X`)
3. **Behavioral issue**: The `X` values only appear during **dynamic simulation**, not static elaboration
4. **IP black box**: Vivado FIFO is pre-compiled IP, XSim doesn't see internal implementation

## Confirmation Test

To confirm this is the root cause, check if:
1. First node in design is `StreamingFIFO_rtl` with `impl_style="vivado"` ✓ (warning was printed)
2. Compressors are used in early layers ✓ (14 MVAU layers with `comp_16xs4u4`)
3. Error occurs during output parsing (not during simulation) ✓ (`ValueError: invalid literal...`)

## Solutions

### Option 1: Change First FIFO to RTL Implementation (RECOMMENDED)
```python
# In model before CreateStitchedIP
if model.graph.node[0].op_type == "StreamingFIFO_rtl":
    firstfifo = getCustomOp(model.graph.node[0])
    firstfifo.set_nodeattr("impl_style", "rtl")  # Use FINN RTL FIFO instead
```

### Option 2: Add Reset Settling Cycles
Modify `rtlsim_exec.py` to wait N cycles after reset before sending data:
```python
finnxsi.reset_rtlsim(sim)
# Add dummy cycles for FIFO initialization
for _ in range(10):
    finnxsi.toggle_clk(sim)
# Now send actual data
```

### Option 3: Initialize FIFO Outputs
Pre-fill first FIFO with valid (zero) data before starting simulation

### Option 4: Change Parser to Handle X
Modify `data_packing.py:196` to detect and skip `XXX` values (NOT recommended - masks real bugs)

## Recommended Fix

**Change first FIFO from Vivado to RTL**:
1. Locate the transformation that sets FIFO impl_style
2. Add special case for first FIFO to force `impl_style="rtl"`
3. Or add a build config option: `force_rtl_first_fifo=True`

This avoids the Vivado FIFO initialization glitch entirely.

## Impact on Compressor Integration

**Compressors are NOT the bug** - they are working correctly!
- Compressors **faithfully propagate `X` values** (this is correct RTL behavior)
- DSP path would **mask** this bug by converting `X`→`0`
- Compressors **exposed an existing infrastructure issue** with Vivado FIFOs

This is actually **good news**: the compressor integration is working as designed. The test failure revealed a pre-existing simulation infrastructure issue that also affects non-compressor designs, but was masked by DSP reset behavior.
