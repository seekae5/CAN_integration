from __future__ import annotations

import time
import unittest
from pathlib import Path
from typing import Any

import can

from can_integration import DEFAULT_CATALOG, load_json
from can_integration.sim import (
    BROADCAST_ARM,
    BROADCAST_DISARM,
    Chain,
    Constant,
    Cycle,
    Follow,
    FromRecording,
    Noise,
    Ramp,
    Recording,
    SimulatedDevice,
    as_behaviour,
)

EXCERPT = Path(__file__).parent / "data" / "0000309_excerpt.TXT"
CATALOG_EXAMPLE = Path(__file__).parent.parent / "catalog.example.json"

SPEED = DEFAULT_CATALOG["inverter_speed"]


def catalog() -> Any:
    return load_json(CATALOG_EXAMPLE)


def recording() -> Recording:
    return Recording.from_file(EXCERPT)


def bare_device(**kwargs: Any) -> SimulatedDevice:
    """Ein Geraet mit einem Telegramm und ohne Bus -- fuer Schrittproben."""
    state = dict.fromkeys(SPEED.signal_names, 0.0)
    return SimulatedDevice(
        [Cycle(SPEED, period=0.01)], state, bus=object(), **kwargs
    )


class RampTests(unittest.TestCase):
    def test_moves_towards_a_fixed_target_at_the_given_rate(self) -> None:
        device = bare_device()
        ramp = Ramp("rpm_actual", target=1000.0, rate=100.0)

        ramp(device, 0.5)

        self.assertEqual(device.get("rpm_actual"), 50.0)

    def test_snaps_to_the_target_once_it_is_within_one_step(self) -> None:
        device = bare_device()
        device.set("rpm_actual", 995.0)
        ramp = Ramp("rpm_actual", target=1000.0, rate=100.0)

        ramp(device, 0.5)

        self.assertEqual(device.get("rpm_actual"), 1000.0)

    def test_ramps_downwards_as_well(self) -> None:
        device = bare_device()
        device.set("rpm_actual", 1000.0)
        ramp = Ramp("rpm_actual", target=0.0, rate=100.0)

        ramp(device, 1.0)

        self.assertEqual(device.get("rpm_actual"), 900.0)

    def test_a_named_target_follows_a_setpoint(self) -> None:
        device = bare_device()
        device.set("rpm_target", 400.0)
        ramp = Ramp("rpm_actual", target="rpm_target", rate=100.0)

        ramp(device, 1.0)
        self.assertEqual(device.get("rpm_actual"), 100.0)

        # Die Messseite schreibt einen neuen Sollwert: die Rampe dreht um.
        device.set("rpm_target", 0.0)
        ramp(device, 1.0)
        self.assertEqual(device.get("rpm_actual"), 0.0)

    def test_a_step_without_time_changes_nothing(self) -> None:
        device = bare_device()
        Ramp("rpm_actual", target=1000.0, rate=100.0)(device, 0.0)

        self.assertEqual(device.get("rpm_actual"), 0.0)

    def test_a_rate_of_zero_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Ramp("rpm_actual", target=1.0, rate=0.0)


class CompositionTests(unittest.TestCase):
    def test_constant_holds_the_value_it_first_saw(self) -> None:
        device = bare_device()
        device.set("rpm_actual", 700.0)
        hold = Constant("rpm_actual")

        hold(device, 0.1)
        device.set("rpm_actual", 0.0)
        hold(device, 0.1)

        self.assertEqual(device.get("rpm_actual"), 700.0)

    def test_follow_couples_two_signals(self) -> None:
        device = bare_device()
        device.set("rpm_actual", 200.0)

        Follow("torque_actual", source="rpm_actual", factor=0.5, bias=10.0)(
            device, 0.1
        )

        self.assertEqual(device.get("torque_actual"), 110.0)

    def test_a_chain_runs_in_order_and_the_last_write_wins(self) -> None:
        device = bare_device()
        chain = Chain(
            [
                Ramp("rpm_actual", target=1000.0, rate=100.0),
                Constant("rpm_actual", 5.0),
            ]
        )

        chain(device, 1.0)

        self.assertEqual(device.get("rpm_actual"), 5.0)

    def test_a_list_of_behaviours_becomes_a_chain(self) -> None:
        behaviour = as_behaviour([Constant("rpm_actual", 1.0)])

        self.assertIsInstance(behaviour, Chain)
        self.assertIsNone(as_behaviour(None))


class FromRecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.behaviour = FromRecording.from_recording(
            recording(), catalog=catalog(), signals=["rpm_actual"]
        )

    def test_the_timeline_has_one_entry_per_recorded_frame(self) -> None:
        # inverter_speed appears 123 times in the excerpt.
        self.assertEqual(len(self.behaviour.timeline), 123)
        self.assertAlmostEqual(self.behaviour.span, 1.25, places=3)

    def test_advancing_the_clock_plays_the_measured_values(self) -> None:
        device = bare_device()

        # Der erste Eintrag liegt bei t = 1 ms; davor hat die Aufzeichnung
        # zu diesem Telegramm noch nichts zu sagen.
        self.behaviour(device, 0.002)
        self.assertEqual(device.get("rpm_actual"), 6821.0)

        # Kurz vor dem aufgezeichneten Stopp, also noch der laufende Antrieb.
        self.behaviour(device, 0.40)
        self.assertEqual(device.get("rpm_actual"), 6848.0)

        # Und dahinter das, was das Log nach dem Kommando zeigt.
        self.behaviour(device, 0.10)
        self.assertEqual(device.get("rpm_actual"), 0.0)

    def test_it_starts_over_when_it_loops(self) -> None:
        device = bare_device()

        self.behaviour(device, self.behaviour.span + 0.01)
        self.assertEqual(device.get("rpm_actual"), 0.0)

        self.behaviour(device, 0.0)

        # Die Uhr behaelt ihre Phase ueber den Sprung, der Verlauf beginnt neu.
        self.assertEqual(device.get("rpm_actual"), 6821.0)

    def test_without_loop_it_stops_at_the_last_value(self) -> None:
        behaviour = FromRecording.from_recording(
            recording(), catalog=catalog(), signals=["rpm_actual"], loop=False
        )
        device = bare_device()

        behaviour(device, 10.0)
        behaviour(device, 10.0)

        self.assertEqual(device.get("rpm_actual"), 0.0)

    def test_values_the_device_does_not_carry_are_reported(self) -> None:
        behaviour = FromRecording.from_recording(recording(), catalog=catalog())
        device = bare_device()

        behaviour(device, 0.002)

        # Das Geraet fuehrt nur inverter_speed; alles andere wird uebersprungen
        # und benannt, statt still zu verschwinden.
        self.assertIn("id_flt", behaviour.ignored)
        self.assertNotIn("rpm_actual", behaviour.ignored)
        self.assertEqual(device.get("rpm_actual"), 6821.0)

    def test_an_empty_timeline_does_nothing(self) -> None:
        behaviour = FromRecording(timeline=(), span=0.0)

        behaviour(bare_device(), 1.0)


class NoiseTests(unittest.TestCase):
    def payload_values(self, device: SimulatedDevice) -> dict[str, float]:
        cycle = device.cycles[0]
        return SPEED.decode(device.payload(cycle))

    def test_a_seed_makes_the_noise_repeatable(self) -> None:
        first = Noise({"rpm_actual": 10.0}, seed=7)
        second = Noise({"rpm_actual": 10.0}, seed=7)

        self.assertEqual(first("rpm_actual", 100.0), second("rpm_actual", 100.0))

    def test_a_signal_without_sigma_is_untouched(self) -> None:
        noise = Noise({"rpm_actual": 10.0}, seed=7)

        self.assertEqual(noise("rpm_target", 100.0), 100.0)

    def test_a_negative_sigma_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Noise({"rpm_actual": -1.0})

    def test_the_noise_reaches_the_payload_but_not_the_state(self) -> None:
        device = bare_device(noise=Noise({"rpm_actual": 50.0}, seed=3))
        device.set("rpm_actual", 1000.0)

        sent = [self.payload_values(device)["rpm_actual"] for _ in range(5)]

        # Der Zustand bleibt sauber -- sonst liefe das Signal als
        # Zufallsbewegung davon, statt um seinen Wert zu streuen.
        self.assertEqual(device.get("rpm_actual"), 1000.0)
        self.assertGreater(len(set(sent)), 1)
        self.assertTrue(all(abs(value - 1000.0) < 400.0 for value in sent))

    def test_noise_that_does_not_fit_the_format_is_dropped(self) -> None:
        class Impossible(Noise):
            def __call__(self, name: str, value: float) -> float:
                return -1.0 if name == "rpm_actual" else value

        device = bare_device(noise=Impossible({"rpm_actual": 1.0}))
        device.set("rpm_actual", 1000.0)

        # rpm_actual ist ein '<H': -1 passt nicht, also geht der saubere Wert
        # auf den Bus statt das Telegramm zu verlieren.
        self.assertEqual(self.payload_values(device)["rpm_actual"], 1000.0)


class ArmedTests(unittest.TestCase):
    """Das Tor, das ein Disarm-Kommando schliesst -- im laufenden Betrieb."""

    def test_a_disarmed_device_freezes_and_an_arm_lets_it_run_again(self) -> None:
        device = SimulatedDevice.from_recording(
            recording(),
            catalog=catalog(),
            interface="virtual",
            channel="can-integration-armed",
            behaviour=Ramp("rpm_actual", target=0.0, rate=1000.0),
        )
        device.start()
        self.addCleanup(device.stop)
        time.sleep(0.05)

        device.armed = False
        frozen = device.get("rpm_actual")
        time.sleep(0.15)
        self.assertEqual(device.get("rpm_actual"), frozen)

        device.armed = True
        time.sleep(0.15)
        self.assertLess(device.get("rpm_actual"), frozen)

    def test_the_disarm_command_clears_the_flag(self) -> None:
        device = SimulatedDevice.from_recording(
            recording(), catalog=catalog(), bus=object()
        )
        broadcast = catalog()["broadcast_command"]

        device.handle(
            can.Message(
                arbitration_id=broadcast.arbitration_id,
                is_extended_id=broadcast.extended,
                data=broadcast.encode({"command": BROADCAST_DISARM}),
            )
        )

        self.assertFalse(device.armed)

    def test_arming_again_makes_the_device_run(self) -> None:
        device = SimulatedDevice.from_recording(
            recording(), catalog=catalog(), bus=object()
        )
        broadcast = catalog()["broadcast_command"]

        for command in (BROADCAST_DISARM, BROADCAST_ARM):
            device.handle(
                can.Message(
                    arbitration_id=broadcast.arbitration_id,
                    is_extended_id=broadcast.extended,
                    data=broadcast.encode({"command": command}),
                )
            )

        self.assertTrue(device.armed)


if __name__ == "__main__":
    unittest.main()
