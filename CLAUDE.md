# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

FINN is an experimental framework from AMD Research & Advanced Development for deep neural network inference on FPGAs. It targets quantized neural networks and generates dataflow-style architectures customized for each network. The framework is **Docker-based only** - direct local execution is not supported due to complex dependencies.

Dependencies are managed through git submodules in `deps/`:
- **qonnx**: Quantized ONNX support (main dependency)
- **brevitas**: Quantization training framework
- **finn-hlslib**: HLS library for dataflow layers
- **finn-experimental**: Experimental features
- **pyverilator**: Verilator wrapper for RTL simulation
- Board definition files (avnet-bdf, xil-bdf, etc.)

## Git Restrictions

**IMPORTANT: Do NOT use git commands except for `git diff`.**

You are not allowed to:
- Create commits (`git commit`)
- Stage files (`git add`)
- Push/pull (`git push`, `git pull`)
- Modify branches (`git checkout`, `git branch`, `git merge`)
- Use any other git operations

The only permitted git command is `git diff` for viewing changes.

## Architecture

### Core Components

**ONNX Model Processing (`src/finn/`):**
- `core/`: ModelWrapper and ONNX graph utilities
- `custom_op/fpgadataflow/`: Custom ONNX operations for FPGA dataflow
  - `hls/`: HLS-based implementations (legacy)
  - `rtl/`: RTL-based implementations (preferred for performance)
  - Key operations: MatrixVectorActivation, VectorVectorActivation, ConvolutionInputGenerator, Thresholding
- `transformation/`: Graph transformation passes
  - `streamline/`: Optimizations for quantized networks
  - `fpgadataflow/`: FPGA-specific transformations (synthesis, resource annotation, DMA insertion)
  - `qonnx/`: QONNX-specific transformations
- `analysis/`: Analysis passes for network characteristics
- `util/`: Utility functions

**Build Flow (`src/finn/builder/`):**
- `build_dataflow.py`: Main entry point (`build_dataflow` console script)
- `build_dataflow_config.py`: Configuration dataclass and default steps
- `build_dataflow_steps.py`: Step implementations (preprocessing, synthesis, integration, deployment)

**RTL Library (`finn-rtllib/`):**
SystemVerilog implementations of dataflow components:
- `mvu/`: Matrix-Vector-Unit and related components (including compressor-based implementations)
- `axi/`: AXI interface components
- `fifo/`: FIFO implementations
- `memstream/`: Memory streaming components
- `thresholding/`: Activation functions
- `swg/`: Sliding window generators

**Compressor Generator (`src/finn/compressor/`):**
Python tool to generate optimized compressor trees for Xilinx 7-Series, UltraScale(+), and Versal FPGAs. Used for efficient multi-operand addition in MVU implementations. See details in the "Current Project Focus" section below.

### Dataflow Execution Models

1. **HLS Backend** (`hlsbackend.py`): Generates Vivado HLS C++ implementations
2. **RTL Backend** (`rtlbackend.py`): Uses pre-designed SystemVerilog components from `finn-rtllib/`

RTL backend is preferred for better performance and resource utilization.

## Development Commands

### Running Tests

**Docker environment (recommended):**
```bash
./run-docker.sh pytest  # Interactive Docker with pytest
```

**Inside Docker or with local setup:**
```bash
# Quick test (excludes slow/vivado/board tests)
pytest -m 'not (vivado or slow or vitis or board or notebooks or bnn_pynq)' --dist=loadfile -n auto

# Main test suite
pytest -k 'not (rtlsim or end2end)' --dist=loadfile -n auto

# RTL simulation tests
pytest -k rtlsim --workers auto

# End-to-end tests (no parallelism)
pytest -k end2end

# Run specific test markers
pytest -m fpgadataflow      # FPGA dataflow layer tests
pytest -m transform         # Transformation tests
pytest -m streamline        # Streamlining tests
pytest -m vivado            # Tests requiring Vivado
pytest -m vitis             # Tests requiring Vitis
```

Available pytest markers (from `setup.cfg`):
- `slow`, `vivado`, `vitis`, `board`: Resource-intensive tests
- `fpgadataflow`, `transform`, `streamline`, `util`: Component categories
- `end2end`, `notebooks`: Integration tests
- `bnn_*`: Board-specific BNN tests (pynq, zcu104, kv260, u250)

### Code Quality

```bash
# Install pre-commit hooks (required before committing)
pre-commit install

# Run hooks manually
pre-commit run --all-files

# Format with black (line length 100)
black --line-length=100 <file>

# Sort imports
isort <file>

# Lint
flake8 --max-line-length=100 --extend-ignore=E203 <file>
```

### Docker Operations

```bash
# Build and run Docker
./run-docker.sh        # Launches interactive shell

# Environment variables
export FINN_XILINX=/path/to/xilinx  # Vivado/Vitis installation
export PLATFORM_REPO_PATHS=/path/to/platforms  # For Alveo boards
export JUPYTER_PORT=8888
export FINN_HOST_BUILD_DIR=/tmp/finn_dev_$USER

# Fetch dependency repositories
./fetch-repos.sh
```

## Git Workflow

**Branch strategy:**
- `main`: Stable releases only (hotfixes only)
- `dev`: Active development (target for PRs)
- Feature branches: `<username>.feature/<name>` or similar

**Pre-commit requirements:**
- All commits must be signed off with `-s` flag (DCO)
- Code must pass black, isort, flake8 hooks
- Large files are prevented
- Trailing whitespace removed

**Testing requirements:**
- New functionality requires at least one unit test in `tests/`
- Tests should use appropriate markers

## Key Architectural Notes

**Custom Operations:**
- All FPGA dataflow operations inherit from `HWCustomOp` (in `hwcustomop.py`)
- RTL operations inherit from `RTLBackend` mixin
- Operations must implement: `get_nodeattr()`, `get_normal_input_shape()`, `execute_node()`, etc.
- RTL operations specify Verilog templates and IP packaging

**Transformations:**
- Inherit from `Transformation` base class (from QONNX)
- Implement `apply()` method that operates on `ModelWrapper`
- Can be chained in build steps
- Common pattern: analyze nodes → modify graph → return (modified_model, changed_flag)

**Build Flow:**
- Configured via `DataflowBuildConfig` dataclass
- Steps are defined in `build_dataflow_steps.py` and registered in lookup dict
- Custom build flows can override `steps` and `generate_outputs` functions
- Intermediate outputs saved in build directory structure

**RTL Integration:**
- RTL modules in `finn-rtllib/` are referenced by Python custom ops
- Compressor support enables efficient large-fanin additions
- IP packaging handled by transformation passes
- Stitching creates final IP blocks with AXI interfaces

## Current Project Focus: Compressor Integration into MVAU

This project is focused on integrating LUT-based compressor trees into FINN's Matrix-Vector Activation Unit (MVAU/MVU) as an alternative to DSP-based computation for low-bitwidth operations.

### Background

The compressor-python generator (`src/finn/compressor/`) builds optimized LUT6CY-based compressor trees that replace DSP slices for dot products when both weights and activations are < 4 bits wide. This provides better resource efficiency than DSP blocks for binary/ternary quantized networks.

### Two Integration Paths

**1. Full dotp_comp Path (Complete Replacement):**
- When `WW < 4` and `AW < 4` in `mvu_vvu_axi.sv`
- Sets `USE_COMPRESSOR = true`, bypassing DSP path entirely
- Uses `dotp_comp.sv` template which instantiates generated `comp_<sig>.sv` modules
- Module signature: `comp_{SIMD}x{s|u}{NA}{s|u}{NB}_a{ACCU_WIDTH}` (e.g., `comp_8xs2s2_a16`)
- Features: fused accumulation, constant-absorbed Baugh-Wooley correction
- Entry point: `src/finn/compressor/src/dotp_finn.py`
- Status: **Fully integrated** — RTL verified (33/33 XSim tests), Python integration complete

**2. add_multi Compressor Path (DSP Line Adder Replacement):**
- For configs where DSP path is used, replaces the binary adder tree in `add_multi.sv`
- Only replaces **unsigned low-part lane reductions** with SIMD >= 4
- High-part signed overflow reductions remain as binary trees
- Uses CATCH_COMP macro in `add_multi.sv` to match (N, ARG_WIDTH, DEPTH) and instantiate compressors
- Module signature: `comp_{N}u{W}_d{D}` (e.g., `comp_5u7_d0`)
- Entry point: `src/finn/compressor/src/add_multi_finn.py` with `--mvu` mode
- Status: **Fully integrated** — RTL verified (8/8 XSim tests), Python integration complete

### Critical Gating Logic

**When dotp_comp is used:**
```systemverilog
USE_COMPRESSOR = IS_MVU && !PUMPED_COMPUTE && (WW < 4) && (AW < 4)
```

**When add_multi compressor is used (within DSP path):**
- SIMD >= 4 (below this, binary tree is more efficient)
- Low-part reduction only (`!RESET_ZERO && ARG_LO >= 0`)
- Matching CATCH_COMP entry exists in `add_multi.sv`

**Otherwise:** Standard binary adder tree or DSP path

### Integration Status

**MVAU (matrixvectoractivation_rtl.py) — Fully integrated:**
- `$COMP_PIPELINE_DEPTH$` and `$USE_COMPRESSOR$` template variables substituted
- Generator functions imported: `from finn.compressor import generate_dotp_comp, generate_add_multi_comps`
- Eligibility checks: `_is_dotp_comp_eligible()` and `_is_add_multi_comp_eligible()`
- All compressor files added to `instantiate_ip()` and `get_rtl_file_list()`
- `$COMP_MODULE_NAME$` expansion handled inside `generate_dotp_comp()`
- add_multi patching handled inside `generate_add_multi_comps()`

**VVU (vectorvectoractivation_rtl.py) — No compressor support (intentional):**
- VVU has a fundamentally different compute pattern than MVU
- MVU: all PEs share same activation vector (broadcast), different weight rows
- VVU: each PE has its own activation AND weight vector (PE-parallel)
- RTL restriction: VVU always routes to `genINT8` path, never `genCompressor`
- VVU only supports DSP58 (VERSION=3); blocked on DSP48E1/E2 at RTL level
- Adding VVU compressor support would require new compressor architecture (PE-independent trees)

### Maintenance Concern: Dual slice_lanes Implementation

The add_multi path has a dual-implementation risk:
- `mvu.sv::sliceLanes()` (SystemVerilog) computes DSP lane widths
- `add_multi_finn.py::slice_lanes()` (Python) replicates this logic

If these diverge, compressors won't match CATCH_COMP guards and **silently fall back** to binary adder tree. The fallback is functionally correct but loses compressor benefit. Consider adding a consistency test.


### Key Files for Compressor Work

**Generator:**
- `src/finn/compressor/src/dotp_finn.py`: Generate comp_<sig>.sv for dotp_comp path
- `src/finn/compressor/src/add_multi_finn.py`: Generate comp_NuW for add_multi path
- `src/finn/compressor/src/passes/emitter.py`: SystemVerilog code generation
- `src/finn/compressor/src/graph/`: Compressor graph representation

**RTL:**
- `finn-rtllib/mvu/mvu_vvu_axi.sv`: Top-level MVU with USE_COMPRESSOR logic
- `finn-rtllib/mvu/dotp_comp.sv`: Template for dotp_comp path (has $COMP_MODULE_NAME$)
- `finn-rtllib/mvu/add_multi.sv`: Lane reduction with CATCH_COMP macro
- `finn-rtllib/mvu/mvu.sv`: Contains sliceLanes() function for lane width computation

**Python Integration:**
- `src/finn/custom_op/fpgadataflow/rtl/matrixvectoractivation_rtl.py` — Has full compressor integration
- `src/finn/custom_op/fpgadataflow/rtl/vectorvectoractivation_rtl.py` — No compressor (different compute pattern)

**Documentation:**
- `src/finn/compressor/REPORT.md`: Detailed status report (read this for full context)
- `src/finn/compressor/README.md`: Usage guide

### Compressor Test Commands

```bash
# Core compressor tests (21 configs)
cd src/finn/compressor && ./run_tests.sh

# dotp_comp integration tests (8 configs)
cd finn-rtllib/mvu/tb && ./run_dotp_comp_tests.sh

# MVU integration tests with dotp_comp (4 configs)
./run_mvu_comp_tests.sh

# add_multi integration tests (8 configs, DSP path)
./run_mvu_add_multi_comp_tests.sh
```

### When Compressors Are Used in End-to-End Tests

Your end2end cybersec test with RTL will exercise compressors only if layers have:
- **dotp_comp path**: WW < 4 AND AW < 4, non-pumped compute, target is Versal or 7-Series (not UltraScale+)
- **add_multi path**: SIMD >= 4, DSP version != 2 (not UltraScale+)

## Important Files

- `setup.cfg`: Package metadata, test configuration, pytest markers
- `.pre-commit-config.yaml`: Code quality hooks (black, isort, flake8)
- `run-docker.sh`: Docker environment launcher
- `docker/quicktest.sh`: Test suite runner
- `src/finn/builder/build_dataflow_config.py`: Build configuration
- `finn-rtllib/`: SystemVerilog RTL library
- `src/finn/compressor/REPORT.md`: **Critical - read this for complete compressor project context**
