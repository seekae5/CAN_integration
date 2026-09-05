from __future__ import annotations

import time
import unittest
from pathlib import Path
from typing import Any

import can

from can_integration import DEFAULT_CATALOG, Device, load_json
from can_integration.sim import (
    BROADCAST_ARM,
    BROADCAST_DISARM,
    COMMAND_RPM_TARGET,
    STOP_COMMAND_IDS,
    Cycle,
    RecordedInverter,
    Recording,
    SimulatedDevice,
    running_moment,
    schedule_from_recording,
    state_from_recording,
)

EXCERPT = Path(__file__).parent / "data" / "0000309_excerpt.TXT"
CATALOG_EXAMPLE = Path(__file__).parent.parent / "catalog.example.json"

INVERTER_SPEED = (0x1A00000C, True)
COMMAND = (0x0A000000, True)


def catalog() -> Any:
    return load_json(CATALOG_EXAMPLE)


def recording() -> Recording:
    return Recording.from_file(EXCERPT)


def channel(name: str) -> str:
    return f"can-integration-simdev-{name}"


def command_frame(message: Any, values: dict[str, float]) -> can.Message:
    return can.Message(
        arbitration_id=message.arbitration_id,
        is_extended_id=message.extended,
        data=message.encode(values),
    )


class ScheduleTests(unittest.TestCase):
    def test_the_schedule_uses_the_measured_cycle_times(self) -> None:
        cycles = schedule_from_recording(recording(), catalog=catalog())

        speed = next(c for c in cycles if c.message.key == INVERTER_SPEED)
        self.assertAlmostEqual(speed.period, 0.010, delta=0.002)
        self.assertTrue(speed.measured)

    def test_the_device_does_not_schedule_the_hosts_telegrams(self) -> None:
        cycles = schedule_from_recording(recording(), catalog=catalog())

        self.assertNotIn(COMMAND, {cycle.message.key for cycle in cycles})

    def test_the_template_fixes_the_length_the_signals_do_not_give(self) -> None:
        # motor_temperature declares a single 16-bit signal, but the test
        # bench sends DLC 8 -- the recording is what knows that.
        cycles = schedule_from_recording(recording(), catalog=catalog())

        temperature = next(
            c for c in cycles if c.message.name == "motor_temperature"
        )
        self.assertEqual(temperature.message.payload_length, 2)
        self.assertEqual(temperature.payload_length, 8)

    def test_a_cycle_needs_a_positive_period(self) -> None:
        with self.assertRaises(ValueError):
            Cycle(DEFAULT_CATALOG["inverter_speed"], period=0.0)

    def test_a_template_shorter_than_the_signals_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Cycle(
                DEFAULT_CATALOG["inverter_speed"], period=0.01, template=b"\x00\x01"
            )


class StateTests(unittest.TestCase):
    def test_the_stop_command_is_found_in_the_recording(self) -> None:
        moment = running_moment(recording(), catalog=catalog())

        # The excerpt starts at 25.80 s of the session; the command was
        # recorded at 26.210 s.
        self.assertAlmostEqual(moment, 0.410, delta=0.005)

    def test_the_running_state_is_the_drive_before_the_stop(self) -> None:
        source = recording()

        state = state_from_recording(
            source, catalog=catalog(), at=running_moment(source, catalog=catalog())
        )

        self.assertEqual(state["rpm_actual"], 6848.0)
        self.assertEqual(state["rpm_target"], 8700.0)

    def test_the_end_state_is_the_stopped_drive(self) -> None:
        state = state_from_recording(recording(), catalog=catalog())

        self.assertEqual(state["rpm_actual"], 0.0)
        self.assertEqual(state["motion_ctrl_state"], 0.0)

    def test_the_state_excludes_the_hosts_own_telegrams(self) -> None:
        state = state_from_recording(recording(), catalog=catalog())

        self.assertNotIn("command_id", state)

    def test_payloads_at_a_moment_before_any_frame_are_empty(self) -> None:
        self.assertEqual(recording().payloads_at(-1.0), {})


class ConstructionTests(unittest.TestCase):
    def test_a_missing_state_value_is_refused_at_construction(self) -> None:
        cycles = schedule_from_recording(recording(), catalog=catalog())

        with self.assertRaises(ValueError) as caught:
            SimulatedDevice(cycles, {"rpm_actual": 1.0})

        self.assertIn("rpm_target", str(caught.exception))

    def test_a_telegram_cannot_be_scheduled_twice(self) -> None:
        message = DEFAULT_CATALOG["inverter_speed"]
        cycles = [Cycle(message, 0.01), Cycle(message, 0.02)]
        state = dict.fromkeys(message.signal_names, 0.0)

        with self.assertRaises(ValueError) as caught:
            SimulatedDevice(cycles, state)

        self.assertIn("inverter_speed", str(caught.exception))

    def test_a_device_without_telegrams_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            SimulatedDevice([], {})


class PayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = recording()
        self.catalog = catalog()
        self.device = SimulatedDevice.from_recording(
            self.source, catalog=self.catalog, bus=object()
        )

    def cycle(self, name: str) -> Cycle:
        return next(c for c in self.device.cycles if c.message.name == name)

    def test_the_payload_reproduces_the_recorded_one(self) -> None:
        # Seeded from the recording and unchanged since, the device must send
        # exactly the bytes the test bench sent at that moment.
        moment = running_moment(self.source, catalog=self.catalog)
        recorded = self.source.payloads_at(moment)[INVERTER_SPEED]

        self.assertEqual(self.device.payload(self.cycle("inverter_speed")), recorded)

    def test_a_changed_value_shows_up_in_the_payload(self) -> None:
        self.device.set("rpm_target", 1234)

        payload = self.device.payload(self.cycle("inverter_speed"))

        self.assertEqual(
            self.catalog["inverter_speed"].decode(payload)["rpm_target"], 1234.0
        )

    def test_bytes_no_signal_covers_keep_the_recorded_content(self) -> None:
        # motor_temperature describes bytes 0-1; the recorded frame is 8 long.
        payload = self.device.payload(self.cycle("motor_temperature"))

        self.assertEqual(len(payload), 8)


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = catalog()
        self.device = SimulatedDevice.from_recording(
            recording(), catalog=self.catalog, bus=object()
        )

    def test_a_setpoint_command_lands_in_the_state(self) -> None:
        self.device.handle(
            command_frame(
                self.catalog["inverter_command"],
                {"command_id": COMMAND_RPM_TARGET, "value": 1500},
            )
        )

        self.assertEqual(self.device.get("rpm_target"), 1500.0)
        self.assertEqual(self.device.received, 1)

    def test_the_recorded_stop_command_stops_the_drive(self) -> None:
        self.assertEqual(self.device.get("rpm_actual"), 6848.0)

        self.device.handle(
            command_frame(
                self.catalog["inverter_command"],
                {"command_id": STOP_COMMAND_IDS[-1], "value": 0},
            )
        )

        self.assertEqual(self.device.get("rpm_actual"), 0.0)
        self.assertEqual(self.device.get("motion_ctrl_state"), 0.0)

    def test_arming_restores_the_running_state(self) -> None:
        broadcast = self.catalog["broadcast_command"]
        self.device.handle(command_frame(broadcast, {"command": BROADCAST_DISARM}))
        self.assertEqual(self.device.get("rpm_actual"), 0.0)

        self.device.handle(command_frame(broadcast, {"command": BROADCAST_ARM}))

        self.assertEqual(self.device.get("rpm_actual"), 6848.0)

    def test_an_unmodelled_command_is_recorded_not_guessed(self) -> None:
        handler = self.device.commands
        assert isinstance(handler, RecordedInverter)

        self.device.handle(
            command_frame(
                self.catalog["inverter_command"],
                {"command_id": 0x0D13, "value": 1},
            )
        )

        self.assertEqual(handler.ignored, [("inverter_command", 0x0D13)])

    def test_a_status_telegram_is_not_taken_for_a_command(self) -> None:
        status = self.catalog["inverter_speed"]
        frame = can.Message(
            arbitration_id=status.arbitration_id,
            is_extended_id=True,
            data=bytes(8),
        )

        self.assertIsNone(self.device.handle(frame))
        self.assertEqual(self.device.received, 0)

    def test_an_error_frame_is_ignored(self) -> None:
        frame = can.Message(
            arbitration_id=self.catalog["inverter_command"].arbitration_id,
            is_extended_id=True,
            data=bytes(8),
            is_error_frame=True,
        )

        self.assertIsNone(self.device.handle(frame))

    def test_a_command_too_short_to_decode_is_not_executed(self) -> None:
        frame = can.Message(
            arbitration_id=self.catalog["inverter_command"].arbitration_id,
            is_extended_id=True,
            data=b"\x10",
        )

        self.assertIsNone(self.device.handle(frame))
        self.assertEqual(self.device.received, 0)


class RoundTripTests(unittest.TestCase):
    """Beide Richtungen durch die echte Bibliothek, ohne Hardware."""

    def simulated(self, name: str) -> SimulatedDevice:
        device = SimulatedDevice.from_recording(
            recording(),
            catalog=catalog(),
            interface="virtual",
            channel=name,
        )
        device.start()
        self.addCleanup(device.stop)
        return device

    def host(self, name: str) -> Device:
        device = Device(
            ["inverter_speed", "motion_control_state"],
            interface="virtual",
            channel=name,
            catalog=catalog(),
            max_age=1.0,
            startup_timeout=5.0,
        )
        device.start()
        self.addCleanup(device.stop)
        return device

    def test_the_host_reads_the_simulated_running_state(self) -> None:
        name = channel("read")
        self.simulated(name)

        host = self.host(name)

        self.assertEqual(host.get("rpm_actual"), 6848.0)
        self.assertEqual(host.get("motion_ctrl_state"), 243.0)

    def test_a_setpoint_written_by_the_host_comes_back(self) -> None:
        name = channel("setpoint")
        self.simulated(name)
        host = self.host(name)

        host.send(
            "inverter_command", {"command_id": COMMAND_RPM_TARGET, "value": 1234}
        )

        self.assertTrue(self.eventually(lambda: host.get("rpm_target") == 1234.0))

    def test_a_disarm_written_by_the_host_stops_the_simulated_drive(self) -> None:
        name = channel("disarm")
        self.simulated(name)
        host = self.host(name)
        self.assertEqual(host.get("motion_ctrl_state"), 243.0)

        host.send("broadcast_command", {"command": BROADCAST_DISARM})

        self.assertTrue(
            self.eventually(lambda: host.get("motion_ctrl_state") == 0.0)
        )
        self.assertEqual(host.get("rpm_actual"), 0.0)

    @staticmethod
    def eventually(condition, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.005)
        return False


if __name__ == "__main__":
    unittest.main()
