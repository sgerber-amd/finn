# LOOKAHEAD8 Integration for VersalAtomCascade

## Problem

VersalAtomCascade counters originally used simple O52→I4 ripple carry chains, causing 81% routing delay due to carries traveling through general-purpose fabric instead of the dedicated carry chain.

## Why LOOKAHEAD8 Integration Failed Initially

**VersalAtom222 was incompatible with LOOKAHEAD8.**

LOOKAHEAD8 requires:
- CYx inputs to be **carry signals** (generate terms)
- PROP = O51 XOR O52 to be **independent of carry-in**

VersalAtom222's original structure (2 LUTs for 6 inputs):
```
lut_1.O51 = FA_sum(col0[0], col0[1], cin)           // sum - OK
lut_1.O52 = FA_sum(col1[0], col1[1], FA_carry(...)) // SUM, not carry!
lut_2.O51 = FA_sum(col2[0], col2[1], carry)         // sum - OK
lut_2.O52 = FA_carry(col2[0], col2[1], carry)       // carry - OK
```

The problem: `lut_1.O52` outputs a **SUM** (intermediate result), not a carry. When fed to LOOKAHEAD8's CYx input, it produced wrong results.

**VersalAtom2** and **VersalAtom14** were already compatible since their O52 outputs are proper FA_carry signals.

## Solution

Restructured VersalAtom222 to use 3 LUTs instead of 2, each with proper carry on O52:

```
lut_0: O51 = FA_sum(col0[0], col0[1], cin)
       O52 = FA_carry(col0[0], col0[1], cin)    // proper carry

lut_1: O51 = FA_sum(col1[0], col1[1], carry0)
       O52 = FA_carry(col1[0], col1[1], carry0) // proper carry

lut_2: O51 = FA_sum(col2[0], col2[1], carry1)
       O52 = FA_carry(col2[0], col2[1], carry1) // proper carry
```

Now all LUTs have:
- O52 = FA_carry (valid CYx input for LOOKAHEAD8)
- PROP = O51 XOR O52 = propagate signal (independent of carry-in)

## Carry Chain Pattern

Following QuaternaryAdder/TernaryAdder architecture:
- Odd positions: use direct O52 from previous LUT
- Even positions: use LOOKAHEAD8 accelerated output (COUTB/COUTD/COUTF/COUTH)

```python
for i in range(1, num_luts):
    if i % 2 == 0:
        l8s[(i-1)//8].out_ports[((i-1)%8)//2].connect_to(luts_chain[i].I4)
    else:
        luts_chain[i-1].O52.connect_to(luts_chain[i].I4)
```

## Trade-off

VersalAtom222 now uses 3 LUTs instead of 2 (+1 LUT per atom), but enables proper LOOKAHEAD8 carry acceleration, significantly reducing routing delay.

## Files Modified

- `src/graph/counters/counter_candidates.py` - VersalAtomCascade.build_hardware()
- `src/passes/lut_placer.py` - Skip BEL placement for LOOKAHEAD8 counters
