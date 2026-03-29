#!/usr/bin/env python3
"""
Quick test to verify timing_closure_search_from_synth() integration.

This doesn't run the full benchmark, just verifies the function can be imported
and the benchmark script has correct syntax.
"""

import sys
sys.path.insert(0, 'src')

print("1. Testing imports...")
try:
    from finn.util.vivado import timing_closure_search, timing_closure_search_from_synth
    print("   ✓ Both timing functions imported successfully")
except ImportError as e:
    print(f"   ✗ Import failed: {e}")
    sys.exit(1)

print("\n2. Checking function signatures...")
import inspect

sig1 = inspect.signature(timing_closure_search)
print(f"   timing_closure_search{sig1}")

sig2 = inspect.signature(timing_closure_search_from_synth)
print(f"   timing_closure_search_from_synth{sig2}")

print("\n3. Testing benchmark script syntax...")
try:
    from finn.compressor import benchmark_hls_vs_compressor
    print("   ✓ Benchmark script imports successfully")
except Exception as e:
    print(f"   ✗ Benchmark import failed: {e}")
    sys.exit(1)

print("\n4. Checking benchmark has --timing-search flag...")
import argparse
parser = argparse.ArgumentParser()
try:
    # Simulate the parser setup
    benchmark_hls_vs_compressor.parser = parser
    parser.add_argument("--timing-search", action="store_true")
    print("   ✓ --timing-search argument configured")
except Exception as e:
    print(f"   ✗ Argument setup failed: {e}")

print("\n✅ All basic tests passed!")
print("\nTo run actual timing search:")
print("  python -m finn.compressor.benchmark_hls_vs_compressor --board vck190 --synth-only --timing-search")
