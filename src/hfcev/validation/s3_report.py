"""Aggregate review of stage 3: totals, validation tests and comparison with a frozen earlier version.

Input: the stage-3 manifest (one record per device), optionally the frozen per-device outcomes
({"devices": {device: {"A1": {...}, "A2": {...}, "A3": {...}}}}) and a reference for the exact
regression of correspondence and the earlier candidate pool (see `validation.reference`).
The log_events layer is scanned once for the receipt month of every message.
Output: a JSON report and a Markdown summary (English or Chinese).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

from .reference import compare

FROZEN_KEYS = dict(A1=("same_time_groups", "same_time_rows", "repaired_groups", "repaired_rows", "supported_rows"),
                   A2=("addition_candidates", "additions", "pending_additions"),
                   A3=("candidates", "new_additions", "total_additions"))


def _sum(records, section) -> dict:
    total = Counter()
    for r in records:
        for k, v in r.get(section, {}).items():
            if isinstance(v, dict):
                total.update({f"{k}:{a}": b for a, b in v.items()})
            else:
                total[k] += v
    return dict(sorted(total.items()))


def _rate(tested, passed) -> dict:
    return dict(tested=tested, passed=passed, rate=round(passed / tested, 6) if tested else None)


def _odometer(records) -> dict:
    gapped, receipt = defaultdict(Counter), defaultdict(Counter)
    for r in records:
        for section in ("stored_timeline", "device_axis"):
            for cls, v in r["odometer"][section].items():
                for gap, s in v.items():
                    gapped[(section, cls, gap)].update(s)
        for cls, s in r["odometer"]["receipt_order"].items():
            receipt[cls].update(s)
    out = dict(stored_timeline={}, device_axis={}, receipt_order={})
    for (section, cls, gap), s in sorted(gapped.items()):
        out[section].setdefault(cls, {})[gap] = _rate(s["tested"], s["passed"])
    for cls, s in sorted(receipt.items()):
        out["receipt_order"][cls] = _rate(s["tested"], s["passed"])
    return out


def _nested(records, section) -> dict:
    """Sum {key: {count or {tested, passed}}} sections over devices."""
    out = {}
    for r in records:
        for key, v in r[section].items():
            slot = out.setdefault(key, {})
            for name, x in v.items():
                if isinstance(x, dict):
                    slot.setdefault(name, Counter()).update(x)
                else:
                    slot[name] = slot.get(name, 0) + x
    return {k: {n: _rate(x["tested"], x["passed"]) if isinstance(x, Counter) else x for n, x in v.items()}
            for k, v in out.items()}


def _gps(records) -> dict:
    counts, speed = Counter(), defaultdict(Counter)
    for r in records:
        g = r.get("gps")
        if not g:
            continue
        for k, v in g.items():
            if k == "speed":
                for cls, x in v.items():
                    speed[cls].update(x)
            elif isinstance(v, dict):
                counts.update({f"{k}:{a}": b for a, b in v.items()})
            else:
                counts[k] += v
    return dict(counts=dict(sorted(counts.items())),
                speed={cls: _rate(x["tested"], x["passed"]) for cls, x in sorted(speed.items())},
                devices_with_additions=sum(r.get("gps", {}).get("published", 0) > 0 for r in records))


def gps_addition_months(layer: Path, devices) -> dict:
    counts = Counter()
    for device in devices:
        path = layer / "gps_additions" / f"{device}.parquet"
        if path.exists():
            t = pq.read_table(path, columns=["time_received_ms"])
            if t.num_rows:
                counts.update(pc.strftime(t["time_received_ms"], format="%Y-%m").to_pylist())
    return dict(sorted(counts.items()))


def receipt_months(layer: Path, devices) -> dict:
    """Messages per receipt month and disposition class (linked / added / outside_window / other)."""
    counts = defaultdict(Counter)
    for device in devices:
        path = layer / "log_events" / f"{device}.parquet"
        if not path.exists():
            continue
        t = pq.read_table(path, columns=["time_log", "disposition"])
        if not t.num_rows:
            continue
        month = pc.strftime(t["time_log"], format="%Y-%m").to_pylist()
        for mo, d in zip(month, t["disposition"].to_pylist()):
            cls = ("linked" if d == "linked_to_stored_record" else "added" if d == "added"
                   else "outside_window" if d == "not_candidate:outside_window" else "other")
            counts[mo or "missing"][cls] += 1
    return {k: dict(v) for k, v in sorted(counts.items())}


def build(manifest_devices: dict, frozen: dict | None = None, reference: dict | None = None,
          layer: Path | None = None) -> dict:
    devices = sorted(manifest_devices)
    records = [manifest_devices[d] for d in devices]
    report = dict(devices=len(devices), fingerprints=sorted({str(r.get("fingerprint")) for r in records}),
                  rows=_sum(records, "rows"), stored=_sum(records, "stored"), groups=_sum(records, "groups"),
                  additions=_sum(records, "additions"), earlier_pool=_sum(records, "candidates"),
                  disposition=_sum(records, "disposition"), clock=_sum(records, "clock"),
                  odometer=_odometer(records), sensitivity=_nested(records, "sensitivity"),
                  earlier_rule=_nested(records, "earlier_rule"), gps=_gps(records))
    report["devices_with_additions"] = sum(r["additions"]["rows"] > 0 for r in records)
    if reference is not None:
        strict = [dict(device=d, section=s, field=k, expected=v, actual=manifest_devices.get(d, {}).get(s, {}).get(k, 0))
                  for d, want in reference["devices"].items() for s, fields in want.items() for k, v in fields.items()
                  if manifest_devices.get(d, {}).get(s, {}).get(k, 0) != v]
        loose = compare(manifest_devices, reference)
        report["regression"] = dict(ok=not strict and loose["ok"], devices_expected=loose["devices_expected"],
                                    devices_checked=loose["devices_checked"], missing_devices=loose["missing_devices"],
                                    fields_checked=sum(len(f) for w in reference["devices"].values() for f in w.values()),
                                    mismatches=strict[:50])
    if frozen is not None:
        fd = frozen["devices"]
        report["frozen"] = {a: {k: sum(fd[d][a].get(k, 0) for d in fd) for k in keys} for a, keys in FROZEN_KEYS.items()}
        report["frozen"]["A1_reason_groups"] = dict(sum((Counter(fd[d]["A1"].get("reason_groups", {})) for d in fd), Counter()))
        rows = []
        for d in devices:
            if d not in fd:
                continue
            r, f = manifest_devices[d], fd[d]
            rows.append(dict(device=d, frozen_repaired_groups=f["A1"]["repaired_groups"],
                             frozen_changed_rows=f["A1"]["repaired_rows"],
                             ordered_by_device_time=r["groups"]["ordered_by_device_time"],
                             ordered_by_received_ms=r["groups"]["ordered_by_received_ms"],
                             frozen_additions=f["A3"]["total_additions"], additions=r["additions"]["rows"],
                             additions_with_device_time=r["additions"]["time_device"],
                             earlier_pool=r["candidates"]["addition_candidates"],
                             earlier_pool_only=r["additions"]["earlier_pool_only"]))
        report["per_device"] = rows
    if layer is not None:
        report["receipt_months"] = receipt_months(layer, devices)
        report["gps"]["additions_by_receipt_month"] = gps_addition_months(layer, devices)
    return report


# ---------------------------------------------------------------- Markdown

TEXT = {
    "en": dict(title="Stage 3 review (two-clock timeline, v3)", regression="Exact regression", stored="Stored records",
               groups="Same-timestamp groups of stored records", additions="Additions", odo_t="Odometer against the stored receipt timeline",
               odo_d="Odometer among verified records on the device clock",
               odo_r="Odometer in receipt order within the log stream", sens="Threshold sensitivity (before the collision step)",
               earlier="Earlier-rule approximation", frozen="Comparison with the frozen version", months="Messages by receipt month",
               questions="Open questions", item="Item", value="Value", cls="Class", tested="Tested", passed="Passed", rate="Rate",
               gps="GPS: correspondence and additions", gps_speed="GPS plausibility (implied speed)",
               gps_months="GPS additions by receipt month"),
    "zh": dict(title="Stage 3 审阅报告（双时钟时间线，v3）", regression="精确回归", stored="存储记录",
               groups="存储记录的同刻组", additions="补充记录", odo_t="里程检验：对照存储的接收时间线",
               odo_d="里程检验：已核验记录之间，仅用设备时钟",
               odo_r="里程检验：日志流内按接收顺序", sens="阈值敏感性（碰撞步骤之前）", earlier="旧规则近似",
               frozen="与冻结版对照", months="按接收月份的报文数", questions="待你确认的问题", item="项目", value="数值",
               cls="类别", tested="可检验", passed="通过", rate="通过率",
               gps="GPS：对应与补充", gps_speed="GPS 合理性（相邻定位推算车速）", gps_months="GPS 补充按接收月份"),
}


def _table(header, rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines + [""]


def _n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else ("—" if x is None else f"{x:.4f}" if isinstance(x, float) else str(x))


def _pct(r) -> str:
    return "—" if r["rate"] is None else f"{100 * r['rate']:.2f}%"


def markdown(report: dict, lang: str = "zh", questions: list[str] | None = None) -> str:
    T = TEXT[lang]
    st, gr, ad, ep = report["stored"], report["groups"], report["additions"], report["earlier_pool"]
    out = [f"# {T['title']}", "", f"devices = {report['devices']}; fingerprints = {', '.join(report['fingerprints'])}", ""]
    if "regression" in report:
        g = report["regression"]
        out += [f"## {T['regression']}", "",
                f"{'PASS' if g['ok'] else 'FAIL'}: {g['devices_checked']}/{g['devices_expected']} devices, "
                f"{g['fields_checked']:,} fields, {len(g['mismatches'])} mismatches "
                f"(unique correspondences {report['stored']['linked']:,}; earlier pool {ep['addition_candidates']:,}).", ""]
    out += [f"## {T['stored']}", ""] + _table([T["item"], T["value"]], [(k, _n(v)) for k, v in st.items()])
    out += [f"## {T['groups']}", ""] + _table([T["item"], T["value"]], [(k, _n(v)) for k, v in gr.items()])
    out += [f"## {T['additions']}", ""] + _table([T["item"], T["value"]],
                                                [(k, _n(v)) for k, v in ad.items()] +
                                                [("earlier_pool:" + k, _n(v)) for k, v in ep.items()] +
                                                [("devices_with_additions", report["devices_with_additions"])])
    rows = [(f"{cls} ({gap})", _n(v["tested"]), _n(v["passed"]), _pct(v))
            for cls, gaps in report["odometer"]["stored_timeline"].items() for gap, v in gaps.items()]
    out += [f"## {T['odo_t']}", ""] + _table([T["cls"], T["tested"], T["passed"], T["rate"]], rows)
    rows = [(f"{cls} ({gap})", _n(v["tested"]), _n(v["passed"]), _pct(v))
            for cls, gaps in report["odometer"]["device_axis"].items() for gap, v in gaps.items()]
    out += [f"## {T['odo_d']}", ""] + _table([T["cls"], T["tested"], T["passed"], T["rate"]], rows)
    rows = [(cls, _n(v["tested"]), _n(v["passed"]), _pct(v)) for cls, v in report["odometer"]["receipt_order"].items()]
    out += [f"## {T['odo_r']}", ""] + _table([T["cls"], T["tested"], T["passed"], T["rate"]], rows)
    rows = [(k, _n(v["linked_verified"]), _n(v["added_verified"]), _pct(v["odometer_at_device_time"]),
             _pct(v["odometer_receipt_order"])) for k, v in report["sensitivity"].items()]
    out += [f"## {T['sens']}", ""] + _table(["lead/lag", "linked verified", "added verified",
                                            "odometer @ device time", "odometer receipt order"], rows)
    rows = [(k, _n(v["pool"]), _n(v["accepted"]), _pct(v["odometer_at_reported_time"]), _pct(v["odometer_receipt_order"]))
            for k, v in report["earlier_rule"].items()]
    out += [f"## {T['earlier']}", ""] + _table(["pool", "size", "accepted", "odometer @ reported time",
                                               "odometer receipt order"], rows)
    if "frozen" in report:
        f = report["frozen"]
        rows = [("A1 repaired groups / v3 groups ordered by device time", _n(f["A1"]["repaired_groups"]),
                 _n(gr["ordered_by_device_time"])),
                ("A1 — / v3 groups ordered by receipt ms", "—", _n(gr["ordered_by_received_ms"])),
                ("A1 changed rows / v3 stored rows with device time ≠ time", _n(f["A1"]["repaired_rows"]),
                 _n(st["device_time_differs_from_time"])),
                ("A2+A3 additions / v3 additions", _n(f["A3"]["total_additions"]), _n(ad["rows"])),
                ("— / v3 additions with verified device time", "—", _n(ad["time_device"])),
                ("A2 candidate pool / v3 pool", _n(f["A2"]["addition_candidates"]), _n(ad["rows"]))]
        out += [f"## {T['frozen']}", ""] + _table(["", "frozen", "v3"], rows)
    if "receipt_months" in report:
        rows = [(mo, *(_n(c.get(k, 0)) for k in ("linked", "added", "outside_window", "other")))
                for mo, c in report["receipt_months"].items()]
        out += [f"## {T['months']}", ""] + _table(["month", "linked", "added", "outside_window", "other"], rows)
    if report.get("gps", {}).get("counts"):
        g = report["gps"]
        out += [f"## {T['gps']}", ""] + _table([T["item"], T["value"]],
                                              [(k, _n(v)) for k, v in g["counts"].items()] +
                                              [("devices_with_additions", g["devices_with_additions"])])
        out += [f"## {T['gps_speed']}", ""] + _table([T["cls"], T["tested"], T["passed"], T["rate"]],
                                                    [(k, _n(v["tested"]), _n(v["passed"]), _pct(v))
                                                     for k, v in g["speed"].items()])
        if g.get("additions_by_receipt_month"):
            out += [f"## {T['gps_months']}", ""] + _table(["month", "added"],
                                                         [(k, _n(v)) for k, v in g["additions_by_receipt_month"].items()])
    if questions:
        out += [f"## {T['questions']}", ""] + [f"{i}. {q}" for i, q in enumerate(questions, 1)] + [""]
    return "\n".join(out)
