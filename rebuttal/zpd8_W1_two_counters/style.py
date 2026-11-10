"""Shared academic style for the rebuttal GIFs: Times-metric serif, navy / brick / green palette."""
import glob, os
import matplotlib
from matplotlib import font_manager

HERE = os.path.dirname(os.path.abspath(__file__))
for f in glob.glob(os.path.join(HERE, "fonts", "*.ttf")):     # optional: LiberationSerif-*.ttf (Times metrics)
    font_manager.fontManager.addfont(f)
matplotlib.rcParams.update({"font.family": "serif",
                            "font.serif": ["Times New Roman", "Liberation Serif", "STIXGeneral", "DejaVu Serif"],
                            "mathtext.fontset": "stix", "font.size": 15,
                            "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": 1.1})
INK, MUTE, LIGHT = "#1a1a1a", "#7a7a7a", "#ececec"
NAVY, BRICK, GREEN, RED = "#3C5488", "#E64B35", "#00A087", "#DC0000"   # Nature (NPG) palette
GRAY1, GRAY2 = "#B9C3D9", "#8491B4"
PINK = "#FBE3DF"
