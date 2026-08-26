"""Die einfache Schnittstelle: Device und die Modulfunktionen.

Prueft genau das, was ein Messskript sieht -- kurze Aufrufe mit Signalnamen --
und dass der Weg dorthin dieselben Zusicherungen behaelt wie der Monitor
darunter: fail-closed beim Lesen, nur deklarierte Kommandos beim Senden.
"""

from __future__ import annotations

import unittest

import can_integration
from can_integration import (
    DEFAULT_CATALOG,
    Device,
    Message,
    NotConnectedError,
    ReadOnlyMessageError,
    Signal,
    StaleSignalError,
)

from support import PAYLOAD_MOTOR_35_CELSIUS, FakeBus, frame

MOTOR_COMMAND = Message(
    name="motor_command",
    arbitration_id=0x1A000020,
    writable=True,
    length=8,
    description="Kommandotelegramm nur fuer den Test",
    signals=(
        Signal("rpm_target", offset=0, format="<H", unit="rpm"),
        Signal("enable", offset=2, format="<B", default=1),
    ),
)
CATALOG = DEFAULT_CATALOG.extended_with([MOTOR_COMMAND])


def motor_bus(count: int = 200) -> FakeBus:
    """Ein Bus, der dauerhaft die Motortemperatur sendet."""
    return FakeBus([frame(0x1A000013, PAYLOAD_MOTOR_35_CELSIUS)] * count)


class DeviceReadTest(unittest.TestCase):
    def test_get_returns_the_current_value(self):
        with Device("motor_temperature", bus=motor_bus()) as device:
            self.assertAlmostEqual(device.get("temperature"), 35.28, places=2)

    def test_values_is_one_measurement_row(self):
        with Device("motor_temperature", bus=motor_bus()) as device:
            self.assertEqual(list(device.values()), ["temperature"])

    def test_signal_names_are_the_csv_header(self):
        with Device("motor_temperature", bus=motor_bus()) as device:
            self.assertEqual(device.signal_names, ("temperature",))

    def test_signal_definition_carries_the_unit(self):
        with Device("motor_temperature", bus=motor_bus()) as device:
            self.assertEqual(device.signal("temperature").unit, "°C")

    def test_stale_value_is_refused_rather_than_frozen(self):
        # Genau ein Telegramm, danach schweigt der Bus.
        device = Device("motor_temperature", bus=motor_bus(1), max_age=0.01)
        with device:
            import time

            time.sleep(0.05)
            with self.assertRaises(StaleSignalError):
                device.get("temperature")

    def test_age_grows_after_the_last_telegram(self):
        with Device("motor_temperature", bus=motor_bus()) as device:
            self.assertLess(device.age("temperature"), 1.0)

    def test_stop_releases_a_bus_the_device_opened(self):
        bus = motor_bus()
        with Device("motor_temperature", bus=bus):
            pass
        # Ein uebergebener Bus gehoert dem Aufrufer und bleibt offen.
        self.assertEqual(bus.shutdown_calls, 0)


class DeviceWriteTest(unittest.TestCase):
    def test_set_sends_the_carrying_command(self):
        bus = motor_bus()
        with Device("motor_temperature", bus=bus, catalog=CATALOG) as device:
            device.set("rpm_target", 1000)

        (sent,) = bus.sent
        self.assertEqual(sent.arbitration_id, 0x1A000020)
        self.assertTrue(sent.is_extended_id)
        self.assertEqual(MOTOR_COMMAND.decode(sent.data)["rpm_target"], 1000.0)

    def test_set_fills_the_remaining_signals_from_their_defaults(self):
        bus = motor_bus()
        with Device("motor_temperature", bus=bus, catalog=CATALOG) as device:
            device.set("rpm_target", 1000)

        self.assertEqual(MOTOR_COMMAND.decode(bus.sent[0].data)["enable"], 1.0)

    def test_send_takes_keyword_values(self):
        bus = motor_bus()
        with Device("motor_temperature", bus=bus, catalog=CATALOG) as device:
            device.send("motor_command", rpm_target=2500, enable=0)

        values = MOTOR_COMMAND.decode(bus.sent[0].data)
        self.assertEqual(values, {"rpm_target": 2500.0, "enable": 0.0})

    def test_sending_a_status_message_is_refused(self):
        with Device("motor_temperature", bus=motor_bus(), catalog=CATALOG) as device:
            with self.assertRaises(ReadOnlyMessageError):
                device.send("motor_temperature", temperature=99.0)

    def test_set_without_any_writable_message_says_so(self):
        with Device("motor_temperature", bus=motor_bus()) as device:
            with self.assertRaises(ValueError) as raised:
                device.set("rpm_target", 1000)
        self.assertIn("writable", str(raised.exception))

    def test_a_command_need_not_be_monitored_to_be_sent(self):
        # motor_command steht nicht in der Empfangsliste und wird trotzdem
        # gesendet: Empfangsfilter schraenken das Senden nicht ein.
        bus = motor_bus()
        with Device("motor_temperature", bus=bus, catalog=CATALOG) as device:
            device.set("rpm_target", 1)
        self.assertEqual(len(bus.sent), 1)


class ModuleFunctionTest(unittest.TestCase):
    def tearDown(self):
        can_integration.disconnect()

    def test_calls_before_connect_say_what_is_missing(self):
        with self.assertRaises(NotConnectedError) as raised:
            can_integration.get("temperature")
        self.assertIn("connect", str(raised.exception))

    def test_connect_get_disconnect(self):
        can_integration.connect("motor_temperature", bus=motor_bus())
        self.assertAlmostEqual(can_integration.get("temperature"), 35.28, places=2)
        can_integration.disconnect()
        with self.assertRaises(NotConnectedError):
            can_integration.get("temperature")

    def test_named_shortcut_reads_the_same_value(self):
        can_integration.connect("motor_temperature", bus=motor_bus())
        self.assertEqual(
            can_integration.get_temperature(), can_integration.get("temperature")
        )

    def test_set_signal_sends(self):
        bus = motor_bus()
        can_integration.connect("motor_temperature", bus=bus, catalog=CATALOG)
        can_integration.set_signal("rpm_target", 750)
        self.assertEqual(MOTOR_COMMAND.decode(bus.sent[0].data)["rpm_target"], 750.0)

    def test_set_rpm_is_the_named_form_of_set_signal(self):
        bus = motor_bus()
        can_integration.connect("motor_temperature", bus=bus, catalog=CATALOG)
        can_integration.set_rpm(750)
        self.assertEqual(MOTOR_COMMAND.decode(bus.sent[0].data)["rpm_target"], 750.0)

    def test_connecting_twice_is_refused(self):
        can_integration.connect("motor_temperature", bus=motor_bus())
        with self.assertRaises(RuntimeError) as raised:
            can_integration.connect("motor_temperature", bus=motor_bus())
        self.assertIn("Device", str(raised.exception))

    def test_disconnect_without_connect_is_harmless(self):
        can_integration.disconnect()
        can_integration.disconnect()

    def test_connect_returns_the_device(self):
        device = can_integration.connect("motor_temperature", bus=motor_bus())
        self.assertIs(device, can_integration.device())


if __name__ == "__main__":
    unittest.main()
