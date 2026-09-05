"""Bus ownership shared by the parts of the simulation.

Both the replay and the simulated device need the same thing: a python-can
bus that is either handed in by the caller or opened -- and later closed --
by the object itself. That contract is the one
:class:`~can_integration.bus.BusConnection` already follows for the receiving
side; it lives here separately because the simulation sends without a receive
filter, which is exactly what ``BusConnection`` is built around.
"""

from __future__ import annotations

import can

from ..bus import DEFAULT_BITRATE

#: A simulation talks to itself by default: ``virtual`` is built into
#: python-can, needs no hardware and no configuration, but it only reaches
#: other buses inside the same process. Two terminals need ``udp_multicast``.
SIM_INTERFACE = "virtual"
SIM_CHANNEL = "can_integration"


class BusOwner:
    """Holds a bus, and remembers whether closing it is its business.

    If ``bus`` is supplied, its lifecycle stays with the caller and the bus
    parameters must not be given, because an existing bus cannot be
    reconfigured.
    """

    def __init__(
        self,
        *,
        bus: can.BusABC | None = None,
        interface: str | None = None,
        channel: str | None = None,
        bitrate: int | None = None,
    ) -> None:
        if bus is not None and (interface, channel, bitrate) != (None, None, None):
            raise TypeError(
                "bus cannot be combined with interface, channel or bitrate"
            )
        if bitrate is not None and bitrate <= 0:
            raise ValueError("bitrate must be greater than zero")

        self._bus = bus
        self._owns_bus = bus is None
        self.config: dict[str, object] = {
            "interface": SIM_INTERFACE if interface is None else interface,
            "channel": SIM_CHANNEL if channel is None else channel,
            "bitrate": DEFAULT_BITRATE if bitrate is None else bitrate,
        }

    @property
    def bitrate(self) -> int:
        return int(self.config["bitrate"])  # type: ignore[arg-type]

    @property
    def interface(self) -> str:
        return str(self.config["interface"])

    @property
    def channel(self) -> str:
        return str(self.config["channel"])

    def connect(self) -> can.BusABC:
        """Open the configured bus if it is not open yet and return it."""
        if self._bus is None:
            self._bus = can.Bus(**self.config)
        return self._bus

    def close(self) -> None:
        """Release a bus this owner opened; leave a borrowed one alone."""
        if self._bus is not None and self._owns_bus:
            self._bus.shutdown()
            self._bus = None
