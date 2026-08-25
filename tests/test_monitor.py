from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

import can
from support import (
    PAYLOAD_50_CELSIUS,
    PAYLOAD_82_CELSIUS,
    PAYLOAD_MOTOR_35_CELSIUS,
    FakeBus,
    frame,
    wait_for,
)

from can_integration import (
    AmbiguousSignalError,
    Config,
    SignalMonitor,
    SignalTimeoutError,
    StaleSignalError,
)

INVERTER_ID = 0x1A000003
MOTOR_ID = 0x1A000013
SPEED_ID = 0x1A00000C


class SignalMonitorTests(unittest.TestCase):
    def test_provides_a_value_without_blocking(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])

        with SignalMonitor("motor_temperature", bus=bus) as monitor:
            self.assertEqual(monitor.value("temperature"), 35.28)

    def test_keeps_the_newest_frame_not_the_oldest(self) -> None:
        bus = FakeBus(
            [
                frame(INVERTER_ID, PAYLOAD_50_CELSIUS),
                frame(INVERTER_ID, PAYLOAD_82_CELSIUS),
            ]
        )

        with SignalMonitor("inverter_status_3", bus=bus) as monitor:
            self.assertTrue(wait_for(lambda: monitor.value("temperature") == 82.94))

    def test_skips_foreign_and_standard_frames(self) -> None:
        bus = FakeBus(
            [
                frame(0x1A000001, PAYLOAD_82_CELSIUS),
                frame(INVERTER_ID, PAYLOAD_82_CELSIUS, extended=False),
                frame(INVERTER_ID, PAYLOAD_50_CELSIUS),
            ]
        )

        with SignalMonitor("inverter_status_3", bus=bus) as monitor:
            self.assertEqual(monitor.value("temperature"), 50.0)

    def test_monitors_several_messages_over_one_bus(self) -> None:
        bus = FakeBus(
            [
                frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS),
                frame(SPEED_ID, PAYLOAD_50_CELSIUS),
            ]
        )

        with SignalMonitor(["motor_temperature", "inverter_speed"], bus=bus) as monitor:
            self.assertEqual(monitor.value("temperature"), 35.28)
            self.assertEqual(monitor.value("rpm_actual"), 100.0)

    def test_reads_every_signal_at_once(self) -> None:
        bus = FakeBus(
            [
                frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS),
                frame(SPEED_ID, PAYLOAD_50_CELSIUS),
            ]
        )

        with SignalMonitor(["motor_temperature", "inverter_speed"], bus=bus) as monitor:
            self.assertEqual(
                monitor.values(),
                {
                    "temperature": 35.28,
                    "rpm_actual": 100.0,
                    "rpm_target": 200.0,
                    "rpm_max": 300.0,
                    "torque_actual": 5000.0,
                },
            )
            self.assertEqual(tuple(monitor.values()), monitor.signal_names)

    def test_qualifies_a_signal_two_messages_share(self) -> None:
        bus = FakeBus(
            [
                frame(0x1A000001, PAYLOAD_82_CELSIUS),
                frame(INVERTER_ID, PAYLOAD_50_CELSIUS),
            ]
        )
        messages = ["inverter_status_1", "inverter_status_3"]

        with SignalMonitor(messages, bus=bus) as monitor:
            self.assertEqual(monitor.value("inverter_status_1.temperature"), 82.94)
            self.assertEqual(monitor.value("inverter_status_3.temperature"), 50.0)
            with self.assertRaises(AmbiguousSignalError):
                monitor.value("temperature")

    def test_reports_the_bus_timestamp_and_age(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS, timestamp=1234.5)])

        with SignalMonitor("motor_temperature", bus=bus) as monitor:
            reading = monitor.reading("temperature")

            assert reading is not None
            self.assertEqual(reading.message, "motor_temperature")
            self.assertEqual(reading.timestamp, 1234.5)
            self.assertEqual(reading.values["temperature"], 35.28)
            self.assertLess(monitor.age("temperature"), 1.0)

    def test_falls_back_to_local_time_without_a_bus_timestamp(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS, timestamp=0.0)])

        with SignalMonitor("motor_temperature", bus=bus) as monitor:
            reading = monitor.reading("temperature")

            assert reading is not None
            self.assertAlmostEqual(reading.timestamp, time.time(), delta=10.0)

    def test_stale_value_raises_instead_of_freezing(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])

        with SignalMonitor("motor_temperature", bus=bus, max_age=0.05) as monitor:
            self.assertEqual(monitor.value("temperature"), 35.28)
            time.sleep(0.1)

            with self.assertRaisesRegex(StaleSignalError, "old"):
                monitor.value("temperature")

    def test_a_single_stale_message_fails_the_whole_row(self) -> None:
        bus = FakeBus(
            [frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS), frame(SPEED_ID, bytes(8))]
        )
        messages = ["motor_temperature", "inverter_speed"]

        with SignalMonitor(messages, bus=bus, max_age=0.05) as monitor:
            self.assertEqual(len(monitor.values()), 5)
            time.sleep(0.1)

            with self.assertRaises(StaleSignalError):
                monitor.values()

    def test_start_fails_when_no_frame_arrives(self) -> None:
        monitor = SignalMonitor(
            "motor_temperature", bus=FakeBus([]), startup_timeout=0.1
        )

        with self.assertRaisesRegex(SignalTimeoutError, "0x1A000013"):
            monitor.start()

    def test_start_names_the_message_that_stayed_away(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])
        monitor = SignalMonitor(
            ["motor_temperature", "inverter_speed"], bus=bus, startup_timeout=0.2
        )

        with self.assertRaisesRegex(SignalTimeoutError, "inverter_speed"):
            monitor.start()

    def test_bus_failure_surfaces_in_the_calling_thread(self) -> None:
        gate = threading.Event()
        bus = FakeBus(
            [frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)],
            error=can.CanError("adapter unplugged"),
            error_gate=gate,
        )

        with SignalMonitor("motor_temperature", bus=bus) as monitor:
            self.assertEqual(monitor.value("temperature"), 35.28)

            gate.set()

            def failed() -> bool:
                try:
                    monitor.value("temperature")
                except can.CanError:
                    return True
                return False

            self.assertTrue(wait_for(failed))

    def test_startup_reports_the_bus_failure_not_a_timeout(self) -> None:
        monitor = SignalMonitor(
            "motor_temperature",
            bus=FakeBus([], error=can.CanError("adapter unplugged")),
            startup_timeout=1.0,
        )

        with self.assertRaisesRegex(can.CanError, "unplugged"):
            monitor.start()

    def test_short_frame_keeps_the_previous_value_and_is_reported(self) -> None:
        bus = FakeBus(
            [
                frame(INVERTER_ID, PAYLOAD_50_CELSIUS),
                frame(INVERTER_ID, bytes(7)),
            ]
        )

        with SignalMonitor("inverter_status_3", bus=bus, max_age=0.05) as monitor:
            self.assertEqual(monitor.value("temperature"), 50.0)
            self.assertTrue(wait_for(lambda: not bus.frames))
            time.sleep(0.1)

            with self.assertRaisesRegex(StaleSignalError, "too short"):
                monitor.value("temperature")

    def test_does_not_close_an_injected_bus(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])

        with SignalMonitor("motor_temperature", bus=bus):
            pass

        self.assertEqual(bus.shutdown_calls, 0)

    def test_stops_the_thread_before_closing_an_owned_bus(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])

        with patch("can_integration.bus.can.Bus", return_value=bus):
            monitor = SignalMonitor("motor_temperature")
            with monitor:
                pass

        self.assertIsNone(monitor._thread)
        self.assertEqual(bus.shutdown_calls, 1)

    def test_reads_from_a_python_can_virtual_bus(self) -> None:
        channel = "can-integration-monitor-test"
        with (
            can.Bus(interface="virtual", channel=channel) as receiver,
            can.Bus(interface="virtual", channel=channel) as transmitter,
        ):
            transmitter.send(
                can.Message(
                    arbitration_id=MOTOR_ID,
                    is_extended_id=True,
                    data=PAYLOAD_MOTOR_35_CELSIUS,
                )
            )

            with SignalMonitor("motor_temperature", bus=receiver) as monitor:
                self.assertEqual(monitor.value("temperature"), 35.28)

    def test_builds_from_a_configuration(self) -> None:
        config = Config.from_dict(
            {
                "messages": ["motor_temperature"],
                "interface": "pcan",
                "max_age": 2.5,
                "startup_timeout": 0.5,
                "limits": {"temperature": 50.0},
            }
        )
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])

        with SignalMonitor.from_config(config, bus=bus) as monitor:
            self.assertEqual(monitor.max_age, 2.5)
            self.assertLess(monitor.value("temperature"), config.limit("temperature"))

    def test_builds_from_a_configuration_with_extra_definitions(self) -> None:
        message = {
            "name": "bench_temperature",
            "arbitration_id": "0x1A000099",
            "signals": [
                {"name": "temperature", "offset": 0, "format": "<H", "scale": 0.01}
            ],
        }
        from can_integration import DEFAULT_CATALOG
        from can_integration.catalog import message_from_dict

        catalog = DEFAULT_CATALOG.extended_with([message_from_dict(message)])
        config = Config.from_dict({"messages": ["bench_temperature"]}, catalog=catalog)
        bus = FakeBus([frame(0x1A000099, PAYLOAD_MOTOR_35_CELSIUS)])

        with SignalMonitor.from_config(config, bus=bus) as monitor:
            self.assertEqual(monitor.value("temperature"), 35.28)

    def test_exposes_the_signal_definition_behind_a_name(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])

        with SignalMonitor("motor_temperature", bus=bus) as monitor:
            self.assertEqual(monitor.signal("temperature").unit, "°C")

    def test_validates_configuration(self) -> None:
        with self.assertRaises(ValueError):
            SignalMonitor("motor_temperature", max_age=0)
        with self.assertRaises(ValueError):
            SignalMonitor("motor_temperature", startup_timeout=-1)

    def test_rejects_a_second_start(self) -> None:
        bus = FakeBus([frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS)])

        with SignalMonitor("motor_temperature", bus=bus) as monitor:
            with self.assertRaises(RuntimeError):
                monitor.start()


if __name__ == "__main__":
    unittest.main()
