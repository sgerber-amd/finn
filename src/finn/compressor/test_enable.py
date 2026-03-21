#!/usr/bin/env python3
import sys, os
# Ensure finn package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from finn.compressor.src.main import generate_compressor
from finn.compressor.src.target import Versal
from finn.compressor.src.utils.shape import Shape

d = generate_compressor(
    target=Versal(), shape=Shape([8]*4), name="test_en", comb_depth=2,
    accumulate=False, accumulator_width=None, gates=[], constants=[],
    path="gen/test_add_multi/test_en.sv", test=False, enable=True)
print(f"delay={d}")
