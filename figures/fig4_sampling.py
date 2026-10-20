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
    i=Inputs(); intervals=i.table('intervals'); segments=i.table('segments_summary'); coverage=i.table('coverage'); fleet=i.table('fleet_daily')
    fig=plt.figure(figsize=(style.DOUBLE,185*style.MM),layout='constrained')
    gs=fig.add_gridspec(3,2);a=fig.add_subplot(gs[0,:]);b=fig.add_subplot(gs[1,0]);gapax=fig.add_subplot(gs[1,1]);c=fig.add_subplot(gs[2,0]);d=fig.add_subplot(gs[2,1])
    totals={}
    for j,(stream,color) in enumerate([('operation',style.COLORS['operation']),('gps',style.COLORS['gps'])]):
        g=grouped(intervals[intervals.stream==stream],['bin_left_s','bin_right_s']).sort_values('bin_left_s')
        totals[stream]=int(g['count'].sum())
        a.bar(np.arange(len(g))+(j-.5)*.38,100*g['count']/g['count'].sum(),width=.38,color=color,label='GPS' if stream=='gps' else 'Operation')
    labels=['0–1 s','1–2 s','2–5 s','5–6 s','6–10 s','10–30 s','30–60 s','1–5 min','5 min–1 h','1 h–1 d','>1 d']
    a.set_xticks(range(len(g)),labels,rotation=25,ha='right');a.set_ylabel('Intervals (%)');a.axvline(6.5,color='#444444',ls=':',lw=.8);a.text(6.6,.9,'60 s',transform=a.get_xaxis_transform(),fontsize=style.SMALL);a.legend(frameon=False)
    style.panel(a,'a','')
    seg=i.release('segments.parquet'); gaps=i.release('gaps.parquet')
    def ecdf(ax,values,**kwargs):
        v=np.sort(np.asarray(values,dtype=float));v=v[np.isfinite(v)]
        x,n=np.unique(v,return_counts=True)
        ax.step(x,np.cumsum(n)/len(v),where='post',**kwargs)
    zero=float((seg.duration_s<=0).mean())
    ecdf(b,seg.duration_s[seg.duration_s>0],color=style.COLORS['operation'],label=f'n = {int((seg.duration_s>0).sum()):,}')
    b.text(.97,.06,f'zero-duration segments: {zero:.1%}',transform=b.transAxes,ha='right',fontsize=style.SMALL)
    b.axvline(300,color='#555555',ls=':',lw=.7);b.legend(frameon=False)
    labels={'no_log_files':'No log files','device_reported_in_logs':'Device reported','device_silent_in_logs':'Device silent'}
    for (label,g),color,ls in zip(gaps.groupby('label'),['#D55E00','#009E73','#999999'],['-','--',':']):
        ecdf(gapax,g.duration_s,color=color,ls=ls,label=f'{labels.get(label,label)} (n = {len(g):,})')
    gapax.legend(frameon=False,fontsize=style.SMALL,loc='upper left',bbox_to_anchor=(0,1.24))
    for ax,label,xlab in [(b,'b','Segment duration (s)'),(gapax,'c','No-data gap duration (s)')]:
        ax.set(xscale='log',xlabel=xlab,ylabel='Cumulative proportion',ylim=(0,1.02));style.panel(ax,label,'')
    coverage['month']=pd.to_datetime(coverage.day).dt.to_period('M').astype(str);m=grouped(coverage,['month']);t=pd.to_datetime(m.month)
    density=np.where(m.active_h>=100,m.gps_fixes/m.active_h.where(m.active_h>0),np.nan)  # months with <100 active vehicle-hours are not shown
    c.plot(t,density,color=style.COLORS['gps'],marker='o',ms=3)
    c.set(ylabel='GPS fixes per active vehicle-hour',xlabel='Receipt month',ylim=(0,None));style.periods(c,labels=True);style.dates(c,interval=6);style.panel(c,'d','')
    fleet['month']=pd.to_datetime(fleet.day).dt.to_period('M').astype(str);f=grouped(fleet,['month']);share=np.where((f.km>0)&(f.month>='2025-06'),100*ratio(f.km_delayed,f.km),np.nan)
    d.plot(pd.to_datetime(f.month),share,color=style.COLORS['purple'],marker='o',ms=3);d.set(ylabel='Delayed distance (%)',xlabel='Receipt month',ylim=(0,100));style.periods(d,labels=True);style.dates(d,interval=6);style.panel(d,'e','')
    caption=('Telemetry sampling and observation gaps vary across streams and periods. a, Interval shares in unequal-width bins; operation uses receipt time and GPS uses device time. The first bin is [0, 1] s and subsequent bins are (a, b]; the dotted boundary marks the 60 s segmentation threshold. b, Conditional empirical cumulative distribution of positive-duration segments; the line marks the 5 min threshold. Zero-duration segments, including both single-record and same-second multiple-record segments, are reported separately and excluded from this CDF. c, Cumulative durations from the current gap table, grouped by log evidence; three gaps connect different vehicles\' usage periods of reused devices and do not represent continuous missing observation of the earlier vehicle. d, GPS fixes per active vehicle-hour (operating-stream active time) in each month; months with fewer than 100 active vehicle-hours are omitted. e, Delayed distance as a share of qualified odometer distance, from June 2025. Blue and grey shading mark unavailable odometer reporting and delayed delivery.')
    i.evidence('fig4_sampling',dict(interval_counts=totals,duration_counts=segments.groupby('kind')['count'].sum().to_dict(),odometer_km=float(f.km.sum()),delayed_km=float(f.km_delayed.sum()),gps_fixes_per_active_hour={str(k):(None if not np.isfinite(v) else float(v)) for k,v in zip(m.month,density)}),caption)
    style.save(fig,i.out,'fig4_sampling')

if __name__ == '__main__': main()
