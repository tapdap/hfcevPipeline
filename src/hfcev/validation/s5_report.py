"""Aggregate review of stage 5: GPS fix status (proxy checked against `result`), stuck GPS,
track vs odometer distance, odometer intervals, CAN-GPS alignment and its clock sensitivity,
fuel-cell runs, trips, refuelling and the device x month statistics layer.
Inputs: the stage-5 manifest (one record per device) and the trips/monthly tables.
Output: a JSON report and a Markdown summary (English or Chinese).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .s4_report import _add, _n, _p, _table


def _trips(layer: Path, devices: list[str], min_km: float, min_cover: float) -> dict:
    """GPS / odometer distance ratio over moving trips well covered by the GPS track."""
    ratios, per_device = [], {}
    for d in devices:
        f = layer / "trips" / f"{d}.parquet"
        if not f.exists():
            continue
        t = pd.read_parquet(f)
        if "km_qualified" not in t:
            continue
        km, gps = t["km_qualified"].sum(), t["gps_km"].sum()
        per_device[d] = dict(trips=len(t), km=float(km), gps_km=float(gps), share=float(gps / km) if km else None)
        good = t[(t["km_qualified"] >= min_km) & (t["gps_covered_s"] >= min_cover * t["active_s"])]
        ratios.append((good["gps_km"] / good["km_qualified"]).to_numpy())
    r = np.concatenate(ratios) if ratios else np.array([])
    return dict(well_covered_trips=int(len(r)),
                ratio=dict(zip(["p10", "p25", "p50", "p75", "p90"], np.percentile(r, [10, 25, 50, 75, 90]).round(4).tolist()))
                if len(r) else {}, per_device=per_device)


def _monthly(layer: Path, devices: list[str]) -> pd.DataFrame:
    frames = []
    for d in devices:
        f = layer / "monthly" / f"{d}.parquet"
        if f.exists():
            m = pd.read_parquet(f)
            if "month" in m:
                frames.append(m.assign(device=d))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build(manifest_devices: dict, layer: Path, device_share_min: float = 0.3, implausible_min: float = 0.1,
          min_km: float = 5, min_cover: float = 0.9) -> tuple[dict, pd.DataFrame]:
    devices = sorted(manifest_devices)
    records = [manifest_devices[d] for d in devices]
    total = {}
    for r in records:
        a = r["alignment"]
        _add(total, dict(records=r["records"], gps_fixes=r["gps_fixes"], fix_status=r["fix_status"],
                         fix_source=r["fix_source"], proxy_check=r["proxy_check"], gps_track=r["gps_track"],
                         odometer=r["odometer"], fc_runs=r["fc_runs"], trips=r["trips"],
                         refuels={k: v for k, v in r["refuels"].items() if k != "rise_hist"},
                         alignment=dict(main=a["main"], unmatched=a["unmatched"], grid=a["grid"],
                                        clocks={k: v for k, v in a["clocks"].items() if k != "chosen_fix_distance_km"})))
        if r["refuels"].get("rise_hist"):
            _add(total.setdefault("refuels", {}), dict(rise_hist=r["refuels"]["rise_hist"]))
    for v in [total["alignment"]["main"], *total["alignment"]["grid"].values(), *total["alignment"]["clocks"].values()]:
        v["share"] = round(v["hit"] / v["n"], 6) if v.get("n") else None
    shares = np.array([r["alignment"]["main"]["share"] or 0 for r in records])
    total["alignment"]["device_share"] = dict(zip(["min", "p25", "p50", "p75", "max"],
                                                  np.percentile(shares, [0, 25, 50, 75, 100]).round(4).tolist())) if len(shares) else {}
    dist = [r["alignment"]["clocks"].get("chosen_fix_distance_km", {}).get("p90") for r in records]
    dist = np.array([x for x in dist if x is not None])
    total["alignment"]["device_p90_distance_km"] = dict(zip(["p50", "p90", "max"], np.percentile(dist, [50, 90, 100]).round(4).tolist())) if len(dist) else {}
    total["trip_distance"] = _trips(layer, devices, min_km, min_cover)
    dc = {}
    for which in ("delayed", "control"):
        parts = [r["delayed_check"] if which == "delayed" else r["delayed_check"]["control"]
                 for r in records if r.get("delayed_check", {}).get("with_gps", 0) >= 50]
        dc[which] = dict(devices=len(parts), increments=sum(x["n"] for x in parts), with_gps=sum(x["with_gps"] for x in parts),
                         gap_over_120s=sum(x["gap_over_120s"] for x in parts),
                         device_median_gap_s=float(np.median([x["gap_s"]["p50"] for x in parts])) if parts else None,
                         device_median_displacement_to_odometer=float(np.median([x["displacement_to_odometer"]["p50"] for x in parts]))
                         if parts else None)
    total["delayed_check"] = dc
    hy = [(d, r["hydrogen"]) for d, r in zip(devices, records) if r.get("hydrogen")]
    if hy:
        keys = ("wraps", "wraps_inferred", "backward", "kg_attributed", "km_on_those_days", "kg_on_those_days", "kwh_on_those_days")
        agg = {k: sum(h[k] for _, h in hy) for k in keys}
        per100 = np.array([h["kg_per_100km"] for _, h in hy if h["kg_per_100km"] is not None and h["km_on_those_days"] >= 1000])
        perkwh = np.array([h["kg_per_kwh_raw_units"] for _, h in hy if h["kg_per_kwh_raw_units"] is not None and h["kwh_on_those_days"] >= 1000])
        q = lambda a: dict(zip(["p10", "p50", "p90"], np.percentile(a, [10, 50, 90]).round(4).tolist())) if len(a) else {}
        total["hydrogen"] = dict(devices=len(hy), **agg,
                                 fleet_kg_per_100km=round(agg["kg_on_those_days"] / agg["km_on_those_days"] * 100, 3) if agg["km_on_those_days"] else None,
                                 fleet_kg_per_kwh=round(agg["kg_on_those_days"] / agg["kwh_on_those_days"], 5) if agg["kwh_on_those_days"] else None,
                                 device_kg_per_100km=q(per100), device_kg_per_kwh=q(perkwh),
                                 kg_per_kwh_outside_0045_0085=sorted(d[-4:] for d, h in hy if h["kg_per_kwh_raw_units"] is not None
                                                                     and h["kwh_on_those_days"] >= 1000
                                                                     and not 0.045 <= h["kg_per_kwh_raw_units"] <= 0.085))
    # device-level GPS condition (candidates for a device note in stage 6)
    gps_devices = {}
    for d, r in zip(devices, records):
        n, fs, g = max(r["gps_fixes"], 1), r["fix_status"], r["gps_track"]
        shares = dict(no_fix=(fs.get("no_fix", 0) + fs.get("no_fix_inferred", 0)) / n,
                      stuck=fs.get("static_while_moving", 0) / n,
                      implausible_steps=g["implausible"] / max(g["steps"], 1))
        limits = dict(no_fix=device_share_min, stuck=device_share_min, implausible_steps=implausible_min)
        hit = [k for k, v in shares.items() if v >= limits[k]]
        if hit:
            gps_devices[d] = dict(classes=hit, **{k: round(v, 4) for k, v in shares.items()},
                                  usable=round(g["usable"] / n, 4))
    total["gps_device_conditions"] = gps_devices
    m = _monthly(layer, devices)
    if len(m):
        f = m.groupby("month")[["active_s", "km_qualified", "km_delayed", "gps_fixes", "gps_km"]].sum()
        total["fleet_month"] = {k: dict(active_h=round(v.active_s / 3600, 1), km=v.km_qualified, km_delayed=v.km_delayed,
                                        gps_fixes_per_active_h=round(v.gps_fixes / (v.active_s / 3600), 1) if v.active_s else None,
                                        gps_to_odometer=round(v.gps_km / v.km_qualified, 4) if v.km_qualified else None)
                                for k, v in f.iterrows()}
    total["device_months"] = dict(rows=int(len(m)), devices=int(m["device"].nunique()) if len(m) else 0,
                                  months=sorted(m["month"].unique().tolist()) if len(m) else [],
                                  moving_rows=int((m["km_qualified"] > 0).sum()) if len(m) else 0)
    total["devices"] = len(records)
    total["fingerprints"] = sorted({str(r.get("fingerprint")) for r in records})
    return total, m


TEXT = {
    "en": dict(title="Stage 5 review (derived quantities)", fix="GPS fix status", track="GPS track and odometer distance",
               odo="Odometer intervals", al="CAN-GPS alignment", clk="Clock sensitivity (rows with a device time)",
               ev="Fuel-cell runs, trips, refuelling", h2="Hydrogen counter (TotH2Kg) restoration", dly="Delayed delivery: GPS evidence for in_segment_delayed odometer increments", fm="Fleet by month (odometer, delayed delivery, GPS density)", mon="Statistics layer (device x month)", q="Open questions"),
    "zh": dict(title="Stage 5 审阅报告（派生量）", fix="GPS 定位状态", track="GPS 轨迹与里程距离", odo="里程区间",
               al="CAN–GPS 配对", clk="时钟敏感性（有设备时间的记录）", ev="燃料电池运行段、出车段、加氢", h2="氢耗计数器（TotH2Kg）恢复", dly="延迟送达：in_segment_delayed 里程增量的 GPS 佐证", fm="按月全车队（里程、延迟送达、GPS 密度）",
               mon="统计层（设备 × 月）", q="待你确认的问题"),
}


def markdown(r: dict, lang: str = "zh", questions: list[str] | None = None) -> str:
    T = TEXT[lang]
    out = [f"# {T['title']}", "", f"devices = {r['devices']}; fingerprints = {', '.join(r['fingerprints'])}", ""]
    fs, pc = r["fix_status"], r["proxy_check"]
    out += [f"## {T['fix']}", ""] + _table(["item", "value"], [
        ("GPS fixes", _n(r["gps_fixes"])),
        *[(f"status {k}", f"{_n(v)} ({_p(v, r['gps_fixes'])})") for k, v in sorted(fs.items())],
        *[(f"source {k}", _n(v)) for k, v in sorted(r["fix_source"].items())],
        ("fixes with known result (result = 0)", f"{_n(pc['known'])} ({_n(pc['result0'])})"),
        ("proxy no-fix on result = 1 (false positives)", f"{_n(pc['proxy_on_result1'])} ({_p(pc['proxy_on_result1'], pc['known'] - pc['result0'])})"),
        ("proxy no-fix on result = 0 / result = 0 repeating a position",
         f"{_n(pc['proxy_on_result0'])} / {_n(pc['result0_repeating'])}"),
        ("devices with a GPS condition (no fix / stuck >= 30 %, implausible steps >= 10 %)",
         str(len(r["gps_device_conditions"])))])
    rows = [(d[-4:], ", ".join(v["classes"]), _p(v["no_fix"], 1), _p(v["stuck"], 1), _p(v["implausible_steps"], 1), _p(v["usable"], 1))
            for d, v in r["gps_device_conditions"].items()]
    out += _table(["device", "condition", "no fix", "stuck", "implausible steps", "usable"], rows) if rows else []
    g, td = r["gps_track"], r["trip_distance"]
    out += [f"## {T['track']}", ""] + _table(["item", "value"], [
        ("usable fixes / steps", f"{_n(g['usable'])} / {_n(g['steps'])}"),
        ("steps above GPS speed limit (excluded)", f"{_n(g['implausible'])} ({_p(g['implausible'], g['steps'])})"),
        ("GPS track km / trip odometer km", f"{_n(g['km'])} / {_n(r['trips']['km_qualified'])}"),
        ("well-covered moving trips (>= 5 km, GPS >= 90 % of active time)", _n(td["well_covered_trips"])),
        ("GPS / odometer ratio p10 / p25 / p50 / p75 / p90", " / ".join(f"{x:.3f}" for x in td["ratio"].values()))])
    o = r["odometer"]
    kinds = ["in_segment", "in_segment_delayed", "across_gap", "across_long_gap", "jump", "backward"]
    out += [f"## {T['odo']}", ""] + _table(["kind", "increments", "km"], [
        (k, _n(o.get(k, 0)), _n(o.get(f"km_{k}", "—")) if f"km_{k}" in o else "—") for k in kinds])
    dc = r["delayed_check"]
    out += [f"## {T['dly']}", ""] + _table(["item", "in_segment_delayed", "control (in_segment, +1 km)"], [
        ("devices (>= 50 increments with GPS)", _n(dc["delayed"]["devices"]), _n(dc["control"]["devices"])),
        ("increments with GPS nearby", _n(dc["delayed"]["with_gps"]), _n(dc["control"]["with_gps"])),
        ("longest GPS gap, median over devices (s)", _n(dc["delayed"]["device_median_gap_s"] or 0), _n(dc["control"]["device_median_gap_s"] or 0)),
        ("longest GPS gap > 120 s", _p(dc["delayed"]["gap_over_120s"], dc["delayed"]["with_gps"]), _p(dc["control"]["gap_over_120s"], dc["control"]["with_gps"])),
        ("GPS displacement across the gap / odometer increment, median over devices",
         f"{dc['delayed']['device_median_displacement_to_odometer'] or 0:.3f}", f"{dc['control']['device_median_displacement_to_odometer'] or 0:.3f}")])
    if r.get("hydrogen"):
        h = r["hydrogen"]
        out += [f"## {T['h2']}", ""] + _table(["item", "value"], [
            ("devices with TotH2Kg", _n(h["devices"])),
            ("counter wraps detected / inferred from odometer / backward steps", f"{_n(h['wraps'])} / {_n(h['wraps_inferred'])} / {_n(h['backward'])}"),
            ("hydrogen attributed to days and trips (kg)", _n(h["kg_attributed"])),
            ("days with distance and consumption: km / kg / kWh", f"{_n(h['km_on_those_days'])} / {_n(h['kg_on_those_days'])} / {_n(h['kwh_on_those_days'])}"),
            ("fleet kg per 100 km (ratio of sums)", _n(h["fleet_kg_per_100km"])),
            ("fleet kg per kWh FcVol x FcCur (0.0300 kg/kWh = 100 % LHV)", f"{h['fleet_kg_per_kwh']:.4f}" if h["fleet_kg_per_kwh"] else "—"),
            ("per-device kg/100 km p10 / p50 / p90 (>= 1000 km)", " / ".join(f"{x:.2f}" for x in h["device_kg_per_100km"].values())),
            ("per-device kg/kWh p10 / p50 / p90 (>= 1000 kWh)", " / ".join(f"{x:.4f}" for x in h["device_kg_per_kwh"].values())),
            ("devices with kg/kWh outside 0.045-0.085", ", ".join(h["kg_per_kwh_outside_0045_0085"]) or "none")])
    a = r["alignment"]
    tol = sorted({int(k.split(":")[1][:-1]) for k in a["grid"]})
    out += [f"## {T['al']}", ""] + _table(["item", "value"], [
        ("records aligned (10 s, nearest)", f"{_n(a['main']['hit'])} / {_n(a['main']['n'])} ({_p(a['main']['hit'], a['main']['n'])})"),
        *[(f"unmatched: {k}", _n(v)) for k, v in a["unmatched"].items()],
        ("per-device share min / p25 / p50 / p75 / max", " / ".join(f"{x:.3f}" for x in a["device_share"].values())),
        *[(f"{mode} tolerance", " / ".join(f"{t}s {a['grid'][f'{mode}:{t}s']['share']:.3f}" for t in tol))
          for mode in ("nearest", "backward")]])
    c = a["clocks"]
    out += [f"## {T['clk']}", ""] + _table(["item", "value"], [
        ("rows with a device time", _n(c["device_vs_device"]["n"])),
        ("aligned on device clock / on receipt clock", f"{c['device_vs_device']['share']:.3f} / {c['receipt_vs_device_on_same_rows']['share']:.3f}"),
        ("same fix chosen by both clocks", f"{c['same_fix_chosen']['share']:.3f}"),
        ("per-device p90 distance between chosen fixes (km) p50 / p90 / max",
         " / ".join(f"{x:.3f}" for x in a["device_p90_distance_km"].values())),
        ("GPS receipt clock vs CAN receipt clock", f"{c['receipt_vs_receipt']['share']:.3f}")])
    f, t, rf = r["fc_runs"], r["trips"], r["refuels"]
    bins = ["<1", "1-2", "2-5", "5-10", "10-15", "15-20", "20-30", ">=30"]
    out += [f"## {T['ev']}", ""] + _table(["item", "value"], [
        ("fuel-cell runs / hours on", f"{_n(f['count'])} / {_n(f['on_s'] / 3600)}"),
        ("energy FcVol x FcCur (kWh if V and A)", _n(f["energy_kwh_raw_units"])),
        ("mean power while on (kW if V and A)", _n(f["energy_kwh_raw_units"] / (f["on_s"] / 3600)) if f["on_s"] else "—"),
        ("trips (moving) / segments", f"{_n(t['count'])} ({_n(t['moving'])}) / {_n(t['segments'])}"),
        ("trips spanning > 24 h", _n(t["span_over_24h"])), ("active hours", _n(t["active_h"])),
        ("refuels (across segments)", f"{_n(rf['count'])} ({_n(rf['across_segments'])})"),
        ("pressure rise histogram " + " ".join(bins), " ".join(_n(x) for x in rf.get("rise_hist", [])))])
    if r.get("fleet_month"):
        rows = [(k, _n(v["active_h"]), _n(v["km"]), _p(v["km_delayed"], v["km"]) if v["km"] else "—",
                 _n(v["gps_fixes_per_active_h"]) if v["gps_fixes_per_active_h"] else "—",
                 f"{v['gps_to_odometer']:.2f}" if v["gps_to_odometer"] else "—") for k, v in r["fleet_month"].items()]
        out += [f"## {T['fm']}", ""] + _table(["month", "active h", "odometer km", "delayed share", "GPS fixes / active h",
                                               "GPS / odometer"], rows)
    dm = r["device_months"]
    out += [f"## {T['mon']}", ""] + _table(["item", "value"], [
        ("device-month rows (devices)", f"{_n(dm['rows'])} ({_n(dm['devices'])})"),
        ("months", f"{dm['months'][0]} … {dm['months'][-1]} ({len(dm['months'])})" if dm["months"] else "—"),
        ("rows with odometer distance", _n(dm["moving_rows"]))])
    if questions:
        out += [f"## {T['q']}", ""] + [f"{i}. {q}" for i, q in enumerate(questions, 1)] + [""]
    return "\n".join(out)
