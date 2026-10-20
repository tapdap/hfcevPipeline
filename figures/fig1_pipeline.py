"""Fig. 1: vehicle and data acquisition, processing stages and release package (schematic; counts from stats)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from figures.common import Inputs, grouped, write_json
from figures import style
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Ellipse, Rectangle, FancyArrowPatch

W, H = 183, 149                      # mm
INK, MUTED, LINE = '#1F2D3A', '#4A5A68', '#6B7B88'
FILL = dict(power='#E8F3EC', acq='#FCF3DC', proc='#FFFFFF', src='#EAF1F8', veh='#F3ECF6', rel='#EAF5EF', panel='#F6F8FA')
EDGE = dict(power='#3D8B5A', acq='#C9A227', proc='#4F6D8A', src='#4F6D8A', veh='#8A5AA0', rel='#3D8B5A', panel='#D5DDE4')
FS, FS_S, FS_T = style.TICK, style.SMALL - .5, style.BASE + .5


def box(ax, x, y, w, h, kind, r=1.6, lw=.8):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={r}', fc=FILL[kind], ec=EDGE[kind], lw=lw))


def text(ax, x, y, s, size=FS, weight='normal', color=INK, ha='left', va='top', **kw):
    ax.text(x, y, s, fontsize=size, fontweight=weight, color=color, ha=ha, va=va, linespacing=1.25, **kw)


def arrow(ax, p, q, color=LINE, lw=.9, style_='-|>', ls='-', both=False):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=('<|-|>' if both else style_), mutation_scale=7, color=color, lw=lw, ls=ls,
                                 shrinkA=0, shrinkB=0))


def tag(ax, x, y, s, kind):
    ax.add_patch(FancyBboxPatch((x, y - 3.2), 6.4, 3.6, boxstyle='round,pad=0,rounding_size=.8', fc=EDGE[kind], ec='none'))
    text(ax, x + 3.2, y - 1.4, s, size=FS_S, weight='bold', color='white', ha='center', va='center')


def signals(ax, x, y, w, s):
    ax.add_patch(FancyBboxPatch((x, y), w, 6.2, boxstyle='round,pad=0,rounding_size=1', fc='white', ec=EDGE['acq'], lw=.6, ls=(0, (2, 1.5))))
    text(ax, x + w / 2, y + 3.1, s, size=FS_S, color=MUTED, ha='center', va='center')


def tank(ax, x, y):
    for k in range(3):
        cx = x + 3 + k * 4.6
        ax.add_patch(Rectangle((cx - 1.8, y), 3.6, 8, fc='#CFE6D7', ec=EDGE['power'], lw=.6))
        ax.add_patch(Ellipse((cx, y), 3.6, 1.6, fc='#CFE6D7', ec=EDGE['power'], lw=.6))
        ax.add_patch(Ellipse((cx, y + 8), 3.6, 1.6, fc='#CFE6D7', ec=EDGE['power'], lw=.6))


def stack(ax, x, y):
    ax.add_patch(Rectangle((x, y), 13, 8.5, fc='#DCE7F1', ec=EDGE['proc'], lw=.6))
    for k in range(7):
        ax.plot([x + 1.6 + k * 1.65] * 2, [y + 1.2, y + 7.3], color='#8DA6BD', lw=1.1)


def battery(ax, x, y):
    ax.add_patch(Rectangle((x, y), 12, 7, fc='#EEF2F5', ec=MUTED, lw=.6))
    ax.add_patch(Rectangle((x + 12, y + 2.3), 1.2, 2.4, fc=MUTED, ec='none'))
    for k in range(3):
        ax.add_patch(Rectangle((x + 1.2 + k * 3.6, y + 1.2), 2.6, 4.6, fc='#9BC5A6', ec='none'))


def fmt(v):
    return f'{v:,.0f}'


def main():
    i = Inputs(); n = i.n
    acc = grouped(i.table('accounting'), ['stream', 'step']).set_index(['stream', 'step'])['rows']
    a = lambda stream, step: int(acc.get((stream, step), 0))
    fc_runs = len(i.release('fc_runs.parquet'))

    fig = plt.figure(figsize=(W * style.MM, H * style.MM))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.axis('off')

    # ---------------------------------------------------------------- a: vehicle and acquisition
    ax.add_patch(FancyBboxPatch((2, 2), 179, 60, boxstyle='round,pad=0,rounding_size=2', fc=FILL['panel'], ec=EDGE['panel'], lw=.7))
    text(ax, 4, 4.2, 'a', size=style.LETTER, weight='bold'); text(ax, 9, 4.6, 'Vehicle and data acquisition', size=FS_T, weight='bold')
    box(ax, 5, 11, 88, 49, 'power', lw=.7); text(ax, 7, 12.5, 'Fuel-cell powertrain', size=FS, weight='bold', color=EDGE['power'])
    tank(ax, 10, 20); text(ax, 18, 30.5, 'Hydrogen tanks\n6 × 410 L, 35 MPa', size=FS_S, ha='center')
    signals(ax, 6.5, 37.5, 25, 'HyBottleP · TotH2Kg\nhydncsu')
    arrow(ax, (28, 24), (46, 24)); text(ax, 37, 25, 'H$_2$', size=FS_S, ha='center', color=MUTED)
    stack(ax, 47, 19.75); text(ax, 53.5, 30.5, 'Fuel-cell system\n143 kW, 478 cells', size=FS_S, ha='center')
    signals(ax, 64, 20.9, 27, 'FcVol · FcCur\ncell V · coolant T')
    arrow(ax, (53.5, 37), (53.5, 46.8))
    battery(ax, 12, 47); text(ax, 18, 55.5, 'Battery 117 kWh', size=FS_S, ha='center')
    ax.add_patch(Rectangle((47, 47), 13, 7, fc='white', ec=MUTED, lw=.6)); text(ax, 53.5, 50.5, 'DC/DC', size=FS_S, ha='center', va='center')
    text(ax, 53.5, 55.5, 'to 220 kW motor', size=FS_S, ha='center', color=MUTED)
    arrow(ax, (26.5, 50.5), (46.6, 50.5), both=True)
    signals(ax, 64, 47.4, 27, 'SOC · VCUTarPower\nTotKM')
    # acquisition chain
    text(ax, 100, 12.5, 'Data acquisition', size=FS, weight='bold', color='#9A7A10')
    arrow(ax, (93.4, 24), (101, 24)); text(ax, 97.2, 23, 'CAN', size=FS_S, ha='center', va='bottom', color=MUTED)
    box(ax, 101, 17.5, 19, 13, 'acq'); text(ax, 110.5, 20.6, 'TBOX', size=FS, weight='bold', ha='center')
    text(ax, 110.5, 25.2, 'CAN frames\n+ GPS', size=FS_S, ha='center', color=MUTED)
    arrow(ax, (120, 24), (128, 24)); text(ax, 124, 23, 'MQTT', size=FS_S, ha='center', va='bottom', color=MUTED)
    box(ax, 128, 17.5, 18, 13, 'src'); text(ax, 137, 20.6, 'Server', size=FS, weight='bold', ha='center')
    text(ax, 137, 25.2, 'receipt\ntime', size=FS_S, ha='center', color=MUTED)
    arrow(ax, (146, 21), (152, 17)); arrow(ax, (146, 27), (152, 31))
    box(ax, 152, 11, 27, 10.5, 'src'); text(ax, 165.5, 13, 'Database', size=FS, weight='bold', ha='center')
    text(ax, 165.5, 17.2, 'decoded, stored', size=FS_S, ha='center', color=MUTED)
    box(ax, 152, 26.5, 27, 10.5, 'rel'); text(ax, 165.5, 28.5, 'Receive logs', size=FS, weight='bold', ha='center')
    text(ax, 165.5, 32.7, 'raw payloads', size=FS_S, ha='center', color=MUTED)
    ax.add_patch(FancyBboxPatch((101, 42), 78, 16, boxstyle='round,pad=0,rounding_size=1.2', fc='white', ec=EDGE['panel'], lw=.6))
    text(ax, 103, 43.8, 'Two clocks', size=FS_S, weight='bold')
    text(ax, 103, 47.6, 'operation records: server receipt time\nGPS fixes: device time\n'
                       f'verified device time added to {fmt(n["records_with_device_time"])} records', size=FS_S, color=MUTED)

    # ---------------------------------------------------------------- b: processing stages
    yb = 67
    text(ax, 4, yb, 'b', size=style.LETTER, weight='bold'); text(ax, 9, yb + .4, 'Processing stages', size=FS_T, weight='bold')
    w, gap, x0, ys, hs = 25.4, 3.5, 39, yb + 11, 27
    stages = [
        ('s3', 'Timestamps', f'clock checks\nlog supplement\n+{fmt(n["log_additions_operation"])} op.\n+{fmt(n["log_additions_gps"])} GPS', 'proc'),
        ('s4', 'Quality', f'segments, gaps\nfield, GPS flags\ndecoding fix\n{fmt(n["segments"])} segm.', 'proc'),
        ('s5', 'Derived', f'GPS status, track\nodometer, H$_2$\n{fmt(n["trips"])} trips\n{fmt(fc_runs)} FC runs\n{fmt(n["refuels"])} refuels', 'proc'),
        ('s6', 'Vehicles', f'{n["tbox_installations"]} installations\n→ {n["vehicles"]} identifiers\n−{fmt(a("operation", "4_excluded_outside_installation"))} op.\n−{fmt(a("gps", "4_excluded_outside_installation"))} GPS', 'veh'),
        ('s7', 'Release', f'{n["fields_released"]} fields\nanonymous IDs\ndictionary\nchecksums', 'rel'),
    ]
    for k, (t, title, body, kind) in enumerate(stages):
        x = x0 + k * (w + gap)
        box(ax, x, ys, w, hs, kind); tag(ax, x + 1.8, ys + 5, t, kind)
        text(ax, x + 9.4, ys + 1.7, title, size=FS, weight='bold')
        text(ax, x + 1.8, ys + 7.2, body, size=FS_S, color=MUTED)
        if k:
            arrow(ax, (x - gap, ys + hs / 2), (x, ys + hs / 2))
    sources = [(yb + 7, 's1', 'Harmonisation', f'database exports\nschema · window · dedup.\n{fmt(a("operation", "2_harmonized"))} op.\n{fmt(a("gps", "2_harmonized"))} GPS', 'src'),
               (yb + 28, 's2', 'Log decoding', 'receive logs + protocol\nmessages · frames\nboth clocks kept', 'rel')]
    for y, t, title, body, kind in sources:
        box(ax, 4, y, 30, 19.5, kind); tag(ax, 5.8, y + 5, t, kind)
        text(ax, 13.4, y + 1.7, title, size=FS, weight='bold'); text(ax, 5.8, y + 7.2, body, size=FS_S, color=MUTED)
    ax.plot([34, 36.3, 36.3, 34], [yb + 16.75, yb + 16.75, yb + 37.75, yb + 37.75], color=LINE, lw=.9)
    arrow(ax, (36.3, ys + hs / 2), (x0, ys + hs / 2))
    s6x = x0 + 3 * (w + gap) + w / 2
    ax.add_patch(FancyBboxPatch((s6x - 26, yb + 3.2), 52, 5, boxstyle='round,pad=0,rounding_size=1', fc=FILL['veh'], ec=EDGE['veh'], lw=.5))
    text(ax, s6x, yb + 5.7, 'registry · inventories · replacement records', size=FS_S, color=EDGE['veh'], ha='center', va='center')
    arrow(ax, (s6x, yb + 8.2), (s6x, ys), color=EDGE['veh'], ls=(0, (2, 1.5)))
    text(ax, x0, ys + hs + 2, 'Every record is accounted for from input to release (Table 3);\ndocumented corrections; no signal interpolation.', size=FS_S, color=MUTED)

    # ---------------------------------------------------------------- c: release
    yc = 117
    text(ax, 4, yc, 'c', size=style.LETTER, weight='bold'); text(ax, 9, yc + .4, 'Release package', size=FS_T, weight='bold')
    s7x = x0 + 4 * (w + gap) + w / 2
    arrow(ax, (s7x, ys + hs), (s7x, yc + 6.5))
    cols = [(4, 'Records with quality flags', f'{fmt(n["operation_records"])} operation records\n{fmt(n["gps_fixes"])} GPS fixes\nper series, Apache Parquet', 'src'),
            (63, 'Events and summaries', f'segments · gaps · {fmt(n["trips"])} trips\nfuel-cell runs · refuelling events\ndaily and monthly tables', 'rel'),
            (122, 'Metadata', 'vehicles · dictionary (units, caveats)\nquality-bit map · SHA-256 manifest\nCSV sample · processing code', 'veh')]
    for x, title, body, kind in cols:
        box(ax, x, yc + 6.5, 57, 18, kind); text(ax, x + 3, yc + 8.3, title, size=FS, weight='bold'); text(ax, x + 3, yc + 13.2, body, size=FS_S, color=MUTED)
    text(ax, 4, yc + 27.5, f'{n["vehicles"]} identifiers · {n["period"][0][:7]} to {n["period"][1][:7]} · '
                           f'GPS coordinates rounded to 0.01°; no altitude in the payload.', size=FS_S, color=MUTED)

    style.save(fig, i.out, 'fig1_pipeline')
    write_json(i.out / 'fig1_pipeline_numbers.json', dict(release=i.n, accounting=grouped(i.table('accounting'), ['stream', 'step']).to_dict('records'),
               fc_runs=fc_runs, sources=i.sources, note='Schematic; every count in the figure is taken from these inputs.'))


if __name__ == '__main__': main()
