"""Generate this publication artifact from documented figure inputs."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from figures.common import Inputs, grouped, ratio, write_json, table_output, tex
from figures import style
import itertools
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LogNorm

def main():
    i=Inputs();n=i.n;values={};sources={}
    mapping={'NumVehicles':'vehicles','NumInstallations':'tbox_installations','NumVehiclesWithChange':'vehicles_with_tbox_change',
             'NumVehicleDays':'active_vehicle_days','NumRecords':'operation_records','NumGPSFixes':'gps_fixes',
             'NumLogAdditionsOperation':'log_additions_operation','NumLogAdditionsGPS':'log_additions_gps','NumDeviceTimeRecords':'records_with_device_time',
             'NumOdometerKm':'odometer_km','NumDelayedKm':'odometer_km_delayed','NumGPSKm':'gps_track_km','NumFCOnHours':'fc_on_hours',
             'NumTrips':'trips','NumMovingTrips':'trips_moving','NumRefuels':'refuels','NumFields':'fields_released','NumSegments':'segments',
             'NumPeakActiveVehicles':'max_active_vehicles_per_day','NumReleaseBytes':'release_bytes'}
    for macro,key in mapping.items():values[macro]=n[key];sources[macro]='key_numbers.json:'+key
    for macro,q in [('NumDistanceRatioLow','p10'),('NumDistanceRatioMedian','p50'),('NumDistanceRatioHigh','p90')]:
        values[macro]=n['trip_gps_to_odometer_well_covered'][q];sources[macro]='key_numbers.json:trip_gps_to_odometer_well_covered.'+q
    h2=n['hydrogen']
    for macro,key in [('NumHTwoKg','kg_attributed'),('NumHTwoWraps','wraps'),('NumHTwoWrapsInferred','wraps_inferred'),('NumHTwoBackward','backward'),
                      ('NumHTwoPerHundredKm','fleet_kg_per_100km'),('NumHTwoPerKWh','fleet_kg_per_kwh')]:
        values[macro]=h2[key];sources[macro]='key_numbers.json:hydrogen.'+key
    for group,stem in [('vehicle_kg_per_100km','NumHTwoPerHundredKm'),('vehicle_kg_per_kwh','NumHTwoPerKWh')]:
        for q,suffix in [('p10','Low'),('p50','Median'),('p90','High')]:
            values[stem+suffix]=h2[group][q];sources[stem+suffix]=f'key_numbers.json:hydrogen.{group}.{q}'
    g=n['gps_proxy_check'];ci=n['clock_integration'];rf=n['refuels_intervals'];wc=n['trip_gps_to_odometer_well_covered']
    for macro,value,source in [
            ('NumProxyKnown',g['known'],'gps_proxy_check.known'),('NumProxyNoFix',g['result0'],'gps_proxy_check.result0'),
            ('NumProxyNoFixFlagged',g['proxy_on_result0'],'gps_proxy_check.proxy_on_result0'),
            ('NumProxyNoFixFlaggedPercent',100*g['proxy_on_result0']/g['result0'],'proxy_on_result0/result0'),
            ('NumProxyValidFlagged',g['proxy_on_result1'],'gps_proxy_check.proxy_on_result1'),
            ('NumProxyMissed',g['result0']-g['proxy_on_result0'],'result0 - proxy_on_result0'),
            ('NumProxyMissedRepeating',g['result0_missed_repeating'],'gps_proxy_check.result0_missed_repeating'),
            ('NumProxyMissedRepeatingPercent',100*g['result0_missed_repeating']/(g['result0']-g['proxy_on_result0']),'missed_repeating/missed'),
            ('NumProxyMissedNew',g['result0_missed_new_position'],'gps_proxy_check.result0_missed_new_position'),
            ('NumProxyMissedNewPercent',100*g['result0_missed_new_position']/g['result0'],'missed_new_position/result0'),
            ('NumClockPairs',int(ci['pairs']),'clock_integration.pairs'),
            ('NumClockOnTimeExcessPercent',100*(ci['on_time_ratio']-1),'clock_integration.on_time_ratio - 1'),
            ('NumClockEnergyExcessPercent',100*(ci['energy_ratio']-1),'clock_integration.energy_ratio - 1'),
            ('NumClockPairsDiffPercent',100*ci['pairs_diff_over_2s']/ci['pairs'],'pairs_diff_over_2s/pairs'),
            ('NumRefuelsOverDay',rf['interval_over_24h'],'refuels_intervals.interval_over_24h'),
            ('NumRefuelsWithinHour',rf['next_within_1h'],'refuels_intervals.next_within_1h'),
            ('NumRefuelsAcrossGaps',rf['across_segments'],'refuels_intervals.across_segments'),
            ('NumWellCoveredCandidates',wc['candidates'],'trip_gps_to_odometer_well_covered.candidates'),
            ('NumWellCoveredKmSharePercent',100*wc['km_share'],'trip_gps_to_odometer_well_covered.km_share')]:
        values[macro]=value;sources[macro]='key_numbers.json:'+source
    # Formula provenance is distinct from a direct JSON field path.
    derived_sources={
        'NumProxyNoFixFlaggedPercent':'100 * gps_proxy_check.proxy_on_result0 / gps_proxy_check.result0',
        'NumProxyMissed':'gps_proxy_check.result0 - gps_proxy_check.proxy_on_result0',
        'NumProxyMissedRepeatingPercent':'100 * gps_proxy_check.result0_missed_repeating / (gps_proxy_check.result0 - gps_proxy_check.proxy_on_result0)',
        'NumProxyMissedNewPercent':'100 * gps_proxy_check.result0_missed_new_position / gps_proxy_check.result0',
        'NumClockOnTimeExcessPercent':'100 * (clock_integration.on_time_ratio - 1)',
        'NumClockEnergyExcessPercent':'100 * (clock_integration.energy_ratio - 1)',
        'NumClockPairsDiffPercent':'100 * clock_integration.pairs_diff_over_2s / clock_integration.pairs',
        'NumWellCoveredKmSharePercent':'100 * trip_gps_to_odometer_well_covered.km_share',
    }
    for macro,expression in derived_sources.items():sources[macro]='formula (key_numbers.json): '+expression
    # stack efficiency on the hydrogen lower heating value, 33.33 kWh/kg
    values['NumStackEfficiencyPercent']=100/(h2['fleet_kg_per_kwh']*33.33);sources['NumStackEfficiencyPercent']='1/(hydrogen.fleet_kg_per_kwh x 33.33 kWh/kg)'
    values['NumWellCoveredTrips']=n['trip_gps_to_odometer_well_covered']['trips'];sources['NumWellCoveredTrips']='key_numbers.json:trip_gps_to_odometer_well_covered.trips'
    c=i.table('correction').drop(columns='vehicle_id').sum();lag=i.table('clock_lag_signed');h=i.table('handover')
    # stack voltage at a fixed operating point across TBOX handovers (vehicle identity check)
    sv=i.table('stack_voltage');sv=sv[sv.records>=50].copy();sv['mi']=pd.PeriodIndex(sv.month,freq='M').astype('int64')
    jumps=[]
    for r in h.itertuples():
        old=sv[(sv.vehicle_id==r.vehicle_id)&(sv.unit==r.from_unit)].sort_values('mi');new=sv[(sv.vehicle_id==r.vehicle_id)&(sv.unit==r.to_unit)].sort_values('mi')
        if len(old) and len(new):jumps.append(abs(new.v_median.iloc[0]-old.v_median.iloc[-1]))
    within=np.abs(np.concatenate([np.diff(g.sort_values('mi').v_median)[np.diff(g.sort_values('mi').mi)==1] for _,g in sv.groupby(['vehicle_id','unit'])]))
    vm=sv.groupby(['vehicle_id','mi']).v_median.median().reset_index()
    between=np.array([abs(x-y) for _,g in vm.groupby('mi') for x,y in itertools.combinations(g.v_median,2)])
    for macro,value,source in [('NumStackHandovers',len(jumps),'handovers with >= 50 records in a month on both sides'),
                               ('NumStackHandoverMedianV',float(np.median(jumps)),'median |dV| across handovers'),
                               ('NumStackWithinMedianV',float(np.median(within)),'median |dV| consecutive months, same installation'),
                               ('NumStackBetweenMedianV',float(np.median(between)),'median |dV| different vehicles, same month'),
                               ('NumStackBetweenHighV',float(np.percentile(between,90)),'p90 |dV| different vehicles, same month')]:
        values[macro]=value;sources[macro]='stack_voltage (>= 50 records per vehicle-unit-month): '+source
    extra={'NumCorrectionCompared':(int(c.compared),'correction.compared.sum'),
           'NumCorrectedAgreementPercent':(100*float(c.corrected_equal/c.compared),'correction.corrected_equal/compared'),
           'NumStoredPressureAgreementPercent':(100*float(c.stored_equal_HyBottleP/c.compared),'correction.stored_equal_HyBottleP/compared'),
           'NumStoredCountAgreementPercent':(100*float(c.stored_equal_HighPPMNum/c.compared),'correction.stored_equal_HighPPMNum/compared'),
           'NumNegativeClockLags':(int(lag.loc[lag.bin_right<=0,'count'].sum()),'clock_lag_signed:negative'),
           'NumHandovers':(len(h),'handover:rows'),'NumFlaggedHandovers':(int((~h.odometer_consistent).sum()),'handover:odometer_consistent=false')}
    for macro,(value,source) in extra.items():values[macro]=value;sources[macro]=source
    acc=i.table('accounting')
    for stream,macro in [('operation','NumHarmonisedOperation'),('gps','NumHarmonisedGPS')]:
        values[macro]=int(acc.loc[(acc.stream==stream)&(acc.step=='2_harmonized'),'rows'].sum());sources[macro]='accounting:'+stream+':2_harmonized'
    values['NumHandoverMedianKmPerDay']=float(h.km_per_day.median());sources['NumHandoverMedianKmPerDay']='handover:km_per_day.median'
    grid=i.table('alignment_tolerance')
    for tol in (0,10,30):
        g=grid[(grid['mode']=='nearest')&(grid.tolerance_s==tol)]
        macro='NumAlignment'+{0:'Zero',10:'Ten',30:'Thirty'}[tol]+'Percent';values[macro]=100*float(g.hit.sum()/g.n.sum());sources[macro]=f'alignment_tolerance:nearest:{tol}'
    lines=['% Generated from statistics; no manually copied manuscript numbers.'];manifest={}
    for macro,value in values.items():
        display=(f'{value:.4f}' if macro.startswith('NumHTwoPerKWh') else f'{value:.2f}' if macro.startswith('NumHTwoPerHundredKm')
                 else f'{value:.1f}' if macro=='NumStackEfficiencyPercent' or (macro.startswith(('NumProxy','NumClock','NumWellCoveredKm')) and 'Percent' in macro) else f'{value:.0f}' if macro.startswith('NumStack')
                 else f'{value:.3f}' if 'Ratio' in macro or 'KmPerDay' in macro or 'Percent' in macro else f'{value:,.0f}')
        lines.append(r'\newcommand{'+chr(92)+macro+'}{'+display.replace(',', '{,}')+'}')
        manifest[macro]=dict(value=value,display=display,source=sources[macro])
    for macro,value in [('DatasetStart',n['period'][0][:10]),('DatasetEnd',n['period'][1][:10])]:
        lines.append(r'\newcommand{'+chr(92)+macro+'}{'+value+'}');manifest[macro]=dict(value=value,display=value,source='key_numbers.json:period')
    (i.out/'numbers.tex').write_text('\n'.join(lines)+'\n');write_json(i.out/'numbers.json',manifest)

if __name__ == '__main__': main()
