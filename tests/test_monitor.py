from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

import can
from support import FakeBus, message

from can_temperature import (
    Config,
    TemperatureMonitor,
    TemperatureStaleError,
    TemperatureTimeoutError,
)

SENSOR_ID = 0x1A000003
PAYLOAD_50_CELSIUS = bytes.fromhex("64 00 C8 00 2C 01 88 13")
PAYLOAD_82_CELSIUS = bytes.fromhex("00 00 00 00 00 00 66 20")


def wait_for(condition, timeout: float = 2.0) -> bool:
    """Give the receiving thread a bounded amount of time to catch up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False


class TemperatureMonitorTests(unittest.TestCase):
    def test_provides_the_temperature_without_blocking(self) -> None:
        bus = FakeBus([message(SENSOR_ID, PAYLOAD_50_CELSIUS)])

        with TemperatureMonitor(SENSOR_ID, bus=bus) as monitor:
            self.assertEqual(monitor.celsius, 50.0)

    def test_keeps_the_newest_frame_not_the_oldest(self) -> None:
        bus = FakeBus(
            [
                message(SENSOR_ID, PAYLOAD_50_CELSIUS),
                message(SENSOR_ID, PAYLOAD_82_CELSIUS),
            ]
        )

        with TemperatureMonitor(SENSOR_ID, bus=bus) as monitor:
            self.assertTrue(wait_for(lambda: monitor.celsius == 82.94))

    def test_skips_foreign_and_standard_frames(self) -> None:
        bus = FakeBus(
            [
                message(0x1A000001, PAYLOAD_82_CELSIUS),
                message(SENSOR_ID, PAYLOAD_82_CELSIUS, extended=False),
                message(SENSOR_ID, PAYLOAD_50_CELSIUS),
            ]
        )

        with TemperatureMonitor(SENSOR_ID, bus=bus) as monitor:
            self.assertEqual(monitor.celsius, 50.0)

    def test_reports_the_bus_timestamp_and_age(self) -> None:
        bus = FakeBus([message(SENSOR_ID, PAYLOAD_50_CELSIUS, timestamp=1234.5)])

        with TemperatureMonitor(SENSOR_ID, bus=bus) as monitor:
            reading = monitor.latest

            self.assertIsNotNone(reading)
            assert reading is not None
            self.assertEqual(reading.timestamp, 1234.5)
            self.assertEqual(reading.celsius, 50.0)
            self.assertLess(monitor.age, 1.0)

    def test_falls_back_to_local_time_without_a_bus_timestamp(self) -> None:
        bus = FakeBus([message(SENSOR_ID, PAYLOAD_50_CELSIUS, timestamp=0.0)])

        with TemperatureMonitor(SENSOR_ID, bus=bus) as monitor:
            reading = monitor.latest

            assert reading is not None
            self.assertAlmostEqual(reading.timestamp, time.time(), delta=10.0)

    def test_stale_value_raises_instead_of_freezing(self) -> None:
        bus = FakeBus([message(SENSOR_ID, PAYLOAD_50_CELSIUS)])

        with TemperatureMonitor(SENSOR_ID, bus=bus, max_age=0.05) as monitor:
            self.assertEqual(monitor.celsius, 50.0)
            time.sleep(0.1)

            with self.assertRaisesRegex(TemperatureStaleError, "old"):
                monitor.celsius

    def test_start_fails_when_no_frame_arrives(self) -> None:
        monitor = TemperatureMonitor(
            SENSOR_ID,
            bus=FakeBus([]),
            startup_timeout=0.1,
        )

        with self.assertRaises(TemperatureTimeoutError):
            monitor.start()

    def test_bus_failure_surfaces_in_the_calling_thread(self) -> None:
        gate = threading.Event()
        bus = FakeBus(
            [message(SENSOR_ID, PAYLOAD_50_CELSIUS)],
            error=can.CanError("adapter unplugged"),
            error_gate=gate,
        )

        with TemperatureMonitor(SENSOR_ID, bus=bus) as monitor:
            self.assertEqual(monitor.celsius, 50.0)

            gate.set()

            def failed() -> bool:
                try:
                    monitor.celsius
                except can.CanError:
                    return True
                return False

            self.assertTrue(wait_for(failed))

    def test_startup_reports_the_bus_failure_not_a_timeout(self) -> None:
        monitor = TemperatureMonitor(
            SENSOR_ID,
            bus=FakeBus([], error=can.CanError("adapter unplugged")),
            startup_timeout=1.0,
        )

        with self.assertRaisesRegex(can.CanError, "unplugged"):
            monitor.start()

    def test_short_frame_keeps_the_previous_value_and_is_reported(self) -> None:
        bus = FakeBus(
            [
                message(SENSOR_ID, PAYLOAD_50_CELSIUS),
                message(SENSOR_ID, bytes(7)),
            ]
        )

        with TemperatureMonitor(SENSOR_ID, bus=bus, max_age=0.05) as monitor:
            self.assertEqual(monitor.celsius, 50.0)
            self.assertTrue(wait_for(lambda: not bus.messages))
            time.sleep(0.1)

            with self.assertRaisesRegex(TemperatureStaleError, "too short"):
                monitor.celsius

    def test_does_not_close_an_injected_bus(self) -> None:
        bus = FakeBus([message(SENSOR_ID, PAYLOAD_50_CELSIUS)])

        with TemperatureMonitor(SENSOR_ID, bus=bus):
            pass

        self.assertEqual(bus.shutdown_calls, 0)

    def test_stops_the_thread_before_closing_an_owned_bus(self) -> None:
        bus = FakeBus([message(SENSOR_ID, PAYLOAD_50_CELSIUS)])

        with patch("can_temperature.sensor.can.Bus", return_value=bus):
            monitor = TemperatureMonitor(SENSOR_ID)
            with monitor:
                pass

        self.assertIsNone(monitor._thread)
        self.assertEqual(bus.shutdown_calls, 1)

    def test_reads_from_a_python_can_virtual_bus(self) -> None:
        channel = "can-temperature-monitor-test"
        with (
            can.Bus(interface="virtual", channel=channel) as receiver,
            can.Bus(interface="virtual", channel=channel) as transmitter,
        ):
            transmitter.send(
                can.Message(
                    arbitration_id=SENSOR_ID,
                    is_extended_id=True,
                    data=PAYLOAD_50_CELSIUS,
                )
            )

            with TemperatureMonitor(SENSOR_ID, bus=receiver) as monitor:
                self.assertEqual(monitor.celsius, 50.0)

    def test_builds_from_a_configuration(self) -> None:
        config = Config(arbitration_id=SENSOR_ID, max_age=2.5, startup_timeout=0.5)
        bus = FakeBus([message(SENSOR_ID, PAYLOAD_50_CELSIUS)])

        with TemperatureMonitor.from_config(config, bus=bus) as monitor:
            self.assertEqual(monitor.arbitration_id, SENSOR_ID)
            self.assertEqual(monitor.max_age, 2.5)
            self.assertEqual(monitor.temperature_offset, 6)

    def test_uses_a_custom_temperature_offset(self) -> None:
        payload = bytes.fromhex("C8 0D 00 00 00 00 00 00")
        bus = FakeBus([message(SENSOR_ID, payload)])

        with TemperatureMonitor(
            SENSOR_ID, bus=bus, temperature_offset=0
        ) as monitor:
            self.assertEqual(monitor.temperature_offset, 0)
            self.assertEqual(monitor.celsius, 35.28)

    def test_builds_from_a_configuration_with_a_custom_offset(self) -> None:
        config = Config(arbitration_id=SENSOR_ID, temperature_offset=0)
        payload = bytes.fromhex("C8 0D 00 00 00 00 00 00")
        bus = FakeBus([message(SENSOR_ID, payload)])

        with TemperatureMonitor.from_config(config, bus=bus) as monitor:
            self.assertEqual(monitor.celsius, 35.28)

    def test_validates_configuration(self) -> None:
        with self.assertRaises(ValueError):
            TemperatureMonitor(SENSOR_ID, max_age=0)
        with self.assertRaises(ValueError):
            TemperatureMonitor(SENSOR_ID, startup_timeout=-1)

    def test_rejects_a_second_start(self) -> None:
        bus = FakeBus([message(SENSOR_ID, PAYLOAD_50_CELSIUS)])

        with TemperatureMonitor(SENSOR_ID, bus=bus) as monitor:
            with self.assertRaises(RuntimeError):
                monitor.start()


if __name__ == "__main__":
    unittest.main()
