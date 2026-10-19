"""Command line: `python -m hfcev [--local configs/local.toml] <command>`.

    run <stage> [--devices A,B] [--workers N] [--force]   process devices of a stage
    status                                               devices finished per stage
    verify <stage> --reference FILE                      compare the stage manifest with a reference
    inventory <layer> [--expect FILE]                    hash a raw input layer into <layer>/_manifest.json
    report s3|s4|s5 [--frozen FILE] [--reference FILE]   aggregate review of a stage (JSON + Markdown)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import config, stages
from .io import Manifest, read_json, write_json
from .runner import fingerprint, run_stage
from .sources import build_file_inventory
from .validation import s3_report, s4_report, s5_report
from .validation.reference import compare, compare_files


def _run(cfg, args) -> int:
    stage = stages.get(args.stage)
    devices = args.devices.split(",") if args.devices else None
    options = {"files": args.files} if args.files else {}
    summary = run_stage(stage, cfg, devices=devices, workers=args.workers, force=args.force, options=options)
    return 1 if summary["failed"] else 0


def _status(cfg, args) -> int:
    for stage in stages.STAGES.values():
        manifest = Manifest(cfg.layer(stage.LAYER) / "_manifest.json", stage.NAME)
        fp = fingerprint(stage, cfg)
        current = sum(all(r["fingerprint"].get(k) == v for k, v in fp.items()) for r in manifest.devices.values())
        print(f"{stage.NAME} {stage.LAYER}: {len(manifest.devices)} devices recorded, "
              f"{current} produced by the current parameters and code")
    return 0


def _verify(cfg, args) -> int:
    stage = stages.get(args.stage)
    manifest = Manifest(cfg.layer(stage.LAYER) / "_manifest.json", stage.NAME)
    reference = read_json(args.reference)
    report = compare(manifest.devices, reference)
    report["reference"] = str(args.reference)
    if "files" in reference:
        layer = cfg.layer(stage.LAYER)
        tasks = read_json(layer / "_pass1_summary.json", {}).get("tasks", [])
        receipts = [read_json(layer / "_pass1" / t / "receipt.json") for t in tasks]
        report["files_check"] = compare_files(receipts, reference["files"])
        report["files_check"]["mismatches"] = report["files_check"]["mismatches"][:20]
    out = cfg.work / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}_verify_{stage.NAME}" / "report.json"
    write_json(out, report)
    shown = {k: v for k, v in report.items() if k != "mismatches"}
    shown["mismatches"] = report["mismatches"][:20]
    print(json.dumps(shown, indent=1))
    print(f"{'PASS' if report['ok'] else 'FAIL'}: {len(report['mismatches'])} mismatches; report {out}")
    return 0 if report["ok"] else 1


def _inventory(cfg, args) -> int:
    root = cfg.layer(args.layer)
    expected = read_json(args.expect) if args.expect else None
    records = build_file_inventory(root, workers=args.workers or cfg.workers, expected=expected)
    write_json(root / "_manifest.json", records)
    print(f"{len(records)} files, {sum(r['bytes'] for r in records):,} bytes"
          f"{', all matching ' + str(args.expect) if expected else ''}; wrote {root / '_manifest.json'}")
    return 0


def _report(cfg, args) -> int:
    stage = stages.get(args.stage)
    if stage.NAME not in ("s3", "s4", "s5"):
        raise SystemExit("reports exist for s3, s4 and s5")
    layer = cfg.layer(stage.LAYER)
    manifest = Manifest(layer / "_manifest.json", stage.NAME)
    questions = [q.strip() for q in args.questions.read_text().splitlines() if q.strip()] if args.questions else None
    out = cfg.work / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}_report_{stage.NAME}"
    if stage.NAME == "s5":
        report, monthly = s5_report.build(manifest.devices, layer)
        write_json(out / "report.json", report)
        monthly.to_csv(out / "device_month.csv", index=False)
        (out / f"summary.{args.lang}.md").write_text(s5_report.markdown(report, args.lang, questions), encoding="utf-8")
        print(f"report {out}")
        return 0
    if stage.NAME == "s4":
        report = s4_report.build(manifest.devices)
        write_json(out / "report.json", report)
        (out / f"summary.{args.lang}.md").write_text(s4_report.markdown(report, args.lang, questions), encoding="utf-8")
        print(f"report {out}")
        return 0
    report = s3_report.build(manifest.devices, frozen=read_json(args.frozen) if args.frozen else None,
                             reference=read_json(args.reference) if args.reference else None, layer=layer)
    report.update(frozen_path=str(args.frozen) if args.frozen else None,
                  reference_path=str(args.reference) if args.reference else None)
    write_json(out / "report.json", report)
    (out / f"summary.{args.lang}.md").write_text(s3_report.markdown(report, args.lang, questions), encoding="utf-8")
    print(f"report {out}")
    return 0 if report.get("regression", {}).get("ok", True) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hfcev", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", type=Path, help="release parameters (default configs/release_v1.toml)")
    ap.add_argument("--local", type=Path, help="machine settings (default configs/local.toml)")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("stage")
    run.add_argument("--devices")
    run.add_argument("--workers", type=int)
    run.add_argument("--force", action="store_true")
    run.add_argument("--files", type=int, help="s2 calibration: process only N evenly spaced log files")
    sub.add_parser("status")
    verify = sub.add_parser("verify")
    verify.add_argument("stage")
    verify.add_argument("--reference", type=Path, required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("layer")
    inventory.add_argument("--expect", type=Path, help="expected [{relative_path, bytes, sha256}] listing")
    inventory.add_argument("--workers", type=int)
    report = sub.add_parser("report")
    report.add_argument("stage")
    report.add_argument("--frozen", type=Path, help="frozen per-device outcomes for side-by-side comparison")
    report.add_argument("--reference", type=Path, help="exact regression targets (as for verify)")
    report.add_argument("--questions", type=Path, help="text file, one open question per line, appended to the summary")
    report.add_argument("--lang", choices=sorted(s3_report.TEXT), default="en")
    args = ap.parse_args(argv)
    cfg = config.load(args.release, args.local)
    return {"run": _run, "status": _status, "verify": _verify, "inventory": _inventory, "report": _report}[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
