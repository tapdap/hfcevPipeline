"""Content correspondence between stored operation records and log messages of one device.

A record and a message correspond when their measured values agree on the six core
fields and on every auxiliary field observed by both (values compared after rounding
to 6 decimals; a missing value never rules a candidate out, an observed zero counts).
A correspondence is accepted only if it is unique in both directions over the whole
device: the record's content occurs once among stored records, exactly one message is
compatible with it, and no other stored record is compatible with that message.
Repeated content is never collapsed to a single candidate.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

CORE = ("FcVol", "FcCur", "SOC", "TotKM", "MaxCellV", "MinCellV")
AUXILIARY = ("VCUTarPower", "TarDCCur", "CoolInActTemp", "CoolOutActTemp", "RadInTemp", "RadOutTemp",
             "EnvTemp", "rad_duty", "air_temp", "air_surge_p")
MATCH_FIELDS = CORE + AUXILIARY
GOOD_IDENTITY = ("topic_agrees", "payload_sn_only")
MAX_PAIRS = 2_000_000  # compute budget per core cluster; above it the cluster is reported unresolved


def canonical(value) -> str | None:
    """Value at 6-decimal resolution, or None when missing or non-finite."""
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    text = format(value, ".6f")
    return "0.000000" if text == "-0.000000" else text


def canonical_column(values) -> list:
    return [canonical(v) for v in values]


def keys(columns: dict[str, list], n: int):
    """(core key or None, auxiliary key) per row from canonical columns."""
    core = list(zip(*(columns[f] for f in CORE))) if n else []
    core = [None if any(v is None for v in c) else c for c in core]
    aux = list(zip(*(columns[f] for f in AUXILIARY))) if n else []
    return core, aux


def stored_keys(table, raw_cores: set):
    """Keys of stored records; auxiliary keys only where the core occurs among messages
    (elsewhere they cannot affect any decision), which keeps memory bounded."""
    n = table.num_rows
    core = list(zip(*(canonical_column(table[f].to_pylist()) for f in CORE))) if n else []
    core = [None if any(v is None for v in c) else c for c in core]
    need = [i for i, c in enumerate(core) if c is not None and c in raw_cores]
    aux = [None] * n
    if need:
        import pyarrow as pa
        sub = table.select(list(AUXILIARY)).take(pa.array(need))
        for i, a in zip(need, zip(*(canonical_column(sub[f].to_pylist()) for f in AUXILIARY))):
            aux[i] = a
    return core, aux


def _compatible(left: tuple, right: tuple) -> tuple[bool, int]:
    shared = 0
    for a, b in zip(left, right):
        if a is not None and b is not None:
            shared += 1
            if a != b:
                return False, shared
    return True, shared


def _classify(old: Counter, raw: dict) -> tuple[dict, dict]:
    """Decide every stored pattern of one core cluster against every message pattern.

    Returns (decision per stored pattern, state per message pattern).
    """
    old_total = sum(old.values())
    raw_total = sum(r["n"] for r in raw.values())
    if len(old) * len(raw) > MAX_PAIRS:
        return ({k: dict(status="computationally_unresolved") for k in old},
                {k: "computationally_unresolved" for k in raw})
    old_degree, raw_degree, sole, shared_n = Counter(), Counter(), {}, {}
    for left, n in old.items():
        for right, row in raw.items():
            ok, shared = _compatible(left, right)
            if ok:
                old_degree[left] += row["n"]
                raw_degree[right] += n
                sole[left], shared_n[left] = right, shared
    result, unique_raw = {}, set()
    for left, n in old.items():
        degree, chosen = old_degree[left], sole.get(left)
        status = "no_compatible_extra_candidate" if degree == 0 else "ambiguous_content"
        decision = dict(status=status, compatible_raw_count=degree)
        if degree == 1 and n == 1 and raw_degree[chosen] == 1:
            row = raw[chosen]
            enough = (old_total == 1 and raw_total == 1) or shared_n[left] > 0
            if not enough:
                decision["status"] = "unique_without_shared_extra"
            elif row["message_time"] is None or row["identity"] not in GOOD_IDENTITY:
                decision["status"] = "unique_invalid_time_or_identity"
            else:
                decision.update(status="unique_core_candidate" if old_total == raw_total == 1 else "unique_extended_candidate",
                                event_id=row["event_id"], message_time=row["message_time"])
                unique_raw.add(chosen)
        result[left] = decision
    states = {key: "unique_content_candidate" if key in unique_raw else
              "no_compatible_old_candidate" if raw_degree[key] == 0 else "ambiguous_or_ineligible_content"
              for key in raw}
    return result, states


def match(old_core, old_aux, raw_core, raw_aux, raw_meta):
    """Correspondence over one device.

    old_core/old_aux: keys per stored record; raw_core/raw_aux: keys per message;
    raw_meta: (event_id, message_time, identity_status) per message.
    Returns (decision per stored record, state per message, message-state counts).
    """
    raw_by_core = defaultdict(dict)
    raw_pattern = []
    for core, aux, meta in zip(raw_core, raw_aux, raw_meta):
        if core is None:
            raw_pattern.append(None)
            continue
        pattern = raw_by_core[core].get(aux)
        if pattern is None:
            raw_by_core[core][aux] = dict(n=1, event_id=meta[0], message_time=meta[1], identity=meta[2])
        else:  # repeated content: exact multiplicity, no singleton metadata
            pattern.update(n=pattern["n"] + 1, event_id=None, message_time=None, identity=None)
        raw_pattern.append((core, aux))
    old_by_core = defaultdict(Counter)
    for core, aux in zip(old_core, old_aux):
        if core is not None and core in raw_by_core:
            old_by_core[core][aux] += 1

    decisions, pattern_state, cluster_size = {}, {}, {}
    for core, raw in raw_by_core.items():
        old = old_by_core.get(core)
        if not old:
            pattern_state.update({(core, aux): "no_old_core_candidate" for aux in raw})
            continue
        cluster, states = _classify(old, raw)
        decisions.update({(core, k): v for k, v in cluster.items()})
        pattern_state.update({(core, k): v for k, v in states.items()})
        cluster_size[core] = (sum(old.values()), sum(r["n"] for r in raw.values()))

    per_record = []
    for core, aux in zip(old_core, old_aux):
        if core is None:
            per_record.append(dict(status="missing_old_core"))
        elif not raw_by_core:
            per_record.append(dict(status="no_log_core_coverage"))
        elif core not in raw_by_core:
            per_record.append(dict(status="no_raw_core_candidate"))
        else:
            per_record.append(dict(decisions[(core, aux)], core_old_count=cluster_size[core][0],
                                   core_raw_count=cluster_size[core][1]))
    per_message = ["missing_raw_core" if p is None else pattern_state[p] for p in raw_pattern]
    return per_record, per_message, Counter(per_message)
