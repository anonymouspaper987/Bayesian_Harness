"""Reviewer 5Xuf W2: first-order approximation vs. exact period-by-period update (Eq. 8).

Left: one note over one MaskedMig quarter (dt = 3 months), default FilterConfig values
(eps = hmm_bg = 0.012, rho = hmm_rho = 0.5, mu = hmm_recover = 1, lambda = hazard_factor = 1e-3),
with the subsystem's regime belief b = 0.5 at the start of the quarter.
Right: MaskedMig accuracy of the two versions (Qwen3.5-27B, budget 24, 5 seeds).
Usage: python3 rebuttal/W2_exact/make_gif.py   (needs matplotlib + pillow)
"""
import math, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

HERE = os.path.dirname(os.path.abspath(__file__))
EPS, RHO, MU, LAM, B0, DT = 0.012, 0.5, 1.0, 1e-3, 0.5, 3
PI = LAM / (LAM + MU)
b = lambda t: PI + (B0 - PI) * math.exp(-(LAM + MU) * t)          # Eq. 7

exact = [0.0]                                                     # Eq. 8, once per month
for tau in range(1, DT + 1):
    s = exact[-1]
    exact.append(s + (1 - s) * min(1.0, EPS + RHO * b(tau)))
approx = min(1.0, DT * (EPS + RHO * b(DT)))                       # one step, belief at the end
ACC = [("First-order approximation\n(used in the paper)", 48.5, 2.2, "#ea580c"),
       ("Exact period-by-period\nupdate (Eq. 8)", 46.9, 4.0, "#2563eb")]

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
fig = plt.figure(figsize=(12.5, 5))
gs = fig.add_gridspec(2, 2, width_ratios=[1.6, 1], height_ratios=[1, 1.6])
axb, axs, axa = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[:, 1])
fig.suptitle("Updating staleness over a 3-month gap: exact (Eq. 8) vs. first-order approximation",
             fontsize=12, fontweight="bold")

ts = [i / 20 for i in range(DT * 20 + 1)]
axb.set_xlim(0, DT + 0.1); axb.set_ylim(0, 0.55); axb.set_ylabel("regime belief b(t)")
axb.set_xticks(range(DT + 1)); axb.tick_params(labelbottom=False)
bline, = axb.plot([], [], color="#6b7280", lw=2)
bdots = axb.scatter([], [], color="#2563eb", zorder=3, s=30)
bend = axb.scatter([], [], color="#ea580c", zorder=4, s=60, marker="D")

axs.set_xlim(0, DT + 0.1); axs.set_ylim(0, max(exact[-1], approx) * 1.35)
axs.set_xticks(range(DT + 1)); axs.set_xlabel("months since last update")
axs.set_ylabel("staleness s")
eline, = axs.step([], [], where="post", color="#2563eb", lw=2.4, label="exact: compound each month with b(tau)")
aline, = axs.step([], [], where="post", color="#ea580c", lw=2.4, ls="--", label="approx: one step, dt x (eps + rho b(t))")
axs.legend(loc="upper left", frameon=False, fontsize=9)
etxt = axs.text(0, 0, "", color="#2563eb", fontweight="bold")
atxt = axs.text(0, 0, "", color="#ea580c", fontweight="bold")

axa.set_title("MaskedMig accuracy (Qwen3.5-27B, budget 24, 5 seeds)", fontsize=10)
axa.set_ylim(0, 60); axa.set_ylabel("accuracy (%)")
abars = axa.bar([0, 1], [0, 0], color=[c for *_, c in ACC], width=0.55)
axa.set_xticks([0, 1], [n for n, *_ in ACC], fontsize=9)
errs, atexts = [], []
note = fig.text(0.5, 0.015, "", ha="center", style="italic", color="#444")
fig.tight_layout(rect=(0, 0.09, 1, 0.94))

F1, F2, F3, HOLD = 30, 6, 16, 22


def draw(i):
    if i < F1:                                   # the quarter unfolds
        T = DT * (i + 1) / F1
        k = [x for x in ts if x <= T]
        bline.set_data(k, [b(x) for x in k])
        done = [tau for tau in range(1, DT + 1) if tau <= T]
        bdots.set_offsets([[tau, b(tau)] for tau in done] or [[float("nan")] * 2])
        xs = [0] + done + [T]
        eline.set_data(xs, [exact[0]] + [exact[t] for t in done] + [exact[len(done)]])
        aline.set_data([0, T], [0, 0])
        etxt.set_text("")
    elif i < F1 + F2:                            # approximation jumps at the end of the gap
        bend.set_offsets([[DT, b(DT)]])
        aline.set_data([0, DT, DT + 0.1], [0, approx, approx])
        eline.set_data([0, 1, 2, 3, DT + 0.1], exact + [exact[-1]])
        etxt.set_position((DT - 0.55, exact[-1] + 0.01)); etxt.set_text(f"{exact[-1]:.3f}")
        atxt.set_position((DT - 0.55, approx - 0.025)); atxt.set_text(f"{approx:.3f}")
    else:                                        # accuracy bars
        f = min(1.0, (i - F1 - F2 + 1) / F3)
        f = 1 - (1 - f) ** 3
        for j, (_, mu, sd, _) in enumerate(ACC):
            abars[j].set_height(mu * f)
        if f >= 1 and not errs:
            for j, (_, mu, sd, _) in enumerate(ACC):
                errs.append(axa.errorbar(j, mu, yerr=sd, color="#111", capsize=6, lw=1.4))
                atexts.append(axa.text(j, mu + sd + 1.5, f"{mu:.1f} ± {sd:.1f}", ha="center", fontweight="bold"))
            note.set_text("Each note's score can differ, but the maintenance decisions are nearly the same.\n"
                          "The two versions differ by 1.6 points, inside the seed-to-seed spread. "
                          "On AcmeSDK (dt = 1) they are identical.")
    return []


FuncAnimation(fig, draw, frames=F1 + F2 + F3 + HOLD, interval=90).save(
    f"{HERE}/w2_exact.gif", writer=PillowWriter(fps=11), dpi=80)
print(f"exact s = {exact[-1]:.4f}, approx s = {approx:.4f}")
