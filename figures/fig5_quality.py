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
    i=Inputs(); q=i.table('field_quality'); gps=i.table('gps_quality')
    m=grouped(q,['field','month']);m['missing_share']=100*ratio(m.missing,m.records)
    full=m.pivot(index='field',columns='month',values='missing_share'); totals=grouped(q,['field']).set_index('field').reindex(full.index)
    selected=full.max(axis=1)>=.01;heat=full.loc[selected]
    fig,(a,b)=plt.subplots(2,1,figsize=(style.DOUBLE,122*style.MM),layout='constrained')
    months=pd.to_datetime(heat.columns); edges=mdates.date2num(list(months)+[months[-1]+pd.offsets.MonthBegin()])
    cmap=plt.get_cmap('Blues').copy();cmap.set_bad('white')
    im=a.pcolormesh(edges,np.arange(len(heat)+1),heat.to_numpy(),vmin=0,vmax=100,cmap=cmap)
    a.set_yticks(np.arange(len(heat))+.5,heat.index,fontsize=style.SMALL);a.invert_yaxis();a.set_xlabel('Receipt month');style.dates(a)
    a.axvline(pd.Timestamp('2025-06-01'),color='#222222',ls=':',lw=.8)
    a.text(pd.Timestamp('2025-06-01'),1.02,'June 2025',transform=a.get_xaxis_transform(),fontsize=style.SMALL)
    style.panel(a,'a','');fig.colorbar(im,ax=a,fraction=.025,pad=.02,label='Missing (%)')
    g=grouped(gps,['month']);cols=['fix_valid','fix_valid_assumed','fix_no_fix','fix_no_fix_inferred','fix_static_while_moving'];bottom=np.zeros(len(g))
    for label,fields,color in [('Valid / assumed',cols[:2],style.COLORS['operation']),('No fix',cols[2:4],style.COLORS['gps']),('Stuck',cols[4:],style.COLORS['purple'])]:
        share=100*ratio(g[fields].fillna(0).sum(axis=1),g.fixes)
        b.bar(pd.to_datetime(g.month)+pd.Timedelta(days=14),share,bottom=bottom,label=label,color=color,width=24);bottom+=share
    b.set(ylim=(0,100),ylabel='GPS fixes (%)',xlabel='Device month');style.dates(b);b.legend(ncol=3,loc='lower right',bbox_to_anchor=(1,1.0),frameon=False,fontsize=style.SMALL)
    style.panel(b,'b','')
    caption=('Field availability changes around June 2025, while the GPS status distribution combines directly reported and inferred fix status. a, Monthly missing shares for fields reaching 0.01% missing in at least one month; the dotted line marks June 2025. Remaining fields are complete to within 0.01% in each observed month: '+', '.join(full.index[~selected])+'. b, Valid / assumed combines valid and valid-assumed fixes, before the additional usable-field clock and uniqueness checks; no fix combines reported and inferred no-fix status; stuck denotes static positions while moving. Direct result codes are available only where receive logs provide them. The displayed device-month axis begins in October 2024; earlier fixes remain included in fleet totals.')
    i.evidence('fig5_quality',dict(fields=len(full),shown_fields=list(heat.index),gps_fixes=int(g.fixes.sum()),status_total=int(g[cols].sum().sum()),operation_records_per_field=totals.records.astype(int).to_dict()),caption)
    style.save(fig,i.out,'fig5_quality')

if __name__ == '__main__': main()
