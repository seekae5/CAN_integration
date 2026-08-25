from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from can_integration import (
    BUILTIN_MESSAGES,
    DEFAULT_CATALOG,
    Catalog,
    Message,
    Signal,
    UnknownMessageError,
    load_json,
)

COOLANT = {
    "name": "coolant",
    "arbitration_id": "0x1A000021",
    "description": "Kühlmittel",
    "source": "Prüfstand",
    "signals": [
        {"name": "coolant_temperature", "offset": 0, "format": "<h", "scale": 0.1}
    ],
}


def write_json(document: object) -> Path:
    path = Path(tempfile.mkdtemp(), "catalog.json")
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class BuiltinCatalogTests(unittest.TestCase):
    def test_contains_the_messages_of_the_original_scripts(self) -> None:
        self.assertEqual(
            sorted(DEFAULT_CATALOG),
            [
                "inverter_speed",
                "inverter_status_1",
                "inverter_status_3",
                "motor_temperature",
                "thrust",
            ],
        )

    def test_keeps_the_documented_layouts(self) -> None:
        payload = bytes.fromhex("64 00 C8 00 2C 01 88 13")

        self.assertEqual(
            DEFAULT_CATALOG["inverter_status_3"].decode(payload)["temperature"], 50.0
        )
        self.assertEqual(
            DEFAULT_CATALOG["motor_temperature"].decode(
                bytes.fromhex("C8 0D 00 00 00 00 00 00")
            ),
            {"temperature": 35.28},
        )
        self.assertEqual(
            DEFAULT_CATALOG["inverter_speed"].decode(payload)["rpm_actual"], 100.0
        )
        self.assertEqual(
            DEFAULT_CATALOG["thrust"].decode(bytes.fromhex("00 00 27 10")),
            {"weight": 10000.0},
        )

    def test_every_entry_names_where_its_layout_comes_from(self) -> None:
        for message in BUILTIN_MESSAGES:
            with self.subTest(message.name):
                self.assertTrue(message.source, "catalog entries must cite a source")

    def test_the_load_cell_stays_a_standard_id(self) -> None:
        self.assertFalse(DEFAULT_CATALOG["thrust"].extended)

    def test_finds_a_message_by_its_id(self) -> None:
        self.assertEqual(
            DEFAULT_CATALOG.by_id(0x1A000013).name, "motor_temperature"
        )
        self.assertEqual(
            DEFAULT_CATALOG.by_id(0x003, extended=False).name, "thrust"
        )

    def test_reports_an_unknown_id(self) -> None:
        with self.assertRaises(UnknownMessageError):
            DEFAULT_CATALOG.by_id(0x1A000013, extended=False)

    def test_reports_an_unknown_name_with_the_known_ones(self) -> None:
        with self.assertRaisesRegex(UnknownMessageError, "motor_temperature"):
            DEFAULT_CATALOG["motortemperatur"]

    def test_describes_the_table(self) -> None:
        listing = DEFAULT_CATALOG.describe()

        self.assertIn("motor_temperature  0x1A000013", listing)
        self.assertIn("thrust  0x003", listing)


class CatalogTests(unittest.TestCase):
    def message(self, name: str, arbitration_id: int) -> Message:
        return Message(name, arbitration_id, (Signal("value", offset=0),))

    def test_refuses_a_duplicate_name(self) -> None:
        catalog = Catalog([self.message("a", 1)])

        with self.assertRaisesRegex(ValueError, "already contains"):
            catalog.add(self.message("a", 2))

    def test_refuses_a_duplicate_arbitration_id(self) -> None:
        catalog = Catalog([self.message("a", 1)])

        with self.assertRaisesRegex(ValueError, "same extended CAN ID"):
            catalog.add(self.message("b", 1))

    def test_the_same_id_in_both_schemes_is_not_a_conflict(self) -> None:
        standard = Message("b", 1, (Signal("value", offset=0),), extended=False)
        catalog = Catalog([self.message("a", 1), standard])

        self.assertEqual(len(catalog), 2)

    def test_extending_leaves_the_base_untouched(self) -> None:
        base = Catalog([self.message("a", 1)])

        extended = base.extended_with([self.message("b", 2)])

        self.assertEqual(sorted(extended), ["a", "b"])
        self.assertEqual(sorted(base), ["a"])

    def test_resolves_names_and_ready_made_messages(self) -> None:
        message = self.message("a", 1)
        catalog = Catalog([message])

        self.assertEqual(catalog.resolve(["a", message]), (message, message))


class CatalogFileTests(unittest.TestCase):
    def test_adds_a_message_to_the_builtin_catalog(self) -> None:
        catalog = load_json(write_json({"messages": [COOLANT]}))

        self.assertEqual(catalog["coolant"].arbitration_id, 0x1A000021)
        self.assertEqual(catalog["coolant"].signals[0].scale, 0.1)
        self.assertIn("motor_temperature", catalog)
        self.assertNotIn("coolant", DEFAULT_CATALOG)

    def test_decodes_a_negative_value_from_a_signed_format(self) -> None:
        catalog = load_json(write_json({"messages": [COOLANT]}))

        self.assertAlmostEqual(
            catalog["coolant"].decode(bytes.fromhex("9C FF"))["coolant_temperature"],
            -10.0,
        )

    def test_refuses_a_name_the_builtin_catalog_already_uses(self) -> None:
        clash = dict(COOLANT, name="motor_temperature")

        with self.assertRaisesRegex(ValueError, "already contains"):
            load_json(write_json({"messages": [clash]}))

    def test_refuses_an_id_the_builtin_catalog_already_uses(self) -> None:
        clash = dict(COOLANT, arbitration_id="0x1A000013")

        with self.assertRaisesRegex(ValueError, "same extended CAN ID"):
            load_json(write_json({"messages": [clash]}))

    def test_refuses_unknown_message_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "offest"):
            load_json(write_json({"messages": [dict(COOLANT, offest=2)]}))

    def test_refuses_unknown_signal_keys(self) -> None:
        broken = dict(
            COOLANT, signals=[{"name": "a", "offset": 0, "scaling": 0.1}]
        )

        with self.assertRaisesRegex(ValueError, "scaling"):
            load_json(write_json({"messages": [broken]}))

    def test_requires_the_messages_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires 'messages'"):
            load_json(write_json({}))

    def test_refuses_a_document_that_is_not_an_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            load_json(write_json([COOLANT]))

    def test_error_messages_name_the_file(self) -> None:
        path = write_json({"messages": [dict(COOLANT, arbitration_id="nope")]})

        with self.assertRaisesRegex(ValueError, "catalog.json"):
            load_json(path)


if __name__ == "__main__":
    unittest.main()
