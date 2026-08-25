from __future__ import annotations

import unittest
from unittest.mock import patch

import can
from support import PAYLOAD_50_CELSIUS, PAYLOAD_MOTOR_35_CELSIUS, FakeBus, frame

from can_integration import (
    Config,
    InvalidFrameError,
    SignalReader,
    SignalTimeoutError,
    UnknownSignalError,
)

INVERTER_ID = 0x1A000003
MOTOR_ID = 0x1A000013
THRUST_ID = 0x003


class SignalReaderTests(unittest.TestCase):
    def test_reads_a_matching_extended_frame(self) -> None:
        bus = FakeBus([frame(INVERTER_ID, PAYLOAD_50_CELSIUS)])
        reader = SignalReader("inverter_status_3", bus=bus)

        reading = reader.read()

        self.assertEqual(reading.message, "inverter_status_3")
        self.assertEqual(reading.values["temperature"], 50.0)
        self.assertEqual(reading.values["u_dc"], 300.0)

    def test_reads_a_standard_id(self) -> None:
        bus = FakeBus([frame(THRUST_ID, bytes.fromhex("00 00 27 10"), extended=False)])
        reader = SignalReader("thrust", bus=bus)

        self.assertEqual(reader.read().values["weight"], 10000.0)

    def test_reads_from_a_python_can_virtual_bus(self) -> None:
        channel = "can-integration-reader-test"
        with (
            can.Bus(interface="virtual", channel=channel) as receiver,
            can.Bus(interface="virtual", channel=channel) as transmitter,
        ):
            reader = SignalReader("inverter_status_3", bus=receiver)
            transmitter.send(
                can.Message(
                    arbitration_id=INVERTER_ID,
                    is_extended_id=True,
                    data=PAYLOAD_50_CELSIUS,
                )
            )

            self.assertEqual(reader.read().values["temperature"], 50.0)

    def test_skips_foreign_and_standard_frames(self) -> None:
        bus = FakeBus(
            [
                frame(0x1A000001, PAYLOAD_50_CELSIUS),
                frame(INVERTER_ID, PAYLOAD_50_CELSIUS, extended=False),
                frame(INVERTER_ID, PAYLOAD_50_CELSIUS),
            ]
        )
        reader = SignalReader("inverter_status_3", bus=bus)

        self.assertEqual(reader.read().values["temperature"], 50.0)

    def test_reads_several_messages_over_one_bus(self) -> None:
        bus = FakeBus(
            [
                frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS),
                frame(INVERTER_ID, PAYLOAD_50_CELSIUS),
            ]
        )
        reader = SignalReader(["motor_temperature", "inverter_status_3"], bus=bus)

        self.assertEqual(reader.read().message, "motor_temperature")
        self.assertEqual(reader.read().message, "inverter_status_3")

    def test_waits_for_the_message_carrying_the_requested_signal(self) -> None:
        bus = FakeBus(
            [
                frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS),
                frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS),
                frame(INVERTER_ID, PAYLOAD_50_CELSIUS),
            ]
        )
        reader = SignalReader(["motor_temperature", "inverter_status_3"], bus=bus)

        self.assertEqual(reader.read_signal("u_dc"), 300.0)

    def test_qualifies_a_signal_two_messages_share(self) -> None:
        bus = FakeBus([frame(INVERTER_ID, PAYLOAD_50_CELSIUS)])
        reader = SignalReader(["motor_temperature", "inverter_status_3"], bus=bus)

        self.assertEqual(
            reader.read_signal("inverter_status_3.temperature"), 50.0
        )

    def test_reports_an_unknown_signal(self) -> None:
        reader = SignalReader("thrust", bus=FakeBus([]))

        with self.assertRaises(UnknownSignalError):
            reader.read_signal("temperature")

    def test_times_out_without_a_matching_frame(self) -> None:
        reader = SignalReader("inverter_status_3", bus=FakeBus([]))

        with self.assertRaises(SignalTimeoutError):
            reader.read(timeout=0)

    def test_times_out_naming_the_message_it_waited_for(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])
        reader = SignalReader(["motor_temperature", "inverter_status_3"], bus=bus)

        with self.assertRaisesRegex(SignalTimeoutError, "0x1A000003"):
            reader.read_signal("u_dc", timeout=0.05)

    def test_rejects_a_short_matching_frame(self) -> None:
        reader = SignalReader(
            "inverter_status_3", bus=FakeBus([frame(INVERTER_ID, bytes(7))])
        )

        with self.assertRaises(InvalidFrameError):
            reader.read()

    def test_does_not_close_an_injected_bus(self) -> None:
        bus = FakeBus([])
        reader = SignalReader("thrust", bus=bus)

        reader.close()

        self.assertEqual(bus.shutdown_calls, 0)

    def test_opens_filters_and_closes_an_owned_bus(self) -> None:
        bus = FakeBus([])

        with patch("can_integration.bus.can.Bus", return_value=bus) as factory:
            with SignalReader(["motor_temperature", "thrust"]):
                pass

        factory.assert_called_once_with(
            interface="pcan",
            channel="PCAN_USBBUS1",
            bitrate=1_000_000,
            can_filters=[
                {"can_id": MOTOR_ID, "can_mask": 0x1FFFFFFF, "extended": True},
                {"can_id": THRUST_ID, "can_mask": 0x7FF, "extended": False},
            ],
        )
        self.assertEqual(bus.shutdown_calls, 1)

    def test_validates_configuration(self) -> None:
        with self.assertRaises(ValueError):
            SignalReader("thrust", bitrate=0)
        with self.assertRaises(TypeError):
            SignalReader("thrust", bus=FakeBus([]), bitrate=500_000)
        with self.assertRaises(ValueError):
            SignalReader([])
        with self.assertRaisesRegex(ValueError, "twice"):
            SignalReader(["thrust", "thrust"])

    def test_forwards_custom_bus_parameters(self) -> None:
        with patch("can_integration.bus.can.Bus", return_value=FakeBus([])) as factory:
            SignalReader(
                "thrust", interface="virtual", channel="test", bitrate=500_000
            ).connect()

        keywords = factory.call_args.kwargs
        self.assertEqual(keywords["interface"], "virtual")
        self.assertEqual(keywords["channel"], "test")
        self.assertEqual(keywords["bitrate"], 500_000)

    def test_reopens_an_owned_bus_after_close(self) -> None:
        buses = [FakeBus([]), FakeBus([])]

        with patch("can_integration.bus.can.Bus", side_effect=buses) as factory:
            reader = SignalReader("thrust")
            reader.connect()
            reader.close()
            reader.connect()
            reader.close()

        self.assertEqual(factory.call_count, 2)
        self.assertEqual([bus.shutdown_calls for bus in buses], [1, 1])

    def test_builds_from_a_configuration_on_a_shared_bus(self) -> None:
        config = Config.from_dict(
            {"messages": ["inverter_status_3"], "interface": "pcan"}
        )
        bus = FakeBus([frame(INVERTER_ID, PAYLOAD_50_CELSIUS)])

        reader = SignalReader.from_config(config, bus=bus)

        self.assertEqual(reader.read().values["temperature"], 50.0)


if __name__ == "__main__":
    unittest.main()
