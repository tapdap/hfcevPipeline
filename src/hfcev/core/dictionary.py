"""Column descriptions for the release data dictionary.

Units: `dbc` = stated in the CAN protocol; `provider` = confirmed by the data provider; `inferred` = from the DBC scaling and observed value
ranges, to be confirmed by the operator; `none` = dimensionless code or count.
"""
from __future__ import annotations

# field -> (description, unit, unit_basis)
FIELDS = {
    "main_state": ("Fuel-cell system main state: 1 standby, 3 start-up, 4 running, 5 derating, 6 shutdown, "
                   "18 fault (codes from the data provider)", "", "provider"),
    "sub_state": ("Fuel-cell system sub-state (code)", "", "none"),
    "CurrentCmd": ("Stack current command", "A", "provider"),
    "BOPTarCur": ("Balance-of-plant target current; stored by the operator database until 9 June 2025, afterwards only in log additions", "A", "provider"),
    "FaultLevel": ("Fault level (code)", "", "none"),
    "FaultCode": ("Fault code; 0 = no fault", "", "none"),
    "MaxCellV": ("Maximum single-cell voltage", "mV", "dbc"),
    "MinCellV": ("Minimum single-cell voltage", "mV", "dbc"),
    "CoolInTarTemp": ("Coolant inlet target temperature", "degC", "provider"),
    "CoolInActTemp": ("Coolant inlet temperature", "degC", "provider"),
    "CoolOutActTemp": ("Coolant outlet temperature", "degC", "provider"),
    "RadInTemp": ("Radiator inlet temperature", "degC", "provider"),
    "RadOutTemp": ("Radiator outlet temperature", "degC", "provider"),
    "EnvTemp": ("Ambient temperature", "degC", "provider"),
    "TarDCCur": ("DC/DC converter target current", "A", "provider"),
    "FcVol": ("Fuel-cell stack voltage", "V", "provider"),
    "FcCur": ("Fuel-cell stack current", "A", "provider"),
    "SOC": ("Traction battery state of charge", "%", "provider"),
    "VCUTarPower": ("Fuel-cell power requested by the vehicle control unit", "kW", "provider"),
    "TotKM": ("Vehicle odometer (1 km resolution; 0 = not reported)", "km", "provider"),
    "HySysState": ("Hydrogen system state (code)", "", "none"),
    "MainValveStat": ("Main hydrogen valve state; stored by the operator database until 9 June 2025, afterwards only in log additions", "", "none"),
    "HyMidP": ("Hydrogen medium (regulated) pressure", "MPa", "provider"),
    "HighPPM": ("Highest hydrogen concentration reported by the leak sensors", "ppm", "provider"),
    "HighPPMNum": ("Index of the leak sensor with the highest concentration; decoding corrected (see Methods); "
                   "15 = not available", "", "none"),
    "HyBottleP": ("Hydrogen storage (tank) pressure; decoding corrected (see Methods)", "MPa", "provider"),
    "TotH2Kg": ("Cumulative hydrogen consumption, transmitted in whole kg; counted in 0.01 kg on 16 bits, so it wraps after "
                "655.35 kg (see h2_kg_restored)", "kg", "provider"),
    "ISO": ("Insulation resistance", "kOhm", "provider"),
    "hydncsu": ("Hydrogen consumed since the fuel-cell system was last powered on (0.01 kg resolution; resets to 0 "
                "at shutdown); stored as HYCSN before 9 June 2025 and merged here", "kg", "provider"),
    "rad_duty": ("Radiator fan duty", "%", "provider"),
    "air_temp": ("Cathode air temperature", "degC", "provider"),
    "air_surge_p": ("Air compressor surge pressure", "kPa", "provider"),
}

# field -> usage caveat (dictionary `caveat` column and the release README); measured values are unchanged.
CAVEATS = {
    "main_state": "Codes 8 and 9 also occur (about 0.04% of records); their meaning is not documented.",
    "TotKM": "0 means not reported, not a parked vehicle; all records before June 2025 are 0. Use GPS track distance before then.",
    "HyBottleP": "0 is not a pressure reading: it occurs while the fuel cell is running (always before March 2025, occasionally later). Treat 0 as missing, not as an empty tank.",
    "HighPPMNum": "After the decoding correction almost always 0.",
    "RadInTemp": "Nearly constant over long periods in most vehicles (sensor appears stuck); see the stuck-value statistics before use.",
    "TotH2Kg": "Wraps to 0 after 655.35 kg; use h2_kg_restored, or h2_kg in the daily and monthly tables.",
    "hydncsu": "Resets to 0 at every shutdown; differences across a shutdown are not consumption.",
}

OPERATION = {
    "vehicle_id": "Anonymous vehicle identifier",
    "unit": "TBOX installation on the vehicle (1 = first); changes at a recorded TBOX replacement",
    "time": "Server receipt time to the second (primary time axis): rounded for database records, truncated for log additions",
    "time_received_ms": "Server receipt time with milliseconds, where a receive-log message is linked",
    "time_device": "Device (payload) time; only where the device clock passed checks V1-V4",
    "time_device_status": "verified, or the first failed device-clock check / reason for no device time",
    "source": "database (operator export) or log_addition (recovered from receive logs)",
    "same_time_variants": "Number of distinct records sharing this vehicle unit's receipt time",
    "segment": "Reporting segment (consecutive records <= 60 s apart), unique per vehicle",
    "missing_mask": "Bit j set: field j (see field_bits) missing; for decoding-corrected fields (HyBottleP, HighPPMNum) all quality bits refer to the corrected values",
    "outside_encodable_mask": "Bit j set: field j outside the range the protocol can encode",
    "all_ones_mask": "Bit j set: field j carries the all-ones 'not available' code",
    "relation_violation": "MinCellV > MaxCellV",
    "counter_backward_mask": "Bit k set: counter k (TotKM, TotH2Kg) decreased inside the segment",
    "log_mismatch_mask": "Bit j set: stored value of field j differs from its receive-log value (linked records)",
    "correction_status": "HyBottleP/HighPPMNum decoding: reconstructed (stored value corrected), dbc_decoded (log addition) or not_reconstructable",
    "gps_index": "Row of the matched GPS fix in the vehicle's GPS file (nearest usable fix within 10 s), null if none",
    "gps_offset_s": "GPS device time minus record receipt time of the matched fix (s)",
    "km_increment": "Odometer increment from the previous valid reading (km)",
    "km_kind": "in_segment, in_segment_delayed, across_gap, across_long_gap, jump, backward (see Methods)",
    "h2_kg_restored": "TotH2Kg with counter wraps restored (kg; cumulative within one TBOX unit, restarts at a TBOX change)",
    "h2_increment": "Restored hydrogen consumed since the previous valid TotH2Kg reading (kg)",
    "h2_kind": "increment, wrap, wrap_inferred (wrap count from odometer distance across a gap) or backward",
    "h2_attributed": "h2_increment is counted in trip / daily consumption (forward step over a gap <= 1 h)",
}

GPS = {
    "vehicle_id": "Anonymous vehicle identifier",
    "unit": "TBOX installation on the vehicle",
    "time": "Device time of the fix",
    "time_received_ms": "Server receipt time of the fix where a receive-log message is linked",
    "source": "database or log_addition",
    "same_time_variants": "Number of distinct fixes sharing this device time",
    "lat": "Latitude, decimal degrees (published only if coordinates are released)",
    "lon": "Longitude, decimal degrees (published only if coordinates are released)",
    "coordinate_class": "Encoding class of the raw coordinates",
    "hemisphere_ok": "Hemisphere letters valid",
    "outside_region": "Outside the region box",
    "speed_plausible": "Implied speed to unique-time neighbours plausible (null if untestable)",
    "device_clock_suspect": "Linked receipt time outside the device-clock window",
    "fix_status": "valid, valid_assumed, no_fix, no_fix_inferred, static_while_moving",
    "fix_source": "result (receive-log GPS flag) or proxy (frozen-position rule)",
    "usable": "Fix usable for speed and distance",
    "step_km": "Great-circle distance from the previous usable fix (<= 60 s, no unusable fix between)",
    "speed_kmh": "step_km over the time step (km/h)",
}

FIELDS.update({
    "veh_spd": ("Vehicle speed (sparse)", "km/h", "provider"),
    "acc": ("Accelerator pedal position (sparse)", "%", "provider"),
    "brake": ("Brake pedal position (sparse)", "%", "provider"),
    "bat_cur": ("Traction battery current (sparse)", "A", "provider"),
    "veh_p": ("Vehicle power (sparse)", "kW", "provider"),
})

# derived tables: column -> description (shared names are described once)
DERIVED = {
    "vehicle_id": "Anonymous vehicle identifier",
    "unit": "TBOX installation on the vehicle",
    "segment": "Reporting segment, unique per vehicle",
    "after_segment": "Segment preceding the gap",
    "start": "Start (receipt time)", "end": "End (receipt time)",
    "duration_s": "Duration (s)", "records": "Operation records", "additions": "Records recovered from receive logs",
    "median_interval_s": "Median interval between consecutive records (s)",
    "share_intervals_le_6s": "Share of intervals <= 6 s",
    "short": "Segment shorter than 300 s", "crosses_midnight": "Segment spans a calendar midnight",
    "same_time_rows": "Records sharing a receipt time with another record",
    "rows_outside_encodable": "Records with a field outside the encodable range",
    "rows_all_ones": "Records with an all-ones code",
    "soc_first": "First SOC in the segment (%)", "soc_last": "Last SOC in the segment (%)",
    "km_first": "First odometer reading in the segment (km)", "km_last": "Last odometer reading in the segment (km)",
    "log_hours_covered": "Hours of the gap covered by receive-log files",
    "device_log_messages": "Receive-log messages of this vehicle inside the gap",
    "label": "no_log_files, device_reported_in_logs or device_silent_in_logs",
    "soc_before": "SOC before the gap (%)", "soc_after": "SOC after the gap (%)",
    "km_before": "Odometer before the gap (km)", "km_after": "Odometer after the gap (km)",
    "km_delta": "Odometer change across the gap (km)",
    "trip": "Trip number, unique per vehicle", "span_s": "Trip span from first to last record (s)",
    "active_s": "Time covered by record intervals <= 60 s (s)", "segments": "Reporting segments in the trip",
    "km_qualified": "Odometer distance (in_segment, in_segment_delayed, across_gap increments; km)",
    "moving": "Odometer or GPS-track distance >= 1 km",
    "fc_on_s": "Fuel-cell on time (FcCur > 1 A; s)", "fc_runs": "Fuel-cell runs started in the trip",
    "energy_kwh_raw_units": "Integral of FcVol x FcCur (kWh if V and A)",
    "soc_start": "SOC at start (%)", "soc_end": "SOC at end (%)", "soc_min": "Minimum SOC (%)", "soc_max": "Maximum SOC (%)",
    "h2_pressure_start": "Hydrogen tank pressure at start (MPa)", "h2_pressure_end": "Hydrogen tank pressure at end (MPa)",
    "refuels": "Refuelling events identified from the telemetry (tank-pressure rises of at least 5 MPa)", "fault_state_records": "Records with the fuel-cell system in the fault state (main_state = 18)",
    "gps_fixes": "GPS fixes", "gps_usable": "Usable GPS fixes", "gps_no_fix": "GPS fixes without a position fix",
    "gps_km": "GPS-track distance (km)", "gps_covered_s": "Time covered by GPS track steps (s)",
    "start_cell": "Start position, 0.1 degree cell (only with released coordinates)",
    "end_cell": "End position, 0.1 degree cell (only with released coordinates)",
    "on_s": "Fuel-cell on time (s)", "mean_power_kw": "Mean FcVol x FcCur / 1000", "p95_power_kw": "95th percentile of FcVol x FcCur / 1000",
    "before": "Last non-zero pressure reading before the rise (receipt time); with `after` it bounds the interval in "
              "which refuelling took place, not the refuelling time itself",
    "after": "First non-zero pressure reading after the rise (receipt time); intervals can span long gaps without data, "
             "in which more than one refuelling may have taken place",
    "p_before": "Tank pressure before (MPa)", "p_after": "Tank pressure after (MPa)",
    "across_segments": "The rise spans a no-data gap",
    "day": "Calendar day (receipt time)", "month": "Calendar month (receipt time)", "days": "Days with records",
    "km_delayed": "Odometer distance from in_segment_delayed increments (km)",
    "h2_kg": "Hydrogen consumed, from attributed restored TotH2Kg increments (kg)",
    "aligned": "Records matched to a GPS fix", "fc_starts": "Fuel-cell runs started", "trips": "Trips started",
    "units": "TBOX installations reporting on the day",
}
