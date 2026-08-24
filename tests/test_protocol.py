import unittest

from can_temperature import InvalidTemperatureFrameError, decode_temperature


class DecodeTemperatureTests(unittest.TestCase):
    def test_decodes_known_temperature(self) -> None:
        payload = bytes.fromhex("64 00 C8 00 2C 01 88 13")

        self.assertEqual(decode_temperature(payload), 50.0)

    def test_accepts_additional_payload_bytes(self) -> None:
        payload = bytes.fromhex("00 00 00 00 00 00 D0 07 FF")

        self.assertEqual(decode_temperature(payload), 20.0)

    def test_rejects_short_payload(self) -> None:
        with self.assertRaisesRegex(InvalidTemperatureFrameError, "7"):
            decode_temperature(bytes(7))

    def test_decodes_at_a_custom_offset(self) -> None:
        payload = bytes.fromhex("C8 0D 00 00 00 00 00 00")

        self.assertEqual(decode_temperature(payload, offset=0), 35.28)

    def test_rejects_a_payload_too_short_for_the_custom_offset(self) -> None:
        with self.assertRaisesRegex(InvalidTemperatureFrameError, "offset 6"):
            decode_temperature(bytes(7), offset=6)

    def test_rejects_a_negative_offset(self) -> None:
        with self.assertRaises(ValueError):
            decode_temperature(bytes(8), offset=-1)


if __name__ == "__main__":
    unittest.main()
