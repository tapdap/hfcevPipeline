"""Generate the synthetic inputs in sample_data/ (three fictitious devices).

Every record is invented.
L0_raw/   database export. Rows exercise each stage-1 rule once: window limits, exact
          duplicates within and across files, legacy records that match or conflict
          with CAN, same-time variants, a month boundary, an all-null string column,
          mixed-case IDs, and 1/100-scaled GPS on an audited date.
L0_logs/  receive logs, protocol/sample.dbc a two-message CAN protocol. Blocks exercise
          each stage-2 rule once: decoded, unknown and fault frames, conflicting signal
          values, topic/sn mismatch, invalid sn, truncated JSON, a cross-file fragment,
          invalid frame bytes, a malformed GPS payload, a missing time and missing data.
          Device 3 adds the stage-3 scenarios for operation records and GPS fixes.
Device 3  stage 3: stored records and log messages that link by content (one pair of
          same-time records ordered by both log clocks), log-only messages added with a
          verified device clock, with a reset device clock and with no next neighbour,
          a message of poor native quality and one without core content.

    python sample_data/make_sample.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROOT = HERE / "L0_raw"
LOGS = HERE / "L0_logs"
PROTOCOL = HERE / "protocol"
D1, D2, D3, OTHER = "QLKJ9999999990001", "QLKJ9999999990002", "QLKJ9999999990003", "QLKJ9999999990009"
DUMP = datetime(2026, 1, 1)

CAN = pa.schema([("id", pa.int64()), ("sn", pa.string()), ("time", pa.timestamp("us")),
                 ("main_state", pa.int64()), ("FcVol", pa.float64()), ("FcCur", pa.float64()),
                 ("SOC", pa.float64()), ("MaxCellV", pa.float64()), ("MinCellV", pa.float64()), ("TotKM", pa.float64()), ("ActPower", pa.float64()), ("Con", pa.float64()),
                 ("CoolOutTarTemp", pa.float64()), ("veh_spd", pa.float64()),
                 ("_src_table", pa.string()), ("_src_db", pa.string()), ("_dump_ts", pa.timestamp("us"))])
LEGACY = pa.schema([f for f in CAN if f.name != "veh_spd"])
GPS = pa.schema([("id", pa.int64()), ("sn", pa.string()), ("time", pa.timestamp("us")),
                 ("LAT", pa.float64()), ("LATD", pa.string()), ("LON", pa.float64()), ("LOND", pa.string()),
                 ("_src_table", pa.string()), ("_src_db", pa.string()), ("_dump_ts", pa.timestamp("us"))])


def at(base: datetime, seconds: int) -> datetime:
    return base + timedelta(seconds=seconds)


def can_row(i, sn, t, vol, table, spd=40.0, cur=100.0, km=1000.0, cells=None):
    soc, vmax, vmin = cells or (None, None, None)
    return dict(id=i, sn=sn, time=t, main_state=2, FcVol=vol, FcCur=cur, SOC=soc, MaxCellV=vmax, MinCellV=vmin,
                TotKM=km, ActPower=50.0, Con=1.0, CoolOutTarTemp=65.0, veh_spd=spd, _src_table=table,
                _src_db="sample", _dump_ts=DUMP)


def gps_row(i, sn, t, lat, lon, table, latd="N", lond="E"):
    return dict(id=i, sn=sn, time=t, LAT=lat, LATD=latd, LON=lon, LOND=lond,
                _src_table=table, _src_db="sample", _dump_ts=DUMP)


B3 = datetime(2024, 5, 8, 10, 0, 0)          # device 3 (stage-3 scenarios)


def tables() -> dict[str, pa.Table]:
    b1 = datetime(2024, 5, 7, 8, 0, 0)       # an audited GPS-scale date
    b2 = datetime(2024, 5, 31, 23, 59, 50)   # straddles the May/June boundary
    t1 = "z_qlkj9999999990001"
    d1 = [
        can_row(1, D1, at(b1, 0), 600.0, t1),
        can_row(2, D1, at(b1, 5), 601.0, t1),
        can_row(3, D1, at(b1, 5), 601.0, t1),                    # exact duplicate of id 2
        can_row(4, D1, at(b1, 10), 602.0, t1),
        can_row(5, f" {D1.lower()} ", at(b1, 10), 650.0, t1),    # same-time variant, messy ID
        can_row(6, D1, at(b1, 15), 603.0, t1),
        can_row(7, D1, datetime(2024, 3, 31, 23, 59, 55), 1.0, t1),  # before window
        can_row(8, D1, datetime(2026, 6, 1, 0, 0, 0), 1.0, t1),      # after window (exclusive end)
    ]
    t2 = "z_qlkj9999999990002"
    d2_part1 = [can_row(1, D2, at(b2, s), 500.0 + s, t2 + "_part1", spd=None) for s in (0, 5, 10)]
    d2_part2 = [can_row(1, D2, at(b2, 10), 510.0, t2 + "_part2", spd=None),   # duplicate across parts
                can_row(2, D2, at(b2, 15), 515.0, t2 + "_part2", spd=None)]
    part2 = pa.Table.from_pylist(d2_part2, CAN)
    # A column the dump typed as string because it is empty in this table.
    part2 = part2.set_column(part2.schema.get_field_index("veh_spd"), "veh_spd", pa.nulls(len(part2), pa.string()))

    run = "run_parameters"
    legacy = [
        {k: v for k, v in can_row(10, D1, at(b1, 15), 603.0, run).items() if k != "veh_spd"},  # matches CAN id 6
        {k: v for k, v in can_row(11, D1, at(b1, 0), 599.0, run).items() if k != "veh_spd"},   # conflicts with CAN id 1
        {k: v for k, v in can_row(12, D1, at(b1, 20), 604.0, run).items() if k != "veh_spd"},  # only in legacy
        {k: v for k, v in can_row(13, D1, at(b1, 20), 604.0, run).items() if k != "veh_spd"},  # duplicate of id 12
        {k: v for k, v in can_row(14, D2, at(b2, 20), 520.0, run).items() if k != "veh_spd"},  # only in legacy
        {k: v for k, v in can_row(15, OTHER, at(b1, 0), 1.0, run).items() if k != "veh_spd"},  # not in cohort
    ]

    # Device 3: fully observed core content (FcVol, FcCur, SOC, TotKM, MaxCellV, MinCellV).
    t3 = "z_qlkj9999999990003"
    d3 = [can_row(i, D3, at(B3, s), vol, t3, cur=50.0, km=km, cells=(80.0, 0.8, 0.7))
          for i, (s, vol, km) in enumerate([(0, 400.0, 2000.0), (5, 405.0, 2000.1), (10, 410.0, 2000.2),
                                            (10, 411.0, 2000.3), (30, 430.0, 2000.6)], start=1)]
    g3 = "z_gps_QLKJ9999999990003"
    gps3 = [gps_row(i, D3, at(B3, 5 * i), 3640.0 + i, 11240.0 + i, g3) for i in range(2)]

    g1 = "z_gps_QLKJ9999999990001"
    gps1 = [
        gps_row(1, D1, at(b1, 0), 3630.1234, 11112.5678, g1),
        gps_row(2, D1, at(b1, 0), 3630.1234, 11112.5678, g1),   # exact duplicate
        gps_row(3, D1, at(b1, 5), 36.501, 111.209, g1),         # stored at 1/100 scale
        gps_row(4, D1, at(b1, 10), 0.0, 0.0, g1),               # zero pair, left as is
        gps_row(5, D1, at(b1, 15), 3630.2, 11112.6, g1),
        gps_row(6, D1, at(b1, 15), 3630.3, 11112.7, g1),        # same-time variant
        gps_row(7, D1, datetime(2024, 3, 1), 3630.0, 11112.0, g1),  # before window
    ]
    g2 = "z_gps_QLKJ9999999990002"
    gps2 = [gps_row(i, D2, at(b2, 5 * i), 3740.0 + i, 11230.0 + i, g2) for i in range(3)]
    return {
        f"can/{t1}.parquet": pa.Table.from_pylist(d1, CAN),
        f"can/{t2}_part1.parquet": pa.Table.from_pylist(d2_part1, CAN),
        f"can/{t2}_part2.parquet": part2,
        f"can_legacy/{run}.parquet": pa.Table.from_pylist(legacy, LEGACY),
        f"gps/{g1}.parquet": pa.Table.from_pylist(gps1, GPS),
        f"gps/{g2}.parquet": pa.Table.from_pylist(gps2, GPS),
        f"can/{t3}.parquet": pa.Table.from_pylist(d3, CAN),
        f"gps/{g3}.parquet": pa.Table.from_pylist(gps3, GPS),
    }


FC_ID, VCU_ID, CELL_ID, UNKNOWN_ID = 0x18F00101, 0x18F00202, 0x18F00303, 0x123
DBC = f"""VERSION ""

BO_ {FC_ID | 0x80000000} FC_STATUS: 8 Vector__XXX
 SG_ FcVol : 0|16@1+ (0.1,0) [0|6553.5] "V" Vector__XXX
 SG_ FcCur : 16|16@1+ (0.1,-100) [-100|6453.5] "A" Vector__XXX
 SG_ SOC : 32|8@1+ (0.4,0) [0|100] "%" Vector__XXX

BO_ {VCU_ID | 0x80000000} VCU_STATUS: 8 Vector__XXX
 SG_ veh_spd : 0|16@1+ (0.01,0) [0|655.35] "km/h" Vector__XXX
 SG_ TotKM : 16|32@1+ (0.1,0) [0|429496729.5] "km" Vector__XXX
 SG_ CabinTemp : 48|8@1- (1,0) [-128|127] "degC" Vector__XXX

BO_ {CELL_ID | 0x80000000} CELL_STATUS: 8 Vector__XXX
 SG_ MaxCellV : 0|16@1+ (0.001,0) [0|65.535] "V" Vector__XXX
 SG_ MinCellV : 16|16@1+ (0.001,0) [0|65.535] "V" Vector__XXX

BO_ 3221225472 VECTOR__INDEPENDENT_SIG_MSG: 0 Vector__XXX
 SG_ Unused : 0|8@1+ (1,0) [0|255] "" Vector__XXX
"""
FC = [112, 23, 220, 5, 200, 0, 0, 0]        # FcVol 600.0 V, FcCur 50.0 A, SOC 80 %
FC2 = [122, 23, 220, 5, 200, 0, 0, 0]       # FcVol 601.0 V
VCU = [160, 15, 16, 39, 0, 0, 246, 0]       # veh_spd 40 km/h, TotKM 1000.0 km, CabinTemp -10
CELL = [32, 3, 188, 2, 0, 0, 0, 0]         # MaxCellV 0.800 V, MinCellV 0.700 V
THREAD = "MQTT Call: sample"


def fc(vol_raw: int) -> list[int]:           # FcVol vol_raw/10 V, FcCur 50.0 A, SOC 80 %
    return [vol_raw & 255, vol_raw >> 8, 220, 5, 200, 0, 0, 0]


def vcu(km_raw: int) -> list[int]:           # veh_spd 40 km/h, TotKM km_raw/10 km
    return [160, 15, *km_raw.to_bytes(4, "little"), 246, 0]


def header(t: str, thread: str = THREAD) -> str:
    return f"{t} [{thread}] INFO  sample.receiver.ReceiveCallBack.messageArrived:148 - "


def topic(t: str, device: str, kind: str) -> str:
    return header(t) + f"接收消息主题:/fleet/tbox/{device}/{kind}\n"


def message(t: str, payload: dict) -> str:
    body = json.dumps(payload, indent="\t", ensure_ascii=False).replace('": ', '":\t')
    return header(t) + "回调接受：接收消息:" + body + "\n"


def log_files() -> dict[str, str]:
    day = "receive-logs/log/2024-05-07"
    a = "".join([
        header("2024-05-07 08:00:00.001", "main") + "Starting receiver\n",
        topic("2024-05-07 08:00:05.100", D1, "CAN"),
        message("2024-05-07 08:00:05.101", dict(type="real_batch", sn=D1, time="2024-05-07 08:00:05",
                data=[0, FC_ID, *FC, 0, VCU_ID, *VCU, 0, UNKNOWN_ID, 1, 2, 3, 4, 5, 6, 7, 8],
                fault=["2024-05-07 08:00:04", 0, VCU_ID, *VCU], checksum=[1, 0])),
        topic("2024-05-07 08:00:05.200", D1, "GPS"),
        message("2024-05-07 08:00:05.201", dict(type="real_gps", sn=D1, time="2024-05-07 08:00:05",
                data=[3630.1234, "N", 11112.5678, "E"], result=1)),
        topic("2024-05-07 08:00:10.100", D1, "CAN"),
        message("2024-05-07 08:00:10.101", dict(type="real_batch", sn=D1, time="2024-05-07 08:00:10",
                data=[0, FC_ID, *FC, 0, FC_ID, *FC2], fault=[])),          # FcVol conflicts
        topic("2024-05-07 08:00:11.100", D2, "CAN"),
        message("2024-05-07 08:00:11.101", dict(type="heart", sn=D1, time="2024-05-07 08:00:11")),  # topic says D2
        topic("2024-05-07 08:00:12.100", D2, "HEART"),
        message("2024-05-07 08:00:12.101", dict(type="heart", sn=D2, time="2024-05-07 08:00:12")),
        message("2024-05-07 08:00:13.101", dict(type="real_gps", sn="bad sn!", time="2024-05-07 08:00:13",
                data=[3630.0, "N", 11112.0, "E"])),
        header("2024-05-07 08:00:14.101") + '回调接受：接收消息:{\n\t"type":\t"real_batch",\n\t"sn":\t"' + D1 + '"',  # cut off
    ])
    b = "".join([
        '\t"data":\t[0, 1, 2]\n}\n',                                         # tail of the previous file
        topic("2024-05-07 08:30:00.100", D2, "CAN"),
        message("2024-05-07 08:30:00.101", dict(type="real_batch", sn=D2, time="2024-05-07 08:30:00",
                data=[0, FC_ID, 300, 0, 0, 0, 0, 0, 0, 0, 0, FC_ID, *FC], fault=[])),  # byte 300 invalid
        message("2024-05-07 08:30:01.101", dict(type="real_gps", sn=D2, time="2024-05-07 08:30:01", data=[36.5])),
        message("2024-05-07 08:30:02.101", dict(type="real_gps", sn=D2, data=[3740.0, "N", 11230.0, "E"])),  # no time
        header("2024-05-07 08:30:03.101") + '{"x": 1}\n',
        header("2024-05-07 08:30:04.101") + "Reconnected\n",
    ])
    c = message("2024-05-07 09:00:00.101", dict(type="real_batch", sn=D2, time="2024-05-07 09:00:00", fault=[]))
    # Device 3, in receipt order: (log time, reported time, FcVol raw, TotKM raw, extra frames).
    blocks = [("09:59:55.200", "2024-05-08 09:59:55", 3995, 19999, None),     # no cell frame: no core content
              ("10:00:00.250", "2024-05-08 10:00:00", 4000, 20000, CELL),     # stored 10:00:00
              ("10:00:05.250", "2024-05-08 10:00:05", 4050, 20001, CELL),     # stored 10:00:05
              ("10:00:10.100", "2024-05-08 10:00:09", 4100, 20002, CELL),     # stored 10:00:10 (same-time pair)
              ("10:00:10.700", "2024-05-08 10:00:10", 4110, 20003, CELL),     # stored 10:00:10 (same-time pair)
              ("10:00:15.300", "2024-05-08 10:00:15", 4150, 20004, CELL),     # log only, verified device clock
              ("10:00:20.400", "2020-01-01 00:00:00", 4200, 20005, CELL),     # log only, reset device clock
              ("10:00:25.100", "2024-05-08 10:00:25", 4250, 20005, "unknown"),  # log only, unknown frame
              ("10:00:30.200", "2024-05-08 10:00:30", 4300, 20006, CELL),     # stored 10:00:30
              ("10:00:35.200", "2024-05-08 10:00:35", 4350, 20007, CELL)]     # log only, no next neighbour
    d = []
    for log_t, reported, vol, km, extra in blocks:
        frames = [0, FC_ID, *fc(vol), 0, VCU_ID, *vcu(km)]
        if extra == "unknown":
            frames += [0, CELL_ID, *CELL, 0, UNKNOWN_ID, 1, 2, 3, 4, 5, 6, 7, 8]
        elif extra:
            frames += [0, CELL_ID, *extra]
        stamp = "2024-05-08 " + log_t
        d.append(topic(stamp, D3, "CAN"))
        d.append(message(stamp, dict(type="real_batch", sn=D3, time=reported, data=frames, fault=[])))
    # Device 3 GPS, in receipt order: (log time, reported time, LAT, LON). Stored fixes: 10:00:00, 10:00:05.
    fixes = [("10:00:40.100", "2024-05-08 10:00:00", 3640.0, 11240.0),     # same as stored 10:00:00: linked
             ("10:00:40.200", "2024-05-08 10:00:05", 3641.5, 11241.5),     # stored 10:00:05 has other coordinates
             ("10:00:40.300", "2024-05-08 10:00:10", 3641.01, 11241.01),   # log only, verified device clock
             ("10:00:40.400", "2020-01-01 00:00:05", 3641.02, 11241.02),   # log only, reset device clock
             ("10:00:40.500", "2024-05-08 10:00:20", 3641.03, 11241.03)]   # log only, no next neighbour
    for log_t, reported, lat, lon in fixes:
        stamp = "2024-05-08 " + log_t
        d.append(topic(stamp, D3, "GPS"))
        d.append(message(stamp, dict(type="real_gps", sn=D3, time=reported, data=[lat, "N", lon, "E"], result=1)))
    return {f"{day}/info/info.2024-05-07-08.0.log": a, f"{day}/info/info.2024-05-07-08.1.log": b,
            f"{day}/warn/warn.2024-05-07-09.0.log": c,
            "receive-logs/log/2024-05-08/info/info.2024-05-08-10.0.log": "".join(d)}


def write_logs() -> None:
    shutil.rmtree(LOGS, ignore_errors=True)
    shutil.rmtree(PROTOCOL, ignore_errors=True)
    PROTOCOL.mkdir(parents=True)
    (PROTOCOL / "sample.dbc").write_text(DBC)
    (PROTOCOL / "sample.dbc.sha256").write_text(hashlib.sha256(DBC.encode()).hexdigest() + "\n")
    inventory = []
    for relative, text in log_files().items():  # already in natural order
        path = LOGS / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        data = path.read_bytes()
        inventory.append(dict(relative_path=relative, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    (LOGS / "_manifest.json").write_text(json.dumps(inventory, indent=1) + "\n")


def main() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    manifest, fingerprints = [], {}
    for relative, table in tables().items():  # insertion order = dump order (first CAN table is the reference)
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path, compression="zstd")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.append(dict(db="sample", table=Path(relative).stem, path=relative, rows=len(table),
                             bytes=path.stat().st_size, status="ok", error=None))
        fingerprints[relative] = dict(bytes=path.stat().st_size, rows=len(table), sha256=sha)
    (ROOT / "_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (ROOT / "_fingerprint.json").write_text(json.dumps(fingerprints, indent=1) + "\n")
    (ROOT.parent / "cohort.csv").write_text(f"device_id\n{D1}\n{D2}\n{D3}\n")
    write_logs()
    print(f"wrote {len(manifest)} tables to {ROOT}, logs to {LOGS}, protocol to {PROTOCOL}")


if __name__ == "__main__":
    main()
