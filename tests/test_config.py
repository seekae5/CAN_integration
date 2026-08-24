from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from can_temperature import Config


class ConfigTests(unittest.TestCase):
    def write_json(self, document: object) -> Path:
        directory = tempfile.mkdtemp()
        path = Path(directory, "measurement.json")
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_reads_a_hexadecimal_arbitration_id(self) -> None:
        config = Config.from_dict({"arbitration_id": "0x1A000003"})

        self.assertEqual(config.arbitration_id, 0x1A000003)

    def test_reads_a_decimal_arbitration_id(self) -> None:
        config = Config.from_dict({"arbitration_id": 436207619})

        self.assertEqual(config.arbitration_id, 0x1A000003)

    def test_applies_defaults(self) -> None:
        config = Config.from_dict({"arbitration_id": "0x1A000003"})

        self.assertIsNone(config.interface)
        self.assertIsNone(config.channel)
        self.assertIsNone(config.bitrate)
        self.assertIsNone(config.limit_celsius)
        self.assertEqual(config.temperature_offset, 6)
        self.assertEqual(config.max_age, 1.0)
        self.assertEqual(config.startup_timeout, 5.0)

    def test_reads_a_full_file(self) -> None:
        path = self.write_json(
            {
                "arbitration_id": "0x1A000003",
                "interface": "pcan",
                "channel": "PCAN_USBBUS1",
                "bitrate": 1000000,
                "temperature_offset": 0,
                "max_age": 0.5,
                "startup_timeout": 10.0,
                "limit_celsius": 120.0,
            }
        )

        config = Config.from_json(path)

        self.assertEqual(config.arbitration_id, 0x1A000003)
        self.assertEqual(config.channel, "PCAN_USBBUS1")
        self.assertEqual(config.bitrate, 1_000_000)
        self.assertEqual(config.temperature_offset, 0)
        self.assertEqual(config.max_age, 0.5)
        self.assertEqual(config.limit_celsius, 120.0)

    def test_rejects_unknown_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "maxage"):
            Config.from_dict({"arbitration_id": 1, "maxage": 0.5})

    def test_requires_an_arbitration_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "arbitration_id"):
            Config.from_dict({"max_age": 0.5})

    def test_rejects_an_unparsable_arbitration_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a valid CAN ID"):
            Config.from_dict({"arbitration_id": "1A000003h"})

    def test_error_message_names_the_file(self) -> None:
        path = self.write_json({"max_age": 0.5})

        with self.assertRaisesRegex(ValueError, "measurement.json"):
            Config.from_json(path)

    def test_rejects_a_json_document_that_is_not_an_object(self) -> None:
        path = self.write_json([1, 2, 3])

        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            Config.from_json(path)

    def test_reads_a_section_of_a_larger_document(self) -> None:
        document = {
            "yokogawa": {"address": "GPIB0::1::INSTR"},
            "can": {"arbitration_id": "0x1A000003", "limit_celsius": 120.0},
        }

        config = Config.from_dict(document["can"])

        self.assertEqual(config.arbitration_id, 0x1A000003)
        self.assertEqual(config.limit_celsius, 120.0)


if __name__ == "__main__":
    unittest.main()
