#!/usr/bin/env python3
"""Turn results/failover.csv into results/failover.png for the report."""
import csv
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
rows = list(csv.DictReader((ROOT / "results" / "failover.csv").open()))

FAILURES = [("link", "Link failure\n(cable cut)"), ("silent", "Silent spine failure\n(stops forwarding, links stay up)")]
SERIES = [("off", "BGP only (BFD off)", "#257de0"), ("on", "BGP + BFD", "#f47230")]
TEXT, MUTED, GRID = "#1f1f1f", "#6b6b6b", "#e6e6e6"

fig, ax = plt.subplots(figsize=(8, 4.8), dpi=200)
width = 0.36
for i, (bfd, label, color) in enumerate(SERIES):
    for x, (failure, _) in enumerate(FAILURES):
        v = [int(r["outage_ms"]) for r in rows if r["failure"] == failure and r["bfd"] == bfd]
        mean = max(statistics.mean(v), 1)          # log scale needs > 0
        lo, hi = max(min(v), 1), max(max(v), 1)
        pos = x + (i - 0.5) * (width + 0.04)
        ax.bar(pos, mean, width, color=color, label=label if x == 0 else None, zorder=2)
        ax.errorbar(pos, mean, yerr=[[mean - lo], [hi - mean]], fmt="none",
                    ecolor=TEXT, elinewidth=1, capsize=4, zorder=3)
        ax.text(pos, hi * 1.35, f"{statistics.mean(v):,.0f} ms", ha="center",
                va="bottom", fontsize=9, color=TEXT)

ax.set_yscale("log")
ax.set_ylim(1, 100_000)
ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
ax.set_ylabel("Traffic outage (ms, log scale)", color=MUTED)
ax.set_xticks(range(len(FAILURES)), [f[1] for f in FAILURES], color=TEXT)
ax.tick_params(axis="y", colors=MUTED)
ax.grid(axis="y", color=GRID, zorder=0)
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.spines["bottom"].set_color(GRID)
n = len(rows) // 4
ax.set_title(f"Outage during failures, EVPN-VXLAN fabric (mean of {n} runs, bars show min-max)",
             fontsize=10, color=TEXT, loc="left")
ax.legend(frameon=False, loc="upper left", fontsize=9)
fig.tight_layout()
out = ROOT / "results" / "failover.png"
fig.savefig(out)
print(f"Saved {out.relative_to(ROOT)}")
