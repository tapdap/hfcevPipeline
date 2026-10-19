"""Cross-check figure and manuscript numbers against independent stats aggregates."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from figures.common import Inputs, write_json
from figures.number_checks import expected_key_number
import json
import math
import re
import numpy as np
import matplotlib.image as mpimg


def main():
    i=Inputs();checks=[]
    def eq(label, actual, expected):
        ok=math.isclose(float(actual),float(expected),rel_tol=1e-10,abs_tol=1e-7)
        checks.append(dict(check=label,actual=actual,expected=expected,passed=ok))
        if not ok: raise AssertionError(f'{label}: {actual} != {expected}')
    def evidence(name):return json.loads((i.out/f'{name}.json').read_text())['metrics']
    f2=evidence('fig2_coverage');f3=evidence('fig3_clocks');f5=evidence('fig5_quality');f6=evidence('fig6_physics');f7=evidence('fig7_consistency')
    for metric,key in [('vehicles','vehicles'),('vehicle_days','active_vehicle_days'),('peak_active','max_active_vehicles_per_day'),('odometer_km','odometer_km')]:eq('Fig.2 '+metric,f2[metric],i.n[key])
    eq('Fig.3 operation additions',f3['monthly_additions']['operation'],i.n['log_additions_operation'])
    eq('Fig.3 GPS additions',f3['monthly_additions']['gps'],i.n['log_additions_gps'])
    eq('Fig.5 fields',f5['fields'],i.n['fields_released']);eq('Fig.5 GPS fixes',f5['gps_fixes'],i.n['gps_fixes']);eq('Fig.5 status conservation',f5['status_total'],i.n['gps_fixes'])
    for field,value in f5['operation_records_per_field'].items():eq('Fig.5 '+field+' denominator',value,i.n['operation_records'])
    eq('Fig.6 refuels',f6['refuels'],i.n['refuels']);eq('Fig.6 pressure-rise range',f6['refuel_overflow'],0)
    for key in ['p10','p50','p90']:eq('Fig.7 '+key,f7[key],i.n['trip_gps_to_odometer_well_covered'][key])
    eq('Fig.7 well-covered trips',f7['well_covered_trips'],i.n['trip_gps_to_odometer_well_covered']['trips'])
    eq('Fig.7 replacement vehicles',f7['handover_vehicles'],i.n['vehicles_with_tbox_change'])
    h=i.table('handover');eq('Fig.7 handovers',f7['handovers'],len(h));eq('Fig.7 flagged',len(f7['flagged']),int((~h.odometer_consistent).sum()))
    lag=i.table('clock_lag');signed=i.table('clock_lag_signed');eq('Fig.3 signed-lag conservation',f3['lag_records'],lag.loc[lag.kind=='receipt_minus_time','count'].sum())
    eq('Fig.3 negative lag',f3['negative'],signed.loc[signed.bin_right<=0,'count'].sum())
    corr=i.table('correction').drop(columns='vehicle_id').sum();eq('Fig.6 compared',f6['compared'],corr.compared)
    for actual,key in zip(f6['agreement'],['stored_equal_HyBottleP','stored_equal_HighPPMNum','corrected_equal']):eq('Fig.6 '+key,actual,corr[key]/corr.compared)
    f4=evidence('fig4_sampling');eq('Fig.4 qualified distance',f4['odometer_km'],i.n['odometer_km']);eq('Fig.4 delayed distance',f4['delayed_km'],i.n['odometer_km_delayed'])
    q=i.table('field_quality')
    eq('Fig.6 zero pressure',f6['pressure_zero'],q.loc[q.field=='HyBottleP','zero'].sum())
    eq('Fig.6 positive rises',f6['positive_pressure_rises'],i.table('pressure_rise')['count'].sum())
    for field in ['HyBottleP','HighPPMNum']:
        for mask in ['outside_encodable','all_ones','log_mismatch']:
            eq(field+' corrected '+mask,q.loc[q.field==field,mask].sum(),0)
    numbers=json.loads((i.out/'numbers.json').read_text())
    for macro,record in numbers.items():
        value=expected_key_number(i.n,macro,record['source'])
        if value is not None:
            eq('Macro '+macro,record['value'],value)
    images=[]
    for p in sorted(i.out.glob('fig[2-7]_*.png')):
        image=mpimg.imread(p);height,width=image.shape[:2]
        eq(p.name+' width',width,int(183/25.4*300))
        assert np.std(image[:,:,:3])>.02 and (p.with_suffix('.pdf')).stat().st_size>1000
        images.append(dict(file=p.name,width=width,height=height))
    eq('PNG count',len(images),6)
    for p in i.out.iterdir():
        if p.suffix in ('.tex','.csv','.json','.txt'):
            text=p.read_text()
            assert not re.search(r'QLKJ\d{13}',text),f'Device identifier in {p.name}'
            tokens=re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b',text)
            assert not any(any(c.isalpha() for c in t) and any(c.isdigit() for c in t) for t in tokens),f'VIN-like token in {p.name}'
    write_json(i.out/'acceptance.json',dict(checks=checks,all_passed=True,images=images,
               scope_notes=[dict(metric='Daily-summary records vs release records',daily_records=f2['daily_records'],release_records=i.n['operation_records'],difference=f2['daily_records']-i.n['operation_records'],reason='Daily summaries may retain installation-boundary day contributions; neither the pipeline nor release totals are changed.'),
                   dict(metric='Segment populations',stage4_segments=i.n['segments'],released_segments=f4['duration_counts']['segments'],reason='key_numbers counts stage-4 segments; Fig.4 counts released segments selected during assembly.')],
               limitations=['Fig.7b compares device medians; boxes summarize devices, not events.',
                            'Protocol resolutions follow the released summary; unit bases follow the dictionary.',
                            'Released GPS coordinates are rounded to 0.01 degree.','Coverage replacement ticks use first observed unit records.']))
    preview=[r'\documentclass[10pt]{article}',r'\usepackage[a4paper,margin=12mm]{geometry}',
             r'\usepackage{booktabs,longtable,array}',r'\newcommand{\TODO}[1]{\textbf{TODO: #1}}',r'\begin{document}']
    for name in ['tab1_fleet','tab2_fields','tab3_accounting']:
        preview += [(i.out/f'{name}.tex').read_text(),r'\clearpage']
    preview += [r'\end{document}']
    (i.out/'tables_preview.tex').write_text('\n'.join(preview))
    print(f'Figure verification: {len(checks)} numerical checks passed; {len(images)} PNG/PDF pairs.')

if __name__=='__main__':main()
