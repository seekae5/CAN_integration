from __future__ import annotations

import unittest

from support import PAYLOAD_MOTOR_35_CELSIUS, FakeBus, frame, wait_for

from can_integration import (
    DEFAULT_CATALOG,
    Config,
    Device,
    Limit,
    LimitError,
    Message,
    SafeCommand,
    SafeState,
    SafeStateError,
    SafeStateResult,
    Signal,
    StaleSignalError,
)
from can_integration.safety import Violation, limits_from_dict, safe_state_from_list

MOTOR_ID = 0x1A000013

#: Ein schreibbares Telegramm, damit die Tests ohne catalog.example.json
#: auskommen: der eingebaute Katalog enthaelt bewusst keines.
DISARM = Message(
    name="test_command",
    arbitration_id=0x0A000000,
    writable=True,
    length=8,
    description="Testkommando",
    signals=(Signal("command", offset=0, format="<B", default=0),),
)
CATALOG = DEFAULT_CATALOG.extended_with([DISARM])
SAFE = SafeState([SafeCommand("test_command", {"command": 0})])


def motor_frames(count: int = 1) -> list:
    return [frame(MOTOR_ID, PAYLOAD_MOTOR_35_CELSIUS) for _ in range(count)]


class LimitTests(unittest.TestCase):
    def test_a_value_inside_its_range_is_no_violation(self) -> None:
        self.assertIsNone(Limit("t", minimum=0, maximum=100).check(50))

    def test_an_upper_limit_reports_the_number_it_broke(self) -> None:
        reason = Limit("temperature", maximum=80).check(85.5)

        self.assertIn("85.5", reason)
        self.assertIn("80", reason)

    def test_a_lower_limit_counts_as_well(self) -> None:
        # Eine einbrechende Zwischenkreisspannung ist genauso ein Abbruchgrund
        # wie eine zu hohe.
        self.assertIsNotNone(Limit("u_dc", minimum=300).check(290))
        self.assertIsNone(Limit("u_dc", minimum=300).check(310))

    def test_the_boundary_itself_is_still_allowed(self) -> None:
        self.assertIsNone(Limit("t", maximum=80).check(80))

    def test_a_limit_without_any_bound_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Limit("t")

    def test_an_inverted_range_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "above maximum"):
            Limit("t", minimum=100, maximum=10)

    def test_an_unknown_action_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "loud"):
            Limit("t", maximum=1, action="loud")

    def test_warn_does_not_abort(self) -> None:
        self.assertFalse(Limit("t", maximum=1, action="warn").aborts)
        self.assertTrue(Limit("t", maximum=1).aborts)


class LimitDeclarationTests(unittest.TestCase):
    def definitions(self) -> tuple[Message, ...]:
        return DEFAULT_CATALOG.resolve(["motor_temperature", "thrust"])

    def test_a_bare_number_means_an_upper_limit(self) -> None:
        (limit,) = limits_from_dict({"temperature": 80}, self.definitions())

        self.assertEqual((limit.minimum, limit.maximum), (None, 80.0))
        self.assertTrue(limit.aborts)

    def test_the_long_form_carries_both_bounds_and_the_action(self) -> None:
        (limit,) = limits_from_dict(
            {"weight": {"min": -5, "max": 5000, "action": "warn"}},
            self.definitions(),
        )

        self.assertEqual((limit.minimum, limit.maximum), (-5, 5000))
        self.assertEqual(limit.action, "warn")

    def test_a_limit_for_an_unmonitored_signal_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit for 'rpm_actual'"):
            limits_from_dict({"rpm_actual": 1}, self.definitions())

    def test_an_unknown_key_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "maximum"):
            limits_from_dict({"temperature": {"maximum": 1}}, self.definitions())


class SafeStateTests(unittest.TestCase):
    def test_an_empty_safe_state_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            SafeState([])

    def test_a_status_telegram_cannot_be_a_safe_command(self) -> None:
        state = SafeState([SafeCommand("motor_temperature", {"temperature": 0})])

        with self.assertRaises(Exception) as caught:
            state.validate(CATALOG)

        self.assertIn("writable", str(caught.exception))

    def test_a_missing_value_is_found_before_the_emergency(self) -> None:
        message = Message(
            name="two_fields",
            arbitration_id=0x0A000001,
            writable=True,
            signals=(
                Signal("a", offset=0, format="<B", default=0),
                Signal("b", offset=1, format="<B"),
            ),
        )
        catalog = DEFAULT_CATALOG.extended_with([message])
        state = SafeState([SafeCommand("two_fields", {"a": 1})])

        with self.assertRaisesRegex(Exception, "needs a value for b"):
            state.validate(catalog)

    def test_commands_go_out_in_the_order_they_are_listed(self) -> None:
        # Am Drehmomentpruefstand entscheidet die Reihenfolge: erst den
        # Pruefling momentfrei, dann die Lastmaschine.
        order: list[str] = []
        state = SafeState(
            [SafeCommand("erst", {}), SafeCommand("dann", {})]
        )

        result = state.apply(lambda name, values, timeout: order.append(name))

        self.assertEqual(order, ["erst", "dann"])
        self.assertTrue(result.complete)

    def test_a_failing_command_does_not_stop_the_rest(self) -> None:
        def send(name, values, timeout):
            if name == "kaputt":
                raise OSError("bus down")

        state = SafeState(
            [SafeCommand("kaputt", {}), SafeCommand("geht", {})], attempts=1
        )

        result = state.apply(send)

        self.assertEqual(result.sent, ("geht",))
        self.assertEqual(result.failed[0][0], "kaputt")
        self.assertFalse(result.complete)
        self.assertIn("INCOMPLETE", str(result))

    def test_a_command_is_retried(self) -> None:
        attempts = []

        def send(name, values, timeout):
            attempts.append(name)
            if len(attempts) < 3:
                raise OSError("noch nicht")

        result = SafeState([SafeCommand("x", {})], attempts=3).apply(send)

        self.assertEqual(len(attempts), 3)
        self.assertTrue(result.complete)

    def test_from_a_json_list(self) -> None:
        state = safe_state_from_list(
            [{"message": "test_command", "values": {"command": 0}}], catalog=CATALOG
        )

        self.assertEqual(state.message_names, ("test_command",))

    def test_an_unknown_key_in_the_json_form_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "kommando"):
            safe_state_from_list([{"kommando": "x"}], catalog=CATALOG)


class ConfigTests(unittest.TestCase):
    def test_limits_reach_the_configuration_as_rules(self) -> None:
        config = Config.from_dict(
            {
                "messages": ["motor_temperature"],
                "limits": {"temperature": {"min": 5, "max": 80, "action": "warn"}},
            }
        )

        rule = config.rule("temperature")
        self.assertEqual((rule.minimum, rule.maximum, rule.action), (5, 80, "warn"))
        self.assertEqual(config.limit("temperature"), 80)

    def test_a_safe_state_is_parsed_and_validated(self) -> None:
        config = Config.from_dict(
            {
                "messages": ["motor_temperature"],
                "safe_state": [
                    {"message": "test_command", "values": {"command": 0}}
                ],
            },
            catalog=CATALOG,
        )

        self.assertEqual(config.safe_state.message_names, ("test_command",))

    def test_a_safe_state_on_a_status_telegram_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Config.from_dict(
                {
                    "messages": ["motor_temperature"],
                    "safe_state": [{"message": "motor_temperature"}],
                },
                catalog=CATALOG,
            )


class DeviceLimitTests(unittest.TestCase):
    """Der ganze Weg: empfangen, pruefen, ausloesen, senden."""

    def device(self, bus: FakeBus, **kwargs) -> Device:
        return Device(
            "motor_temperature",
            bus=bus,
            max_age=0.05,
            startup_timeout=2.0,
            catalog=CATALOG,
            **kwargs,
        )

    def test_a_violation_stops_the_measurement_and_sends_the_safe_state(self) -> None:
        bus = FakeBus(motor_frames(3))
        device = self.device(
            bus,
            limits=[Limit("temperature", maximum=30.0)],
            safe_state=SAFE,
        )
        device.start()
        self.addCleanup(lambda: device._monitor.stop())

        self.assertTrue(wait_for(lambda: device.tripped is not None))
        self.assertTrue(wait_for(lambda: bool(bus.sent)))

        with self.assertRaises(LimitError):
            device.get("temperature")
        self.assertEqual(bus.sent[0].arbitration_id, DISARM.arbitration_id)
        self.assertEqual(device.last_safe_state.sent, ("test_command",))

    def test_a_warning_is_recorded_but_the_measurement_goes_on(self) -> None:
        bus = FakeBus(motor_frames(3))
        with self.device(
            bus, limits=[Limit("temperature", maximum=30.0, action="warn")]
        ) as device:
            self.assertTrue(wait_for(lambda: bool(device.violations)))

            self.assertIsNone(device.tripped)
            self.assertEqual(device.get("temperature"), 35.28)
            self.assertFalse(bus.sent)

    def test_a_violation_is_reported_once_per_crossing(self) -> None:
        bus = FakeBus(motor_frames(20))
        with self.device(
            bus, limits=[Limit("temperature", maximum=30.0, action="warn")]
        ) as device:
            self.assertTrue(wait_for(lambda: bool(device.violations)))

            # Zwanzig Telegramme ueber der Grenze, aber nur eine Flanke.
            self.assertEqual(len(device.violations), 1)

    def test_a_value_inside_its_range_raises_nothing(self) -> None:
        bus = FakeBus(motor_frames(3))
        with self.device(bus, limits=[Limit("temperature", maximum=80.0)]) as device:
            self.assertEqual(device.get("temperature"), 35.28)
            self.assertEqual(device.violations, ())


class WatchdogTests(unittest.TestCase):
    def test_a_message_that_stops_arriving_trips_the_run(self) -> None:
        # Ein Sensor, der schweigt, ist kein unkritischer Sensor.
        bus = FakeBus(motor_frames(1))
        device = Device(
            "motor_temperature",
            bus=bus,
            max_age=0.05,
            startup_timeout=2.0,
            catalog=CATALOG,
            safe_state=SAFE,
        )
        device.start()
        self.addCleanup(lambda: device._monitor.stop())

        self.assertTrue(wait_for(lambda: device.tripped is not None))

        self.assertIsNone(device.tripped.limit)
        self.assertIn("no telegram", device.tripped.reason)
        self.assertTrue(wait_for(lambda: bool(bus.sent)))

    def test_without_limits_or_a_safe_state_nothing_watches(self) -> None:
        # Bisheriges Verhalten bleibt: der veraltete Wert faellt beim Lesen
        # auf und die Messung kann sich wieder fangen.
        bus = FakeBus(motor_frames(1))
        with Device(
            "motor_temperature", bus=bus, max_age=0.05, startup_timeout=2.0
        ) as device:
            self.assertTrue(
                wait_for(lambda: device.age("temperature") > 0.05)
            )

            with self.assertRaises(StaleSignalError):
                device.get("temperature")
            self.assertEqual(device.violations, ())


class SafeStateOnExitTests(unittest.TestCase):
    def test_a_clean_end_leaves_the_bench_disarmed_as_well(self) -> None:
        bus = FakeBus(motor_frames(3))
        with Device(
            "motor_temperature",
            bus=bus,
            max_age=5.0,
            startup_timeout=2.0,
            catalog=CATALOG,
            safe_state=SAFE,
        ) as device:
            self.assertEqual(device.get("temperature"), 35.28)

        self.assertEqual(len(bus.sent), 1)
        self.assertEqual(bus.sent[0].arbitration_id, DISARM.arbitration_id)

    def test_a_safe_state_that_does_not_get_through_is_reported(self) -> None:
        bus = FakeBus(motor_frames(3))
        bus.send_error = OSError("bus down")
        device = Device(
            "motor_temperature",
            bus=bus,
            max_age=5.0,
            startup_timeout=2.0,
            catalog=CATALOG,
            safe_state=SafeState(
                [SafeCommand("test_command", {"command": 0})], attempts=1
            ),
        )
        device.start()

        with self.assertRaises(SafeStateError) as caught:
            device.stop()

        self.assertIn("INCOMPLETE", str(caught.exception))
        self.assertFalse(device.last_safe_state.complete)

    def test_without_a_safe_state_nothing_is_sent(self) -> None:
        bus = FakeBus(motor_frames(3))
        with Device(
            "motor_temperature", bus=bus, max_age=5.0, startup_timeout=2.0
        ) as device:
            self.assertEqual(device.safe(), SafeStateResult())

        self.assertFalse(bus.sent)


class ViolationTests(unittest.TestCase):
    def test_a_watchdog_violation_always_aborts(self) -> None:
        self.assertTrue(Violation(reason="weg", monotonic=0.0).aborts)

    def test_the_text_names_what_kind_it_was(self) -> None:
        limit = Limit("t", maximum=1)
        self.assertIn("[limit]", str(Violation("zu heiss", 0.0, limit=limit)))
        self.assertIn("[watchdog]", str(Violation("weg", 0.0)))


if __name__ == "__main__":
    unittest.main()
