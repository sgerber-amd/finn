"""
Generate a compressor core for FINN's dotp_comp module.

The static dotp_comp template (in finn-rtllib/mvu/) instantiates a generated
compressor core whose module name encodes the configuration signature, e.g.
`comp_8xs2s2_a16`.  This script generates that core: a LUT-mapped reduction tree
with fused accumulation, specific to a (SIMD, WW, AW, signedness) configuration.

Usage:
  python dotp_finn.py --simd 8 --ww 2 --aw 2 --accu_width 16 \
                      --signed_activations --target Versal -o gen/

Outputs:
  comp_<sig>.sv  — the generated compressor core (module `comp_<sig>`)
"""

import sys
import os
import re
import argparse
from .main import generate_compressor
from .utils.mul_comp_map import MulCompMap
from .target import resolve_target, resolve_target_name, Target, Versal, SevenSeries
from .utils.shape import Shape
from typing import Optional


def expand_template(template_path, output_path, substitutions):
    """
    Expand a text template by replacing placeholder tokens.

    Parameters
    ----------
    template_path : str
        Path to the template file.
    output_path : str
        Path where expanded output will be written.
    substitutions : dict
        Mapping of placeholder tokens to replacement values.

    Raises
    ------
    FileNotFoundError
        If template_path does not exist or output directory is missing.
    ValueError
        If any $PLACEHOLDER$ tokens remain after substitution.
    """
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.isdir(output_dir):
        raise FileNotFoundError(f"Output directory does not exist: {output_dir}")

    with open(template_path, "r") as f:
        text = f.read()
    for key, value in substitutions.items():
        text = text.replace(key, value)
    remaining = re.findall(r'\$[A-Z_]+\$', text)
    if remaining:
        raise ValueError(
            f"Unsubstituted placeholders in {output_path}: {remaining}")
    with open(output_path, "w") as f:
        f.write(text)


def compute_params(simd, weight_width, activation_width, signed_activations):
    """Map finn parameters to compressor parameters, respecting NA >= NB."""
    # Weights are always signed in finn
    sa_finn = True
    sb_finn = signed_activations

    # mul_comp_map requires NA >= NB. Swap operands if needed.
    if weight_width >= activation_width:
        na, nb = weight_width, activation_width
        sa, sb = sa_finn, sb_finn
        swapped = False
    else:
        na, nb = activation_width, weight_width
        sa, sb = sb_finn, sa_finn
        swapped = True

    n = simd
    return n, na, nb, sa, sb, swapped


def make_signature(n, sa, na, sb, nb):
    """Build the compressor file signature string, e.g. '8xs2u2'."""
    return f"{n}x{'s' if sa else 'u'}{na}{'s' if sb else 'u'}{nb}"


def comp_module_name(n, sa, na, sb, nb, accu_width):
    """Return the config-specific compressor module name, e.g. 'comp_8xs2s2_a16'."""
    return "comp_" + make_signature(n, sa, na, sb, nb) + f"_a{accu_width}"


def compute_natural_output_width(n, na, nb, sa, sb):
    """
    Calculate the natural output width for a dot product compressor.

    *** ATTEMPTED FIX FOR CONSTANTS WIDTH MISMATCH BUG (2026-04-07/08) ***
    This function was added to attempt fixing the -256 offset bug in SIMD >= 16.
    The fix calculates constants at natural width then sign-extends, but tests
    still fail with -256 offset. Root cause remains unclear. See REPORT.md 5.01.

    This matches the standalone compressor test calculation and represents
    the minimum number of bits needed to represent the output without overflow.

    Parameters
    ----------
    n : int
        SIMD parallelism (number of multiplications)
    na : int
        Operand A bit width
    nb : int
        Operand B bit width
    sa : bool
        Operand A is signed
    sb : bool
        Operand B is signed

    Returns
    -------
    int
        Natural output width in bits
    """
    clog2 = lambda x: (x - 1).bit_length()

    if na == 1 and nb == 1:
        # Special case: 1-bit x 1-bit multiplication
        if sa == sb:
            return clog2(n + 1)  # Range [0:N]
        else:
            return 1 + clog2(n)  # Range [-N:0]
    else:
        # General case: multi-bit multiplication
        if nb == 1 and not sb:
            # Unsigned 1-bit operand B
            return clog2(n) + na
        else:
            # Standard case
            return clog2(n) + na + nb


def generate_comp_module(target, n, na, nb, sa, sb, accu_width,
                         pipeline_every, output_dir, name=None):
    """Generate the compressor core with fused accumulation.

    When *name* is None (the default), the module is named after its
    configuration signature, e.g. ``comp_8xs2s2_a16``.  This keeps module
    names unique across different compressor configurations in the same
    Vivado project.
    """
    if name is None:
        name = comp_module_name(n, sa, na, sb, nb, accu_width)
    m = MulCompMap(na, nb, sa, sb)
    shape_cols = [col * n for col in m.shape()]
    shape = Shape((len(col) for col in shape_cols))

    # Gates are always generated. For 7-Series, target.absorbing_counter_candidates
    # contains only SinglePredCandidate (safe), with the broken MuxCYPredAdderCandidate
    # and RippleSumPredAdderCandidate disabled. This means absorption will use only
    # simple LUT2-based gate absorption, not the buggy complex absorption counters.
    gates = [[f"{val:x}" for val in col] for col in shape_cols]

    # ========================================================================
    # *** ATTEMPTED FIX FOR CONSTANTS WIDTH MISMATCH BUG (2026-04-07/08) ***
    # ========================================================================
    # Absorb abs_term as a constant input to the compressor tree.
    # This ensures the correction is applied every accumulation cycle,
    # not just once at the output.
    #
    # CRITICAL CHANGE: Use natural output width (np) for the constant value,
    # then SIGN-EXTEND to accu_width.
    #
    # BACKGROUND - Why This Bug Wasn't Caught by Standalone Tests:
    # -------------------------------------------------------------
    # The standalone compressor-python test suite (test/compressor-python/)
    # has two modes for constants with accumulation:
    #
    # 1. `accu` mode (WITHOUT `ca`): Constants added OUTSIDE accumulator
    #    - Template adds abs_term at output: `p = comp_p + abs_p`
    #    - No extension needed - each uses natural width independently
    #    - Tested up to SIMD=9, all passed
    #
    # 2. `ca accu` mode (WITH `ca`): Constants added INSIDE compressor tree
    #    - Constants absorbed into compressor, fed into accumulator
    #    - REQUIRES sign-extension when accu_width > np
    #    - NOT comprehensively tested due to 7-Series gate absorption bugs
    #    - Large SIMD configs (16+) hit gate absorption bugs and hung
    #
    # FINN Integration Difference:
    # -----------------------------
    # FINN ALWAYS uses the `ca accu` pattern (constants inside compressor).
    # This exposed the sign-extension bug that standalone tests missed.
    #
    # THE BUG:
    # --------
    # The Baugh-Wooley correction constant has a natural bit width based on
    # operand sizes (np), but the accumulator operates at accu_width. When
    # constants are absorbed into the compressor tree and fed into the
    # accumulator, they must be sign-extended to accu_width.
    #
    # Without sign-extension, the constant is zero-extended, treating the
    # negative correction as a positive value, causing massive errors.
    #
    # Example for SIMD=1, s4×u4:
    #   - Natural width np = 8 bits
    #   - abs_term = -120
    #   - As 8-bit: 0x88 = 136 (unsigned) = -120 (signed)
    #   - Sign bit: bit 7 = 1
    #   - accu_width = 16 bits
    #   - Sign-extended (CORRECT): bits [7:15] = 1 → 0xFF88 = -120 (signed)
    #   - Zero-extended (WRONG): bits [8:15] = 0 → 0x0088 = 136 (unsigned)
    #   - Error per cycle: 136 - (-120) = +256
    #   - After 31 accumulation cycles: 31 × 256 = +7936 total error
    #
    # Example for SIMD=16, s4×u4:
    #   - Natural width np = 12 bits
    #   - abs_term = -1920
    #   - As 12-bit: 0x880 = 2176 (unsigned) = -1920 (signed)
    #   - Sign-extended (CORRECT): 0xF880 = -1920 (signed 16-bit)
    #   - Zero-extended (WRONG): 0x0880 = 2176 (unsigned)
    #   - Error per cycle: 2176 - (-1920) = +4096
    #   - With SIMD=16 this manifests as different error patterns
    #
    # THE ATTEMPTED FIX:
    # ------------------
    # 1. Calculate constant value at natural width np
    # 2. Extract constant bits [0:np-1]
    # 3. Sign-extend by replicating bit (np-1) into bits [np:accu_width-1]
    #
    # NOTE: This fix was implemented but tests still fail with -256 offset.
    # Either the fix is incomplete or there's a different root cause.
    # See REPORT.md section 5.01 for details.
    # ========================================================================
    np = compute_natural_output_width(n, na, nb, sa, sb)
    abs_term = n * m.absolute_term()
    if abs_term != 0:
        # Calculate constant at natural width
        abs_val = abs_term % (1 << np)

        # Generate constant bits at natural width
        const_bits = [(abs_val >> i) & 1 for i in range(np)]

        # Sign-extend to accumulator width
        # If the sign bit (bit np-1) is set, extend with 1s; otherwise with 0s
        sign_bit = (abs_val >> (np - 1)) & 1
        constants = const_bits + [sign_bit] * (accu_width - np)
    else:
        constants = []

    comp_path = os.path.join(output_dir, name + ".sv")
    delay = generate_compressor(
        target=target,
        shape=shape,
        name=name,
        comb_depth=pipeline_every,
        accumulate=True,
        accumulator_width=accu_width,
        gates=gates,
        constants=constants,
        path=comp_path,
        test=False,
        enable=True,
    )
    return name, comp_path, delay


def generate_dotp_comp(fpgapart, simd, ww, aw, accu_width, signed_act, output_dir):
    """
    Generate the dotp_comp path: compressor core + expanded template.

    This is the high-level entry point called by FINNs generate_hdl().

    Parameters
    ----------
    fpgapart : str
        FPGA part string (e.g. "xcvc1902-...").
    simd, ww, aw, accu_width : int
        MVU parameters.
    signed_act : bool
        Whether activations are signed.
    output_dir : str
        Directory for generated files (= code_gen_dir).

    Returns
    -------
    dict with keys:
        comp_name  : str   — module name (e.g. "comp_8xs2s2_a16")
        comp_delay : int   — pipeline depth
        files      : list  — paths of all generated files
    """

    target = resolve_target(fpgapart)
    n, na, nb, sa, sb, _swapped = compute_params(simd, ww, aw, signed_act)

    comp_name, comp_path, comp_delay = generate_comp_module(
        target, n, na, nb, sa, sb, accu_width,
        pipeline_every=1,  # Max pipelining
        output_dir=output_dir)

    #Expand dotp_comp template with the generated module name
    src_dir = os.path.dirname(os.path.abspath(__file__))
    compressor_root = os.path.abspath(os.path.join(src_dir, ".."))
    dotp_comp_template = os.path.join(compressor_root, "hdl", "dotp_comp_template.sv")
    dotp_comp_path = os.path.join(output_dir, "dotp_comp.sv")
    expand_template(dotp_comp_template, dotp_comp_path,
                    {"$COMP_MODULE_NAME$": comp_name})

    return {
        "comp_name": comp_name,
        "comp_delay": comp_delay,
        "files": [dotp_comp_path, comp_path],
    }


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, ".."))
    default_dotp_template = os.path.join(repo_root, "hdl", "dotp_comp_template.sv")

    parser = argparse.ArgumentParser(
        prog="dotp_finn",
        description="Generate a compressor core for FINN's dotp_comp module."
    )
    parser.add_argument('--simd', type=int, required=True, help="SIMD (operand pairs per cycle)")
    parser.add_argument('--ww', type=int, required=True, help="Weight bit width")
    parser.add_argument('--aw', type=int, required=True, help="Activation bit width")
    parser.add_argument('--accu_width', type=int, required=True, help="Accumulator bit width")
    parser.add_argument('--signed_activations', action='store_true',
                        help="Activations are signed")
    parser.add_argument('-t', '--target', default="Versal",
                        choices=["Versal", "7-Series"],
                        help="Target FPGA generation")
    parser.add_argument('-p', '--pipeline_every', type=int, default=None,
                        help="Pipeline registers every N combinational stages")
    parser.add_argument('-o', '--output_dir', default="../gen",
                        help="Output directory for generated files")
    parser.add_argument('-n', '--name', default=None,
                        help="Module name override (default: comp_<sig>)")
    parser.add_argument('--dotp-template', default=default_dotp_template,
                        help="Path to dotp_comp template file to expand")
    parser.add_argument('--dotp-output-name', default="dotp_comp.sv",
                        help="Output file name for expanded dotp_comp template")
    parser.add_argument('--skip-dotp-template', action='store_true',
                        help="Skip expanding dotp_comp template")
    args = parser.parse_args()
    target = resolve_target_name(args.target)
    os.makedirs(args.output_dir, exist_ok=True)

    #Compute compressor parameters
    n, na, nb, sa, sb, swapped = compute_params(
        args.simd, args.ww, args.aw, args.signed_activations)

    #Generate the compressor core with fused accumulation
    comp_name, comp_path, comp_delay = generate_comp_module(
        target, n, na, nb, sa, sb, args.accu_width,
        args.pipeline_every, args.output_dir, name=args.name)

    dotp_path = None
    if not args.skip_dotp_template:
        template_path = os.path.abspath(args.dotp_template)
        if not os.path.isfile(template_path):
            raise FileNotFoundError(
                f"dotp template not found: {template_path}. Use --dotp-template or --skip-dotp-template."
            )
        dotp_path = os.path.join(args.output_dir, args.dotp_output_name)
        expand_template(
            template_path,
            dotp_path,
            {"$COMP_MODULE_NAME$": comp_name},
        )

    sig = make_signature(n, sa, na, sb, nb)
    print(f"Generated compressor core: {comp_path}")
    if dotp_path is not None:
        print(f"Expanded dotp template: {dotp_path}")
    print(f"  Module name:     {comp_name}")
    print(f"  Configuration:   {sig}")
    print(f"  Pipeline depth:  {comp_delay}")
    print(f"  Operands:        {'swapped' if swapped else 'not swapped'} (NA={na} >= NB={nb})")


if __name__ == "__main__":
    main()
