"""Generate this publication artifact from documented figure inputs."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from figures.common import Inputs, grouped, ratio, write_json, table_output, tex
from figures import style
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LogNorm

def main():
    i=Inputs();v=i.table('vehicles');n=i.n
    rows=[('Anonymous identifiers',f"{n['vehicles']:,}"),('Operator groups (anonymous)',f"A: {(v.operator_group=='A').sum()}; B: {(v.operator_group=='B').sum()}; unassigned: {v.operator_group.isna().sum()}"),
          ('Observation period',n['period'][0][:10]+' to '+n['period'][1][:10]),('Recorded series-days',f"{n['active_vehicle_days']:,}"),
          ('Operation records',f"{n['operation_records']:,}"),('GPS fixes',f"{n['gps_fixes']:,}"),('Unit installations',f"{n['tbox_installations']:,}"),
          ('Vehicles with replacement units',f"{n['vehicles_with_tbox_change']:,}"),('Qualified odometer distance (km)',f"{n['odometer_km']:,.0f}"),
          ('Fuel-cell on-time (h)',f"{n['fc_on_hours']:,.0f}"),('Trips / moving trips',f"{n['trips']:,} / {n['trips_moving']:,}"),('Refuelling events',f"{n['refuels']:,}"),
          ('Released measured fields',str(n['fields_released'])),('Release size (GB)',f"{n['release_bytes']/1e9:.2f}")]
    table_output(i,'tab1_fleet',pd.DataFrame(rows,columns=['Characteristic','Value']),'Release overview. For release v1.0, 101 identifiers represent 100 confirmed vehicles and one unresolved telemetry series; totals include that series. Distances and on-time are rounded to whole units. GB denotes decimal gigabytes; vehicle specifications await confirmation outside this table.','tab:fleet')
    i.evidence('tab1_fleet',dict(vehicles=len(v),operation_records=int(v.records.sum()),gps_fixes=int(v.gps_fixes.sum())), 'Values from key_numbers.json and anonymous vehicle summaries; no identity ledger is read.')

if __name__ == '__main__': main()
