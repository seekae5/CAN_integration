from __future__ import annotations

import struct
import time
import unittest
from typing import Any

from support import wait_for

from can_integration import (
    Calibration,
    CalibrationError,
    Config,
    Device,
    Limit,
    LimitError,
    TareResult,
)
from can_integration.calibration import (
    calibrations_from_dict,
    check_at_rest,
    summarise,
)
from can_integration.catalog import DEFAULT_CATALOG

THRUST_ID = 0x003


class ScaleBus:
    """Eine Waegezelle, die auf jedes ``recv`` einen frischen Wert legt.

    Anders als ``FakeBus`` geht ihr der Vorrat nicht aus: ein Nullabgleich
    braucht eine Reihe *unterschiedlicher* Telegramme, keine Wiederholung
    desselben.
    """

    def __init__(self, grams: float = 0.0, *, step: float = 0.0) -> None:
        self.grams = grams
        self.step = step
        self.sent: list[Any] = []
        self.silent = False

    def recv(self, timeout: float | None = None) -> Any:
        time.sleep(0.001)
        if self.silent:
            return None
        self.grams += self.step
        payload = struct.pack(">i", int(round(self.grams)))
        return _frame(payload)

    def send(self, message: Any, timeout: float | None = None) -> None:
        self.sent.append(message)

    def shutdown(self) -> None:
        pass


def _frame(payload: bytes) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        arbitration_id=THRUST_ID,
        data=payload,
        is_extended_id=False,
        is_error_frame=False,
        is_remote_frame=False,
        timestamp=0.0,
    )


class CalibrationValueTests(unittest.TestCase):
    def test_offset_is_subtracted_before_the_factor_scales(self) -> None:
        calibration = Calibration("weight", offset=100.0, factor=2.0)

        self.assertEqual(calibration.apply(150.0), 100.0)

    def test_undo_returns_the_reported_value(self) -> None:
        calibration = Calibration("weight", offset=37.0, factor=1.004)

        self.assertAlmostEqual(calibration.undo(calibration.apply(512.0)), 512.0)

    def test_a_factor_of_zero_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "zero"):
            Calibration("weight", factor=0.0)

    def test_the_identity_is_recognised(self) -> None:
        self.assertTrue(Calibration("weight").is_identity)
        self.assertFalse(Calibration("weight", offset=1.0).is_identity)

    def test_the_metadata_line_names_the_reference(self) -> None:
        line = Calibration("weight", offset=37, factor=1.004, reference="500 g").describe()

        self.assertIn("offset=37", line)
        self.assertIn("500 g", line)

    def test_replacing_one_part_keeps_the_other(self) -> None:
        calibration = Calibration("weight", offset=37.0, factor=1.004)

        self.assertEqual(calibration.with_offset(0.0).factor, 1.004)
        self.assertEqual(calibration.with_factor(1.0).offset, 37.0)


class TareSummaryTests(unittest.TestCase):
    def test_the_mean_becomes_the_offset(self) -> None:
        result = summarise("weight", [10.0, 12.0, 11.0], 1.0)

        self.assertEqual(result.offset, 11.0)
        self.assertEqual(result.samples, 3)
        self.assertEqual(result.spread, 2.0)

    def test_a_tare_without_a_single_reading_is_refused(self) -> None:
        with self.assertRaises(CalibrationError):
            summarise("weight", [], 1.0)

    def test_a_moving_bench_is_refused(self) -> None:
        result = summarise("weight", [0.0, 40.0], 1.0)

        with self.assertRaisesRegex(CalibrationError, "not at rest"):
            check_at_rest(result, tolerance=1.0)

    def test_no_tolerance_accepts_anything(self) -> None:
        check_at_rest(summarise("weight", [0.0, 400.0], 1.0), tolerance=None)


class DeclarationTests(unittest.TestCase):
    def definitions(self) -> tuple[Any, ...]:
        return DEFAULT_CATALOG.resolve(["thrust"])

    def test_a_declaration_becomes_a_calibration(self) -> None:
        (entry,) = calibrations_from_dict(
            {"weight": {"offset": 37, "factor": 1.004, "reference": "500 g"}},
            self.definitions(),
        )

        self.assertEqual((entry.offset, entry.factor), (37, 1.004))

    def test_an_unknown_key_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "tara"):
            calibrations_from_dict({"weight": {"tara": 1}}, self.definitions())

    def test_a_calibration_for_an_unmonitored_signal_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "calibration for 'temperature'"):
            calibrations_from_dict({"temperature": {"offset": 1}}, self.definitions())

    def test_a_bare_number_is_not_enough(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be an object"):
            calibrations_from_dict({"weight": 37}, self.definitions())

    def test_the_configuration_carries_it(self) -> None:
        config = Config.from_dict(
            {
                "messages": ["thrust"],
                "calibrations": {"weight": {"offset": 37, "reference": "leer"}},
            }
        )

        self.assertEqual(config.calibration_of("weight").offset, 37)
        self.assertEqual(len(config.calibration_rules), 1)


class DeviceTareTests(unittest.TestCase):
    def bench(self, bus: ScaleBus, **kwargs: Any) -> Device:
        device = Device(
            "thrust", bus=bus, max_age=1.0, startup_timeout=2.0, **kwargs
        )
        device.start()
        self.addCleanup(device.stop)
        return device

    def test_taring_zeroes_the_reading_at_once(self) -> None:
        bus = ScaleBus(37.0)
        bench = self.bench(bus)
        self.assertEqual(bench.get("weight"), 37.0)

        result = bench.tare("weight", duration=0.05, minimum_samples=3)

        # Sofort, nicht erst mit dem naechsten Telegramm: sonst laege genau
        # im Moment der Kontrolle noch der alte Nullpunkt an.
        self.assertEqual(bench.get("weight"), 0.0)
        self.assertEqual(result.offset, 37.0)
        self.assertGreaterEqual(result.samples, 3)
        self.assertIsInstance(result, TareResult)

    def test_the_tare_shows_up_as_a_calibration(self) -> None:
        bench = self.bench(ScaleBus(37.0))

        bench.tare("weight", duration=0.05, minimum_samples=3, reference="leer")

        entry = bench.calibration("weight")
        self.assertEqual(entry.offset, 37.0)
        self.assertEqual(entry.reference, "leer")
        self.assertEqual(bench.calibrations, (entry,))

    def test_taring_a_moving_bench_is_refused(self) -> None:
        # Ein Nullabgleich waehrend des Anlaufs vergiftet still den ganzen Lauf.
        bench = self.bench(ScaleBus(0.0, step=5.0))

        with self.assertRaisesRegex(CalibrationError, "not at rest"):
            bench.tare("weight", duration=0.05, minimum_samples=3, tolerance=1.0)

        self.assertIsNone(bench.calibration("weight"))

    def test_taring_twice_does_not_drift(self) -> None:
        bench = self.bench(ScaleBus(37.0))

        bench.tare("weight", duration=0.05, minimum_samples=3)
        bench.tare("weight", duration=0.05, minimum_samples=3)

        self.assertEqual(bench.calibration("weight").offset, 37.0)
        self.assertEqual(bench.get("weight"), 0.0)

    def test_a_silent_sensor_does_not_hang_the_tare(self) -> None:
        bus = ScaleBus(37.0)
        bench = Device("thrust", bus=bus, max_age=5.0, startup_timeout=2.0)
        bench.start()
        self.addCleanup(bench.stop)
        bench.get("weight")
        bus.silent = True

        started = time.monotonic()
        with self.assertRaisesRegex(CalibrationError, "reading"):
            bench.tare("weight", duration=0.05, minimum_samples=3)

        self.assertLess(time.monotonic() - started, 2.0)


class DeviceCalibrateTests(unittest.TestCase):
    def bench(self, bus: ScaleBus) -> Device:
        device = Device("thrust", bus=bus, max_age=1.0, startup_timeout=2.0)
        device.start()
        self.addCleanup(device.stop)
        return device

    def test_the_span_is_set_against_a_known_weight(self) -> None:
        bus = ScaleBus(37.0)
        bench = self.bench(bus)
        bench.tare("weight", duration=0.05, minimum_samples=3)

        bus.grams = 37.0 + 498.0
        calibration = bench.calibrate(
            "weight", 500.0, duration=0.05, minimum_samples=3, reference="500 g"
        )

        self.assertAlmostEqual(bench.get("weight"), 500.0, places=6)
        self.assertAlmostEqual(calibration.factor, 500.0 / 498.0, places=9)
        self.assertEqual(calibration.offset, 37.0)

    def test_the_factor_holds_for_other_loads(self) -> None:
        bus = ScaleBus(0.0)
        bench = self.bench(bus)
        bench.tare("weight", duration=0.05, minimum_samples=3)
        bus.grams = 498.0
        bench.calibrate("weight", 500.0, duration=0.05, minimum_samples=3)

        bus.grams = 996.0
        self.assertTrue(wait_for(lambda: abs(bench.get("weight") - 1000.0) < 1e-6))

    def test_calibrating_against_zero_is_refused(self) -> None:
        bench = self.bench(ScaleBus(10.0))

        with self.assertRaisesRegex(ValueError, "tare"):
            bench.calibrate("weight", 0.0, duration=0.05, minimum_samples=3)

    def test_calibrating_without_a_load_is_refused(self) -> None:
        bench = self.bench(ScaleBus(37.0))
        bench.tare("weight", duration=0.05, minimum_samples=3)

        with self.assertRaisesRegex(CalibrationError, "zero point"):
            bench.calibrate("weight", 500.0, duration=0.05, minimum_samples=3)


class OrderTests(unittest.TestCase):
    """Kalibrierung wirkt vor den Grenzwerten, nicht danach."""

    def test_a_limit_sees_the_calibrated_value(self) -> None:
        bus = ScaleBus(100.0)
        device = Device(
            "thrust",
            bus=bus,
            max_age=1.0,
            startup_timeout=2.0,
            limits=[Limit("weight", maximum=150.0)],
        )
        device.start()
        self.addCleanup(lambda: device._monitor.stop())
        self.assertEqual(device.get("weight"), 100.0)

        # Derselbe Rohwert, doppelt gewichtet: jetzt liegt er ueber der Grenze.
        device.set_calibration(Calibration("weight", factor=2.0))

        self.assertTrue(wait_for(lambda: device.tripped is not None))
        with self.assertRaises(LimitError):
            device.get("weight")

    def test_a_tare_can_bring_a_value_back_under_its_limit(self) -> None:
        bus = ScaleBus(160.0)
        device = Device(
            "thrust",
            bus=bus,
            max_age=1.0,
            startup_timeout=2.0,
            calibrations=[Calibration("weight", offset=100.0)],
            limits=[Limit("weight", maximum=150.0)],
        )
        device.start()
        self.addCleanup(device.stop)

        # 160 g roh, 100 g Vorlast -> 60 g gemessen, also innerhalb der Grenze.
        self.assertEqual(device.get("weight"), 60.0)
        self.assertIsNone(device.tripped)


if __name__ == "__main__":
    unittest.main()
