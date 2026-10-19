"""Split raw receive logs into blocks and identify messages. Nothing is repaired or guessed.

A log file is a sequence of blocks, each starting with a header line
`YYYY-MM-DD HH:MM:SS[.fff] [thread] ... - `. A topic block announces the MQTT topic of
the next message block on the same thread; a message block carries one JSON payload,
possibly spanning several lines. Every block keeps its byte offsets in the file.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from functools import lru_cache

HEADER = re.compile(rb"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+\[(?P<thread>[^\r\n]*?)\][^\r\n]*? - ", re.M)
TOPIC = re.compile(r"(?:接收消息主题|接收主题)[:：]\s*(\S+)")
PAYLOAD = re.compile(r"(?:回调接受[:：]\s*)?(?:接收消息|接收到的消息|接收数据)[:：]\s*")
DEVICE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
UNASSIGNED = "_unassigned"


def _loads_strict(text: str):
    def reject(value):
        raise ValueError("Non-finite JSON constant: " + value)
    return json.loads(text, parse_constant=reject)


def _decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("gb18030"), "gb18030"


def log_blocks(raw: bytes):
    """Yield one dict per block: offset, end_offset, log_time, kind, reason, topic, payload.

    kind is `message`, `topic`, `other` (non-message log line), `reject` (unparseable)
    or `fragment` (bytes before the first header). Topics pair with the next message on
    the same thread. Truncated objects are rejected, never joined across files.
    """
    heads = list(HEADER.finditer(raw))
    first = heads[0].start() if heads else len(raw)
    if raw[:first].strip():
        yield dict(offset=0, end_offset=first, kind="fragment", reason="unframed_prefix",
                   log_time=None, topic=None, payload=None)
    topics = {}
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(raw)
        thread = head["thread"].decode("utf8", "replace")
        block = dict(offset=head.start(), end_offset=end, log_time=head["time"].decode(),
                     kind="other", reason=None, topic=None, payload=None)
        try:
            body, _ = _decode(raw[head.end():end])
        except UnicodeError:
            block.update(kind="reject", reason="invalid_text_encoding")
            yield block
            continue
        topic = TOPIC.search(body)
        if topic:
            topics[thread] = topic.group(1)
            block.update(kind="topic", topic=topic.group(1))
            yield block
            continue
        marker = PAYLOAD.search(body)
        if not marker:
            if body.lstrip().startswith("{"):
                block.update(kind="reject", reason="unrecognised_json_prefix")
            yield block
            continue
        block["topic"] = topics.pop(thread, None)
        try:
            payload = _loads_strict(body[marker.end():].strip())
            if not isinstance(payload, dict):
                raise ValueError("JSON payload is not an object")
            block.update(kind="message", payload=payload)
        except (ValueError, UnicodeError):
            block.update(kind="reject", reason="invalid_or_truncated_json")
        yield block


def identity(payload: dict, topic: str | None) -> tuple[str, str, str | None]:
    """(device, status, topic device). The payload `sn` alone assigns the device;
    the topic is only a cross-check."""
    sn = payload.get("sn")
    match = re.search(r"/tbox/([^/]+)/", topic or "")
    topic_sn = match.group(1) if match else None
    if not isinstance(sn, str) or not DEVICE.fullmatch(sn):
        return UNASSIGNED, "invalid_or_missing_sn", topic_sn
    if topic_sn is not None and sn != topic_sn:
        return sn, "topic_sn_mismatch", topic_sn
    return sn, "topic_agrees" if topic_sn else "payload_sn_only", topic_sn


def event_id(file_sha256: str, relative_path: str, offset: int) -> str:
    """Stable 32-hex ID of a block: source file content, path and byte offset."""
    return hashlib.sha256(f"{file_sha256}\0{relative_path}\0{offset}".encode()).hexdigest()[:32]


@lru_cache(maxsize=65536)
def parse_time(value) -> datetime | None:
    """Naive wall-clock timestamp, or None. Timezone-aware or malformed values are not accepted."""
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace(",", "."))
    except ValueError:
        return None
    return None if parsed.tzinfo is not None else parsed
