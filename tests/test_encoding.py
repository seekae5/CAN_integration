"""Kodierung physikalischer Werte in einen CAN-Payload.

Gegenstueck zu test_signals.py: dort wird dekodiert, hier kodiert. Beide
Richtungen muessen exakt invers sein, sonst meint ein gesetzter Sollwert etwas
anderes als der zurueckgelesene Istwert.
"""

from __future__ import annotations

import unittest

from can_integration import (
    InvalidValueError,
    Message,
    ReadOnlyMessageError,
    Signal,
    UnknownSignalError,
)


def command(**overrides) -> Message:
    """Ein schreibbares Telegramm, wie es im Katalog stehen wuerde."""
    defaults = dict(
        name="motor_command",
        arbitration_id=0x1A000020,
        writable=True,
        length=8,
        signals=(
            Signal("rpm_target", offset=0, format="<H", unit="rpm"),
            Signal("enable", offset=2, format="<B", default=1),
        ),
    )
    defaults.update(overrides)
    return Message(**defaults)


class SignalEncodingTest(unittest.TestCase):
    def test_raw_is_the_inverse_of_decode(self):
        signal = Signal("temperature", offset=0, format="<H", scale=0.01)
        self.assertEqual(signal.raw(50.0), 5000)

    def test_scale_and_bias_are_undone(self):
        signal = Signal("t", offset=0, format="<H", scale=0.1, bias=-40.0)
        payload = bytearray(2)
        signal.encode(25.0, payload)
        self.assertAlmostEqual(signal.decode(payload), 25.0, places=6)

    def test_integer_signal_rounds_to_its_step(self):
        signal = Signal("temperature", offset=0, format="<H", scale=0.01)
        # 50.004 liegt zwischen zwei Bit-Schritten und muss auf einen fallen.
        self.assertEqual(signal.raw(50.004), 5000)
        self.assertEqual(signal.raw(50.006), 5001)

    def test_float_signal_is_not_rounded(self):
        signal = Signal("value", offset=0, format="<f")
        payload = bytearray(4)
        signal.encode(0.5, payload)
        self.assertEqual(signal.decode(payload), 0.5)

    def test_value_outside_the_format_is_refused(self):
        signal = Signal("rpm", offset=0, format="<H")
        with self.assertRaises(InvalidValueError) as raised:
            signal.raw(70_000)
        self.assertIn("rpm", str(raised.exception))
        self.assertIn("<H", str(raised.exception))

    def test_negative_value_in_unsigned_signal_is_refused(self):
        with self.assertRaises(InvalidValueError):
            Signal("rpm", offset=0, format="<H").raw(-1)

    def test_signed_signal_accepts_negative_values(self):
        signal = Signal("torque", offset=0, format="<h", scale=0.1)
        payload = bytearray(2)
        signal.encode(-12.3, payload)
        self.assertAlmostEqual(signal.decode(payload), -12.3, places=6)

    def test_float_overflow_is_reported_as_an_invalid_value(self):
        # struct wirft hier OverflowError statt struct.error -- der Aufrufer
        # soll trotzdem nur eine Fehlerart kennen muessen.
        with self.assertRaises(InvalidValueError):
            Signal("value", offset=0, format="<f").raw(1e40)

    def test_nan_and_inf_are_refused(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(InvalidValueError):
                    Signal("value", offset=0, format="<f").raw(value)
                with self.assertRaises(InvalidValueError):
                    Signal("rpm", offset=0, format="<H").raw(value)

    def test_non_numeric_value_is_refused(self):
        with self.assertRaises(InvalidValueError):
            Signal("rpm", offset=0, format="<H").raw("1000")

    def test_boolean_is_not_accepted_as_a_number(self):
        with self.assertRaises(InvalidValueError):
            Signal("enable", offset=0, format="<B").raw(True)

    def test_unusable_default_fails_while_the_catalog_is_built(self):
        with self.assertRaises(InvalidValueError):
            Signal("rpm", offset=0, format="<H", default=70_000)

    def test_scale_zero_is_refused(self):
        with self.assertRaises(ValueError):
            Signal("rpm", offset=0, format="<H", scale=0.0)


class MessageEncodingTest(unittest.TestCase):
    def test_encode_fills_declared_signals(self):
        payload = command().encode({"rpm_target": 1000, "enable": 0})
        self.assertEqual(payload, bytes.fromhex("e8 03 00 00 00 00 00 00"))

    def test_encode_uses_declared_defaults(self):
        payload = command().encode({"rpm_target": 1000})
        self.assertEqual(command().decode(payload)["enable"], 1.0)

    def test_signal_without_default_must_be_given(self):
        with self.assertRaises(InvalidValueError) as raised:
            command().encode({"enable": 1})
        self.assertIn("rpm_target", str(raised.exception))

    def test_unknown_signal_is_refused(self):
        with self.assertRaises(UnknownSignalError) as raised:
            command().encode({"rpm_target": 1, "drehzahl": 2})
        self.assertIn("drehzahl", str(raised.exception))

    def test_read_only_message_cannot_be_encoded(self):
        with self.assertRaises(ReadOnlyMessageError) as raised:
            command(writable=False).encode({"rpm_target": 1000})
        self.assertIn("0x1A000020", str(raised.exception))

    def test_length_pads_the_payload(self):
        self.assertEqual(len(command().encode({"rpm_target": 1})), 8)

    def test_without_length_the_payload_is_as_long_as_its_signals(self):
        message = command(length=None)
        self.assertEqual(message.payload_length, 3)
        self.assertEqual(len(message.encode({"rpm_target": 1})), 3)

    def test_length_shorter_than_the_signals_is_refused(self):
        with self.assertRaises(ValueError) as raised:
            command(length=2)
        self.assertIn("shorter", str(raised.exception))

    def test_encode_and_decode_round_trip(self):
        message = command()
        values = {"rpm_target": 4321, "enable": 0}
        self.assertEqual(message.decode(message.encode(values)), {
            "rpm_target": 4321.0,
            "enable": 0.0,
        })

    def test_messages_are_read_only_by_default(self):
        self.assertFalse(
            Message(
                name="status",
                arbitration_id=0x100,
                signals=(Signal("v", offset=0),),
            ).writable
        )

    def test_describe_states_the_direction(self):
        self.assertIn("lesen+senden", command().describe())
        self.assertIn("nur lesen", command(writable=False).describe())


if __name__ == "__main__":
    unittest.main()
