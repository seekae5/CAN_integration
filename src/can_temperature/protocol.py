"""Pure functions for decoding a CAN temperature telegram."""

from struct import Struct

#: Extended CAN IDs observed on real buses. Informational only: the physical
#: meaning of each ID, and the byte offset its temperature lives at, must be
#: confirmed per ID (see ``offset`` of ``decode_temperature``) before use.
KNOWN_TEMPERATURE_IDS = frozenset({0x1A000001, 0x1A000003, 0x1A000013})

TEMPERATURE_SCALE_CELSIUS = 0.01

#: Byte offset used by the originally documented inverter telegrams
#: (0x1A000001, 0x1A000003). Other telegrams have been observed to place the
#: same little-endian uint16 temperature at a different offset within the
#: same eight-byte payload, e.g. offset 0 for 0x1A000013.
DEFAULT_TEMPERATURE_OFFSET = 6

_TEMPERATURE_FIELD = Struct("<H")


class InvalidTemperatureFrameError(ValueError):
    """Raised when a matching CAN frame cannot contain a temperature."""


def decode_temperature(
    payload: bytes | bytearray | memoryview,
    *,
    offset: int = DEFAULT_TEMPERATURE_OFFSET,
) -> float:
    """Decode a temperature in degrees Celsius from a CAN payload.

    The temperature is a little-endian unsigned 16-bit value at ``offset``,
    scaled by 0.01 degrees Celsius per bit. The default offset matches the
    known inverter telegrams; pass a different ``offset`` for telegrams that
    place the temperature elsewhere in the payload.
    """
    if offset < 0:
        raise ValueError("offset must not be negative")

    required_length = offset + _TEMPERATURE_FIELD.size
    if len(payload) < required_length:
        raise InvalidTemperatureFrameError(
            f"CAN payload too short: expected at least {required_length} bytes "
            f"for a temperature at offset {offset}, got {len(payload)}"
        )

    (temperature_raw,) = _TEMPERATURE_FIELD.unpack_from(payload, offset)
    return temperature_raw * TEMPERATURE_SCALE_CELSIUS
