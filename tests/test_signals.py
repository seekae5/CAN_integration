from __future__ import annotations

import unittest

from support import PAYLOAD_50_CELSIUS, frame

from can_integration import (
    AmbiguousSignalError,
    InvalidFrameError,
    Message,
    Signal,
    UnknownSignalError,
    resolve_signal,
    signal_keys,
)
from can_integration.signals import format_can_id, parse_can_id

TEMPERATURE = Signal("temperature", offset=6, format="<H", scale=0.01, unit="°C")
INVERTER = Message(
    name="inverter",
    arbitration_id=0x1A000003,
    signals=(Signal("u_dc", offset=4), TEMPERATURE),
)
THRUST = Message(
    name="thrust",
    arbitration_id=0x003,
    extended=False,
    signals=(Signal("weight", offset=0, format=">i", unit="g"),),
)


class SignalTests(unittest.TestCase):
    def test_decodes_a_scaled_little_endian_value(self) -> None:
        self.assertEqual(TEMPERATURE.decode(PAYLOAD_50_CELSIUS), 50.0)

    def test_decodes_a_signed_big_endian_value(self) -> None:
        weight = Signal("weight", offset=0, format=">i")

        self.assertEqual(weight.decode(bytes.fromhex("FF FF FF 9C")), -100.0)

    def test_applies_a_bias(self) -> None:
        shifted = Signal("temperature", offset=0, format="<H", scale=0.1, bias=-40.0)

        self.assertAlmostEqual(shifted.decode(bytes.fromhex("D0 07")), 160.0)

    def test_accepts_additional_payload_bytes(self) -> None:
        self.assertEqual(TEMPERATURE.decode(PAYLOAD_50_CELSIUS + b"\xff"), 50.0)

    def test_rejects_a_payload_too_short_for_the_offset(self) -> None:
        with self.assertRaisesRegex(InvalidFrameError, "too short"):
            TEMPERATURE.decode(bytes(7))

    def test_rejects_a_negative_offset(self) -> None:
        with self.assertRaises(ValueError):
            Signal("temperature", offset=-1)

    def test_rejects_a_format_describing_several_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one value"):
            Signal("everything", offset=0, format="<4H")

    def test_rejects_a_name_that_looks_qualified(self) -> None:
        with self.assertRaises(ValueError):
            Signal("inverter.temperature", offset=0)

    def test_reports_its_width(self) -> None:
        self.assertEqual(TEMPERATURE.size, 2)
        self.assertEqual(TEMPERATURE.end, 8)


class MessageTests(unittest.TestCase):
    def test_decodes_every_signal(self) -> None:
        self.assertEqual(
            INVERTER.decode(PAYLOAD_50_CELSIUS),
            {"u_dc": 300.0, "temperature": 50.0},
        )

    def test_reports_the_required_payload_length(self) -> None:
        self.assertEqual(INVERTER.minimum_length, 8)
        self.assertEqual(THRUST.minimum_length, 4)

    def test_matches_only_its_own_frames(self) -> None:
        self.assertTrue(INVERTER.matches(frame(0x1A000003, PAYLOAD_50_CELSIUS)))
        self.assertFalse(INVERTER.matches(frame(0x1A000001, PAYLOAD_50_CELSIUS)))
        self.assertFalse(
            INVERTER.matches(frame(0x1A000003, PAYLOAD_50_CELSIUS, extended=False))
        )
        self.assertFalse(
            INVERTER.matches(frame(0x1A000003, PAYLOAD_50_CELSIUS, error=True))
        )
        self.assertFalse(
            INVERTER.matches(frame(0x1A000003, PAYLOAD_50_CELSIUS, remote=True))
        )

    def test_a_standard_id_is_not_the_same_message_as_an_extended_one(self) -> None:
        self.assertTrue(THRUST.matches(frame(0x003, bytes(4), extended=False)))
        self.assertFalse(THRUST.matches(frame(0x003, bytes(4), extended=True)))

    def test_builds_a_hardware_filter(self) -> None:
        self.assertEqual(
            INVERTER.can_filter,
            {"can_id": 0x1A000003, "can_mask": 0x1FFFFFFF, "extended": True},
        )
        self.assertEqual(
            THRUST.can_filter,
            {"can_id": 0x003, "can_mask": 0x7FF, "extended": False},
        )

    def test_rejects_an_id_outside_its_addressing_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "29-bit"):
            Message("x", 0x20000000, (TEMPERATURE,))
        with self.assertRaisesRegex(ValueError, "11-bit"):
            Message("x", 0x800, (TEMPERATURE,), extended=False)

    def test_rejects_duplicate_signals(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            Message("x", 1, (TEMPERATURE, TEMPERATURE))

    def test_rejects_a_message_without_signals(self) -> None:
        with self.assertRaises(ValueError):
            Message("x", 1, ())

    def test_reports_an_unknown_signal_with_the_available_ones(self) -> None:
        with self.assertRaisesRegex(UnknownSignalError, "u_dc"):
            INVERTER.signal("kelvin")

    def test_labels_itself_at_the_width_of_its_addressing_scheme(self) -> None:
        self.assertEqual(INVERTER.label, "inverter (0x1A000003)")
        self.assertEqual(THRUST.label, "thrust (0x003)")


class SignalLookupTests(unittest.TestCase):
    def test_names_stay_plain_while_they_are_unique(self) -> None:
        self.assertEqual(
            tuple(signal_keys((INVERTER, THRUST))),
            ("u_dc", "temperature", "weight"),
        )

    def test_names_become_qualified_when_two_messages_share_one(self) -> None:
        other = Message("other", 0x1A000001, (TEMPERATURE,))

        self.assertEqual(
            sorted(signal_keys((INVERTER, other))),
            ["inverter.temperature", "other.temperature", "u_dc"],
        )

    def test_resolves_a_plain_name(self) -> None:
        message, signal = resolve_signal((INVERTER, THRUST), "weight")

        self.assertEqual(message.name, "thrust")
        self.assertEqual(signal.name, "weight")

    def test_resolves_a_qualified_name(self) -> None:
        message, signal = resolve_signal((INVERTER,), "inverter.temperature")

        self.assertEqual(signal, TEMPERATURE)

    def test_refuses_an_ambiguous_plain_name(self) -> None:
        other = Message("other", 0x1A000001, (TEMPERATURE,))

        with self.assertRaisesRegex(AmbiguousSignalError, "other.temperature"):
            resolve_signal((INVERTER, other), "temperature")

    def test_lists_the_available_names_for_an_unknown_one(self) -> None:
        with self.assertRaisesRegex(UnknownSignalError, "weight"):
            resolve_signal((INVERTER, THRUST), "kelvin")

    def test_reports_an_unknown_message_in_a_qualified_name(self) -> None:
        with self.assertRaisesRegex(UnknownSignalError, "unknown message"):
            resolve_signal((INVERTER,), "thrust.weight")


class CanIdTests(unittest.TestCase):
    def test_parses_hexadecimal_and_decimal(self) -> None:
        self.assertEqual(parse_can_id("0x1A000003"), 0x1A000003)
        self.assertEqual(parse_can_id(436207619), 0x1A000003)

    def test_rejects_an_unparsable_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a valid CAN ID"):
            parse_can_id("1A000003h")

    def test_formats_at_the_width_of_the_addressing_scheme(self) -> None:
        self.assertEqual(format_can_id(0x1A000003), "0x1A000003")
        self.assertEqual(format_can_id(0x003, extended=False), "0x003")


if __name__ == "__main__":
    unittest.main()
