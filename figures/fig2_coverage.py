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
    i = Inputs(); c = i.table('coverage'); fleet = i.table('fleet_daily'); spans = i.table('unit_span')
    order = c.groupby('vehicle_id').day.min().sort_values(kind='stable').index
    days = pd.date_range(pd.to_datetime(c.day).min(), pd.to_datetime(c.day).max())
    heat = c.pivot(index='vehicle_id', columns='day', values='active_h')
    heat.columns = pd.to_datetime(heat.columns)
    heat = heat.reindex(index=order, columns=days)
    fig = plt.figure(figsize=(style.DOUBLE, 158 * style.MM), layout='constrained')
    gs = fig.add_gridspec(3, 1, height_ratios=[1, 1, 4.5])
    top, dist, ax = [fig.add_subplot(gs[j]) for j in range(3)]
    daily = fleet.set_index(pd.to_datetime(fleet.day)).reindex(days)
    t = daily.index
    top.plot(t, daily.vehicles, color=style.COLORS['operation'])
    top.set_ylabel('Active identifiers'); style.panel(top, 'a', f"Temporal coverage · {i.n['vehicles']} identifiers")
    dist.plot(t, daily.km.where(t >= pd.Timestamp('2025-06-01')), color=style.COLORS['log']); dist.set_ylabel('Distance (km)'); dist.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v:,.0f}'))
    for a in (top, dist):
        style.periods(a, labels=a is top); style.dates(a); a.tick_params(labelbottom=False)
    x = mdates.date2num(days)
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list('active', plt.get_cmap('Blues')(np.linspace(.3, 1, 256))); cmap.set_bad('white')
    im = ax.imshow(heat.to_numpy(), aspect='auto', origin='upper', cmap=cmap, vmin=0, vmax=24,
                   extent=[x[0]-.5, x[-1]+.5, len(order)-.5, -.5], interpolation='nearest')
    for r in spans[spans.unit > 1].itertuples():
        ax.plot(pd.Timestamp(r.first_time), order.get_loc(r.vehicle_id), marker='v', color='black', ms=2, mew=.5)
    ax.set_yticks([]); ax.set_ylabel('Anonymous identifiers (n = 101), ordered by first record')
    style.dates(ax); ax.set_xlabel('Receipt date'); style.panel(ax, 'b', 'Daily active hours; orange ticks mark a new unit')
    fig.colorbar(im, ax=ax, label='Active hours per day', fraction=.025, pad=.02)
    caption = ('Fleet observations vary over time, with odometer coverage beginning in June 2025. a, Daily active identifiers and qualified odometer distance; absent days are left blank. b, Daily active hours for 101 anonymous identifiers (100 confirmed vehicles and one unresolved series) ordered by first record; white denotes no records and black triangles mark the first operation record of a replacement unit. Blue shading denotes the period without odometer reporting; grey denotes the October 2025–February 2026 delayed-delivery period.')
    i.evidence('fig2_coverage', dict(vehicles=len(order), vehicle_days=len(c), daily_records=int(c.records.sum()), peak_active=int(fleet.vehicles.max()),
                                 odometer_km=float(fleet.km.sum()), replacement_units=int((spans.unit>1).sum())), caption)
    style.save(fig,i.out,'fig2_coverage')

if __name__ == '__main__': main()
