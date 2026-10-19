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
    i=Inputs();a=grouped(i.table('accounting'),['stream','step']);checks={}
    for stream in ['operation','gps']:
        g=a[a.stream==stream].set_index('step').rows
        harmonized=int(g['0_input']-sum(v for k,v in g.items() if k.startswith('1_')))
        released=int(g['2_harmonized']+g['3_log_additions']-g['4_excluded_outside_installation'])
        assert harmonized==g['2_harmonized'] and released==g['5_released']
        checks[stream]=dict(harmonized=harmonized,released=released)
    data={stream:a[a.stream==stream].set_index('step').rows for stream in ['operation','gps']}
    specs=[('Input rows',['0_input']),('Missing timestamps',['1_null_time']),('Outside time window',['1_outside_window']),('Exact duplicates removed',['1_retired_can_exact_duplicate','1_retired_run_exact_duplicate','1_retired_duplicate']),('Legacy rows matched to CAN',['1_retired_run_matches_can_shared_fields']),('Harmonised',['2_harmonized']),('Added from receive logs',['3_log_additions']),('Outside unit installation',['4_excluded_outside_installation']),('Released',['5_released'])]
    rows=[]
    for label,keys in specs:
        row={'Processing step':label}
        for stream,col in [('operation','Operation'),('gps','GPS')]:
            n=int(sum(data[stream].get(k,0) for k in keys));den=int(data[stream]['0_input'])
            row[col]=f'{n:,} ({100*n/den:.2f}%)'
        rows.append(row)
    frame=pd.DataFrame(rows)
    table_output(i,'tab3_accounting',frame,'Record accounting for operating and GPS streams. Percentages use input rows of the corresponding stream. Removal and addition rows describe transitions; totals are not additive.','tab:accounting')
    logs=a[a.stream.isin(['log_messages','log_gps_fixes'])].copy()
    logs['step']=logs.step.str.removeprefix('disposition_').str.replace('not_candidate:','Not used: ',regex=False).str.replace('_',' ',regex=False)
    logs.to_csv(i.out/'supplement_log_accounting.csv',index=False)
    i.evidence('tab3_accounting',checks,'Conservation checked independently for operation and GPS streams.')

if __name__ == '__main__': main()
