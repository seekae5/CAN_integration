from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import can

from can_temperature import (
    InvalidTemperatureFrameError,
    TemperatureSensor,
    TemperatureTimeoutError,
)


def message(
    arbitration_id: int,
    data: bytes,
    *,
    extended: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        arbitration_id=arbitration_id,
        data=data,
        is_extended_id=extended,
        is_error_frame=False,
        is_remote_frame=False,
    )


class FakeBus:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = messages
        self.shutdown_calls = 0

    def recv(self, timeout: float | None = None) -> Any:
        if self.messages:
            return self.messages.pop(0)
        return None

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class TemperatureSensorTests(unittest.TestCase):
    sensor_id = 0x1A000003
    payload_50_celsius = bytes.fromhex("64 00 C8 00 2C 01 88 13")

    def test_reads_matching_extended_frame(self) -> None:
        bus = FakeBus([message(self.sensor_id, self.payload_50_celsius)])
        sensor = TemperatureSensor(self.sensor_id, bus=bus)

        self.assertEqual(sensor.read_temperature(), 50.0)

    def test_reads_from_python_can_virtual_bus(self) -> None:
        channel = "can-temperature-test"
        with (
            can.Bus(interface="virtual", channel=channel) as receiver,
            can.Bus(interface="virtual", channel=channel) as transmitter,
        ):
            sensor = TemperatureSensor(self.sensor_id, bus=receiver)
            transmitter.send(
                can.Message(
                    arbitration_id=self.sensor_id,
                    is_extended_id=True,
                    data=self.payload_50_celsius,
                )
            )

            self.assertEqual(sensor.read_temperature(), 50.0)

    def test_skips_foreign_and_standard_frames(self) -> None:
        bus = FakeBus(
            [
                message(0x1A000001, self.payload_50_celsius),
                message(self.sensor_id, self.payload_50_celsius, extended=False),
                message(self.sensor_id, self.payload_50_celsius),
            ]
        )
        sensor = TemperatureSensor(self.sensor_id, bus=bus)

        self.assertEqual(sensor.read_temperature(), 50.0)

    def test_times_out_without_matching_frame(self) -> None:
        sensor = TemperatureSensor(self.sensor_id, bus=FakeBus([]))

        with self.assertRaises(TemperatureTimeoutError):
            sensor.read_temperature(timeout=0)

    def test_rejects_short_matching_frame(self) -> None:
        sensor = TemperatureSensor(
            self.sensor_id,
            bus=FakeBus([message(self.sensor_id, bytes(7))]),
        )

        with self.assertRaises(InvalidTemperatureFrameError):
            sensor.read_temperature()

    def test_does_not_close_injected_bus(self) -> None:
        bus = FakeBus([])
        sensor = TemperatureSensor(self.sensor_id, bus=bus)

        sensor.close()

        self.assertEqual(bus.shutdown_calls, 0)

    def test_opens_filters_and_closes_owned_bus(self) -> None:
        bus = FakeBus([])

        with patch("can_temperature.sensor.can.Bus", return_value=bus) as bus_factory:
            with TemperatureSensor(self.sensor_id):
                pass

        bus_factory.assert_called_once_with(
            interface="pcan",
            channel="PCAN_USBBUS1",
            bitrate=1_000_000,
            can_filters=[
                {
                    "can_id": self.sensor_id,
                    "can_mask": 0x1FFFFFFF,
                    "extended": True,
                }
            ],
        )
        self.assertEqual(bus.shutdown_calls, 1)

    def test_validates_configuration(self) -> None:
        with self.assertRaises(ValueError):
            TemperatureSensor(0x20000000)
        with self.assertRaises(ValueError):
            TemperatureSensor(self.sensor_id, bitrate=0)


if __name__ == "__main__":
    unittest.main()
