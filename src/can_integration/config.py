"""Measurement configuration, loadable from a JSON file.

A configuration selects messages from the catalog by name. It never repeats
their arbitration IDs or byte offsets: those belong in the catalog, where they
are declared once and reviewed once.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .catalog import DEFAULT_CATALOG, Catalog, load_json
from .signals import Message, resolve_signal, signal_keys

DEFAULT_MAX_AGE = 1.0
DEFAULT_STARTUP_TIMEOUT = 5.0

#: ``catalog`` is derived from the ``"catalog"`` path in the JSON file and is
#: therefore not a field a caller may set through ``from_dict``.
_DERIVED_FIELDS = frozenset({"catalog"})


@dataclass(frozen=True)
class Config:
    """Parameters of one measurement.

    ``messages`` names catalog entries; everything about their layout comes
    from the catalog. ``interface``, ``channel`` and ``bitrate`` stay ``None``
    unless the file overrides the defaults of the CAN backend. ``limits`` is
    carried for the calling measurement application; this package never
    enforces a limit on its own.
    """

    messages: tuple[str, ...]
    interface: str | None = None
    channel: str | None = None
    bitrate: int | None = None
    max_age: float = DEFAULT_MAX_AGE
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT
    limits: Mapping[str, float] = field(default_factory=dict)
    catalog: Catalog = field(default_factory=lambda: DEFAULT_CATALOG)

    def __post_init__(self) -> None:
        names = self.messages
        if isinstance(names, str):
            names = (names,)
        object.__setattr__(self, "messages", tuple(names))
        if not self.messages:
            raise ValueError("configuration requires at least one message")

        try:
            definitions = self.catalog.resolve(self.messages)
        except LookupError as error:
            raise ValueError(str(error)) from None

        duplicates = sorted(
            {name for name in self.messages if self.messages.count(name) > 1}
        )
        if duplicates:
            raise ValueError(f"message listed twice: {', '.join(duplicates)}")

        object.__setattr__(self, "limits", MappingProxyType(dict(self.limits)))
        for name, limit in self.limits.items():
            try:
                resolve_signal(definitions, name)
            except LookupError as error:
                raise ValueError(f"limit for {name!r}: {error}") from None
            if not isinstance(limit, (int, float)) or isinstance(limit, bool):
                raise ValueError(f"limit for {name!r} must be a number")

    @property
    def definitions(self) -> tuple[Message, ...]:
        """The catalog entries this configuration selects."""
        return self.catalog.resolve(self.messages)

    @property
    def signal_names(self) -> tuple[str, ...]:
        """Every signal these messages provide, qualified where ambiguous."""
        return tuple(signal_keys(self.definitions))

    def limit(self, name: str) -> float | None:
        """The configured limit for a signal, or None if there is none."""
        return self.limits.get(name)

    @classmethod
    def from_dict(
        cls,
        values: Mapping[str, Any],
        *,
        catalog: Catalog = DEFAULT_CATALOG,
    ) -> Config:
        """Build a configuration from an already parsed JSON object."""
        allowed = {entry.name for entry in fields(cls)} - _DERIVED_FIELDS
        if "catalog" in values:
            raise ValueError(
                "'catalog' names a file of extra message definitions and is "
                "only supported by Config.from_json; pass catalog=... here"
            )

        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown configuration keys: {', '.join(unknown)}")
        if "messages" not in values:
            raise ValueError("configuration requires 'messages'")

        return cls(**dict(values), catalog=catalog)

    @classmethod
    def from_json(cls, path: str | Path) -> Config:
        """Load a configuration from a JSON file containing one object.

        An optional ``"catalog"`` key names a JSON file with further message
        definitions; its path is resolved relative to the configuration file,
        so a measurement directory stays portable.
        """
        path = Path(path)
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"{path}: configuration must be a JSON object")

        values = dict(document)
        catalog_file = values.pop("catalog", None)
        catalog = DEFAULT_CATALOG
        if catalog_file is not None:
            if not isinstance(catalog_file, str):
                raise ValueError(f"{path}: 'catalog' must be a file name")
            catalog = load_json(path.parent / catalog_file)

        try:
            return cls.from_dict(values, catalog=catalog)
        except ValueError as error:
            raise ValueError(f"{path}: {error}") from None
