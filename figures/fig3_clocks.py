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
    i=Inputs(); lag=i.table('clock_lag_signed'); clock=i.table('clock'); added=i.table('log_additions_monthly'); grid=i.table('alignment_tolerance')
    fig=plt.figure(figsize=(style.DOUBLE,155*style.MM),layout='constrained')
    gs=fig.add_gridspec(2,3);a=fig.add_subplot(gs[0,0]);device=fig.add_subplot(gs[0,1]);b=fig.add_subplot(gs[0,2]);c=fig.add_subplot(gs[1,:2]);d=fig.add_subplot(gs[1,2])
    finite=lag[np.isfinite(lag.bin_left)&np.isfinite(lag.bin_right)]
    total=int(lag['count'].sum()); negative=int(lag.loc[lag.bin_right<=0,'count'].sum())
    overflow=int(lag.loc[~np.isfinite(lag.bin_right),'count'].sum())
    a.bar(finite.bin_left,100*finite['count']/total,width=.05,align='edge',color=np.where(finite.bin_left<0,style.COLORS['anomaly'],style.COLORS['operation']),lw=0)
    a.axvline(0,color='#333333',lw=.6); a.set(xlabel='Receipt − stored time (s)',ylabel='Records per bin (%)',xlim=(-1,1.5))
    a.text(.98,.55,f'n = {total:,}\nnegative: {negative/total:.1%}\n> 2 s: {overflow:,}',transform=a.transAxes,va='top',ha='right',fontsize=style.SMALL)
    style.panel(a,'a','Signed receipt-clock difference')
    device_lag=i.table('clock_lag');g=device_lag[device_lag.kind=='receipt_minus_device'];positive=g[(g.bin_left>0)&np.isfinite(g.bin_right)]
    device.stairs(100*positive['count'].to_numpy()/g['count'].sum(),np.r_[positive.bin_left.to_numpy(),positive.bin_right.iloc[-1]],color=style.COLORS['operation'])
    device.set(xscale='log',xlabel='Receipt − device time (s)',ylabel='Records per bin (%)')
    device.text(.03,.96,f'n = {int(g["count"].sum()):,}',transform=device.transAxes,va='top',fontsize=style.SMALL)
    style.panel(device,'b','')
    columns=[x for x in clock if x.startswith('log_clock_')]
    counts=clock[columns].sum(); colors=[style.COLORS['log'],style.COLORS['anomaly'],style.COLORS['purple'],style.COLORS['excluded']]
    labels={'log_clock_verified':'Verified','log_clock_clock_lag_out_of_range':'Lag out of range','log_clock_clock_out_of_order':'Out of order','log_clock_clock_reused':'Reused time'}
    columns=sorted(columns,key=lambda k:k!='log_clock_verified')
    for j,(col,color) in enumerate(zip(columns,colors)):
        share=100*counts[col]/counts.sum()
        b.barh(j,share,color=color)
        inside=share>50
        b.text(share-2 if inside else share+2,j,f'{int(counts[col]):,}',va='center',ha='right' if inside else 'left',color='white' if inside else '#222222',fontsize=style.SMALL)
    b.set(yticks=range(len(columns)),yticklabels=[labels[x] for x in columns],xlabel='Log messages (%)',xlim=(0,105))
    b.invert_yaxis()
    style.panel(b,'c','Device-clock checks (pre-assembly)')
    months=pd.date_range(pd.Timestamp(i.n['period'][0]).to_period('M').start_time,pd.Timestamp(i.n['period'][1]).to_period('M').start_time,freq='MS')
    totals={}
    for stream,color,marker in [('operation',style.COLORS['operation'],'o'),('gps',style.COLORS['gps'],'s')]:
        z=added[added.stream==stream].groupby('month')['count'].sum();z.index=pd.to_datetime(z.index)
        z=z.reindex(months,fill_value=0);totals[stream]=int(z.sum())
        c.bar(z.index+pd.Timedelta(days=-5 if stream=='operation' else 5),z.values,width=10,color=color,label=f"{'GPS' if stream=='gps' else 'Operation'} (n = {int(z.sum()):,})")
    c.yaxis.set_major_formatter(plt.FuncFormatter(lambda v,_:f'{v:,.0f}'));c.set(ylabel='Released log additions',xlabel='Receipt month');style.periods(c, labels=True);style.dates(c);c.legend(frameon=False,fontsize=style.SMALL)
    style.panel(c,'d','Log supplementation by month')
    hits={}
    for mode,linestyle,color in [('nearest','-',style.COLORS['operation']),('backward','--',style.COLORS['gps'])]:
        g=grouped(grid[grid['mode']==mode],['tolerance_s']);d.plot(g.tolerance_s,100*ratio(g.hit,g.n),linestyle=linestyle,color=color,marker='o',ms=3,label=mode.title())
        hits[mode]=float(g.loc[g.tolerance_s==10,'hit'].sum()/g.loc[g.tolerance_s==10,'n'].sum())
    d.axvline(10,color=style.COLORS['excluded'],ls=':',lw=.8);d.set(xlabel='Tolerance (s)',ylabel='Paired operation records (%)',ylim=(0,80));d.legend(frameon=False)
    d.text(.97,.05,f"nearest, 10 s: {hits['nearest']:.1%}",transform=d.transAxes,ha='right',fontsize=style.SMALL)
    style.panel(d,'e','CAN–GPS alignment (pre-assembly)')
    caption=('Receipt and stored timestamps agree at sub-second scale for most matched records, while device timestamps require clock checks. a, Receipt minus stored time in 0.05 s bins; the main distribution is consistent with rounding stored receipt times to the nearest second, while log additions retain second truncation (Methods); negative values and two observations above 2 s are retained in the counts. b, Positive receipt-minus-device lags in the original logarithmic bins; non-positive and tail counts remain in the denominator. c, Log-message clock classifications. d, Monthly additions to the released streams occur in months with receive-log coverage. Shading identifies unavailable odometer reporting and delayed delivery. e, Nearest and backward GPS pairing rates: zero tolerance requires coincident timestamps, while 5 s accommodates the nominal GPS interval. Counts in c and e are before installation exclusions (Methods).')
    i.evidence('fig3_clocks',dict(lag_records=total,negative=negative,over_2_s=overflow,clock_messages=int(counts.sum()),monthly_additions=totals,alignment_10s=hits),caption)
    style.save(fig,i.out,'fig3_clocks')

if __name__ == '__main__': main()
