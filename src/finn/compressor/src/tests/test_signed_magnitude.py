#############################################################################
# Copyright (C) 2024 - 2025 Advanced Micro Devices, Inc. All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
#
# @brief    Unit tests for signed magnitude addition support
#############################################################################

import unittest

from ..passes.compressor_constructor import CompressorConstructor
from ..target import Versal
from ..utils.shape import Shape


def signed_mag_to_int(sign: int, magnitude: int) -> int:
    return -magnitude if sign else magnitude


def int_to_signed_mag(value: int) -> tuple:
    return (1 if value < 0 else 0, abs(value))


class TestSignedMagnitudeConfig(unittest.TestCase):
    def setUp(self):
        self.constructor = CompressorConstructor()

    def test_configure_2_inputs_4bit(self):
        shape, gates = self.constructor.configure_signed_magnitude_inputs(2, 4)
        # Column 0: 2 mag bits + 2 sign bits = 4
        # Columns 1-3: 2 mag bits each
        self.assertEqual(list(shape), [4, 2, 2, 2])
        # Column 0 gates: 2 XOR + 2 AND
        self.assertEqual(gates[0], ["6", "6", "8", "8"])
        # Other columns: XOR only
        for col in gates[1:]:
            self.assertEqual(col, ["6", "6"])

    def test_configure_4_inputs_8bit(self):
        shape, gates = self.constructor.configure_signed_magnitude_inputs(4, 8)
        # Column 0: 4 mag bits + 4 sign bits = 8
        self.assertEqual(shape[0], 8)
        # Other columns: 4 mag bits
        for col in shape[1:]:
            self.assertEqual(col, 4)
        # Column 0 gates: 4 XOR + 4 AND
        self.assertEqual(gates[0], ["6", "6", "6", "6", "8", "8", "8", "8"])


class TestSignedMagnitudeCompressor(unittest.TestCase):
    def setUp(self):
        self.target = Versal()
        self.constructor = CompressorConstructor()

    def test_compressor_creation(self):
        c = self.constructor(
            self.target.counter_candidates,
            self.target.absorbing_counter_candidates,
            self.target.final_adder,
            Shape([4, 2, 2, 2]),
            "test_signed_mag",
            signed_magnitude=(2, 4),
        )
        self.assertIsNotNone(c)
        self.assertTrue(len(c.stages) > 0)


class TestSignedMagnitudeReference(unittest.TestCase):
    def test_reference_addition(self):
        # +5 + -3 = +2
        a = signed_mag_to_int(0, 5)
        b = signed_mag_to_int(1, 3)
        result = a + b
        sign, mag = int_to_signed_mag(result)
        self.assertEqual(sign, 0)
        self.assertEqual(mag, 2)

        # +3 + -5 = -2
        a = signed_mag_to_int(0, 3)
        b = signed_mag_to_int(1, 5)
        result = a + b
        sign, mag = int_to_signed_mag(result)
        self.assertEqual(sign, 1)
        self.assertEqual(mag, 2)

        # -5 + -3 = -8
        a = signed_mag_to_int(1, 5)
        b = signed_mag_to_int(1, 3)
        result = a + b
        sign, mag = int_to_signed_mag(result)
        self.assertEqual(sign, 1)
        self.assertEqual(mag, 8)

        # +5 + +3 = +8
        a = signed_mag_to_int(0, 5)
        b = signed_mag_to_int(0, 3)
        result = a + b
        sign, mag = int_to_signed_mag(result)
        self.assertEqual(sign, 0)
        self.assertEqual(mag, 8)


if __name__ == "__main__":
    unittest.main()
