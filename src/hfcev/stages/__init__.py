"""Processing stages, in Methods order. Each module follows the contract in `hfcev.runner`."""
from . import s1_harmonize, s2_logs, s3_timeline, s4_quality, s5_derived, s6_vehicles, s7_release

STAGES = {m.NAME: m for m in (s1_harmonize, s2_logs, s3_timeline, s4_quality, s5_derived, s6_vehicles, s7_release)}
ALIASES = {"harmonize": "s1", "logs": "s2", "timeline": "s3", "quality": "s4", "derived": "s5", "vehicles": "s6", "release": "s7"}


def get(name: str):
    if name == "stats":
        from ..validation import stats
        return stats
    return STAGES[ALIASES.get(name, name)]
