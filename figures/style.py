"""Shared Scientific Data figure style (dimensions in mm, fonts in pt)."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'hfcev-matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager

MM = 1 / 25.4
SINGLE, DOUBLE = 89 * MM, 183 * MM
COLORS = dict(operation='#0072B2', gps='#E69F00', log='#009E73', excluded='#999999',
              anomaly='#D55E00', purple='#CC79A7', sky='#56B4E9', black='#222222')
FONT = 'Arial' if any(f.name == 'Arial' for f in font_manager.fontManager.ttflist) else 'DejaVu Sans'
# Text sizes (pt) at the printed width; figures are drawn at 183 mm (double column).
BASE, TICK, SMALL, LETTER = 8, 7.5, 7, 10
plt.rcParams.update({'font.family': FONT, 'font.size': BASE, 'axes.titlesize': BASE,
                     'axes.labelsize': BASE, 'xtick.labelsize': TICK, 'ytick.labelsize': TICK,
                     'legend.fontsize': TICK, 'legend.title_fontsize': TICK, 'axes.linewidth': .6,
                     'lines.linewidth': 1.2, 'lines.markersize': 4,
                     'axes.spines.top': False, 'axes.spines.right': False,
                     'xtick.major.width': .6, 'ytick.major.width': .6, 'xtick.major.size': 3, 'ytick.major.size': 3,
                     'xtick.minor.size': 1.8, 'ytick.minor.size': 1.8, 'axes.labelpad': 3,
                     'legend.handlelength': 1.6, 'legend.borderaxespad': .3,
                     'savefig.facecolor': 'white', 'figure.facecolor': 'white',
                     'pdf.fonttype': 42, 'ps.fonttype': 42, 'axes.unicode_minus': True})


def panel(ax, letter, title=''):
    """Bold panel letter just outside the top-left corner of the axes (fixed offset in points)."""
    ax.annotate(letter, xy=(0, 1), xycoords='axes fraction', xytext=(-30, 8), textcoords='offset points',
                weight='bold', size=LETTER, va='bottom', ha='left', annotation_clip=False)


def periods(ax, labels=False):
    ax.axvspan(mdates.datestr2num('2024-10-01'), mdates.datestr2num('2025-06-01'),
               color='#D9E4EA', alpha=.35, lw=0, zorder=0)
    ax.axvspan(mdates.datestr2num('2025-10-01'), mdates.datestr2num('2026-03-01'),
               color='#BDBDBD', alpha=.25, lw=0, zorder=0)

    if labels:
        for date, label in [('2025-02-01', 'odometer not reported'), ('2025-12-15', 'delayed-delivery period')]:
            ax.text(mdates.datestr2num(date), 1.02, label, transform=ax.get_xaxis_transform(), ha='center', fontsize=SMALL, color='#444444')


def dates(ax, interval=3):
    """Date axis 2024-10 to 2026-05; a tick every `interval` months (3 for wide panels, 6 for narrow)."""
    months = [1, 4, 7, 10] if interval <= 3 else [1, 7]
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=months))
    ax.xaxis.set_minor_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.tick_params(axis='x', rotation=0)
    ax.set_xlim(mdates.datestr2num('2024-10-01'), mdates.datestr2num('2026-06-01'))


def save(fig, out, name):
    fig.savefig(out / f'{name}.pdf', metadata={'Creator': 'HFCEV figure scripts', 'CreationDate': None})
    fig.savefig(out / f'{name}.png', dpi=300)
    plt.close(fig)
