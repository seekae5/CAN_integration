"""Pure functions for decoding the inverter temperature telegram."""

from struct import Struct

SUPPORTED_TEMPERATURE_IDS = frozenset({0x1A000001, 0x1A000003})
PAYLOAD_LENGTH = 8
TEMPERATURE_SCALE_CELSIUS = 0.01

_TEMPERATURE_FIELD = Struct("<H")
_TEMPERATURE_OFFSET = 6


class InvalidTemperatureFrameError(ValueError):
    """Raised when a matching CAN frame cannot contain a temperature."""


def decode_temperature(payload: bytes | bytearray | memoryview) -> float:
    """Decode the temperature in degrees Celsius from an inverter payload.

    The known telegrams consist of four little-endian unsigned 16-bit values.
    Temperature is the fourth value (bytes 6 and 7) with a scale of
    0.01 degrees Celsius per bit.
    """
    if len(payload) < PAYLOAD_LENGTH:
        raise InvalidTemperatureFrameError(
            f"CAN payload too short: expected at least {PAYLOAD_LENGTH} bytes, "
            f"got {len(payload)}"
        )

    (temperature_raw,) = _TEMPERATURE_FIELD.unpack_from(payload, _TEMPERATURE_OFFSET)
    return temperature_raw * TEMPERATURE_SCALE_CELSIUS

