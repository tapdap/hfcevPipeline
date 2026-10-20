"""Fig. 8: usage examples from the released event and calendar tables (no record-level data)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from figures.common import Inputs
from figures import style
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm

MIN_RUN_S = 60        # fuel-cell runs shorter than this are not shown in b
MIN_MONTH_KM = 1000   # series-months with less qualified distance are not used in c


def main():
    i = Inputs()
    daily = i.release('daily.parquet'); runs = i.release('fc_runs.parquet'); refuels = i.release('refuels.parquet')
    fig, axs = plt.subplots(2, 2, figsize=(style.DOUBLE, 120 * style.MM), layout='constrained')
    a, b, c, d = axs.ravel()

    # a: daily distance per series-day (odometer reported from June 2025)
    km = daily.loc[daily.km_qualified > 0, 'km_qualified']
    a.hist(km, bins=np.arange(0, 801, 20), color=style.COLORS['operation'], lw=0)
    a.axvline(km.median(), color=style.COLORS['anomaly'], lw=.9, ls='--')
    a.text(km.median() + 12, .95, f'median {km.median():.0f} km', transform=a.get_xaxis_transform(), va='top', fontsize=style.SMALL)
    a.set(xlabel='Odometer distance per series-day (km)', ylabel='Series-days', xlim=(0, 800))
    style.panel(a, 'a')

    # b: fuel-cell runs, mean power against duration
    r = runs[(runs.on_s >= MIN_RUN_S) & runs.mean_power_kw.notna()]
    xe = np.geomspace(MIN_RUN_S / 60, 600, 41); ye = np.arange(0, 175, 4)
    h = b.hist2d(r.on_s / 60, r.mean_power_kw.clip(0, 171.9), bins=[xe, ye], cmap='Blues', norm=LogNorm(), rasterized=True)
    fig.colorbar(h[3], ax=b, label='Runs', pad=.01)
    b.axhline(159, color='#777777', lw=.7, ls=':')
    b.text(.98, 159.5, 'stack maker: 159 kW', transform=b.get_yaxis_transform(), ha='right', va='bottom', fontsize=style.SMALL, color='#444444')
    b.set(xscale='log', xlabel='Fuel-cell run duration (min)', ylabel='Mean stack power (kW)', ylim=(0, 172))
    style.panel(b, 'b')

    # c: monthly hydrogen consumption per 100 km (series-days with both distance and consumption)
    both = daily[(daily.km_qualified > 0) & (daily.h2_kg > 0)].copy()
    both['month'] = pd.to_datetime(both.day).dt.to_period('M')
    vm = both.groupby(['vehicle_id', 'month'])[['km_qualified', 'h2_kg']].sum()
    vm = vm[vm.km_qualified >= MIN_MONTH_KM]; vm['rate'] = vm.h2_kg / vm.km_qualified * 100
    q = vm.groupby('month').rate.quantile([.25, .5, .75]).unstack()
    fleet = both.groupby('month')[['km_qualified', 'h2_kg']].sum(); fleet = fleet.h2_kg / fleet.km_qualified * 100
    t = q.index.to_timestamp() + pd.Timedelta(days=14)
    c.fill_between(t, q[.25], q[.75], color=style.COLORS['sky'], alpha=.35, lw=0, label='series, IQR')
    c.plot(t, q[.5], color=style.COLORS['operation'], marker='o', label='series, median')
    c.plot(fleet.index.to_timestamp() + pd.Timedelta(days=14), fleet.values, color=style.COLORS['anomaly'], ls='--', lw=1, label='fleet total')
    c.set(ylabel='Hydrogen (kg per 100 km)'); c.legend(frameon=False, loc='upper left', ncols=3)
    c.set_ylim(0, max(14, float(q[.75].max()) * 1.25))
    style.dates(c, interval=3); c.set_xlim(pd.Timestamp('2025-06-01'), pd.Timestamp('2026-06-01'))
    style.panel(c, 'c')

    # d: tank pressure before and after refuelling
    bins = np.arange(0, 40.1, 1)
    d.hist(refuels.p_before, bins=bins, color=style.COLORS['gps'], alpha=.85, lw=0, label='before')
    d.hist(refuels.p_after, bins=bins, color=style.COLORS['log'], alpha=.85, lw=0, label='after')
    d.axvline(35, color='#777777', lw=.7, ls=':'); d.text(34.5, .95, '35 MPa', transform=d.get_xaxis_transform(), ha='right', va='top', fontsize=style.SMALL, color='#444444')
    d.set(xlabel='Corrected tank pressure (MPa)', ylabel='Refuelling events'); d.legend(frameon=False, loc='upper left', bbox_to_anchor=(.3, 1))
    style.panel(d, 'd')

    style.save(fig, i.out, 'fig8_usage')
    caption = 'Usage examples from event and calendar tables. a, Positive odometer distance per series-day; 82 days above 800 km are outside the displayed range, but are included in the median. b, Mean stack power versus fuel-cell on-time for runs of at least 60 s; colour denotes run counts and the dotted line marks the stack manufacturer’s 159 kW rating. Power is calculated as FcVol × FcCur. c, Monthly hydrogen consumption: series median and interquartile range, and the distance-weighted fleet aggregate (dashed). d, Tank pressures before and after refuelling; the dotted line marks 35 MPa.'
    i.evidence('fig8_usage', dict(
        vehicle_days_with_distance=int(len(km)), daily_km_median=float(km.median()),
        runs_shown=int(len(r)), run_minutes_median=float((r.on_s / 60).median()), run_power_median_kw=float(r.mean_power_kw.median()),
        vehicle_months=int(len(vm)), monthly_rate_median=[[str(m), float(v)] for m, v in q[.5].items()],
        fleet_monthly_rate=[[str(m), float(v)] for m, v in fleet.items()],
        refuels=int(len(refuels)), p_before_median=float(refuels.p_before.median()), p_after_median=float(refuels.p_after.median())), caption)


if __name__ == '__main__': main()
