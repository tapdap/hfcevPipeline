"""Strict DBC reader for fixed-length Intel (little-endian) integer signals.

Any other DBC feature (Motorola byte order, multiplexing, float signals) stops the
run instead of silently decoding wrong values. Values are scaled, never clipped;
enumerations stay numeric.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

MESSAGE = re.compile(r"^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+")
SIGNAL = re.compile(r'^\s*SG_\s+(\w+)\s*:\s*(\d+)\|(\d+)@([01])([+-])\s+\(([^,]+),([^\)]+)\)\s+\[([^|]+)\|([^\]]+)\]\s+"([^"]*)"')
NON_BUS_MESSAGE = "VECTOR__INDEPENDENT_SIG_MSG"


@dataclass(frozen=True)
class Signal:
    name: str
    start: int
    length: int
    signed: bool
    factor: float
    offset: float
    unit: str

    def decode(self, value: int) -> float:
        raw = (value >> self.start) & ((1 << self.length) - 1)
        if self.signed and raw & (1 << (self.length - 1)):
            raw -= 1 << self.length
        return raw * self.factor + self.offset


@dataclass
class Message:
    frame_id: int
    extended: bool
    name: str
    length: int
    signals: list = field(default_factory=list)

    def decode(self, payload: bytes) -> dict[str, float]:
        if len(payload) != self.length:
            raise ValueError("DLC mismatch")
        value = int.from_bytes(payload, "little")
        return {s.name: s.decode(value) for s in self.signals}


def read_dbc(path: Path) -> dict[int, Message]:
    """CAN identifier -> Message. Raises on any unsupported construct."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    messages, current = {}, None
    for line in text.splitlines():
        match = MESSAGE.match(line)
        if match:
            numeric, name, length = int(match[1]), match[2], int(match[3])
            if name == NON_BUS_MESSAGE:
                current = None
                continue
            extended, identifier = bool(numeric & 0x80000000), numeric & 0x7FFFFFFF
            if identifier > (0x1FFFFFFF if extended else 0x7FF) or not 1 <= length <= 8:
                raise ValueError(f"Unsupported CAN identifier/length: {name}")
            if identifier in messages:
                raise ValueError(f"Ambiguous CAN ID: {identifier}")
            current = messages[identifier] = Message(identifier, extended, name, length)
        elif re.match(r"^\s*SG_\s+", line):
            if current is None:
                continue
            match = SIGNAL.match(line)
            if not match:
                raise ValueError(f"Unsupported signal or multiplexing: {line.strip()}")
            name, start, length, order, sign, factor, offset, _, _, unit = match.groups()
            start, length, factor, offset = int(start), int(length), float(factor), float(offset)
            if order != "1" or not 1 <= length <= 64 or start + length > current.length * 8:
                raise ValueError(f"Only fixed Intel signals are supported: {name}")
            if not (math.isfinite(factor) and math.isfinite(offset)):
                raise ValueError(f"Non-finite scaling: {name}")
            if any(s.name == name for s in current.signals):
                raise ValueError(f"Duplicate signal: {name}")
            current.signals.append(Signal(name, start, length, sign == "-", factor, offset, unit))
        elif re.match(r"^\s*(SIG_VALTYPE_|SG_MUL_VAL_|SGTYPE_)\s+\d", line):
            raise ValueError("Unsupported typed or multiplexed signal")
    if not messages or any(not m.signals for m in messages.values()):
        raise ValueError("DBC has no messages or a message without signals")
    return messages


def qualified_name(can_id: int, message: str, signal: str) -> str:
    """Unambiguous signal key: the same signal name can occur in several messages."""
    return json.dumps([can_id, message, signal], separators=(",", ":"))
