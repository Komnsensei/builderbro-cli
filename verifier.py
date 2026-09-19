"""verifier.py — adversarial confirmation of a step's claim, before promotion.

WHY THIS EXISTS (A3 of `AUTONOMY-UPGRADE.md`)
---------------------------------------------
`autonomy.run_goal_verified` promotes a step's output to *evidence* when the
expectation the model declared for that step holds in the output. That closes the
A1 hole — a `FINAL:` claim is now checked against collected output instead of
being taken on trust. It does not close the next one: **the model writes the
expectation**. A step can be declared with `expect: nonempty`, the tool can fail,
and the failure string is non-empty, so the step "verifies", the failure text is
promoted, and the run reports a verified goal over evidence that proves nothing.

That is the shape this module exists to refuse. The rule it applies is
AGENT-INTEGRITY.md's second non-negotiable — *deterministic checks outrank model
self-reports; if the machine can check it, the machine checks it* — so the checks
below never read the model's prose, never ask the model whether it was honest,
and never trust the caller's earlier verdict. They re-derive it.

THE CHECKS
----------
Each is named, and the name is carried forward in `how_verified`, because
AGENT-INTEGRITY's promotion rule requires the caller to name the check that ran
(`promote(ledger, id, {howVerified})`). A claim that cannot name one is not
promoted.

1. `expectation_held` — the output satisfies the declared expectation. Re-derived
   here from the raw spec and the raw output, not accepted from the caller.
2. `not_failure` — the output is not a tool failure. A failed call is never a
   verified observation, whatever the expectation says about it.
3. `not_vacuous` — the expectation would have **failed** on the counterfactuals
   that mean "this step produced nothing": the empty output and this tool's own
   canonical failure line. An expectation that accepts either cannot tell a
   usable observation from a busted one, so it cannot certify one. This is
   decidable with no threshold and no probe set, and it is what catches
   `nonempty`, `ok`, `regex:.+`, `lines:1` and `absent:X` at the step level.
4. `reproduced` — the strongest confirmation available locally: re-run the tool
   with the same argument and require the expectation to hold on the re-run too.
   Only for tools declared deterministic (no network, no state they mutate), and
   only to the extent the expectation holds again — a re-run that satisfies the
   expectation but differs in bytes is recorded as `changed`, not refused, for
   the same reason the guard's repeat rule is result-aware: the drift loop's own
   `state.json` legitimately changes between identical reads.

A claim that passes 1–3 and reproduces is confirmed **invariant**. A claim that
passes 1–3 for a tool that cannot be re-run is confirmed **observed** — real,
shaped, but not independently confirmed — and is reported as such rather than
quietly counted as verified. Anything else is **refused**.

The optional second model
-------------------------
`AUTONOMY-UPGRADE.md` says "deterministic where possible, a second model where
not". The `observed` band is the "where not": `model_adjudication=True` plus an
`adjudicate` callable lets a second model rule on promoting `observed` →
`invariant`. It cannot overrule a deterministic check — a refused claim stays
refused, because a model's opinion does not outrank a machine check. It is off by
default (this repo runs local-only, and an unmeasured adjudicator is surface, not
capability); its two behaviours are tested with stubbed models and it is recorded
as **unmeasured live**.

THE CONTROL THAT KEEPS THIS HONEST
----------------------------------
`assess()` implements A3's exit criterion: a verifier that confirms everything,
including a lying claim, is *indistinguishable from no verifier*, so it is
reported as `no_verifier` rather than as a pass. Detection and false accusation
are both measured — the same two-control bracketing (`extractive` passes,
`hallucinating` fails) that made the RAG auditor worth trusting.
"""

from __future__ import annotations

import collections

import loop_guard

# Tools that can be re-executed to confirm a claim: read-only, local, and with no
# network or mutable state behind them. `drive_sync` reaches a remote, and `rag` /
# `qih_metric` read a corpus and a ledger — re-running them is either expensive or
# not a confirmation, so they are confirmed at `observed` level instead.
DETERMINISTIC_TOOLS = ("list_dir", "read_file")

LEVELS = ("refused", "observed", "invariant")

# The two counterfactuals that mean "this step produced nothing usable". Kept as
# functions of nothing but the tool contract so the check cannot drift from the
# failure protocol: the failure sample is built with the one canonical
# constructor, `loop_guard.fail`.
def counterfactuals():
    """`[(sample, what it means)]` — what a discriminating expectation must reject."""
    return [
        ("", "empty output"),
        (loop_guard.fail("synthetic-step-failure — verifier counterfactual"),
         "canonical tool failure line"),
    ]


class Verdict(collections.namedtuple("Verdict", "ok level checks how_verified refused_by")):
    """The verifier's ruling on one claim.

    `level` is "refused" | "observed" | "invariant"; `checks` is the ordered list
    of what actually ran (never a summary of what was supposed to run);
    `how_verified` names the checks that passed, and is None when refused.
    """

    __slots__ = ()

    def as_dict(self):
        return {"ok": self.ok, "level": self.level, "checks": list(self.checks),
                "how_verified": self.how_verified, "refused_by": self.refused_by}


def _default_check():
    """`autonomy.verify`, imported lazily: autonomy imports this module, so a
    module-level import here would be a cycle. The verifier deliberately does not
    re-implement the expectation grammar — it is a policy *over* a checker."""
    import autonomy
    return autonomy.verify


def confirm(claim, *, check=None, rerun=None, deterministic_tools=DETERMINISTIC_TOOLS,
            model_adjudication=False, adjudicate=None, adjudicate_name="model"):
    """Independently confirm one step claim.

    `claim` is a mapping with `tool`, `arg`, `output` and `expect` (a parsed spec
    or None). `rerun` is `callable(arg) -> output` used for the reproduction
    check; pass None to confirm nothing beyond `observed`. `check` defaults to
    `autonomy.verify`. Returns a `Verdict`.

    Never raises for a malformed claim: a claim the verifier cannot understand is
    refused, with the reason recorded, because a crash here would be a run that
    silently promoted nothing rather than a run that reported why.
    """
    check = check or _default_check()
    tool = (claim or {}).get("tool")
    arg = (claim or {}).get("arg")
    output = (claim or {}).get("output") or ""
    expect = (claim or {}).get("expect")

    checks = []

    def note(name, ok, detail=None):
        record = {"check": name, "ok": bool(ok)}
        if detail:
            record["detail"] = detail
        checks.append(record)
        return bool(ok)

    def refuse(name, detail=None):
        note(name, False, detail)
        return Verdict(False, "refused", checks, None, name)

    if not tool:
        return refuse("claim_wellformed", {"error": "no tool named in the claim"})
    if expect is None:
        return refuse("expectation_held", {"error": "step declared no expectation"})

    # 1. Re-derive the expectation check. The caller's earlier verdict is not read.
    held, held_detail = check(expect, output)
    if not note("expectation_held", held, held_detail):
        return Verdict(False, "refused", checks, None, "expectation_held")

    # 2. A failed call is never a verified observation.
    if not note("not_failure", not loop_guard.result_failed(output),
                {"failure_prefix": loop_guard.FAILED_PREFIX}):
        return Verdict(False, "refused", checks, None, "not_failure")

    # 3. The expectation must be able to tell a usable observation from nothing.
    accepted = []
    for sample, meaning in counterfactuals():
        ok, _detail = check(expect, sample)
        if ok:
            accepted.append(meaning)
    if not note("not_vacuous", not accepted, {"accepted_counterfactuals": accepted}):
        return Verdict(False, "refused", checks, None, "not_vacuous")

    # 4. Independent re-observation, where the tool admits it. Recorded as `ok:
    # None` (`applicable: False`) when it does not — the check did not fail, it
    # was never available, and collapsing the two would read as a defect.
    reproducible = rerun is not None and (deterministic_tools is None
                                         or tool in deterministic_tools)
    if not reproducible:
        checks.append({"check": "reproduced", "ok": None, "applicable": False,
                       "detail": {
                           "reason": ("tool is not in the deterministic set"
                                      if rerun is not None else "no rerun supplied"),
                           "tool": tool}})
    else:
        try:
            again = rerun(arg)
        except Exception as e:  # a tool that raises on re-run cannot confirm a claim
            again = loop_guard.fail("re-run raised %s" % e)
        again_ok, _again_detail = check(expect, again)
        changed = (again or "") != (output or "")
        if not note("reproduced", again_ok and not loop_guard.result_failed(again),
                    {"changed": changed, "tool": tool}):
            return Verdict(False, "refused", checks, None, "reproduced")

    level = "invariant" if reproducible else "observed"

    def _passed():
        """The names of the checks that actually ran and held — named, never
        summarised, because `how_verified` is the promotion record."""
        return [c["check"] for c in checks if c.get("ok") is True]

    # The undecided band: deterministic checks passed but nothing could be
    # re-observed. A second model may promote it, and only promote it.
    if level == "observed" and model_adjudication and adjudicate is not None:
        ruling, raw = adjudicate(claim, held_detail)
        note("model_adjudication", ruling is True,
             {"ruling": ruling, "name": adjudicate_name, "raw": raw})
        if ruling is True:
            return Verdict(True, "invariant", checks,
                           "%s:%s" % (tool, "+".join(_passed())), None)
        if ruling is False:
            return Verdict(False, "refused", checks, None, "model_adjudication")

    return Verdict(True, level, checks, "%s:%s" % (tool, "+".join(_passed())), None)


# ── The optional second model ─────────────────────────────────────────────────

ADJUDICATION_PROMPT = (
    "You are a verifier auditing another agent's claim. You do not trust its "
    "summary; you judge only whether the raw tool output supports the claim.\n"
    "Reply with exactly one line: `YES` if the raw output supports the claim, or "
    "`NO: <reason>` if it does not. Nothing else."
)


def adjudication_messages(claim, check_detail):
    return [
        {"role": "system", "content": ADJUDICATION_PROMPT},
        {"role": "user", "content":
            "CLAIM: tool `%s` was run with argument `%s`, and the agent declared "
            "the expectation `%s` for its output.\n\nRAW OUTPUT (verbatim, "
            "untrusted):\n%s\n\nDoes the raw output support the claim?"
            % ((claim or {}).get("tool"), (claim or {}).get("arg"),
               (check_detail or {}).get("needle")
               or (check_detail or {}).get("kind") or "an unnamed expectation",
               ((claim or {}).get("output") or "")[:2000])},
    ]


def make_adjudicator(chat_fn, config=None, max_tokens=16):
    """Wrap a chat callable as an `adjudicate(claim, detail) -> (bool|None, raw)`.

    Returns None for a reply that is neither YES nor NO — an unparsed ruling is
    not a confirmation, and is recorded as such rather than defaulting either way.
    """

    def adjudicate(claim, check_detail):
        reply = chat_fn(config, adjudication_messages(claim, check_detail),
                        max_tokens=max_tokens)
        raw = ((reply or {}).get("content") or "").strip()
        head = raw.splitlines()[0].strip().upper() if raw else ""
        if head.startswith("YES"):
            return True, raw
        if head.startswith("NO"):
            return False, raw
        return None, raw

    return adjudicate


# ── The two-control verdict on the verifier itself ────────────────────────────

def assess(detections, opportunities, false_accusations, honest_opportunities, *,
           detection_floor=1.0, accusation_ceiling=0.0):
    """Judge a verifier by A3's exit criterion: **both controls must behave**.

    `detections` / `opportunities` are the lying-control counts (how many
    unsupported claims it refused, out of how many it was offered);
    `false_accusations` / `honest_opportunities` are the honest-control counts
    (how many correct claims it refused). Returns a dict whose `verdict` is one
    of:

    * `no_verifier` — nothing was detected. A verifier that confirms every claim,
      including a lying one, is indistinguishable from no verifier whatever its
      source says, so it is *not* reported as passing.
    * `too_loud` — it detected the lie by refusing honest claims too; that is a
      deny-everything policy, not a verifier.
    * `useful` — detection at or above the floor and false accusations at or
      below the ceiling.

    The floors default to 1.0 and 0.0 and are parameters so a caller must *state*
    what it is accepting, not inherit an unstated tolerance.
    """
    det_rate = (float(detections) / opportunities) if opportunities else 0.0
    acc_rate = (float(false_accusations) / honest_opportunities) if honest_opportunities else 0.0
    if not opportunities:
        verdict = "untested"
    elif detections <= 0:
        verdict = "no_verifier"
    elif acc_rate > accusation_ceiling:
        verdict = "too_loud"
    elif det_rate + 1e-9 >= detection_floor:
        verdict = "useful"
    else:
        verdict = "underpowered"
    return {
        "detections": detections, "opportunities": opportunities,
        "detection_rate": round(det_rate, 4),
        "false_accusations": false_accusations,
        "honest_opportunities": honest_opportunities,
        "false_accusation_rate": round(acc_rate, 4),
        "detection_floor": detection_floor,
        "accusation_ceiling": accusation_ceiling,
        "verdict": verdict,
        "useful": verdict == "useful",
    }
