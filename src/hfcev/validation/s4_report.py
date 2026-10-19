"""Aggregate review of stage 4: segments, gaps, field flags, stored-vs-log comparison,
decoding correction and GPS flags, from the stage-4 manifest (one record per device).
Output: a JSON report and a Markdown summary (English or Chinese).
"""
from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np


def _add(total: dict, part: dict) -> None:
    for k, v in part.items():
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, dict):
            _add(total.setdefault(k, {}), v)
        elif isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
            total[k] = (np.array(total.get(k, [0] * len(v))) + np.array(v)).tolist()
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            total[k] = total.get(k, 0) + v


def build(manifest_devices: dict) -> dict:
    records = [manifest_devices[d] for d in sorted(manifest_devices)]
    total = {}
    for r in records:
        _add(total, {k: r[k] for k in ("records", "segments", "gaps", "intervals", "fields", "relations", "counters",
                                       "log_comparison", "gps") if k in r})
        for f, v in r.get("stuck_jump", {}).items():
            slot = total.setdefault("stuck_jump", {}).setdefault(f, {})
            _add(slot, dict(stuck=v["stuck"], jump={k: x for k, x in v["jump"].items() if k != "threshold"}))
        c = r.get("correction", {})
        if c.get("applied"):
            _add(total.setdefault("correction", {}), {k: v for k, v in c.items() if k != "continuity_step_histogram"})
            _add(total["correction"].setdefault("continuity", {}), c["continuity_step_histogram"]["classes"])
            total["correction"]["step_bins"] = c["continuity_step_histogram"]["bins"]
    for f, v in total.get("fields", {}).items():
        v["dbc_signal"] = any(r["fields"].get(f, {}).get("dbc_signal") for r in records)
    total["devices"] = len(records)
    total["fingerprints"] = sorted({str(r.get("fingerprint")) for r in records})
    return total


TEXT = {
    "en": dict(title="Stage 4 review (quality annotation)", seg="Reporting segments", gaps="No-data gaps",
               iv="Receipt-clock intervals between consecutive records", fields="Field flags",
               cmp="Stored vs log-decoded values (linked records)", corr="HyBottleP / HighPPMNum decoding correction",
               cont="Continuity of HyBottleP (|step| between consecutive records, dt <= 60 s)", gps="GPS flags",
               rel="Relations and counters", sj="Stuck values and jumps (statistics only)", q="Open questions"),
    "zh": dict(title="Stage 4 审阅报告（质量标注）", seg="上报段", gaps="无数据间隔", iv="相邻记录的接收时钟间隔",
               fields="字段标记", cmp="存储值与日志解码值对照（有日志对应的存储记录）",
               corr="HyBottleP / HighPPMNum 解码修正", cont="HyBottleP 连续性（相邻记录 |变化量|，间隔 ≤ 60 秒）",
               gps="GPS 标记", rel="字段关系与计数器", sj="卡值与跳变（仅统计）", q="待你确认的问题"),
}


def _table(header, rows) -> list[str]:
    return (["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
            + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows] + [""])


def _n(x) -> str:
    if isinstance(x, float):
        return f"{x:,.1f}"
    return f"{x:,}" if isinstance(x, int) else str(x)


def _p(a, b) -> str:
    return f"{100 * a / b:.2f}%" if b else "—"


def markdown(r: dict, lang: str = "zh", questions: list[str] | None = None) -> str:
    T = TEXT[lang]
    out = [f"# {T['title']}", "", f"devices = {r['devices']}; fingerprints = {', '.join(r['fingerprints'])}", ""]
    rec, seg, gaps = r["records"], r["segments"], r["gaps"]
    out += [f"## {T['seg']}", ""] + _table(["item", "value"], [
        ("records (stored / additions)", f"{_n(rec['total'])} ({_n(rec['stored'])} / {_n(rec['additions'])})"),
        ("segments", _n(seg["count"])), ("short (< threshold)", f"{_n(seg['short'])} ({_p(seg['short'], seg['count'])})"),
        ("crossing midnight", _n(seg["crosses_midnight"])), ("total duration (h)", _n(seg["duration_s"] / 3600)),
        *[(f"segments if gap > {k}", _n(v)) for k, v in seg["count_by_gap"].items()]])
    rows = [(label, _n(c), _n(gaps["duration_s_by_label"].get(label, 0) / 3600), _n(gaps["odometer_rose_by_label"].get(label, 0)))
            for label, c in sorted(gaps["by_label"].items())]
    out += [f"## {T['gaps']}", ""] + _table(["label", "gaps", "hours", "odometer rose across gap"], rows)
    iv = r["intervals"]
    tot = sum(iv.values())
    out += [f"## {T['iv']}", ""] + _table(["interval", "count", "share"], [(k, _n(v), _p(v, tot)) for k, v in iv.items()])
    rows = []
    for f, v in r["fields"].items():
        n = v["records"]
        rows.append((f, "yes" if v["dbc_signal"] else "no", _p(v["missing"], n), _p(v["zero"], n),
                     _n(v["outside_encodable"]), _n(v["all_ones"])))
    out += [f"## {T['fields']}", ""] + _table(["field", "DBC signal", "missing", "zero", "outside encodable", "all-ones code"], rows)
    if "stuck_jump" in r:
        rows = []
        for f, v in r["stuck_jump"].items():
            n = r["fields"][f]["records"]
            st, j = v["stuck"], v["jump"]
            keys = sorted(st, key=lambda k: int(k[:-1]))
            rows.append((f, *[_p(st[k]["records_nonzero"], n) for k in keys], *[_p(st[k]["records_zero"], n) for k in keys],
                         _n(j.get("pairs", 0)), _n(j.get("jumps", 0)), _p(j.get("jumps", 0), j.get("pairs", 0))))
        keys = sorted(next(iter(r["stuck_jump"].values()))["stuck"], key=lambda k: int(k[:-1]))
        out += [f"## {T['sj']}", "",
                "constant run = equal consecutive values inside one segment; jump = step > share of the device's "
                "value range (p99.5 - p0.5) between records <= 60 s apart.", ""]
        out += _table(["field", *[f"non-zero run >= {k}" for k in keys], *[f"zero run >= {k}" for k in keys],
                       "pairs", "jumps", "jump share"], rows)
    rows = [(f"{k}", _n(v.get("tested", 0)), _n(v.get("violated", v.get("decreasing", 0))))
            for k, v in {**r["relations"], **r["counters"]}.items()]
    out += [f"## {T['rel']}", ""] + _table(["check", "tested", "violated / decreasing"], rows)
    rows = []
    for f, v in r["log_comparison"].items():
        both = v.get("equal", 0) + v.get("differ", 0)
        rows.append((f, _n(v.get("equal", 0)), _n(v.get("differ", 0)), _p(v.get("differ", 0), both),
                     _n(v.get("stored_only", 0)), _n(v.get("log_only", 0)), _n(v.get("both_missing", 0))))
    out += [f"## {T['cmp']}", ""] + _table(["field", "equal", "differ", "differ share", "stored only", "log only",
                                            "both missing"], rows)
    if "correction" in r:
        c, v = r["correction"], r["correction"]["verification"]
        out += [f"## {T['corr']}", ""] + _table(["item", "value"], [
            ("stored records / reconstructed / not reconstructable",
             f"{_n(c['stored'])} / {_n(c['reconstructed'])} / {_n(c['not_reconstructable'])}"),
            ("basis", ", ".join(f"{k} {_n(x)}" for k, x in c["basis"].items())),
            ("linked records compared with the log", _n(v["compared"])),
            ("corrected = log (both fields)", f"{_n(v['corrected_equal'])} ({_p(v['corrected_equal'], v['compared'])})"),
            ("stored = log, HyBottleP", f"{_n(v['stored_equal_HyBottleP'])} ({_p(v['stored_equal_HyBottleP'], v['compared'])})"),
            ("stored = log, HighPPMNum", f"{_n(v['stored_equal_HighPPMNum'])} ({_p(v['stored_equal_HighPPMNum'], v['compared'])})")])
        bins = c["step_bins"]
        labels = [f"≤{bins[i + 1]}" for i in range(len(bins) - 1)]
        rows = []
        for k, x in sorted(c["continuity"].items()):
            for which in ("stored", "corrected"):
                h = x.get(which)
                if not h:
                    continue
                up, down = x.get(which + "_large_steps_up_down", [0, 0])
                rows.append((k, which, _n(sum(h)), *[_p(a, sum(h)) for a in h], f"{_n(up)}/{_n(down)}"))
        out += [f"## {T['cont']}", ""] + _table(["class", "values", "pairs", *labels, "|step|>5 up/down"], rows)
    g = r["gps"]
    out += [f"## {T['gps']}", ""] + _table(["item", "value"], [
        ("fixes (stored / additions)", f"{_n(g['fixes'])} ({_n(g['stored'])} / {_n(g['additions'])})"),
        *[(f"class {k}", _n(v)) for k, v in g["coordinate_class"].items()],
        ("hemisphere letters invalid", _n(g["hemisphere_bad"])), ("outside region box", _n(g["outside_region"])),
        ("implied speed tested / implausible", f"{_n(g['speed']['tested'])} / {_n(g['speed']['implausible'])} "
                                               f"({_p(g['speed']['implausible'], g['speed']['tested'])})"),
        ("same-time rows", _n(g["same_time_rows"])), ("device clock suspect (stored, linked)", _n(g["device_clock_suspect"]))])
    if questions:
        out += [f"## {T['q']}", ""] + [f"{i}. {q}" for i, q in enumerate(questions, 1)] + [""]
    return "\n".join(out)
