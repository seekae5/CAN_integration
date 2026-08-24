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


if __name__ == "__main__":
    unittest.main()

