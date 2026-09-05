from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from typing import Any

import can

from can_integration import DEFAULT_CATALOG, Device, StaleSignalError, load_json
from can_integration.sim import LogPlayer, Recording, host_sent_keys

EXCERPT = Path(__file__).parent / "data" / "0000309_excerpt.TXT"
CATALOG_EXAMPLE = Path(__file__).parent.parent / "catalog.example.json"

INVERTER_SPEED = (0x1A00000C, True)
DISCOVERY = (0x01000001, True)
COMMAND = (0x0A000000, True)

#: Host telegrams in the excerpt: two ``discover`` and two commands.
HOST_FRAMES = 4


def recording() -> Recording:
    return Recording.from_file(EXCERPT)


def channel(name: str) -> str:
    """A channel of its own per test: virtual buses share one by name."""
    return f"can-integration-sim-{name}"


class ExplodingBus:
    """Fails on the first send, so the thread's fate can be observed."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def send(self, message: Any, timeout: float | None = None) -> None:
        raise self.error

    def shutdown(self) -> None:  # pragma: no cover - never owned by the player
        pass


class DirectionTests(unittest.TestCase):
    def test_host_telegrams_are_taken_from_the_catalog(self) -> None:
        catalog = load_json(CATALOG_EXAMPLE)

        keys = host_sent_keys(catalog)

        # Writable messages are commands the host sends ...
        self.assertIn(COMMAND, keys)
        # ... and discovery_request is the one the host repeats without being
        # writable, because its payload is a constant rather than a value.
        self.assertIn(DISCOVERY, keys)
        self.assertNotIn(INVERTER_SPEED, keys)

    def test_device_direction_leaves_the_hosts_own_traffic_out(self) -> None:
        source = recording()

        player = LogPlayer(
            source, direction="device", catalog=load_json(CATALOG_EXAMPLE)
        )

        self.assertEqual(len(player.frames), len(source) - HOST_FRAMES)
        self.assertEqual(set(player.skipped), {DISCOVERY, COMMAND})
        self.assertIn("0x0A000000", player.describe_skipped())

    def test_all_direction_replays_the_recording_unchanged(self) -> None:
        source = recording()

        player = LogPlayer(source, direction="all")

        self.assertEqual(len(player.frames), len(source))
        self.assertEqual(player.skipped, ())
        self.assertIn("Richtungsfilter aus", player.describe_skipped())

    def test_a_catalog_without_the_command_skips_nothing(self) -> None:
        # The builtin catalog declares no writable inverter command, so the
        # filter has nothing to remove -- and must not guess.
        player = LogPlayer(recording(), direction="device", catalog=DEFAULT_CATALOG)

        self.assertEqual(player.skipped, ())

    def test_an_unknown_direction_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            LogPlayer(recording(), direction="sideways")

        self.assertIn("sideways", str(caught.exception))


class ConfigurationTests(unittest.TestCase):
    def test_a_bus_cannot_be_combined_with_bus_parameters(self) -> None:
        with self.assertRaises(TypeError):
            LogPlayer(recording(), bus=ExplodingBus(OSError()), channel="x")

    def test_a_negative_speed_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            LogPlayer(recording(), speed=-1.0)

    def test_the_bitrate_defaults_to_the_recorded_one(self) -> None:
        player = LogPlayer(recording())

        self.assertEqual(player.bitrate, 1_000_000)

    def test_duration_follows_the_speed(self) -> None:
        player = LogPlayer(recording(), speed=2.0, direction="all")

        self.assertAlmostEqual(player.duration, 1.25 / 2, places=2)


class ReplayTests(unittest.TestCase):
    def test_every_frame_reaches_the_bus(self) -> None:
        name = channel("every-frame")
        listener = can.Bus(interface="virtual", channel=name)
        self.addCleanup(listener.shutdown)
        player = LogPlayer(
            recording(), interface="virtual", channel=name, speed=0, direction="all"
        )
        self.addCleanup(player.close)

        sent = player.run()

        self.assertEqual(sent, 739)
        received = [listener.recv(timeout=0.0) for _ in range(sent)]
        self.assertEqual(len(received), sent)
        self.assertIsNone(listener.recv(timeout=0.0))

    def test_a_replayed_frame_keeps_its_identifier_and_payload(self) -> None:
        name = channel("payload")
        listener = can.Bus(interface="virtual", channel=name)
        self.addCleanup(listener.shutdown)
        source = recording().select(include=[INVERTER_SPEED])
        player = LogPlayer(
            source, interface="virtual", channel=name, speed=0, direction="all"
        )
        self.addCleanup(player.close)

        player.run()

        frame = listener.recv(timeout=0.0)
        assert frame is not None
        self.assertEqual(frame.arbitration_id, INVERTER_SPEED[0])
        self.assertTrue(frame.is_extended_id)
        self.assertEqual(
            DEFAULT_CATALOG["inverter_speed"].decode(frame.data)["rpm_actual"],
            6821.0,
        )

    def test_stopping_ends_a_looping_replay(self) -> None:
        name = channel("looping")
        player = LogPlayer(
            recording(), interface="virtual", channel=name, speed=50.0, loop=True
        )

        player.start()
        time.sleep(0.1)
        player.stop()

        self.assertGreater(player.sent, 0)
        self.assertTrue(player.wait(timeout=1.0))

    def test_a_stop_event_cuts_the_replay_short(self) -> None:
        name = channel("stop-event")
        player = LogPlayer(recording(), interface="virtual", channel=name, speed=1.0)
        self.addCleanup(player.close)
        stop = threading.Event()
        stop.set()

        self.assertEqual(player.run(stop), 0)

    def test_a_failing_bus_surfaces_when_the_player_is_stopped(self) -> None:
        player = LogPlayer(recording(), bus=ExplodingBus(OSError("bus down")))

        player.start()
        player.wait(timeout=2.0)

        with self.assertRaises(OSError):
            player.stop()


class DeviceIntegrationTests(unittest.TestCase):
    """The replay against the real receiving path, not against a fake bus."""

    def device(self, name: str, **kwargs: Any) -> Device:
        return Device(
            ["inverter_speed", "inverter_status_3"],
            interface="virtual",
            channel=name,
            **kwargs,
        )

    def test_a_device_reads_live_values_from_the_replay(self) -> None:
        name = channel("device-live")
        player = LogPlayer(
            recording(), interface="virtual", channel=name, speed=10.0, loop=True
        )
        player.start()
        self.addCleanup(player.stop)

        with self.device(name, max_age=1.0, startup_timeout=5.0) as device:
            seen = set()
            for _ in range(20):
                seen.add(device.get("rpm_actual"))
                time.sleep(0.02)

        # The window carries the recorded shutdown, so the speed cannot be
        # constant: a replay that froze would show a single value.
        self.assertGreater(len(seen), 1)
        self.assertIn(0.0, seen)

    def test_values_go_stale_once_the_replay_stops(self) -> None:
        name = channel("device-stale")
        player = LogPlayer(
            recording(), interface="virtual", channel=name, speed=10.0, loop=True
        )
        player.start()

        with self.device(name, max_age=0.1, startup_timeout=5.0) as device:
            device.get("rpm_actual")
            player.stop()
            time.sleep(0.3)

            with self.assertRaises(StaleSignalError):
                device.get("rpm_actual")


if __name__ == "__main__":
    unittest.main()
