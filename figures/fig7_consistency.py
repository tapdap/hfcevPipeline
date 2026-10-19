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
    i=Inputs();trip=i.table('trip_distance');delayed=i.table('delayed_check');handover=i.table('handover');odo=i.table('odometer_daily')
    fig=plt.figure(figsize=(style.DOUBLE,240*style.MM),layout='constrained');gs=fig.add_gridspec(6,3,height_ratios=[1.3,1.3,1,1,1,1])
    a=fig.add_subplot(gs[:2,0]);b=fig.add_subplot(gs[0,1:]);d=fig.add_subplot(gs[1,1:])
    r=trip.loc[trip.well_covered,'gps_to_odometer'].dropna();edges=np.r_[np.arange(0,2.01,.05),np.inf];counts=np.histogram(r,edges)[0]
    a.bar(np.arange(len(counts))*.05,100*counts/len(r),width=.05,color=style.COLORS['operation'],align='edge',lw=0)
    for q,ls in zip([.1,.5,.9],[':', '-', '--']):a.axvline(r.quantile(q),ls=ls,color=style.COLORS['anomaly'],lw=.9)
    a.set(xlabel='GPS / odometer distance',ylabel='Well-covered trips per bin (%)',xlim=(0,1.5))
    a.text(.04,.98,f'median {r.median():.2f}',transform=a.transAxes,va='top',fontsize=style.SMALL)
    style.panel(a,'a','Distance agreement')
    stats={}
    for ax,col,title in [(b,'gap_s','Longest GPS gap (s)'),(d,'displacement_ratio','Gap displacement / increment')]:
        for j,(kind,color) in enumerate([('delayed',style.COLORS['anomaly']),('control',style.COLORS['operation'])]):
            g=delayed[(delayed.kind==kind)&(delayed.with_gps>0)].dropna(subset=[col+'_p50']).reset_index(drop=True)
            floor=1 if col=='gap_s' else .01
            y=g[col+'_p50'].clip(lower=floor)
            x=j+np.random.default_rng(0).uniform(-.12,.12,len(g))
            ax.boxplot(y,positions=[j],widths=.4,showfliers=False,manage_ticks=False,medianprops={'color':color})
            ax.scatter(x,y,s=8,color=color,alpha=.65,edgecolors='none')
            stats[kind]=dict(units=len(g),increments=int(g.increments.sum()),with_gps=int(g.with_gps.sum()),gap_over_120s=int(g.gap_over_120s.sum()))
        ax.axhline(120 if col=='gap_s' else 1,color='#777777',ls=':',lw=.7)
        ax.set_xticks([0,1],['Delayed increments','Control (+1 km)']);ax.set_ylabel(title);ax.set_xlim(-.6,1.6)
        ax.set_yscale('log');ax.grid(axis='y',alpha=.15)
    style.panel(b,'b','Device summaries: median and IQR')
    vehicles=sorted(handover.vehicle_id.unique());styles={1:'-',2:'-',3:'-'};unit_colors={1:style.COLORS['operation'],2:style.COLORS['gps'],3:style.COLORS['log']}
    for k,vid in enumerate(vehicles):
        ax=fig.add_subplot(gs[2+k//3,k%3]);g=odo[odo.vehicle_id==vid]
        for unit,frame in g.groupby('unit'):
            frame=frame.sort_values('day');t=pd.to_datetime(frame.day)
            # Reindex to every day: absent observations break the line.
            daily=frame.set_index(t)[['km_min','km_max']].reindex(pd.date_range(t.min(),t.max()))
            ax.plot(daily.index,daily.km_max/1000,color=unit_colors[unit],ls='-',lw=1.3)
            ax.vlines(t,frame.km_min/1000,frame.km_max/1000,color=unit_colors[unit],alpha=.3,lw=.5)
        for h in handover[handover.vehicle_id==vid].itertuples():
            old=g[g.unit==h.from_unit].sort_values('day');new=g[g.unit==h.to_unit].sort_values('day')
            ax.axvline(pd.Timestamp(new.day.iloc[0]),color='#777777',lw=.5,ls=':')
            color=style.COLORS['log'] if h.odometer_consistent else style.COLORS['anomaly']
            for date,km in [(old.day.iloc[-1],h.km_before),(new.day.iloc[0],h.km_after)]:
                ax.plot(pd.Timestamp(date),km/1000,'o' if h.odometer_consistent else 'X',ms=4.5,color=color,zorder=3)
        flag=handover[(handover.vehicle_id==vid)&(~handover.odometer_consistent)]
        ax.set_title(vid+(' · flagged' if len(flag) else ''),loc='left',color=style.COLORS['anomaly'] if len(flag) else '#222222',pad=3)
        if len(flag):
            threshold=i.cfg.release['s6_vehicles']['handover_max_km_per_day']
            ax.text(.04,.84,f'{flag.km_per_day.iloc[0]:.0f} > {threshold} km/day',transform=ax.transAxes,color=style.COLORS['anomaly'],fontsize=style.SMALL)
        if k%3==0:ax.set_ylabel('Odometer (10³ km)')
        style.dates(ax,interval=6);style.periods(ax)
        if k==1: ax.text(pd.Timestamp('2025-12-15'),1.03,'data-loss period',transform=ax.get_xaxis_transform(),ha='center',fontsize=style.SMALL,color='#444444')
        ax.set_xlim(pd.Timestamp('2025-06-01'),pd.Timestamp('2026-06-01'))
        ax.tick_params(labelbottom=k>=9)
        if k==0:style.panel(ax,'c')
    from matplotlib.lines import Line2D
    handles=[Line2D([0],[0],color=unit_colors[u],ls=styles[u],label=f'Unit {u}') for u in (1,2,3)]
    handles += [Line2D([0],[0],color=style.COLORS['log'],marker='o',ls='none',ms=4.5,label='Consistent endpoint'),
                Line2D([0],[0],color=style.COLORS['anomaly'],marker='X',ls='none',ms=4.5,label='Flagged endpoint')]
    fig.legend(handles=handles,loc='outside lower center',ncol=5,frameon=False)
    caption=(f"GPS and odometer distances broadly agree for well-covered trips, with one flagged unit handover. a, Distance ratios for trips ≥5 km with GPS coverage ≥90%; dotted, solid and dashed lines denote the 10th, 50th and 90th percentiles. Values above 1.5 are outside the displayed range. b, Distributions of device medians, with boxes showing quartiles across devices and points showing individual device medians; reference lines mark 120 s and a displacement ratio of 1. Logarithmic axes; zero values are drawn at the axis floor (1 s; ratio 0.01). The delayed and control groups contain {stats['delayed']['with_gps']:,} and {stats['control']['with_gps']:,} increments with GPS, respectively, before installation exclusions (Methods). c, Daily odometer ranges and maxima for 12 vehicles and 13 handovers. Colours distinguish units; vertical lines mark the first positive-reading day of the new unit. Some units have only isolated endpoint readings. Missing days remain blank. V084 has a flagged endpoint difference of 450.291 km/day against the 400 km/day threshold. Grey shading marks platform data loss.")
    i.evidence('fig7_consistency',dict(well_covered_trips=len(r),p10=float(r.quantile(.1)),p50=float(r.median()),p90=float(r.quantile(.9)),
               delay_unit_summaries=stats,handover_vehicles=len(vehicles),handovers=len(handover),consistent=int(handover.odometer_consistent.sum()),
               flagged=handover.loc[~handover.odometer_consistent,['vehicle_id','km_per_day']].to_dict('records')),caption)
    style.save(fig,i.out,'fig7_consistency')

if __name__ == '__main__': main()
