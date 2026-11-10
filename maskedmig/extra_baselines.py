"""Reviewer TvTY, W1(c): three extra maintenance baselines for MaskedMig (masked_loop.py).

  llm_rank  LLM-driven prioritisation. Every quarter the model sees the record of every note and
            returns the notes it would check first, most suspicious first.
  mem0      Mem0-style update. Every exam is a piece of new information. For each note that was
            in context the model decides NONE / UPDATE / DELETE; UPDATE and DELETE flag the note.
  amem      A-Mem-style link evolution. Notes are linked by content similarity (each note to its
            K_LINKS nearest notes). When a note fails, its linked notes are flagged. This is the
            rule of FactorRuleFilter with groups taken from content instead of the given library.

All three extend TTLFilter (h = 5e-3, the clock of the ttl baseline). Flagged notes are probed
first; notes without a flag follow the TTL order. LLM calls are not charged to the budget.
These systems have no step that picks notes to verify under a budget, so what is ported here is
the mechanism each one uses to decide which memory to touch.
"""
import collections
import concurrent.futures as cf
import re

from bayesian_harness.filters import TTLFilter
from llm import chat, parse_marker_json

K_LINKS = 5            # amem: links per note
FLAG_SCORE = 0.8       # same as FactorRuleFilter.suspect_score


class _FlagTTL(TTLFilter):
    def __init__(self, cfg):
        super().__init__(cfg, h_ttl=5e-3)
        self.flag = {}                       # note -> flag score (above any TTL score)
        self.stats = collections.Counter()
        self.notes = None
        self.chat = None
        self.workers = 8

    def bind(self, notes, port, model, workers=8):
        self.notes = notes
        self.workers = workers
        self.chat = lambda msgs, max_tokens: chat(port, model, msgs, max_tokens=max_tokens)

    def stale_score(self, k, t):
        if k in self.flag:
            return self.flag[k]
        return super().stale_score(k, t)

    def observe_probe(self, k, is_valid, t):
        super().observe_probe(k, is_valid, t)
        self.flag.pop(k, None)

    def on_repair(self, k, t):
        super().on_repair(k, t)
        self.flag.pop(k, None)


RANK_SYS = """You maintain a set of notes about library APIs for a coding agent.
Libraries release new versions over time. A release can remove or rename APIs,
and then the notes about them go stale. Each period you can check only a
limited number of notes. Your job is to choose which notes to check first."""

RANK_USER = """## Notes

id | library | API | months since last confirmed working | times read in tasks | task passes since last check | task failures since last check
{rows}

## Results of the checks in the last period

{events}

## Budget

About {budget} checks this period.

Return the ids of the {k} notes most likely to be stale, most suspicious first.
Answer with one line and nothing else:

RANK {{"ids": [<id>, <id>, ...]}}"""


class LLMRankFilter(_FlagTTL):
    name = "llm_rank"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.hist = {}
        self.events = []

    def add_entry(self, k, parents=(), t=0.0, alpha=None, beta=None):
        super().add_entry(k, parents, t, alpha, beta)
        self.hist[k] = [0, 0]

    def update(self, k, y, t, msg=None, weight=1.0):
        super().update(k, y, t, msg, weight)
        self.hist[k][0 if y == 1 else 1] += 1

    def observe_probe(self, k, is_valid, t):
        super().observe_probe(k, is_valid, t)
        self.hist[k] = [0, 0]
        self.events.append((k, bool(is_valid)))

    def on_repair(self, k, t):
        super().on_repair(k, t)
        self.hist[k] = [0, 0]

    def plan(self, ids, t, use_count, budget):
        self.flag.clear()
        rows = []
        for i, k in enumerate(ids):
            n = self.notes[k]
            rows.append(f"{i} | {n['lib']} | {n['api']} | {t - self.last_success[k]:.0f} | "
                        f"{use_count[k]} | {self.hist[k][0]} | {self.hist[k][1]}")
        if self.events:
            ev = "\n".join(f"- {self.notes[k]['lib']} / {self.notes[k]['api']}: "
                           f"{'still valid' if ok else 'STALE'}" for k, ok in self.events)
        else:
            ev = "(no checks yet)"
        self.events = []
        k_ask = min(len(ids), 2 * int(budget))
        picked = []
        try:
            self.stats["llm_calls"] += 1
            reply = self.chat([{"role": "system", "content": RANK_SYS},
                               {"role": "user", "content": RANK_USER.format(
                                   rows="\n".join(rows), events=ev, budget=int(budget), k=k_ask)}],
                              max_tokens=700)
            got = parse_marker_json(reply, "RANK") or {}
            raw = got.get("ids")
            if not isinstance(raw, list):
                raw = [int(x) for x in re.findall(r"\d+", reply.split("RANK", 1)[-1])] if "RANK" in reply else []
            for x in raw:
                try:
                    i = int(x)
                except Exception:                                # noqa: BLE001
                    continue
                if 0 <= i < len(ids) and ids[i] not in picked:
                    picked.append(ids[i])
        except Exception:                                        # noqa: BLE001
            self.stats["llm_errors"] += 1
        if not picked:
            self.stats["rank_empty"] += 1
        picked = picked[:k_ask]
        for r, k in enumerate(picked):
            self.flag[k] = 1.0 - 0.4 * r / max(len(picked), 1)   # 0.6..1.0, keeps the model's order
        self.stats["ranked"] += len(picked)


MEM0_SYS = """You are a smart memory manager which controls the memory of a system.
You compare newly retrieved information with the existing memories. For each
existing memory you decide one operation:
- NONE: the new information is consistent with the memory, or says nothing about it.
- UPDATE: the new information shows the memory is outdated and must be revised.
- DELETE: the new information contradicts the memory."""

MEM0_USER = """## Existing memories

{mems}

## New information

A task on library {lib} about `{api}` was just attempted with these memories in context.
Outcome: {outcome}
Error: {err}
Submitted code:
{sub}

Answer with one line and nothing else:

MEMORY {{"ops": [{{"id": <memory id>, "event": "NONE" | "UPDATE" | "DELETE"}}, ...]}}"""


class Mem0Filter(_FlagTTL):
    name = "mem0"

    def _judge(self, rec):
        inj = [k for k in rec.get("injected", []) if k in self.notes]
        if not inj:
            return []
        mems = "\n\n".join(f"[{i}] {self.notes[k]['title']}\n{self.notes[k]['text'][:500]}"
                           for i, k in enumerate(inj))
        try:
            reply = self.chat([{"role": "system", "content": MEM0_SYS},
                               {"role": "user", "content": MEM0_USER.format(
                                   mems=mems, lib=rec["lib"], api=rec["api"],
                                   outcome="passed" if rec["solved"] else "FAILED",
                                   err=(rec.get("err") or "none") if not rec["solved"] else "none",
                                   sub=(rec.get("submission") or "(empty)")[:600])}],
                              max_tokens=200)
            got = parse_marker_json(reply, "MEMORY") or {}
            out = []
            for op in got.get("ops") or []:
                try:
                    i = int(op.get("id"))
                except Exception:                                # noqa: BLE001
                    continue
                if 0 <= i < len(inj) and str(op.get("event", "")).upper() in ("UPDATE", "DELETE"):
                    out.append(inj[i])
            return out
        except Exception:                                        # noqa: BLE001
            return None

    def observe_exams(self, recs, t):
        if not recs:
            return
        with cf.ThreadPoolExecutor(self.workers) as ex:
            res = list(ex.map(self._judge, recs))
        for r in res:
            self.stats["llm_calls"] += 1
            if r is None:
                self.stats["llm_errors"] += 1
                continue
            for k in r:
                self.flag[k] = FLAG_SCORE
                self.stats["flagged"] += 1


class AMemFilter(_FlagTTL):
    name = "amem"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.links = {}

    def bind(self, notes, port, model, workers=8):
        super().bind(notes, port, model, workers)
        from sklearn.feature_extraction.text import TfidfVectorizer
        ids = sorted(notes)
        docs = [f"{notes[k]['title']}\n{notes[k]['text']}\n{notes[k]['claims'][0]['snippet']}" for k in ids]
        X = TfidfVectorizer(token_pattern=r"[A-Za-z_][A-Za-z0-9_]+", sublinear_tf=True).fit_transform(docs)
        S = (X @ X.T).toarray()
        for i, k in enumerate(ids):
            order = sorted((j for j in range(len(ids)) if j != i), key=lambda j: -S[i, j])[:K_LINKS]
            for j in order:                                      # links are undirected
                self.links.setdefault(k, set()).add(ids[j])
                self.links.setdefault(ids[j], set()).add(k)
        same = sum(1 for k in ids for j in self.links.get(k, ()) if notes[j]["lib"] == notes[k]["lib"])
        tot = sum(len(v) for v in self.links.values())
        self.stats["links"] = tot
        self.stats["links_same_lib"] = same

    def _evolve(self, k):
        for j in self.links.get(k, ()):
            self.flag[j] = FLAG_SCORE
            self.stats["flagged"] += 1

    def update(self, k, y, t, msg=None, weight=1.0):
        super().update(k, y, t, msg, weight)
        if y == 0:
            self.flag[k] = FLAG_SCORE
            self._evolve(k)
        else:
            self.flag.pop(k, None)

    def observe_probe(self, k, is_valid, t):
        super().observe_probe(k, is_valid, t)
        if not is_valid:
            self._evolve(k)


EXTRA = {"llm_rank": LLMRankFilter, "mem0": Mem0Filter, "amem": AMemFilter}
