from __future__ import annotations

import unittest
from pathlib import Path

from can_integration import DEFAULT_CATALOG, load_json
from can_integration.sim import LogFormatError, Recording, parse_log

EXCERPT = Path(__file__).parent / "data" / "0000309_excerpt.TXT"
CATALOG_EXAMPLE = Path(__file__).parent.parent / "catalog.example.json"

CURRENT_CONTROL = (0x1A000006, True)
VOLTAGE_CONTROL = (0x1A000007, True)
INVERTER_SPEED = (0x1A00000C, True)
DISCOVERY = (0x01000001, True)
COMMAND = (0x0A000000, True)

#: A log in the shape the CL1000 writes, small enough to reason about.
SYNTHETIC = """# Logger type: CL1000
# Value separator: ";"
# Time separator: ":"
# Time separator ms: ""
# Time and date separator: "T"
# Bit-rate: 500000
# Time: 20240301T235959
Timestamp;Type;ID;Data
23:59:59000;1;1a000003;0102030405060708
23:59:59500;1;003;01020304
00:00:00500;1;1a000003;1112131415161718
"""


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = Recording.from_file(EXCERPT)

    def test_reads_every_frame_of_the_excerpt(self) -> None:
        self.assertEqual(len(self.recording), 739)
        self.assertAlmostEqual(self.recording.duration, 1.25, places=3)

    def test_reads_the_header(self) -> None:
        self.assertEqual(self.recording.bitrate, 1_000_000)
        self.assertEqual(self.recording.header["Logger type"], "CL1000")
        self.assertEqual(self.recording.header["Session No."], "286")

    def test_unquotes_header_values(self) -> None:
        # The separators arrive as ";" including the quotation marks.
        self.assertEqual(self.recording.header["Value separator"], ";")

    def test_start_time_comes_from_the_header(self) -> None:
        start = self.recording.start_time
        assert start is not None
        self.assertEqual((start.year, start.hour, start.second), (2000, 0, 7))

    def test_first_frame_starts_the_clock(self) -> None:
        first = self.recording.frames[0]
        self.assertEqual(first.t, 0.0)
        self.assertEqual(first.arbitration_id, 0x1A000006)
        self.assertTrue(first.extended)
        self.assertEqual(first.data, bytes.fromhex("fcd2753853d23b37"))
        self.assertEqual(first.key, CURRENT_CONTROL)

    def test_counts_are_ordered_by_traffic(self) -> None:
        counts = self.recording.counts()
        self.assertEqual(counts[CURRENT_CONTROL], 123)
        self.assertEqual(counts[DISCOVERY], 2)
        self.assertEqual(counts[COMMAND], 2)
        self.assertEqual(sum(counts.values()), 739)
        self.assertEqual(next(iter(counts)), CURRENT_CONTROL)

    def test_cycle_time_of_the_fast_telegrams(self) -> None:
        cycles = self.recording.cycle_times()
        self.assertAlmostEqual(cycles[CURRENT_CONTROL], 10.0, delta=2.0)
        self.assertAlmostEqual(cycles[DISCOVERY], 500.0, delta=10.0)

    def test_a_single_frame_has_no_cycle_time(self) -> None:
        # 0x01100000 appears once in this window; one frame is no cycle.
        self.assertNotIn((0x01100000, True), self.recording.cycle_times())

    def test_first_and_last_payload_show_the_disarm(self) -> None:
        # The recorded command at t=26.213 s stops the drive: the voltage
        # controller is active at the start of the window and zero at its end.
        first = self.recording.first_payloads()[VOLTAGE_CONTROL]
        last = self.recording.last_payloads()[VOLTAGE_CONTROL]
        self.assertNotEqual(first, bytes(8))
        self.assertEqual(last, bytes(8))

    def test_select_keeps_timing_and_drops_telegrams(self) -> None:
        without_commands = self.recording.select(exclude=[COMMAND, DISCOVERY])

        self.assertEqual(len(without_commands), 739 - 4)
        self.assertNotIn(COMMAND, without_commands.counts())
        self.assertEqual(without_commands.frames[0].t, 0.0)
        self.assertEqual(without_commands.header, self.recording.header)

    def test_select_include_is_a_whitelist(self) -> None:
        only_speed = self.recording.select(include=[INVERTER_SPEED])

        self.assertEqual(set(only_speed.counts()), {INVERTER_SPEED})


class CoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = Recording.from_file(EXCERPT)

    def test_builtin_catalog_covers_only_part_of_the_recording(self) -> None:
        coverage = self.recording.coverage()

        self.assertEqual(
            {message.name for message in coverage.known.values()},
            {"inverter_status_3", "inverter_speed", "motor_temperature"},
        )
        self.assertIn(CURRENT_CONTROL, coverage.unknown)

    def test_unknown_telegrams_are_reported_not_dropped(self) -> None:
        coverage = self.recording.coverage()

        self.assertEqual(
            len(coverage.known) + len(coverage.unknown), len(coverage.counts)
        )
        report = coverage.report()
        self.assertIn("ohne Katalogeintrag", report)
        self.assertIn("0x1A000006", report)

    def test_the_example_catalog_closes_most_of_the_gap(self) -> None:
        catalog = load_json(CATALOG_EXAMPLE)

        coverage = self.recording.coverage(catalog)

        self.assertEqual(coverage.unknown, ())
        self.assertEqual(coverage.known[COMMAND].name, "inverter_command")

    def test_a_message_the_recording_never_carries_is_not_reported(self) -> None:
        coverage = self.recording.coverage(DEFAULT_CATALOG)

        self.assertNotIn("thrust", {m.name for m in coverage.known.values()})


class SyntheticLogTests(unittest.TestCase):
    def test_midnight_rollover_keeps_time_moving_forward(self) -> None:
        recording = parse_log(SYNTHETIC)

        self.assertEqual([round(frame.t, 3) for frame in recording.frames],
                         [0.0, 0.5, 1.5])

    def test_a_short_identifier_stays_standard(self) -> None:
        recording = parse_log(SYNTHETIC)

        standard = recording.frames[1]
        self.assertEqual(standard.arbitration_id, 0x003)
        self.assertEqual(standard.data, bytes.fromhex("01020304"))

    def test_an_identifier_above_eleven_bits_must_be_extended(self) -> None:
        # The type column says standard; the identifier says otherwise, and
        # the identifier is the one that cannot be wrong.
        recording = parse_log(
            "Timestamp;Type;ID;Data\n00:00:00000;0;1a000003;0000000000000000\n"
        )

        self.assertTrue(recording.frames[0].extended)

    def test_other_separators_are_taken_from_the_header(self) -> None:
        recording = parse_log(
            '# Value separator: ","\n'
            '# Time separator: "-"\n'
            '# Time separator ms: "."\n'
            "Timestamp,Type,ID,Data\n"
            "00-00-01.250,1,1a000003,0000000000000000\n"
            "00-00-02.750,1,1a000003,0000000000000000\n"
        )

        self.assertEqual([frame.t for frame in recording.frames], [0.0, 1.5])

    def test_a_broken_payload_names_its_line(self) -> None:
        with self.assertRaises(LogFormatError) as caught:
            parse_log("Timestamp;Type;ID;Data\n00:00:00000;1;1a000003;xyz\n")

        self.assertIn("line 2", str(caught.exception))

    def test_a_missing_column_names_its_line(self) -> None:
        with self.assertRaises(LogFormatError) as caught:
            parse_log("Timestamp;Type;ID;Data\n00:00:00000;1;1a000003\n")

        self.assertIn("line 2", str(caught.exception))

    def test_a_broken_timestamp_names_its_line(self) -> None:
        with self.assertRaises(LogFormatError) as caught:
            parse_log("Timestamp;Type;ID;Data\n0000000;1;1a000003;00\n")

        self.assertIn("line 2", str(caught.exception))

    def test_an_empty_recording_has_no_duration(self) -> None:
        recording = parse_log("# Logger type: CL1000\nTimestamp;Type;ID;Data\n")

        self.assertEqual(len(recording), 0)
        self.assertEqual(recording.duration, 0.0)
        self.assertEqual(recording.counts(), {})


if __name__ == "__main__":
    unittest.main()
