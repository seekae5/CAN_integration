from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from can_integration import DEFAULT_CATALOG, Config, Message, Signal


class ConfigTests(unittest.TestCase):
    def write_json(self, document: object, name: str = "measurement.json") -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_selects_messages_from_the_catalog(self) -> None:
        config = Config.from_dict({"messages": ["motor_temperature"]})

        self.assertEqual(config.messages, ("motor_temperature",))
        self.assertEqual(config.definitions[0].arbitration_id, 0x1A000013)

    def test_accepts_a_single_message_name(self) -> None:
        config = Config.from_dict({"messages": "thrust"})

        self.assertEqual(config.messages, ("thrust",))

    def test_applies_defaults(self) -> None:
        config = Config.from_dict({"messages": ["motor_temperature"]})

        self.assertIsNone(config.interface)
        self.assertIsNone(config.channel)
        self.assertIsNone(config.bitrate)
        self.assertEqual(dict(config.limits), {})
        self.assertEqual(config.max_age, 1.0)
        self.assertEqual(config.startup_timeout, 5.0)
        self.assertIs(config.catalog, DEFAULT_CATALOG)

    def test_lists_the_signals_of_the_selected_messages(self) -> None:
        config = Config.from_dict({"messages": ["motor_temperature", "thrust"]})

        self.assertEqual(config.signal_names, ("temperature", "weight"))

    def test_qualifies_signal_names_shared_by_two_messages(self) -> None:
        config = Config.from_dict(
            {"messages": ["inverter_status_1", "inverter_status_3"]}
        )

        self.assertIn("inverter_status_1.temperature", config.signal_names)
        self.assertIn("inverter_status_3.temperature", config.signal_names)

    def test_reads_a_full_file(self) -> None:
        path = self.write_json(
            {
                "messages": ["motor_temperature", "inverter_speed"],
                "interface": "pcan",
                "channel": "PCAN_USBBUS1",
                "bitrate": 1000000,
                "max_age": 0.5,
                "startup_timeout": 10.0,
                "limits": {"temperature": 120.0, "rpm_actual": 6000},
            }
        )

        config = Config.from_json(path)

        self.assertEqual(config.channel, "PCAN_USBBUS1")
        self.assertEqual(config.bitrate, 1_000_000)
        self.assertEqual(config.max_age, 0.5)
        self.assertEqual(config.limit("temperature"), 120.0)
        self.assertIsNone(config.limit("weight"))

    def test_loads_extra_definitions_relative_to_the_configuration(self) -> None:
        path = self.write_json(
            {"messages": ["coolant"], "catalog": "bench.json"}
        )
        (path.parent / "bench.json").write_text(
            json.dumps(
                {
                    "messages": [
                        {
                            "name": "coolant",
                            "arbitration_id": "0x1A000021",
                            "signals": [{"name": "coolant_temperature", "offset": 0}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        config = Config.from_json(path)

        self.assertEqual(config.definitions[0].arbitration_id, 0x1A000021)
        self.assertEqual(config.signal_names, ("coolant_temperature",))

    def test_accepts_ready_made_message_definitions(self) -> None:
        message = Message("bench", 0x123, (Signal("value", offset=0),))
        catalog = DEFAULT_CATALOG.extended_with([message])

        config = Config.from_dict({"messages": ["bench"]}, catalog=catalog)

        self.assertEqual(config.definitions, (message,))

    def test_rejects_unknown_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "maxage"):
            Config.from_dict({"messages": ["thrust"], "maxage": 0.5})

    def test_rejects_a_catalog_key_without_a_file_to_resolve_it_against(self) -> None:
        with self.assertRaisesRegex(ValueError, "from_json"):
            Config.from_dict({"messages": ["thrust"], "catalog": "bench.json"})

    def test_requires_messages(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires 'messages'"):
            Config.from_dict({"max_age": 0.5})

    def test_rejects_an_empty_message_list(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one message"):
            Config.from_dict({"messages": []})

    def test_rejects_a_message_listed_twice(self) -> None:
        with self.assertRaisesRegex(ValueError, "twice"):
            Config.from_dict({"messages": ["thrust", "thrust"]})

    def test_rejects_an_unknown_message_and_names_the_known_ones(self) -> None:
        with self.assertRaisesRegex(ValueError, "motor_temperature"):
            Config.from_dict({"messages": ["motortemperatur"]})

    def test_rejects_a_limit_for_a_signal_that_is_not_monitored(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit for 'temperature'"):
            Config.from_dict({"messages": ["thrust"], "limits": {"temperature": 50}})

    def test_rejects_an_ambiguous_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "several messages"):
            Config.from_dict(
                {
                    "messages": ["inverter_status_1", "inverter_status_3"],
                    "limits": {"temperature": 50},
                }
            )

    def test_accepts_a_qualified_limit(self) -> None:
        config = Config.from_dict(
            {
                "messages": ["inverter_status_1", "inverter_status_3"],
                "limits": {"inverter_status_3.temperature": 50},
            }
        )

        self.assertEqual(config.limit("inverter_status_3.temperature"), 50)

    def test_rejects_a_limit_that_is_not_a_number(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a number"):
            Config.from_dict({"messages": ["thrust"], "limits": {"weight": "heiss"}})

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
            "can": {"messages": ["motor_temperature"], "limits": {"temperature": 120}},
        }

        config = Config.from_dict(document["can"])

        self.assertEqual(config.limit("temperature"), 120)

    def test_the_shipped_example_is_valid(self) -> None:
        config = Config.from_json(
            Path(__file__).resolve().parent.parent / "config.example.json"
        )

        self.assertIn("motor_temperature", config.messages)
        self.assertIn("coolant", config.catalog)


if __name__ == "__main__":
    unittest.main()
