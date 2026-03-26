# Open Questions, points

## ultrascale fabric support.
As the title says, in the /compressor project, currently using ultrascale defaults to an error, as the variant isnt supported. In the paper it seems to have been tested though, and mentions the following:
"On UltraScale+ devices, a slice comprises eight rather than four LUTs. Its carry chain can be
partitioned after four LUTs so that it can trivially accommodate two of the slice counters proposed
for the 7 Series architecture. The ternary adder and the flexible compression goal proposed by
Preußer [19] for the terminal addition are also compatible with these architectures."

## narrow weights
Currently, if were using architectures with DSP48E1, and use the full range of weights (including the most negatie twos compliment), and thus get narrow_weights = False, we default to using the hls MVAU. 
1. This is a DSP issue, so if we are using compressor trees, this isnt an issue. Compressor tree viability is currently checked after the decision between using hls or rtl is made.
2. Thomas mentioned that this is a legacy constraint that isnt required anymore.

## end2end testing.
### error cybersec
Here we have the issue that in standard operation cybersec defaults to hls. If we want to force rtl, we have to ensure that standalone_thresshold=True, as absorption into the mvau isnt possible currently with rtl. 
This change however breaks the cybersec test with the folowing error:
```
AssertionError: MultiThreshold_3: MultiThreshold out_scale must be 1 for HLS conversion.
```
Is this a known issue? It seems that we get a MultiThreshold that doesnt match the RTL requirement of out_scale=1 as it scales arbitrarily.

## other testing:
### unit testing mvau nodes
running benchmark_hls_vs_compressor with th pynq-z1 board config. To make this run in the first place I had to take out the narrow_weights guard which blocks rtl mvau nodes with this specific board. This led to a compressor error. here:
TypeError: SinglePredCandidate.extend_to_fit() missing 1 required positional argument: 'gates'

```
def construct_absorption_stage(self,
                                   input_shape: Shape,
                                   gates: List[str],
                                   absorption_counters: GateAbsorptionCounterCandidate
                                   ):
        s = GateAbsorbedStage()
        cur_shape = input_shape
        cur_gates = gates[:]
        for idx in range(len(input_shape)):
            while cur_shape[idx] > 0:
                best = self.get_best_inlined_counter(
                    cur_shape[idx:], cur_gates[idx:], absorption_counters)
                cur_shape = cur_shape - (best.input_shape << idx)
                for i in range(len(cur_shape)):
                    new = list(reversed(list(reversed(cur_gates[i]))[:cur_shape[i]]))
                    cur_gates[i] = new
                s.append_counter(best, idx)
        return s

def get_best_inlined_counter(self, input_shape, gates, absorption_counters):
    candidates = []
    for counter in absorption_counters:
        candidate = counter.extend_to_fit(input_shape, gates)
        if candidate:
            candidates.append(candidate)
    return max(candidates, key=lambda x: (x.efficiency, x.strength))


def extend_to_fit(self, inputs: Shape,
                      gates: List[List[str]]) -> GateAbsorptionCounter:
        if inputs[0] > 0:
            return SinglePred(gates[0][0])
```so 
See the mismatch in function signature of the first and third funciton.
