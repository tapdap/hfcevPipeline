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
    i=Inputs();q=grouped(i.table('field_quality'),['field']);dictionary=i.release('dictionary.csv')
    d=dictionary[dictionary.table=='operation'].drop_duplicates('column');t=q.merge(d,left_on='field',right_on='column',validate='one_to_one')
    resolutions=i.table('field_resolution').set_index('field').resolution.to_dict()
    rows=[]
    ordered=['main_state','sub_state','CurrentCmd','BOPTarCur','FcCur','FcVol','MaxCellV','MinCellV','CoolInTarTemp','CoolInActTemp','CoolOutActTemp','RadInTemp','RadOutTemp','EnvTemp','air_temp','air_surge_p','rad_duty','HySysState','MainValveStat','HyMidP','HighPPM','HighPPMNum','HyBottleP','TotH2Kg','hydncsu','SOC','TarDCCur','VCUTarPower','TotKM','ISO','FaultLevel','FaultCode']
    t['order']=t.field.map({f:j for j,f in enumerate(ordered)})
    for r in t.sort_values('order').itertuples():
        rows.append(dict(Field=r.field,Description=r.description+('; '+r.note if r.note else ''),Unit=r.unit or '—',Basis=r.unit_basis or 'none',
                         **{'Missing (%)':f'{100*r.missing/r.records:.3f}','Zero (%)':f'{100*r.zero/r.records:.3f}'},
                         Resolution=(str(resolutions.get(r.field,''))+' '+(r.unit or '')).strip() if resolutions.get(r.field,'') else r'\TODO{Not mapped}'))
    frame=pd.DataFrame(rows);frame.to_csv(i.out/'tab2_fields.csv',index=False)
    widths=[31,52,10,13,14,14,22]
    lines=['% Requires longtable and booktabs; TODO entries require the manuscript TODO macro.',r'\begingroup',r'\scriptsize',r'\setlength{\tabcolsep}{2pt}',
           r'\begin{longtable}{'+''.join('p{'+str(x)+'mm}' for x in widths)+'}',r'\caption{Released measured fields. Percentages use all records per field as denominator and are rounded to three decimal places; 0.000\% does not establish zero missing records. Unit basis follows the release dictionary: dbc, stated in the CAN protocol; provider, confirmed by the data provider; none, not applicable.}\label{tab:fields}'+r'\\',
           r'\toprule',' & '.join(tex(x) for x in frame.columns)+r' \\',r'\midrule',r'\endfirsthead',r'\toprule',' & '.join(tex(x) for x in frame.columns)+r' \\',r'\midrule',r'\endhead']
    for row in frame.itertuples(index=False,name=None):lines.append(' & '.join(str(v) if str(v).startswith(r'\TODO{') else tex(v) for v in row)+r' \\')
    lines.extend([r'\bottomrule',r'\end{longtable}',r'\endgroup'])
    (i.out/'tab2_fields.tex').write_text('\n'.join(lines)+'\n')
    i.evidence('tab2_fields',dict(fields=len(frame),resolution_todos=int(frame.Resolution.str.startswith('\\TODO').sum())), 'Resolution uses absolute protocol scaling factors; units follow the dictionary. Unmapped fields remain TODO.')

if __name__ == '__main__': main()
