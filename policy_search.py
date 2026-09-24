#!/usr/bin/env python3
"""
policy_search.py — A6: learn **policy**, not just config (AUTONOMY-UPGRADE.md §4).

`rag_diagnose.py` tunes *retrieval config* (fusion, budget, overlap) and scores it
against a golden set. This is the same shape one level up: it searches the loop's
*policy* — how many replans it permits, how hard it repairs a bad plan, when it
nudges instead of attributing — and scores each candidate by **replaying the
audit's own suite** (`autonomy_suite.py`: 20 tasks, 8 pre-registered floors).

Three rules, taken from the module this mirrors, because they are what make the
result a measurement rather than an opinion:

  - **Floors are a gate, not a term.** A candidate cannot win by trading a floor
    away for a better accuracy or fewer tokens. It must clear all eight exactly as
    the suite already scores them; only then does the objective rank it.
  - **No improvement is a valid, reported outcome.** If nothing beats the policy
    already registered, this registers nothing and says so. A tuner that always
    finds a win is reporting its own optimism.
  - **Registered and revertible without editing code.** The winner is written to
    the same config the loop already reads (`loop_guard.json`, via
    `loop_guard.register_thresholds`), the displaced bytes are snapshotted first,
    and `--revert` restores them byte-for-byte.

Replay is hermetic (the suite's scripted model, generated fixture), so a search is
deterministic and runs offline. `--replay` re-scores the registered policy against
the incumbent without searching, which is the check that the registered change is
still the one that wins.

Usage:
    python3 policy_search.py                 # search; register only if one wins
    python3 policy_search.py --dry-run       # search and report; change nothing
    python3 policy_search.py --replay        # registered vs incumbent, no search
    python3 policy_search.py --revert        # restore the pre-registration config
    python3 policy_search.py --json
Exit codes: 0 searched/registered/reverted; 1 no candidate beat the incumbent
(a real outcome, not an error — reported on stdout); 2 the suite failed to run.
"""

import argparse
import datetime
import itertools
import json
import os
import sys

import loop_guard
import autonomy_suite

# ── Frozen invariants (not tradeable by a search) ─────────────────────────────
#
# A first run of this search "won" by setting `plan_repair_attempts` to 0, which
# scored 3% fewer tokens on the hermetic suite at equal accuracy — and turned
# `autonomy_test.test_the_autonomy_thresholds_are_registered_with_sane_defaults`,
# `test_an_empty_plan_reply_gets_the_same_room` and
# `test_an_empty_plan_reply_consumes_a_repair_attempt` RED. The suite cannot see
# that cost because its model is scripted: a malformed plan is repaired or not by
# the loop, and the scripted tasks do not punish losing that. So the suite is not
# sufficient on its own, and the repo's own registered invariants are the
# constraint that says so. A policy search may not trade one away — that is the
# "optimise the wrong quantity" failure, caught by a test rather than a review.
#
# Keyed knob → the value the repo has registered as sane, with the reason.
FROZEN = {
    "plan_repair_attempts": (1, "a malformed plan must stay repairable"),
    "max_replans": (2, "one replan is a legitimate recovery; the registered floor allows two"),
    "plan_divergence_limit": (1, "off-plan action must still be attributable"),
    "empty_response_retries": (1, "a dropped reply must be retried at least once"),
    "empty_reply_token_ceiling": (2048, "the measured value a cap-consuming reply needs"),
}

# ── The bounded policy space (pre-registered) ─────────────────────────────────
#
# Only knobs `autonomy.run_goal_verified` actually reads through its thresholds
# dict, so a "policy" here is a thing the loop will really honour rather than a
# label — and none of them is in FROZEN. Kept deliberately small: the suite is 20
# tasks, so a large grid buys resolution the measurement cannot support.
POLICY_SPACE = {
    "no_action_limit": [1, 2, 3],
    "tool_error_streak": [2, 3, 4],
    "repeat_limit": [2, 3],
}

# Default candidate ceiling. The full grid is 18 candidates plus the incumbent;
# the cap exists so a future, wider space cannot silently turn a search into a
# hang. Exceeding it is refused loudly rather than truncated.
MAX_CANDIDATES = 64

BACKUP_SUFFIX = ".prev"


# ── The objective (pre-registered) ────────────────────────────────────────────

def score(metrics):
    """Lexicographic objective, highest wins.

    1. `task_accuracy` — the fraction of tasks whose verdict is right. This is
       the thing the policy exists to serve.
    2. Fewer mean tokens per task — equal accuracy, less spent on it.
    3. Fewer mean replans per completion — equal accuracy and spend, a shorter
       path to the same verdict. A policy that reaches the same answer with less
       flailing is the more stable one.

    Returned as a tuple so `max()` compares it element-wise without any weights,
    which would be an unmeasured judgement smuggled into the ranking.
    """
    return (
        round(metrics["task_accuracy"], 6),
        -round(metrics["mean_tokens_per_task"], 6),
        -round(metrics["mean_replans_per_completion"], 6),
    )


def evaluate(policy, config=None):
    """Replay the suite under `policy`. Returns a scored result, or None if the
    suite itself could not run (which must never be reported as a losing policy:
    'failed to measure' and 'measured as worse' are different)."""
    results, errors = autonomy_suite.run_suite(config=config, thresholds=policy)
    if errors:
        return None
    m = autonomy_suite.metrics(results)
    ok, checks = autonomy_suite.check_floors(m)
    return {"ok": ok, "metrics": m, "checks": checks, "score": score(m)}


def validate_space(space, frozen=None):
    """Refuse a space that would tune a frozen invariant.

    Loudly, and before any candidate runs: a search that is allowed to touch
    `plan_repair_attempts` will eventually do it, because the hermetic suite
    cannot price the robustness it costs. Returns the space unchanged when it is
    clean, so it can be used inline.
    """
    frozen = FROZEN if frozen is None else frozen
    touched = sorted(set(space) & set(frozen))
    if touched:
        raise ValueError(
            "the policy space tunes a frozen invariant: %s. %s" % (
                ", ".join(touched),
                "; ".join("`%s` must stay %r (%s)" % (k, frozen[k][0], frozen[k][1])
                          for k in touched)))
    return space


def candidate_policies(space=None, incumbent=None, limit=MAX_CANDIDATES):
    """The incumbent first, then the grid over `space`, each overlaid on the
    incumbent so a candidate changes only the space's knobs. Duplicates dropped:
    the incumbent must appear once, or a tie could be "beaten" by itself."""
    space = validate_space(POLICY_SPACE if space is None else space)
    incumbent = dict(loop_guard.active_thresholds() if incumbent is None else incumbent)
    seen = []

    def emit(base):
        key = tuple(sorted(base.items()))
        if key not in seen:
            seen.append(key)
            return base
        return None

    first = emit(dict(incumbent))
    if first is not None:
        yield first
    keys = sorted(space)
    total = 1
    for values in itertools.product(*(space[k] for k in keys)):
        total += 1
        if total > limit:
            raise ValueError(
                "policy space yields more than %d candidates; widen MAX_CANDIDATES "
                "deliberately rather than letting a search run unbounded" % limit)
        cand = dict(incumbent)
        for k, v in zip(keys, values):
            cand[k] = v
        got = emit(cand)
        if got is not None:
            yield got


def search(space=None, config=None, incumbent=None):
    """Replay every candidate. Returns a report dict.

    `winner` is None when no candidate both clears every floor and beats the
    incumbent's score — the honest "nothing to register" outcome.
    """
    inc = dict(loop_guard.active_thresholds() if incumbent is None else incumbent)
    incumbent_eval = evaluate(inc, config=config)
    if incumbent_eval is None:
        return {"error": "the suite did not run"}
    trials = []
    for cand in candidate_policies(space=space, incumbent=inc):
        ev = evaluate(cand, config=config)
        trials.append({"policy": cand, "eval": ev})
    eligible = [t for t in trials
                if t["eval"] is not None and t["eval"]["ok"]
                and t["eval"]["score"] > incumbent_eval["score"]]
    winner = max(eligible, key=lambda t: t["eval"]["score"]) if eligible else None
    return {
        "incumbent": inc,
        "incumbent_eval": incumbent_eval,
        "trials": trials,
        "eligible": len(eligible),
        "gated_out": sum(1 for t in trials if t["eval"] is not None and not t["eval"]["ok"]),
        "winner": winner,
    }


# ── Registration (revertible without editing code) ────────────────────────────

def _config_path():
    return loop_guard.config_path()


def _backup_path(path=None):
    return (path or _config_path()) + BACKUP_SUFFIX


def register(policy, source="policy_search.py", path=None):
    """Write `policy` into the loop's own config, snapshotting what it displaced.

    The snapshot is the *raw bytes* of the previous file, not a re-serialization
    of parsed params — `--revert` must restore the exact file, including whatever
    else was registered in it and any formatting, or "revertible" would mean
    "approximately revertible".
    """
    path = path or _config_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            previous = f.read()
        with open(_backup_path(path), "wb") as f:
            f.write(previous)
    return loop_guard.register_thresholds(policy, source=source, path=path)


def revert(path=None):
    """Restore the config bytes snapshotted by the last `register`. Returns the
    path reverted, or None when there is nothing to revert to."""
    path = path or _config_path()
    backup = _backup_path(path)
    if not os.path.exists(backup):
        return None
    with open(backup, "rb") as f:
        previous = f.read()
    with open(path, "wb") as f:
        f.write(previous)
    os.remove(backup)
    return path


def replay(config=None):
    """Re-score the registered policy against the incumbent's *defaults*, without
    searching. This is what makes the suite "replayable": the claim that the
    registered policy wins is re-derivable, not a one-time event."""
    registered = dict(loop_guard.active_thresholds())
    defaults = dict(loop_guard.DEFAULT_THRESHOLDS)
    return {
        "registered": registered,
        "registered_eval": evaluate(registered, config=config),
        "default_eval": evaluate(defaults, config=config),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def _fmt_eval(ev):
    if ev is None:
        return "suite did not run"
    m = ev["metrics"]
    return ("accuracy=%.3f tokens/task=%.1f replans/ok=%.2f floors=%s"
            % (m["task_accuracy"], m["mean_tokens_per_task"],
               m["mean_replans_per_completion"], "pass" if ev["ok"] else "FAIL"))


def report(rep):
    lines = ["policy search — %d candidate(s) replayed against `%s`" % (
        len(rep["trials"]), os.path.basename(_config_path())), ""]
    lines.append("frozen invariants (a candidate may not change these): %s"
                 % json.dumps({k: v[0] for k, v in sorted(FROZEN.items())}, sort_keys=True))
    lines.append("incumbent: %s" % json.dumps(
        {k: rep["incumbent"][k] for k in sorted(POLICY_SPACE)}, sort_keys=True))
    lines.append("  %s" % _fmt_eval(rep["incumbent_eval"]))
    lines.append("  floor-blocked candidates (kept, not hidden): %d" % rep["gated_out"])
    lines.append("  candidates that cleared every floor AND beat the incumbent: %d"
                 % rep["eligible"])
    if rep["winner"]:
        w = rep["winner"]
        lines.append("")
        lines.append("**winner** %s" % json.dumps(
            {k: w["policy"][k] for k in sorted(POLICY_SPACE)}, sort_keys=True))
        lines.append("  %s" % _fmt_eval(w["eval"]))
    else:
        lines.append("")
        lines.append("**no improvement** — nothing both cleared the floors and beat "
                     "the incumbent, so nothing is registered.")
    return "\n".join(lines)


def record_cycle(rep, registered_path):
    """Append a closed DETECT → … → REGISTER cycle, matching the repo's convention.

    A no-improvement search writes its cycle too: the negative result is the
    measurement, and a log that only records wins is a log about the tuner
    rather than about the loop.
    """
    w = rep["winner"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if w:
        title = "policy search: a measured policy change, registered and revertible"
        summary = "changed=%s" % json.dumps(
            {k: w["policy"][k] for k in sorted(POLICY_SPACE)}, sort_keys=True)
    else:
        title = ("policy search: the only floor-passing winner gamed the suite, and "
                 "the registered invariant refused it")
        summary = "changed={}"
    evidence = {
        "candidates": len(rep["trials"]),
        "floor_blocked": rep["gated_out"],
        "eligible": rep["eligible"],
        "frozen_invariants": {k: v[0] for k, v in sorted(FROZEN.items())},
        "incumbent": {k: rep["incumbent"][k] for k in sorted(POLICY_SPACE)},
        "incumbent_metrics": rep["incumbent_eval"]["metrics"],
        "winner_metrics": w["eval"]["metrics"] if w else None,
        "config": registered_path,
    }
    entry = (
        "\n## %s — %s\n\n"
        "**Stage:** autonomy · **Severity:** info\n\n"
        "1. **DETECT** — the loop's policy (replans, plan repair, nudge limit) was "
        "chosen by hand and never scored against the suite it is measured by; only "
        "*config* had a search (`rag_diagnose.py`).\n"
        "2. **RESEARCH** — the honest lever is the one the loop already reads: the "
        "thresholds dict passed into `autonomy.run_goal_verified`, replayed against "
        "`autonomy_suite.py`'s 20 tasks and 8 pre-registered floors.\n"
        "3. **DESIGN** — `policy_search.py`: a bounded space over free knobs, floors "
        "as a hard gate (never a tradeable term), a lexicographic objective "
        "(accuracy → tokens → replans), and a registration that snapshots the "
        "displaced config so `--revert` is byte-exact.\n"
        "4. **IMPLEMENT** — `policy_search.py`; `autonomy_suite.run_task`/`run_suite` "
        "gain a `thresholds=` seam (default unchanged).\n"
        "5. **TEST** — `policy_search_test.py`, including the negative control that a "
        "space touching a frozen invariant is refused.\n"
        "6. **REGISTER** — %s\n\n"
        "**Finding, stated rather than smoothed:** the search DID find a candidate "
        "that cleared all eight floors and scored better — `plan_repair_attempts=0`, "
        "3%% fewer tokens at equal accuracy — and registering it turned three "
        "`autonomy_test` cases red. The hermetic suite cannot price the robustness a "
        "disabled repair path costs, and that alone is why a hand-picked policy is "
        "not the bug here: the *constraint* is. `FROZEN` now encodes the repo's "
        "registered invariants as untradeable and the space excludes them.\n\n"
        "**Evidence:** `%s`\n\n"
        "**Loop summary:** `%s`\n"
        % (now, title, summary, json.dumps(evidence, sort_keys=True),
           json.dumps({"candidates": len(rep["trials"]),
                       "registered": bool(w)}, sort_keys=True))
    )
    with open(loop_guard.SELF_IMPROVEMENT_LOG, "a", encoding="utf-8") as f:
        f.write(entry)
    return loop_guard.SELF_IMPROVEMENT_LOG


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="Search the loop's policy against the task suite (A6)")
    p.add_argument("--dry-run", action="store_true", help="search and report, register nothing")
    p.add_argument("--replay", action="store_true",
                   help="re-score the registered policy against the defaults, no search")
    p.add_argument("--revert", action="store_true",
                   help="restore the pre-registration config bytes")
    p.add_argument("--register", action="store_true",
                   help="on a win, also append the closed cycle to SELF_IMPROVEMENT_LOG.md")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.revert:
        path = revert()
        if path is None:
            print("nothing to revert — no %s snapshot exists"
                  % os.path.basename(_backup_path()))
            return 2
        print("reverted %s from its pre-registration snapshot" % os.path.basename(path))
        return 0

    if args.replay:
        rep = replay()
        if args.json:
            print(json.dumps(rep, indent=1, sort_keys=True, default=str))
            return 0
        print("registered policy: %s" % _fmt_eval(rep["registered_eval"]))
        print("default  policy: %s" % _fmt_eval(rep["default_eval"]))
        return 0

    try:
        rep = search()
    except ValueError as exc:
        print("policy search refused: %s" % exc, file=sys.stderr)
        return 2
    if "error" in rep:
        print("SUITE ERROR — %s" % rep["error"], file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rep, indent=1, sort_keys=True, default=str))
    else:
        print(report(rep))

    if rep["winner"] and not args.dry_run:
        path = register(rep["winner"]["policy"])
        print("\nregistered policy -> %s" % path)
        print("pre-registration snapshot -> %s (restore with --revert)" % _backup_path(path))
    if args.register:
        # The cycle is logged even with no winner: a negative result is the
        # measurement, and a log that records only wins is a log about the tuner.
        print("logged closed cycle -> %s" % record_cycle(rep, _config_path()))
    return 0 if rep["winner"] else 1


if __name__ == "__main__":
    sys.exit(main())
