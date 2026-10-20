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

def density(ax,h,pair,xedges,yedges,xlabel,ylabel):
    g=h[h.pair==pair];z=np.zeros((len(yedges)-1,len(xedges)-1),dtype=int)
    x=np.searchsorted(xedges,g.x_left);y=np.searchsorted(yedges,g.y_left);z[y,x]=g['count'].to_numpy()
    im=ax.pcolormesh(xedges,yedges,np.ma.masked_equal(z,0),norm=LogNorm(vmin=1,vmax=max(2,z.max())),cmap='cividis',rasterized=True)
    ax.set(xlabel=xlabel,ylabel=ylabel);return im,int(z.sum())


def main():
    i=Inputs();h=i.table('hist2d');one=i.table('hist1d');corr=i.table('correction');ref=i.release('refuels.parquet')
    fig,axes=plt.subplots(2,3,figsize=(style.DOUBLE,145*style.MM),layout='constrained');a,b,c,d,e,f=axes.flat
    populations={}
    specs=[(a,'vi',np.arange(0,521,5),np.arange(0,521,5),'Stack current (A)','Stack voltage (V)'),
           (b,'cells',np.arange(0,1201,10),np.arange(0,1201,10),'Minimum cell voltage (mV)','Maximum cell voltage (mV)'),
           (c,'power',np.arange(0,146,1),np.arange(0,241,2),'Requested power (kW)','Stack power (kW)')]
    for ax,pair,xe,ye,xlabel,ylabel in specs:
        im,n=density(ax,h,pair,xe,ye,xlabel,ylabel);populations[pair]=n
        fig.colorbar(im,ax=ax,fraction=.045,pad=.02,label='Records');ax.text(.03,.97,f'n = {n:,}',transform=ax.transAxes,va='top',fontsize=style.SMALL)
    b.plot([0,1200],[0,1200],ls='--',color=style.COLORS['anomaly'],lw=.8)
    style.panel(a,'a','Stack current–voltage');style.panel(b,'b','Cell-voltage consistency');style.panel(c,'c','Power tracking')
    p=one[one.field=='HyBottleP'].copy();pressure_total=int(p['count'].sum())
    quality=i.table('field_quality');zero=int(quality.loc[quality.field=='HyBottleP','zero'].sum())
    zero_bin=p.index[(p.bin_left<=0)&(p.bin_right>0)][0];p.loc[zero_bin,'count']-=zero
    assert p['count'].min()>=0
    # 1 MPa bins: the stored resolution (0.1 MPa) aliases against the 0.25 MPa statistics bins
    p=p.groupby(np.floor(p.bin_left))['count'].sum()
    d.bar(p.index,100*p/p.sum(),width=1,align='edge',color=style.COLORS['operation'],lw=0)
    d.set(xlabel='Hydrogen pressure (MPa)',ylabel='Non-zero readings (%)');d.text(.03,.95,f'Zero readings: {zero/pressure_total:.1%}',transform=d.transAxes,va='top',fontsize=style.SMALL);d.set_ylim(0,d.get_ylim()[1]*1.18);style.panel(d,'d','')
    rise_hist=i.table('pressure_rise');labels=['0–1','1–2','2–5','5–10','10–15','15–20','20–30','≥30']
    e.bar(range(len(rise_hist)),rise_hist['count'],color=style.COLORS['log'])
    e.set_xticks(range(len(labels)),labels,rotation=60,ha='right',rotation_mode='anchor');e.axvline(2.5,color='#555555',ls=':',lw=.8)
    e.set(xlabel='Positive pressure rise (MPa)',ylabel='Consecutive rises',yscale='log');e.text(.03,.95,f'n = {int(rise_hist["count"].sum()):,}',transform=e.transAxes,va='top',fontsize=style.SMALL);e.set_ylim(top=e.get_ylim()[1]*4);style.panel(e,'e','')
    rise=ref.p_after-ref.p_before
    counts=corr.drop(columns='vehicle_id').sum();rates=[counts.stored_equal_HyBottleP/counts.compared,counts.stored_equal_HighPPMNum/counts.compared,counts.corrected_equal/counts.compared]
    bars=f.barh([0,1,2],np.array(rates)*100,color=[style.COLORS['excluded'],style.COLORS['gps'],style.COLORS['log']],height=.6)
    f.set_yticks([0,1,2],['HyBottleP\nstored','HighPPMNum\nstored','Both\ncorrected']);f.invert_yaxis();f.set(xlabel='Agreement with decoded log (%)',xlim=(0,100))
    for bar,r in zip(bars,rates):
        inside=r>.5
        f.text(bar.get_width()-2 if inside else bar.get_width()+2,bar.get_y()+bar.get_height()/2,f'{r:.1%}',va='center',ha='right' if inside else 'left',color='white' if inside else '#222222',fontsize=style.SMALL)
    f.text(.98,.98,f'n = {int(counts.compared):,}',transform=f.transAxes,ha='right',va='top',fontsize=style.SMALL);style.panel(f,'f','Decoding-correction verification')
    caption = 'Physical plausibility and decoding correction. a–c, Joint distributions of stack current and voltage, cell-voltage extrema, and requested and stack power, for current above 1 A. Panel-specific sample sizes are shown; colour denotes logarithmic counts. The dashed line in b marks equality. d, Non-zero corrected tank pressures, with zero share annotated separately. e, Positive pressure rises; the dotted line marks 5 MPa. f, Agreement with decoded logs; Both corrected requires agreement of both fields.'
    i.evidence('fig6_physics',dict(density_records=populations,pressure_records=pressure_total,pressure_zero=zero,positive_pressure_rises=int(rise_hist['count'].sum()),refuels=len(ref),refuel_overflow=int(((rise<0)|(rise>40)).sum()),compared=int(counts.compared),agreement=rates),caption)
    style.save(fig,i.out,'fig6_physics')

if __name__ == '__main__': main()
