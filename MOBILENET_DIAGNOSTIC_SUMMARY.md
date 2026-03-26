# MobileNet RTLSim Test Failure - Diagnostic Report

## Test Execution Summary
- **Test**: `test_end2end_mobilenet_stitched_ip_rtlsim`
- **Result**: FAILED after 19:55:38 runtime
- **Error**: `ValueError: invalid literal for int() with base 16: '0XXX'`
- **Location**: `src/finn/util/data_packing.py:196`

## Root Cause Analysis

### ERROR: Undefined Values in RTL Simulation Output
The RTL simulation produced hex values containing `XXX` (undefined logic values), which failed to parse as valid hexadecimal.

### Key Findings

#### 1. Compressor Integration Status ✓
**Result**: Compressors ARE active in this design
- **14 MVAU layers** using `comp_16xs4u4_a{11-16}` (dotp_comp path)
- **1 MVAU layer** using `comp_4xs4u4_a14`
- **Signature**: 16/4 PEs with signed 4-bit weights × unsigned 4-bit activations
- **Path**: Full dotp_comp path (WW=4, AW=4 triggering `USE_COMPRESSOR=true`)

#### 2. Vivado Synthesis Warnings ⚠️
**CRITICAL WARNING [BD 41-759]**: Unconnected input pins tied to 0:
```
/MVAU_rtl_0/MVAU_rtl_0_wstrm/s_axis_0_tvalid
/Thresholding_rtl_0/in1_V_TVALID
/VVAU_rtl_0/VVAU_rtl_0_wstrm/s_axis_0_tvalid
/Thresholding_rtl_1/in1_V_TVALID
```

**Analysis**: Weight streaming (`wstrm`) memory interface has `tvalid` signal tied to 0.
- This means weight memory streaming never signals "valid data"
- Could cause MVAUs to read uninitialized weight values
- Would manifest as `XXX` in simulation when weights are accessed

#### 3. XSim Compilation Status ✓
- **No errors** in xelab.log (33,135 lines)
- **No uninitialized signal warnings**
- Successfully built simulation library
- All compressor modules compiled successfully

## Hypothesis: Weight Stream Initialization Issue

The `tvalid` signals being tied to 0 suggests:
1. **Weight memory streaming not properly configured** in block design
2. **MVAUs may attempt to read weights before they're loaded**
3. **Uninitialized weight values (`X`) propagate through compressor tree**
4. **Output contains undefined values** → parsing fails

### Why This Affects Compressor Designs More
- Compressor path is more sensitive to initialization than DSP path
- DSP blocks have defined reset behavior; LUT6CY compressors propagate `X` values
- First layers (MVAU_rtl_0-14 use compressors) would fail first

## Recommended Next Steps

### Immediate Investigation
1. **Check weight streaming configuration**:
   ```bash
   grep -r "wstrm\|weight.*stream" /scratch/users/sgerber/finn_temp_files/vivado_stitch_proj__252ah9w/finn_vivado_stitch_proj.srcs/
   ```

2. **Verify MVAU_rtl_0 instantiation** (first layer with issue):
   ```bash
   find /scratch/users/sgerber/finn_temp_files/code_gen_ipgen_MVAU_rtl_0* -name "*.v" -o -name "*.sv"
   ```

3. **Check if issue is compressor-specific**:
   - Compare MVAU_rtl_0-14 (with compressors) vs MVAU_rtl_15+ (likely DSP path)
   - See if only compressor layers produce `XXX`

### Verification Tests
1. **Run cppsim only** (skip rtlsim) to verify functional correctness
2. **Enable rtlsim on single MVAU** without full stitched design
3. **Check if weights are properly loaded** before simulation starts

### Potential Fixes
1. **Properly connect `tvalid` signals** in block design generation
2. **Add weight pre-loading step** before RTL simulation
3. **Initialize weight memory** with defined values instead of `X`
4. **Fix memstream_axi wrapper** to properly drive valid signals

## Files for Further Investigation

### Critical Sources
- `/scratch/users/sgerber/finn_temp_files/vivado_stitch_proj__252ah9w/vivado.log` - Full Vivado log
- `/scratch/users/sgerber/finn_temp_files/code_gen_ipgen_MVAU_rtl_0*/` - First MVAU with compressor
- `src/finn/custom_op/fpgadataflow/rtl/matrixvectoractivation_rtl.py` - MVAU Python integration
- `src/finn/transformation/fpgadataflow/create_stitched_ip.py` - Block design generation

### Related Components
- `finn-rtllib/memstream/memstream_axi.sv` - Weight streaming wrapper
- `finn-rtllib/mvu/mvu_vvu_axi.sv` - Top-level MVU with compressor logic
- `finn-rtllib/mvu/dotp_comp.sv` - Compressor instantiation template

## Conclusion

**Root Cause**: Weight memory streaming interfaces (`wstrm`) have `tvalid` tied to 0, preventing proper weight loading.

**Impact**: MVAUs (especially compressor-based ones) read uninitialized (`X`) weight values, which propagate through the design.

**Next Action**: Investigate block design generation to fix `tvalid` signal connectivity in weight streaming modules.
