"""Staleness filters. Shared by the Phase-0 simulator and the real harness.

Two latent variables per knowledge entry
(reliability p_k as a Beta, own validity v_k as a Bernoulli staleness prob)
plus a shared world-factor layer with explaining-away via local exact
enumeration. Ablations are configuration flags on the same class, so the
paper's ablation table corresponds one-to-one to FilterConfig fields.

Pure python + math. No numpy dependency here so the harness can import it
inside any environment.
"""

import math
import random
from dataclasses import dataclass, field


def _clamp(s, lo=1e-6, hi=1.0 - 1e-6):
    return min(hi, max(lo, s))


@dataclass
class FilterConfig:
    # observation model
    q: float = 0.10                 # P(success | entry invalid) under the model
    # structure ablation flags (paper Table 3 rows)
    use_factors: bool = True        # w/o factor layer -> False
    use_two_var: bool = True        # w/o two-variable decomposition -> use DecayedBetaFilter
    use_global_factor: bool = True  # w/o f_0 -> False
    use_error_channel: bool = True  # w/o error-message channel -> False
    use_hazard: bool = True         # w/o time update -> False
    hazard_learn: bool = True       # Gamma-Exponential learning of the factor hazard
    # priors
    alpha0: float = 5.0             # entry distilled from ~4 successes (IDEA Sec 3.9)
    beta0: float = 1.0
    s0_entry: float = 0.0
    s0_factor: float = 0.02
    hazard_factor: float = 1e-3     # prior mean hazard per unit time (episode)
    hazard_entry: float = 1e-4      # entry's own v_k hazard
    gamma_a0: float = 2.0           # Gamma prior on factor hazard, mean = a0/b0
    f0_recovery: float = 0.15       # mean reversion rate of the global factor
    # error-message channel: P(msg class | validity, failure). Offline-calibrated.
    msg_probs_valid: dict = field(default_factory=lambda: {
        "transient": 0.80, "env_changed": 0.05, "usage_error": 0.15})
    msg_probs_invalid: dict = field(default_factory=lambda: {
        "transient": 0.55, "env_changed": 0.375, "usage_error": 0.075})
    # factor confirmation / rebase
    confirm_threshold: float = 0.90
    probe_stale_lr: float = 20.0   # likelihood ratio a verified-stale probe
                                   # contributes to its parent factors
    calibrate: bool = False        # online Platt scaling of P(stale)
    calib_min_obs: int = 12        # probes needed before the map is applied
    # hierarchical HMM variant (ours_hhmm)
    hmm_recover: float = 1.0        # changing -> stable rate per month (bursts last ~1 month)
    hmm_rho: float = 0.5            # P(a child note breaks in a month | its factor is changing)
    hmm_bg: float = 0.012           # P(a note breaks in a month | factor stable), background drift
    pool_kappa: float = 5.0         # strength of the subsystem-level prior on note reliability
    fixed_pbar: float = -1.0        # >= 0: validity-only ablation, quality p_k frozen at this constant (no Beta learning)
    thompson: bool = False          # Thompson sampling of factor states (user 2026-09-06: off, hurt slightly offline)
    learn_rho: bool = False         # 每个因子在线学 "大改时兄弟笔记被波及的比例" (Beta(2,2) 先验, 均值 0.5)
    live_share: bool = False        # 当期实时重算共享嫌疑 (论文数字为 False)
    compose_per_period: int = 0     # 1 = Eq. 8 逐期复合 (dt>1 时每期推进因子信念再复合); 0 = 一阶形式 dt*(bg+rho*b(t)) (论文数字)
    # decision thresholds shared with policies
    flag_threshold: float = 0.5     # staleness score above this = downgraded
    repair_threshold: float = 0.9   # own s_k above this = trigger rewrite

    @property
    def gamma_b0(self):
        return self.gamma_a0 / self.hazard_factor


class _Entry:
    __slots__ = ("alpha", "beta", "s", "parents", "t_last", "synced")

    def __init__(self, alpha, beta, s, parents, t):
        self.alpha, self.beta, self.s = alpha, beta, s
        self.parents, self.t_last = list(parents), t
        self.synced = t          # when the entry was last written/verified


class _Factor:
    __slots__ = ("s", "t_last", "birth_t", "exposed_until", "is_global",
                 "cross_t")

    def __init__(self, s, t, is_global=False):
        self.s, self.t_last = s, t
        self.birth_t, self.exposed_until = t, t
        self.is_global = is_global
        self.cross_t = None      # first time evidence pushed s past 0.5


class BaseFilter:
    """Common interface. stale_score in [0,1]; reliability = posterior mean of p_k."""

    name = "base"

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        raise NotImplementedError

    def add_factor(self, f, t=0.0, is_global=False):
        pass

    def stale_score(self, k, t):
        raise NotImplementedError

    def reliability(self, k):
        raise NotImplementedError

    def repair_signal(self, k, t):
        raise NotImplementedError

    def update(self, k, y, t, msg=None, weight=1.0):
        raise NotImplementedError

    def observe_probe(self, k, is_valid, t):
        """A verification that read the live contract and compared it with the
        note. Unlike an execution outcome this is a direct reading of the
        latent validity, so it resolves the entry's own staleness outright
        (IDEA Sec 3.8, active re-verification). Reliability is untouched: a
        probe says nothing about how good the note was when it was current."""
        return

    def on_repair(self, k, t):
        raise NotImplementedError

    def retrieval_score(self, k, t):
        return self.reliability(k) * (1.0 - self.stale_score(k, t))

    def extra_cost(self):
        return 0


class _PlattCalibrator:
    """Online Platt scaling on the staleness log-odds.

    Active re-verification already produces exactly the pairs a calibrator
    needs: the probability predicted just before a probe, and the validity the
    probe then read off. No external labels are involved, so this stays
    something the agent can do for itself at run time.
    """

    __slots__ = ("a", "b", "n", "lr")

    def __init__(self, lr=0.05):
        self.a, self.b, self.n, self.lr = 1.0, 0.0, 0, lr

    def observe(self, p_pred, is_stale):
        z = math.log(_clamp(p_pred) / (1 - _clamp(p_pred)))
        p = 1.0 / (1.0 + math.exp(-(self.a * z + self.b)))
        g = p - (1.0 if is_stale else 0.0)
        self.a -= self.lr * g * z
        self.b -= self.lr * g
        self.a = min(4.0, max(0.05, self.a))
        self.n += 1

    def apply(self, p, min_obs):
        if self.n < min_obs:
            return p
        z = math.log(_clamp(p) / (1 - _clamp(p)))
        return 1.0 / (1.0 + math.exp(-(self.a * z + self.b)))


class CorrelatedStalenessFilter(BaseFilter):
    """Ours. Two latent variables + shared factor layer + hazard + error channel."""

    name = "ours"
    GLOBAL = "__f0__"

    def __init__(self, cfg: FilterConfig):
        self.cfg = cfg
        self.calib = _PlattCalibrator()
        self.entries = {}
        self.factors = {}
        self.children = {}          # factor id -> [entry ids]
        self.gamma_a = cfg.gamma_a0
        self.gamma_b = cfg.gamma_b0
        self.confirm_events = []    # (t, factor_id)
        if cfg.use_global_factor and cfg.use_factors:
            self.add_factor(self.GLOBAL, 0.0, is_global=True)

    # -- construction ------------------------------------------------------
    def add_factor(self, f, t=0.0, is_global=False):
        if f not in self.factors:
            self.factors[f] = _Factor(self.cfg.s0_factor if not is_global else 0.0,
                                      t, is_global)
            self.children.setdefault(f, [])

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        cfg = self.cfg
        pas = []
        if cfg.use_factors:
            for f in parents:
                self.add_factor(f, t)
                pas.append(f)
                self.children[f].append(k)
            if cfg.use_global_factor:
                pas.append(self.GLOBAL)
                self.children[self.GLOBAL].append(k)
        self.entries[k] = _Entry(alpha if alpha is not None else cfg.alpha0,
                                 beta if beta is not None else cfg.beta0,
                                 cfg.s0_entry, pas, t)

    # -- time updates (lazy hazard, IDEA Sec 3.4a) --------------------------
    def _hazard(self):
        if self.cfg.hazard_learn:
            return self.gamma_a / self.gamma_b
        return self.cfg.hazard_factor

    def _touch_factor(self, f, t):
        fa = self.factors[f]
        dt = t - fa.t_last
        if dt <= 0:
            return fa
        if fa.is_global:
            fa.s *= math.exp(-self.cfg.f0_recovery * dt)
        elif self.cfg.use_hazard:
            fa.s = _clamp(fa.s + (1 - fa.s) * (1 - math.exp(-self._hazard() * dt)))
            if self.cfg.hazard_learn:      # censored exposure accounting
                self.gamma_b += t - fa.exposed_until
                fa.exposed_until = t
        fa.t_last = t
        return fa

    def _touch_entry(self, k, t):
        e = self.entries[k]
        dt = t - e.t_last
        if dt > 0:
            if self.cfg.use_hazard:
                h = self.cfg.hazard_entry
                if not self.cfg.use_factors:
                    # per-item ablation: fold the factor hazard into the entry
                    h += self.cfg.hazard_factor
                e.s = _clamp(e.s + (1 - e.s) * (1 - math.exp(-h * dt)))
            e.t_last = t
        return e

    # -- scores --------------------------------------------------------------
    def _pi(self, k, t):
        e = self._touch_entry(k, t)
        pi = 1.0 - e.s
        for f in e.parents:
            pi *= 1.0 - self._touch_factor(f, t).s
        return pi

    def stale_score(self, k, t):
        raw = 1.0 - self._pi(k, t)
        if self.cfg.calibrate:
            return self.calib.apply(raw, self.cfg.calib_min_obs)
        return raw

    def raw_stale_score(self, k, t):
        return 1.0 - self._pi(k, t)

    def reliability(self, k):
        e = self.entries[k]
        return e.alpha / (e.alpha + e.beta)

    def repair_signal(self, k, t):
        # rewrite is triggered by the entry's OWN staleness (factor staleness is
        # absorbed into children on confirmation, see _maybe_confirm)
        return self._touch_entry(k, t).s > self.cfg.repair_threshold

    # -- observation update (local exact enumeration, IDEA Sec 3.4b-c) -------
    def _msg_lik(self, msg, valid):
        p = (self.cfg.msg_probs_valid if valid else self.cfg.msg_probs_invalid)
        return p.get(msg, 1.0)

    def update(self, k, y, t, msg=None, weight=1.0):
        cfg = self.cfg
        e = self._touch_entry(k, t)
        if msg == "usage_error" and cfg.use_error_channel and y == 0:
            # IDEA Sec 3.4e: usage errors are evidence about p_k, not staleness
            w = weight * (1.0 - e.s)
            e.beta += w
            return
        parents = e.parents
        for f in parents:
            self._touch_factor(f, t)
        pbar = e.alpha / (e.alpha + e.beta)
        n = 1 + len(parents)
        svals = [e.s] + [self.factors[f].s for f in parents]
        Z = 0.0
        marg = [0.0] * n
        resp = 0.0
        full = (1 << n) - 1
        for mask in range(1 << n):
            pr = 1.0
            for i in range(n):
                pr *= (1.0 - svals[i]) if (mask >> i) & 1 else svals[i]
            V = mask == full
            if y == 1:
                like = pbar if V else cfg.q
            else:
                like = (1 - pbar) if V else (1 - cfg.q)
            if msg is not None and cfg.use_error_channel and y == 0:
                like *= self._msg_lik(msg, V)
            w_cfg = pr * (like ** weight if weight != 1.0 else like)
            Z += w_cfg
            for i in range(n):
                if (mask >> i) & 1:
                    marg[i] += w_cfg
            if V:
                resp += w_cfg
        if Z <= 0:
            return
        e.s = _clamp(1.0 - marg[0] / Z)
        for i, f in enumerate(parents):
            fa = self.factors[f]
            fa.s = _clamp(1.0 - marg[i + 1] / Z)
            if not fa.is_global and fa.s >= 0.5 and fa.cross_t is None:
                fa.cross_t = t   # rough changepoint estimate for absorption
        w = weight * (resp / Z)          # responsibility-weighted Beta update
        if y == 1:
            e.alpha += w
        else:
            e.beta += w
        for f in parents:
            self._maybe_confirm(f, t)

    # -- factor confirmation: absorb into children, rebase, learn hazard ------
    def _maybe_confirm(self, f, t):
        fa = self.factors[f]
        if fa.is_global or fa.s <= self.cfg.confirm_threshold:
            return
        if self.cfg.hazard_learn:
            self.gamma_a += 1.0
            self.gamma_b += max(0.0, t - fa.exposed_until)
            fa.exposed_until = t
        cutoff = fa.cross_t if fa.cross_t is not None else t
        for k in self.children[f]:
            e = self.entries[k]
            # skip children rewritten after the inferred changepoint: they
            # were written against the new world and must not be poisoned
            if e.synced > cutoff:
                continue
            e.s = _clamp(1.0 - (1.0 - e.s) * (1.0 - fa.s))
        fa.s = self.cfg.s0_factor
        fa.birth_t = t
        fa.t_last = t
        fa.cross_t = None
        self.confirm_events.append((t, f))

    def observe_probe(self, k, is_valid, t):
        if self.cfg.calibrate:
            self.calib.observe(self.raw_stale_score(k, t), not is_valid)
        e = self._touch_entry(k, t)
        if is_valid:
            e.s = _clamp(0.0)
            # the note is confirmed current, so its parents cannot all be
            # broken; relax the factors it hangs off
            for f in e.parents:
                fa = self._touch_factor(f, t)
                fa.s = _clamp(min(fa.s, self.cfg.s0_factor))
        else:
            e.s = _clamp(1.0)
            # A verified stale reading is also strong evidence about WHY. An
            # entry rarely goes stale on its own (hazard_entry is tiny), so a
            # confirmed-stale note indicts its parents. Without this the factor
            # never reaches the confirmation threshold when most siblings are
            # still succeeding, and the cluster is never cleared.
            for f in e.parents:
                fa = self._touch_factor(f, t)
                if fa.is_global:
                    continue
                odds = fa.s / max(1e-9, 1 - fa.s) * self.cfg.probe_stale_lr
                fa.s = _clamp(odds / (1 + odds))
                if fa.s >= 0.5 and fa.cross_t is None:
                    fa.cross_t = t
                self._maybe_confirm(f, t)

    def on_repair(self, k, t):
        # rewrite inherits reputation, resets own staleness (IDEA Sec 3.8)
        e = self.entries[k]
        e.s = 0.0
        e.t_last = t
        e.synced = t


class FlatBetaFilter(BaseFilter):
    """Bayesian-Agent proxy: per-entry Beta only. No time, no structure.

    On rewrite it must reset the Beta to the prior: with a single number there
    is no way to separate 'the entry was fine, the world moved' from 'the entry
    was bad', so the accumulated failures poison the reputation and cannot be
    inherited. This asymmetry is exactly the point of the two-variable ablation.
    """

    name = "flat_bayes"

    def __init__(self, cfg: FilterConfig):
        self.cfg = cfg
        self.ab = {}

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [alpha if alpha is not None else self.cfg.alpha0,
                      beta if beta is not None else self.cfg.beta0]

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        return 1.0 - self.reliability(k)

    def repair_signal(self, k, t):
        return self.reliability(k) < 0.4

    def update(self, k, y, t, msg=None, weight=1.0):
        if y == 1:
            self.ab[k][0] += weight
        else:
            self.ab[k][1] += weight

    def on_repair(self, k, t):
        self.ab[k] = [self.cfg.alpha0, self.cfg.beta0]


class DecayedBetaFilter(BaseFilter):
    """w/o two-variable ablation: single Beta whose counts decay toward the
    prior at the factor hazard rate. Time-aware but structureless and without
    a separate validity variable."""

    name = "decayed_beta"

    def __init__(self, cfg: FilterConfig):
        self.cfg = cfg
        self.state = {}   # k -> [alpha, beta, t_last]

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.state[k] = [alpha if alpha is not None else self.cfg.alpha0,
                         beta if beta is not None else self.cfg.beta0, t]

    def _touch(self, k, t):
        st = self.state[k]
        dt = t - st[2]
        if dt > 0:
            d = math.exp(-(self.cfg.hazard_factor + self.cfg.hazard_entry) * dt)
            st[0] = self.cfg.alpha0 + (st[0] - self.cfg.alpha0) * d
            st[1] = self.cfg.beta0 + (st[1] - self.cfg.beta0) * d
            st[2] = t
        return st

    def reliability(self, k):
        a, b, _ = self.state[k]
        return a / (a + b)

    def stale_score(self, k, t):
        self._touch(k, t)
        return 1.0 - self.reliability(k)

    def repair_signal(self, k, t):
        self._touch(k, t)
        return self.reliability(k) < 0.45

    def update(self, k, y, t, msg=None, weight=1.0):
        st = self._touch(k, t)
        st[0 if y == 1 else 1] += weight

    def on_repair(self, k, t):
        self.state[k] = [self.cfg.alpha0, self.cfg.beta0, t]


class TTLFilter(BaseFilter):
    """MemoryBank-style age decay: staleness grows with time since the last
    observed success, with no evidence-driven component. Reliability is a
    plain Beta so the use/skip gate is comparable."""

    name = "ttl"

    def __init__(self, cfg: FilterConfig, h_ttl=None):
        self.cfg = cfg
        self.h = h_ttl if h_ttl is not None else cfg.hazard_factor
        self.ab = {}
        self.last_success = {}

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [alpha if alpha is not None else self.cfg.alpha0,
                      beta if beta is not None else self.cfg.beta0]
        self.last_success[k] = t

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        return 1.0 - math.exp(-self.h * max(0.0, t - self.last_success[k]))

    def repair_signal(self, k, t):
        return self.stale_score(k, t) > self.cfg.repair_threshold

    def update(self, k, y, t, msg=None, weight=1.0):
        if y == 1:
            self.ab[k][0] += weight
            self.last_success[k] = t
        else:
            self.ab[k][1] += weight

    def observe_probe(self, k, is_valid, t):
        # the classic cache rule: a verification resets the clock. Without
        # this the same oldest notes are re-probed every month and the
        # baseline degrades below what TTL actually does in practice.
        if is_valid:
            self.last_success[k] = t

    def on_repair(self, k, t):
        self.ab[k] = [self.cfg.alpha0, self.cfg.beta0]
        self.last_success[k] = t


class ContractFilter(BaseFilter):
    """Deterministic contract checking (Skill-Drift-style): an entry is flagged
    iff a loud environment-change error was observed for it. Per-item, binary,
    blind to silent drift. Beta reliability kept for the shared use gate."""

    name = "contract"

    def __init__(self, cfg: FilterConfig):
        self.cfg = cfg
        self.ab = {}
        self.flagged = {}

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [alpha if alpha is not None else self.cfg.alpha0,
                      beta if beta is not None else self.cfg.beta0]
        self.flagged[k] = False

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        return 1.0 if self.flagged[k] else 0.0

    def repair_signal(self, k, t):
        return self.flagged[k]

    def update(self, k, y, t, msg=None, weight=1.0):
        if y == 1:
            self.ab[k][0] += weight
        else:
            self.ab[k][1] += weight
            if msg == "env_changed":
                self.flagged[k] = True

    def on_repair(self, k, t):
        self.flagged[k] = False
        self.ab[k] = [self.cfg.alpha0, self.cfg.beta0]


class JudgeFilter(BaseFilter):
    """LLM-judge proxy: every `period` observations of an entry, ask a noisy
    judge whether the entry is stale. `judge_fn(k, t) -> bool` is injected
    (the simulator provides a noisy oracle; the harness provides a real LLM
    call). Verdicts are trusted verbatim, i.e. uncalibrated by design."""

    name = "llm_judge"

    def __init__(self, cfg: FilterConfig, judge_fn=None, period=20):
        self.cfg = cfg
        self.judge_fn = judge_fn
        self.period = period
        self.ab = {}
        self.n_obs = {}
        self.verdict = {}
        self.queries = 0

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [alpha if alpha is not None else self.cfg.alpha0,
                      beta if beta is not None else self.cfg.beta0]
        self.n_obs[k] = 0
        self.verdict[k] = False

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        return 1.0 if self.verdict[k] else 0.0

    def repair_signal(self, k, t):
        return self.verdict[k]

    def update(self, k, y, t, msg=None, weight=1.0):
        self.ab[k][0 if y == 1 else 1] += weight
        self.n_obs[k] += 1
        if self.judge_fn is not None and self.n_obs[k] % self.period == 0:
            self.queries += 1
            self.verdict[k] = bool(self.judge_fn(k, t))

    def on_repair(self, k, t):
        self.verdict[k] = False
        self.ab[k] = [self.cfg.alpha0, self.cfg.beta0]

    def extra_cost(self):
        return self.queries


class RandomFilter(BaseFilter):
    """Uniformly random maintenance: the budget is spent on notes drawn at
    random. The floor any deliberate policy has to clear; scores are hashed
    from (note, month, seed) so a run is reproducible without carrying state.
    """

    name = "random"

    def __init__(self, cfg: FilterConfig, seed=0):
        self.cfg = cfg
        self.seed = seed
        self.ab = {}

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [self.cfg.alpha0, self.cfg.beta0]

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        import hashlib
        h = hashlib.sha1(f"{self.seed}|{k}|{int(t)}".encode()).digest()
        return 0.03 + 0.9 * (h[0] / 255.0)     # above any sensible floor

    def repair_signal(self, k, t):
        return False

    def update(self, k, y, t, msg=None, weight=1.0):
        self.ab[k][0 if y == 1 else 1] += weight

    def on_repair(self, k, t):
        pass


class BayesianAgentFilter(BaseFilter):
    """Bayesian-Agent (Wu et al., 2026, arXiv:2606.08348), adapted from skill
    maintenance to note maintenance. Faithful where the settings overlap, and
    explicit about the three places they do not.

    What is kept from the paper: one Beta per object with the paper's default
    Beta(1,1) prior (their Sec. 3.2); evidence accumulates monotonically and a
    repair does NOT reset the posterior (their Appendix I trace carries the
    posterior through a patch); the action policy's triggers from their Eq. 15,
    patch when the same failure mode has been seen at least twice, retire when
    beta >= 4 and the posterior mean is below 0.45. Both of those actions map
    to our repair, since re-deriving a note from the current code is the note
    analogue of patching or redesigning a skill; split and compress operate on
    skill text and have no note counterpart.

    What the paper's model does not have, on its own account: no time
    component, no drift model, and no cross-object structure. Its own
    discussion (their Sec. 4.6) states the method is "less suitable for ...
    highly nonstationary environments", which is precisely the regime under
    test here. This baseline is therefore the strongest published point of
    comparison, run in the setting its authors name as its boundary.
    """

    name = "bayesian_agent"

    def __init__(self, cfg: FilterConfig, alpha0=1.0, beta0=1.0):
        self.cfg = cfg
        self.a0, self.b0 = alpha0, beta0          # paper default Beta(1,1)
        self.ab = {}
        self.modes = {}                           # note -> failure-mode counts

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [self.a0, self.b0]
        self.modes[k] = {}

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        return 1.0 - self.reliability(k)

    def repair_signal(self, k, t):
        # Eq. 15: patch on a repeated failure mode, retire on dominant failure
        a, b = self.ab[k]
        if b - self.b0 >= 4 and a / (a + b) < 0.45:
            return True                            # retire -> re-derive
        return max(self.modes[k].values(), default=0) >= 2   # patch

    def update(self, k, y, t, msg=None, weight=1.0):
        self.ab[k][0 if y == 1 else 1] += weight
        if y == 0:
            mode = msg or "unknown_failure"
            self.modes[k][mode] = self.modes[k].get(mode, 0) + weight

    def observe_probe(self, k, is_valid, t):
        # a probe is a verified observation like any other in their model
        self.ab[k][0 if is_valid else 1] += 1.0
        if not is_valid:
            self.modes[k]["stale_on_probe"] = \
                self.modes[k].get("stale_on_probe", 0) + 1

    def on_repair(self, k, t):
        # the posterior persists through a patch (their Appendix I); the
        # failure-mode tally is cleared because the defect it counted has been
        # addressed, else the patch trigger would fire forever
        self.modes[k] = {}


class FrequencyFilter(BaseFilter):
    """Cache-style heuristic: maintain what gets used. Staleness is driven by
    recency of use rather than by any belief about the world, so a note that
    is queried often stays trusted and a note nobody has touched decays.

    This is the baseline a practitioner writes first, and on repo facts it is
    not a straw man: notes are reused unevenly (a median django note is used
    five times), so use frequency really does carry information about which
    notes matter. It carries none about which notes have expired, which is the
    gap the method has to exploit.
    """

    name = "frequency"

    def __init__(self, cfg: FilterConfig, half_life=40.0):
        self.cfg = cfg
        self.half_life = half_life
        self.ab = {}
        self.uses = {}
        self.last_use = {}

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [alpha if alpha is not None else self.cfg.alpha0,
                      beta if beta is not None else self.cfg.beta0]
        self.uses[k] = 0
        self.last_use[k] = t

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        # idle time drives suspicion; each additional use damps it
        idle = max(0.0, t - self.last_use.get(k, t))
        s = 1.0 - 0.5 ** (idle / self.half_life)
        return _clamp(s / (1.0 + math.log1p(self.uses.get(k, 0))))

    def repair_signal(self, k, t):
        return self.reliability(k) < 0.4

    def update(self, k, y, t, msg=None, weight=1.0):
        self.uses[k] = self.uses.get(k, 0) + 1
        self.last_use[k] = t
        self.ab[k][0 if y == 1 else 1] += weight

    def observe_probe(self, k, is_valid, t):
        self.last_use[k] = t
        if not is_valid:
            self.uses[k] = 0

    def on_repair(self, k, t):
        self.ab[k] = [self.cfg.alpha0, self.cfg.beta0]
        self.uses[k] = 0
        self.last_use[k] = t


class FactorRuleFilter(BaseFilter):
    """Non-Bayesian factor propagation: when one note under a factor is seen to
    fail, mark every sibling suspect until each is checked. No posterior, no
    hazard, no weighing of how strong the evidence was.

    This baseline exists to make the method earn its machinery. Sibling
    invalidation on the repo-facts environment is 5x to 25x above base rate at
    file granularity, and with a signal that strong a crude rule may capture
    most of the available gain. Whatever the method wins on top of this rule is
    what probabilistic inference is actually worth: ordering the queue when the
    budget cannot cover every sibling, and not spending on a factor whose
    evidence was weak.
    """

    name = "factor_rule"

    def __init__(self, cfg: FilterConfig, suspect_score=0.8):
        self.cfg = cfg
        self.suspect_score = suspect_score
        self.ab = {}
        self.parents = {}
        self.children = {}
        self.suspect = set()

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [alpha if alpha is not None else self.cfg.alpha0,
                      beta if beta is not None else self.cfg.beta0]
        self.parents[k] = tuple(parents)
        for f in parents:
            self.children.setdefault(f, set()).add(k)

    def add_factor(self, f, t=0.0, is_global=False):
        self.children.setdefault(f, set())

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        return self.suspect_score if k in self.suspect else 0.0

    def repair_signal(self, k, t):
        return self.reliability(k) < 0.4

    def _flag_siblings(self, k):
        for f in self.parents.get(k, ()):
            self.suspect |= self.children.get(f, set())

    def update(self, k, y, t, msg=None, weight=1.0):
        self.ab[k][0 if y == 1 else 1] += weight
        if y == 0:
            self._flag_siblings(k)
        else:
            self.suspect.discard(k)

    def observe_probe(self, k, is_valid, t):
        self.suspect.discard(k)
        if not is_valid:
            self._flag_siblings(k)

    def on_repair(self, k, t):
        self.ab[k] = [self.cfg.alpha0, self.cfg.beta0]
        self.suspect.discard(k)


class OracleFilter(BaseFilter):
    """Upper bound: reads true validity through `truth_fn(k, t) -> bool valid`."""

    name = "oracle"

    def __init__(self, cfg: FilterConfig, truth_fn=None):
        self.cfg = cfg
        self.truth_fn = truth_fn
        self.ab = {}

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        self.ab[k] = [alpha if alpha is not None else self.cfg.alpha0,
                      beta if beta is not None else self.cfg.beta0]

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def stale_score(self, k, t):
        return 0.0 if self.truth_fn(k, t) else 1.0

    def repair_signal(self, k, t):
        return not self.truth_fn(k, t)

    def update(self, k, y, t, msg=None, weight=1.0):
        self.ab[k][0 if y == 1 else 1] += weight

    def on_repair(self, k, t):
        pass


class HierarchicalHMMFilter(BaseFilter):
    """Ours, standard-model form: a two-state hidden Markov model per factor
    (stable / changing, continuous-time rates lambda and mu), note validity as
    a latent Bernoulli whose monthly break probability is bg + rho * 1[factor
    changing], a hierarchical Beta on note reliability (notes borrow strength
    from their subsystem, partial pooling), and Thompson sampling of factor
    states for the probe ranking. Local inference is exact enumeration over
    (note valid?, factor changing?)."""

    name = "ours_hhmm"
    GLOBAL = "__f0__"

    def __init__(self, cfg: FilterConfig):
        self.cfg = cfg
        self.entries = {}          # k -> dict(s, s_pre, a_own, b_own, parents, t_last, synced)
        self.factors = {}          # f -> dict(b, t_last, exposed)
        self.children = {}
        self.groups = {}           # k -> 部门列表 (分层先验用, 与 HMM 共享无关)
        self.gamma_a = cfg.gamma_a0          # Gamma posterior on lambda (stable->changing)
        self.gamma_b = cfg.gamma_b0
        self._ts_cache = (None, {})
        self._rng = random.Random(7)

    # -- construction ----------------------------------------------------
    def add_factor(self, f, t=0.0, is_global=False):
        if f not in self.factors:
            self.factors[f] = {"b": 0.0, "t_last": t, "exposed": t, "rho_a": 2.0, "rho_b": 2.0}
            self.children.setdefault(f, [])

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None, weights=None):
        """parents 可带权重 (weights: {factor: w in (0,1]}), 边权乘进危险率和证据 (LLM 建图用)."""
        pas = []; w = {}
        for f in parents:
            self.add_factor(f, t); self.children[f].append(k)
            if self.cfg.use_factors:            # E1 消融: 不共享 HMM 状态, 只保留分组用于分层先验
                pas.append(f); w[f] = float((weights or {}).get(f, 1.0))
        self.groups[k] = list(parents)
        self.entries[k] = {"s": 0.0, "s_pre": 0.0, "a_own": 0.0, "b_own": 0.0,
                           "parents": pas, "w": w, "t_last": t, "synced": t, "obs_t": None}

    # -- HMM time update -------------------------------------------------
    def _lam(self):
        return self.gamma_a / self.gamma_b

    def _touch_factor(self, f, t):
        fa = self.factors[f]
        dt = t - fa["t_last"]
        if dt > 0:
            lam, mu = self._lam(), self.cfg.hmm_recover
            pi = lam / (lam + mu)
            b = fa["b"]
            fa["b"] = _clamp(pi + (b - pi) * math.exp(-(lam + mu) * dt))
            if self.cfg.hazard_learn:
                self.gamma_b += dt * (1.0 - b)      # censored exposure in the stable state
            fa["b_from"], fa["t_from"] = b, fa["t_last"]   # 本段传播的起点 (逐期复合用)
            fa["t_last"] = t
        return fa

    def _rho(self, f):
        fa = self.factors[f]
        return fa["rho_a"] / (fa["rho_a"] + fa["rho_b"]) if self.cfg.learn_rho else self.cfg.hmm_rho

    def _touch_entry(self, k, t):
        e = self.entries[k]
        dt = t - e["t_last"]
        if dt > 0:
            e["s_pre"] = e["s"]
            if self.cfg.compose_per_period and dt > 1.0 + 1e-9:
                self._compose_entry(e, t, dt)
                return e
            # First-order approximation of Eq. 8 over a gap of dt periods (used for all paper numbers).
            #   Exact (Eq. 8, applied once per period tau = 1..dt, regime belief advanced each period):
            #       s <- s + (1 - s) * (eps + rho * b(tau))          repeated dt times
            #   Here (one step, belief taken at the END of the gap, b(t)):
            #       s <- s + (1 - s) * min(1, dt * (eps + rho * b(t)))
            #   i.e. the one-period break probability is multiplied by dt instead of compounded.
            #   dt = 1 (AcmeSDK, monthly): identical to Eq. 8.  dt = 3 (MaskedMig, quarterly): approximate.
            #   compose_per_period=1 switches to the exact form (_compose_entry).
            h = self.cfg.hmm_bg * dt                                   # eps * dt
            for f in e["parents"]:
                h += e["w"][f] * self._rho(f) * dt * self._touch_factor(f, t)["b"]   # + rho * dt * b(t)
            if not self.cfg.use_factors:        # E1: 用先验平稳概率代替共享状态
                lam, mu = self._lam(), self.cfg.hmm_recover
                h += self.cfg.hmm_rho * dt * lam / (lam + mu)
            e["s"] = _clamp(e["s"] + (1.0 - e["s"]) * min(1.0, h))
            e["t_last"] = t
            e["obs_t"] = None
        return e

    def _b_at(self, f, tau):
        """因子 f 在时刻 tau 的信念 (tau 落在它最后一段传播区间里时按 Eq. 7 回推; 更早的时刻用该段起点)."""
        fa = self.factors[f]
        t0 = fa.get("t_from")
        if t0 is None or tau >= fa["t_last"]:
            return fa["b"]
        if tau <= t0:
            return fa["b_from"]
        lam, mu = self._lam(), self.cfg.hmm_recover
        pi = lam / (lam + mu)
        return _clamp(pi + (fa["b_from"] - pi) * math.exp(-(lam + mu) * (tau - t0)))

    def _compose_entry(self, e, t, dt):
        """Eq. 8 逐期复合: 每期 s <- s + (1-s)(bg + sum_f w rho b_f(tau)), tau = t_last+1, ..., t."""
        for f in e["parents"]:
            self._touch_factor(f, t)
        n = max(1, int(math.ceil(dt - 1e-9)))
        step = dt / n
        s = e["s"]
        for i in range(1, n + 1):
            tau = e["t_last"] + i * step
            h = self.cfg.hmm_bg * step
            for f in e["parents"]:
                h += e["w"][f] * self._rho(f) * step * self._b_at(f, tau)
            if not self.cfg.use_factors:
                lam, mu = self._lam(), self.cfg.hmm_recover
                h += self.cfg.hmm_rho * step * lam / (lam + mu)
            s = s + (1.0 - s) * min(1.0, h)
        e["s"] = _clamp(s)
        e["t_last"] = t
        e["obs_t"] = None

    def _live_s(self, k, t):
        """当期还没被观测的笔记: 用因子的当前信念重算本期的失效概率 (连坐当期生效).
        月初算好的嫌疑用的是当时的因子概率; 兄弟笔记被探出坏了以后因子概率上升, 这里立刻反映."""
        e = self._touch_entry(k, t)
        if e["obs_t"] == t:
            return e["s"]
        h = self.cfg.hmm_bg
        for f in e["parents"]:
            h += e["w"][f] * self._rho(f) * self._touch_factor(f, t)["b"]
        if not self.cfg.use_factors:
            lam, mu = self._lam(), self.cfg.hmm_recover
            h += self.cfg.hmm_rho * lam / (lam + mu)
        return _clamp(e["s_pre"] + (1.0 - e["s_pre"]) * min(1.0, h))

    # -- scores ------------------------------------------------------------
    def stale_score(self, k, t):
        return self._live_s(k, t) if self.cfg.live_share else self._touch_entry(k, t)["s"]

    def raw_stale_score(self, k, t):
        return self.stale_score(k, t)

    def probe_score(self, k, t):
        """Thompson sampling: draw each factor's state once per period, rank by
        the note's break probability under the drawn states."""
        e = self._touch_entry(k, t)
        if not self.cfg.thompson:
            return e["s"]
        if self._ts_cache[0] != t:
            self._ts_cache = (t, {})
        draws = self._ts_cache[1]
        h = 0.0
        for f in e["parents"]:
            if f not in draws:
                draws[f] = 1.0 if self._rng.random() < self._touch_factor(f, t)["b"] else 0.0
            h += self.cfg.hmm_rho * draws[f]
        return _clamp(e["s_pre"] + (1.0 - e["s_pre"]) * min(1.0, self.cfg.hmm_bg + h))

    def _pool_mean(self, k):
        a = self.cfg.alpha0; b = self.cfg.beta0
        for f in self.groups.get(k, ()):
            for j in self.children[f]:
                a += self.entries[j]["a_own"]; b += self.entries[j]["b_own"]
        return a / (a + b)

    def reliability(self, k):
        if self.cfg.fixed_pbar >= 0:            # validity-only 消融: p_k 不学, 每次失败携带同样多的过期证据
            return self.cfg.fixed_pbar
        e = self.entries[k]; kap = self.cfg.pool_kappa
        if kap <= 0:                            # E2 消融: 不借部门信息
            return (e["a_own"] + self.cfg.alpha0) / (e["a_own"] + e["b_own"] + self.cfg.alpha0 + self.cfg.beta0)
        m = self._pool_mean(k)
        return (e["a_own"] + kap * m) / (e["a_own"] + e["b_own"] + kap)

    def repair_signal(self, k, t):
        return self.stale_score(k, t) > self.cfg.repair_threshold

    # -- observations ------------------------------------------------------
    def _joint_update(self, k, t, lik_valid, lik_invalid, weight=1.0):
        """Exact enumeration over (note broken?, factor changing?) for one
        parent factor at a time; the note's monthly break probability under
        each factor state links the two."""
        e = self._touch_entry(k, t)
        cfg = self.cfg
        if not e["parents"]:
            s = e["s"]; z = s * lik_invalid + (1 - s) * lik_valid
            e["s"] = _clamp(s * lik_invalid / z) if z > 0 else s
            e["obs_t"] = t
            return
        s_pre = e["s_pre"]
        s_post_num = 0.0; s_post_den = 0.0
        for f in e["parents"]:
            fa = self._touch_factor(f, t); b = fa["b"]
            p1 = min(1.0, s_pre + (1 - s_pre) * (cfg.hmm_bg + e["w"][f] * self._rho(f)))   # P(broken | changing), 边权打折
            p0 = min(1.0, s_pre + (1 - s_pre) * cfg.hmm_bg)                   # P(broken | stable)
            w = {}
            for z in (0, 1):
                pz = b if z else (1 - b); pb = p1 if z else p0
                for v in (0, 1):                       # v=1 broken
                    pv = pb if v else (1 - pb); lk = lik_invalid if v else lik_valid
                    w[(z, v)] = pz * pv * (lk ** weight if weight != 1.0 else lk)
            Z = sum(w.values())
            if Z <= 0:
                continue
            fa["b"] = _clamp((w[(1, 0)] + w[(1, 1)]) / Z)
            s_post_num += (w[(0, 1)] + w[(1, 1)]) / Z; s_post_den += 1
            if fa["b"] >= 0.5 and b < 0.5 and self.cfg.hazard_learn:
                self.gamma_a += 1.0                    # one observed entry into the changing state
        if s_post_den:
            e["s"] = _clamp(s_post_num / s_post_den)
        e["obs_t"] = t

    def update(self, k, y, t, msg=None, weight=1.0):
        cfg = self.cfg
        e = self._touch_entry(k, t)
        if msg == "usage_error" and cfg.use_error_channel and y == 0:
            e["b_own"] += weight * (1.0 - e["s"]); return
        pbar = self.reliability(k)
        if y == 1:
            lv, li = pbar, cfg.q
        else:
            lv, li = 1 - pbar, 1 - cfg.q
            if msg is not None and cfg.use_error_channel:
                lv *= (cfg.msg_probs_valid.get(msg, 1.0)); li *= (cfg.msg_probs_invalid.get(msg, 1.0))
        self._joint_update(k, t, lv, li, weight)
        r = 1.0 - e["s"]                                # responsibility that the note was valid
        if y == 1: e["a_own"] += weight * r
        else: e["b_own"] += weight * r

    def observe_probe(self, k, is_valid, t):
        # a probe reads validity exactly: likelihood is 1 for the observed state, 0 otherwise
        if self.cfg.learn_rho:          # 因子被认为在大改时, 探测结果告诉我们波及比例
            for f in self.entries[k]["parents"]:
                fa = self._touch_factor(f, t)
                if fa["b"] > 0.5:
                    if is_valid: fa["rho_b"] += 1.0
                    else: fa["rho_a"] += 1.0
        self._joint_update(k, t, 1.0 if is_valid else 0.0, 0.0 if is_valid else 1.0)
        e = self.entries[k]
        e["s"] = _clamp(0.0 if is_valid else 1.0); e["obs_t"] = t

    def on_repair(self, k, t):
        e = self.entries[k]
        e["s"] = 0.0; e["s_pre"] = 0.0; e["t_last"] = t; e["synced"] = t; e["obs_t"] = t



class TwoCountersFilter(BaseFilter):
    """Two counters (Table 4 baseline, 2026-09-21): a TTL clock plus a running
    success rate per note, no probabilistic model.

    Ranking quantity = age since the last verification, in months. A failed
    exam counts as staleness evidence (it adds `alarm_months` to the age) only
    when the note's running success rate is above the mean of its subsystem
    (library); a failure of a note that was already doing worse than its
    peers is charged to quality instead (b += 1). A success resets the clock,
    as in the TTL baseline. Reliability = the running success rate with a
    Beta(1,1) prior, which is the "Quality" column for this policy.
    """

    name = "two_counters"

    def __init__(self, cfg: FilterConfig, h_ttl=5e-3, alarm_months=24.0):
        self.cfg = cfg
        self.h = h_ttl
        self.alarm_months = alarm_months
        self.ab = {}
        self.last_verified = {}
        self.alarm = {}
        self.parents = {}
        self.children = {}

    def add_factor(self, f, t=0.0, is_global=False):
        self.children.setdefault(f, [])

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None, weights=None):
        self.ab[k] = [1.0, 1.0]
        self.last_verified[k] = t
        self.alarm[k] = 0.0
        self.parents[k] = tuple(parents)
        for f in parents:
            self.children.setdefault(f, []).append(k)

    def reliability(self, k):
        a, b = self.ab[k]
        return a / (a + b)

    def _group_mean(self, k):
        a = b = 0.0
        for f in self.parents.get(k, ()):
            for j in self.children.get(f, ()):
                a += self.ab[j][0]; b += self.ab[j][1]
        return a / (a + b) if a + b > 0 else 0.5

    def rank_quantity(self, k, t):
        """Effective age in months: time since last verification plus alarms."""
        return max(0.0, t - self.last_verified[k]) + self.alarm[k]

    def stale_score(self, k, t):
        return 1.0 - math.exp(-self.h * self.rank_quantity(k, t))

    def repair_signal(self, k, t):
        return self.stale_score(k, t) > self.cfg.repair_threshold

    def update(self, k, y, t, msg=None, weight=1.0):
        if y == 1:
            self.ab[k][0] += weight
            self.last_verified[k] = t
            return
        if self.reliability(k) > self._group_mean(k):
            self.alarm[k] += self.alarm_months * weight     # doing better than its peers, so the world moved
        else:
            self.ab[k][1] += weight                         # doing worse than its peers, so the note is poor

    def observe_probe(self, k, is_valid, t):
        if is_valid:
            self.last_verified[k] = t
            self.alarm[k] = 0.0

    def on_repair(self, k, t):
        self.ab[k] = [1.0, 1.0]
        self.last_verified[k] = t
        self.alarm[k] = 0.0



def build_filter(method, cfg=None, judge_fn=None, truth_fn=None):
    """Method registry. Names match the paper's Tables 1 and 3 rows."""
    cfg = cfg or FilterConfig()
    if method == "ours":
        return CorrelatedStalenessFilter(cfg)
    if method == "ours_calib":
        import copy
        c = copy.deepcopy(cfg); c.calibrate = True
        return CorrelatedStalenessFilter(c)
    if method == "ours_no_factor":
        import copy
        c = copy.deepcopy(cfg); c.use_factors = False; c.use_global_factor = False
        return CorrelatedStalenessFilter(c)
    if method == "ours_no_f0":
        import copy
        c = copy.deepcopy(cfg); c.use_global_factor = False
        return CorrelatedStalenessFilter(c)
    if method == "ours_no_error_channel":
        import copy
        c = copy.deepcopy(cfg); c.use_error_channel = False
        return CorrelatedStalenessFilter(c)
    if method == "ours_no_hazard":
        import copy
        c = copy.deepcopy(cfg); c.use_hazard = False; c.hazard_learn = False
        return CorrelatedStalenessFilter(c)
    if method == "ours_no_twovar":
        return DecayedBetaFilter(cfg)
    if method == "ours_hhmm":
        return HierarchicalHMMFilter(cfg)
    if method in ("ours_hhmm_nots",):          # 旧名, 等价于 ours_hhmm
        return HierarchicalHMMFilter(cfg)
    if method in ("ours_hhmm_no_factor", "ours_hhmm_no_pool", "ours_hhmm_no_recover", "ours_hhmm_no_error_channel"):
        import copy
        c = copy.deepcopy(cfg)
        if method.endswith("no_factor"): c.use_factors = False
        if method.endswith("no_pool"): c.pool_kappa = 0.0
        if method.endswith("no_recover"): c.hmm_recover = 1e-6      # 大改状态只进不出 (退回单向危险率)
        if method.endswith("no_error_channel"): c.use_error_channel = False
        return HierarchicalHMMFilter(c)
    if method == "ours_hhmm_fixed_lambda":        # 消融: 不在线学习 stable->changing 速率 lambda, 固定在先验均值 a0/b0 = lambda0
        import copy
        c = copy.deepcopy(cfg); c.hazard_learn = False
        return HierarchicalHMMFilter(c)
    if method == "ours_hhmm_validity_only":      # p̄ 固定在先验均值 alpha0/(alpha0+beta0); --cfg fixed_pbar=x 可改
        import copy
        c = copy.deepcopy(cfg)
        if c.fixed_pbar < 0: c.fixed_pbar = c.alpha0 / (c.alpha0 + c.beta0)
        return HierarchicalHMMFilter(c)
    if method in ("ours_hhmm_lrho", "ours_hhmm_tags_lrho"):
        import copy
        c = copy.deepcopy(cfg); c.learn_rho = True
        return HierarchicalHMMFilter(c)
    if method == "ours_hhmm_tags":                 # 标签层由调用方通过 parents 传入
        return HierarchicalHMMFilter(cfg)
    if method == "ours_hhmm_ts":
        import copy
        c = copy.deepcopy(cfg); c.thompson = True
        return HierarchicalHMMFilter(c)
    if method == "bayesian_agent":
        # faithful adaptation of arXiv:2606.08348 (Beta(1,1), Eq.15 triggers,
        # posterior persists through repair)
        return BayesianAgentFilter(cfg)
    if method == "flat_bayes":
        # our simplified per-note Beta (Beta(5,1), reset on repair); kept for
        # comparability with earlier runs and as a two-variable ablation
        return FlatBetaFilter(cfg)
    if method == "ttl":
        return TTLFilter(cfg, h_ttl=5e-3)
    if method == "two_counters":            # Table 4 baseline: TTL clock + running success rate
        return TwoCountersFilter(cfg)
    if method == "random":
        return RandomFilter(cfg)
    if method == "frequency":
        return FrequencyFilter(cfg)
    if method == "factor_rule":
        return FactorRuleFilter(cfg)
    if method == "contract":
        return ContractFilter(cfg)
    if method == "llm_judge":
        return JudgeFilter(cfg, judge_fn=judge_fn)
    if method == "oracle":
        return OracleFilter(cfg, truth_fn=truth_fn)
    raise ValueError(f"unknown method {method}")
