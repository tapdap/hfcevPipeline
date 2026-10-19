"""Compare a stage manifest with reference expectations (e.g. a frozen earlier release).

Reference JSON layout:
    {"totals": {stream: rows}, "devices": {device: {stream: {field: value, ...}}}}
Only fields present in both the reference and the manifest record are compared,
so a reference may be as coarse (row counts) or as strict (SHA-256) as available.
A reference may also hold per-input-file counters ("files": {path: {counter: n}}),
checked against stage receipts that list the files they processed.
"""
from __future__ import annotations

from collections import Counter


def compare(manifest_devices: dict, reference: dict) -> dict:
    expected = reference["devices"]
    common = sorted(set(expected) & set(manifest_devices))
    missing = sorted(set(expected) - set(manifest_devices))
    mismatches, checked = [], Counter()
    for device in common:
        for stream, want in expected[device].items():
            got = manifest_devices[device].get(stream, {})
            for field in want:
                if field not in got:
                    continue
                checked[field] += 1
                if want[field] == got[field]:
                    continue
                entry = dict(device=device, stream=stream, field=field)
                if field == "daily":
                    days = set(want[field]) | set(got[field])
                    entry["days_differing"] = sum(want[field].get(d) != got[field].get(d) for d in days)
                else:
                    entry.update(expected=want[field], actual=got[field])
                mismatches.append(entry)
    want_totals = reference.get("totals", {})
    totals = {s: sum(manifest_devices[d].get(s, {}).get("rows", 0) for d in common) for s in want_totals}
    return dict(ok=not missing and not mismatches and totals == want_totals,
                devices_expected=len(expected), devices_checked=len(common), missing_devices=missing,
                fields_checked=dict(checked), totals_expected=want_totals, totals_actual=totals,
                mismatches=mismatches)


def compare_files(receipts: list[dict], reference_files: dict) -> dict:
    """Each receipt's counters must equal the sum of the reference counters of its files."""
    mismatches, checked = [], 0
    for receipt in receipts:
        expected = Counter()
        for path in receipt["files"]:
            expected.update(reference_files[path])
        for key, value in expected.items():
            checked += 1
            if receipt["counts"].get(key, 0) != value:
                mismatches.append(dict(group=receipt["key"], counter=key, expected=value,
                                       actual=receipt["counts"].get(key, 0)))
    return dict(ok=not mismatches, groups=len(receipts), files=sum(len(r["files"]) for r in receipts),
                counters_checked=checked, mismatches=mismatches)
