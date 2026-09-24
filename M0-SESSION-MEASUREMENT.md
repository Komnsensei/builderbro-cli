# M0 — what the session actually does

**Milestone:** `bro-freebuff-brain-spec.md` §14, M0 — *instrument first*.
**Measured:** 2026-09-23, on the operator's live session.
**Client:** core `0.0.186` (`linux-arm64`), npm wrapper `0.0.154` — the spec's
§18.7 was measured against `0.0.180`, so the drift below is drift within four
patch versions.
**Instrument:** `bro_session_measure.py` (89 tests when this was measured; 94 now,
the reader having gained 5 for the `_message_spans` refusal — the counts in this
document are the ones M0 produced, not today's, `python3 bro_session_measure_test.py`).

**Reproduce:**

```bash
python3 bro_session_measure.py --report /tmp/m0.json     # the snapshot, read-only
python3 bro_session_measure.py --watch 140               # both watermark rules, live
python3 bro_session_measure.py --spawn-probe             # §19.9 Q1–Q5, isolated HOME
```

M0's exit criterion is a written measurement, numbers rather than impressions,
"including one honest `inconclusive` if that is the result". There are four
below, and the three amendments are the reason this milestone exists: each of
them would have failed *silently* in M1, and two of them would have failed in
the expensive direction — refusing every turn on a working brain.

---

## 1. What the spec got right

| §18.7 / §19 claim | Measured | Verdict |
| --- | --- | --- |
| The lock is global and is `{instanceId, pid}` | `{instanceId: 47ede7b7-…, pid: 17101}`, exactly 2 keys, no extras | **confirmed** |
| The lock's `pid` is the **core**, not a wrapper | `/proc/17101/cmdline` = `/root/.config/manicode/freebuff` | **confirmed** |
| The operator's own session is the normal case | the lock's owner *is* the live session this measurement ran inside | **confirmed** |
| The newest `chats/` directory is not the conversation | newest by name `2026-09-23T11-48-56.035Z`; the conversation is `2026-09-21T08-23-48.129Z` — **2.14 days apart** | **confirmed** |
| A conversation dir holds `chat-meta.json`; a launch log does not | 54 dirs: 15 conversations, 39 log dirs. The shape test resolves it | **confirmed** |
| The project key is the path **basename**, and collides | `recent-projects.json` lists both `/mnt/sdcard/Download/builderbro` and `/sdcard/Download/builderbro`; both key to `builderbro` → `ambiguous` | **confirmed** |
| `chat-messages.json` is a compact single-line array | **0 newlines in 2,098,666 bytes** | **confirmed** |
| `content` is empty on assistant messages; the answer is in `blocks[]` | **5 of 5** assistant messages have empty `content` (spec: 51 of 53) | **confirmed** |
| `timestamp` is display-only (`"05:52 AM"`), `id` carries epoch ms | not used for ordering anywhere in the instrument | **confirmed** |
| `mode-divider` messages exist and are `variant: ai` | 1 divider, id `divider-1790164293867` | **confirmed** |
| Reasoning is separable by structure, not heuristic | `textType: "reasoning"` — 105 reasoning blocks vs 85 answer blocks | **confirmed** |

## 2. Amendment A — the tool block is `tool`, not `tool-call`

`§18.7` measured tool-call blocks as `{"type": "tool-call", …}` on `0.0.180`. On
`0.0.186` the type is **`tool`**. Measured across the live record: **142 tool
blocks, all of type `tool`, zero of type `tool-call`**.

A reader written from `§18.7` would therefore find **no tool calls at all**. It
would then report `cannot_verify` on every turn, see no evidence that anything
acted, and — worst of the failure modes available here — *look exactly like a
careful verifier*. This is the defect the module's tests call out by name.

The instrument accepts both spellings and **reports which one fired**
(`resolve_turn` → `detail.tool_block_type`), so the next drift is a number in a
report rather than a silent refusal.

Two more shape differences in the same block, both `0.0.186`:

| Field | §18.7 | measured |
| --- | --- | --- |
| `type` | `tool-call` | `tool` |
| `output` | an object (`{…}`) | a **string** — e.g. `"files: \n  - files.zip\n…"` |

`toolName` / `input` / `toolCallId` / `agentId` match the spec. The tools
actually observed, by name — this is the auditable evidence `§C4` verifies:

```
run_terminal_command 81 · str_replace 29 · read_files 16 · code_search 5
write_file 5 · write_todos 3 · list_directory 2 · glob 1   = 142
```

## 3. Amendment B — `isComplete` exists, and the watermark rule is backwards

### 3.1 The flag the spec did not know about

`§18.6` proposed deciding that a turn is finished by watching for a quiet period
(`QUIET_MS`, default 1200 ms proposed). The record carries an explicit flag
instead: `isComplete`, present and `true` on **3** assistant messages, and
**absent** on the turn currently being written.

Absent is the signal: 3 of 5 assistant messages carry `isComplete: true`, the
divider carries none (it is a marker, not a turn), and the in-flight message
carries none. So a snapshot can decide completion structurally rather than by
timing it, which is the difference between a rule that works and a rule that
needs a clock.

The instrument reports which signal fired and refuses to call a record
`complete` on the quiet-period signal alone, because a quiet period cannot be
evaluated from a snapshot.

### 3.2 The watermark rule: measured, and it does not work as written

`§18.7` proposes a watermark at the end of what has been read, verified with a
prefix hash, and says a mismatch costs "a resync… never a silent misread". The
hash works. The **offset** does not.

**240 s of polling the live record at 0.25 s (1,081 polls), end-of-file
watermark:** the file grew by 4,940 bytes and produced **4 resyncs and 0 usable
deltas**.

**Why, at byte resolution.** Two snapshots 25 s apart:

| | |
| --- | --- |
| first differing byte offset | **1,959,925** |
| bytes rewritten in place | 61 |
| bytes appended | 5,975 |
| the in-flight message spans | bytes **1,451,109 → 1,965,960** |
| the last **finished** message ends at | byte **1,450,253** |

Everything up to the divergence was byte-identical, and the divergence is
*inside the message being written* — the client rewrites that message as its
blocks and metadata grow, so the bytes just before the closing bracket move on
every append. A watermark at the end of file is therefore invalidated by the
growth it is watching for.

**The correction.** The watermark belongs at the end of the last **settled**
message. Measured on the live record:

```
settled boundary : byte 1,458,377   (16 of 17 messages settled, 1 in flight)
record           : 2,098,666 bytes
deliberately unconsumed : 640,289 bytes — 31% of the record
```

**115 s of polling with the settled watermark:** grew 3,910 bytes → **0 resyncs**,
58 in-flight re-reads. The turn being written comes back on every poll until it
settles, which is correct: it is the turn being watched, and it is not yet an
answer.

Two things had to be right for that to work, and both were found by measuring
rather than by design:

1. **"Settled" must include messages that carry no flag.** A user message is
   complete the moment it is written, and so is a `mode-divider`. Treating
   "no flag" as "in flight" left **12 of 17** messages permanently unconsumed and
   parked the boundary at byte 1,457,492 with only 3 consumed — for no reason.
2. **The boundary must be computed over bytes, not over a decoded string.** The
   first implementation decoded the file and used string indices; it found the
   live record unreadable in principle, because a string index stops equalling a
   byte offset as soon as any character is not ASCII — and the live record
   contains literal multi-byte characters. It now scans JSON structure in bytes,
   which is safe because every structural character of JSON is ASCII.

### 3.3 What the correction costs

Decoding the settled region on every poll measured **1.75 s per poll** against
**22 ms** for the end-of-file rule — 80×. The instrument now *advances* the
boundary over the new suffix instead of recomputing it, which is sound because
the prefix hash has already proved those bytes unchanged:

```
cold settled read                     8.496 s
advance over the verified prefix      0.903 s     (9.4x)
```

Still not free: the residual 0.9 s is the in-flight message itself, so a bridge
can poll this record at about **1 Hz, not 5 Hz**. That is a measured budget for
M1, not a caveat.

### 3.4 The channel is not monotonic

Two reads 25 s apart saw the record go from **2,037,463 to 2,016,573 bytes** —
*shrinking* by 20,890, with the in-flight message's `isComplete` going from
present to absent. So the record is not append-only in any direction, and the
reader treats a shrink as a `resync` rather than a negative slice. Any design
that assumes "the file only grows" is wrong on this box.

## 4. Amendment C — the session axis cannot be read by substring

`§19.2` names the correct idea — process liveness is not session connectivity —
and names the wrong source of truth: "the client's `log.jsonl` tail".

`log.jsonl` is structured JSON, and it records **the payloads of the session's
own actions**: every file written, every command run. So the lifecycle phrases
appear inside content that has nothing to do with lifecycle. Measured on the
live conversation's log:

```
raw substring hits for the two phrases        27
of which the phrase is inside a payload       18   (a source file, a command's output)
of which the client actually said it           9
```

The instrument's first live reading reported **`over`** on a session that was
demonstrably working — because the phrase it matched was in a test fixture the
session had just written to disk. That verdict would have made the router refuse
**every turn** and fall through to Groq on a live brain: exactly the "wrong
answer in the safe-looking direction" failure this appendix exists to prevent.

The fix is to read the event's own `msg` field and count payload mentions
separately. Live result:

```
session axis        reconnecting          last event: "Reconnection detected, firing onReconnect callback"
real lifecycle events  9
payload mentions      35
```

Plus one rule the spec did not state: **a marker is a statement about the past.**
If the record was written *after* the last `over` marker, the session is
connected and the marker is stale. Both directions are pinned by tests.

## 5. §19.9 — the seven questions

| # | Question | Answer |
| --- | --- | --- |
| 1 | Does the client delete the lock on clean exit, or leave a stale file? | **inconclusive** — no clean exit was observed, so there was nothing to observe. Unanswered, not assumed. |
| 2 | Is `instanceId` regenerated per launch, or stable? | **inconclusive** — one instanceId observed (`47ede7b7-…`). Needs two launches, which needs two clean exits. |
| 3 | What does a second launch actually do? | **parks.** After 60 s the second instance was still alive, painting a TUI (3,195 bytes of output), with **no exit code**. The spec's hypothesis is confirmed, and it is worse than an error: a second launch is *silent*. |
| 4 | Does a parked second instance write anything detectable? | Not to disk. It wrote **no lock file** in its own home and **no download** (the seeded core skipped the 49.4 MB fetch). It is detectable only by its rendered stdout. The real lock was byte-unchanged before and after. |
| 5 | Is `pid` the core or a wrapper/child? | **the core** — `/proc/17101/cmdline` is `/root/.config/manicode/freebuff`. |
| 6 | Does the project key follow `--cwd` or the shell's `cwd`? | **Partly answered, and a better key exists.** The collision is real (§1). But the record self-identifies: `metadata.runState.sessionState.fileContext.projectRoot` = `/mnt/sdcard/Download/builderbro`, with `cwd` the same. M1 should break the collision by reading the record rather than by trusting the basename. |
| 7 | Ready-frame time on a cold cache; is `--continue` faster? | **inconclusive here.** The isolated launch parked without completing a handshake, and this instrument does not answer terminal queries. The existing `live_mcp_probe.py` measured **≈12.3 s to first frame** on this box; that is the number M1 should start from and re-measure. |

One more measured fact the probe needed: **`HOME` is honoured by the client.** That
is what makes a second launch measurable without contesting the operator's lock —
and it is why a bare scratch `HOME` spends its whole budget downloading 49.4 MB,
so the probe seeds the core instead.

## 6. The completion rule, and a defect it found in itself

Run against the live record, the `§18.6` completion rule resolves a real turn:
the last settled reply is `ai-1790164732561-649f8aefca423`, signal `isComplete`,
**5,910 characters** of answer and **58 tool calls** — with the instrument
walking back **9** in-flight turns to reach it.

That self-check found a defect in the rule as I first wrote it. Taking the
**newest** qualifying assistant message made `complete` *unreachable*: on a live
box the newest qualifying message is always the turn currently being written, so
**12 of 12** real prompts returned `streaming`. The reply to a prompt is the
**first** qualifying message at or after it — which also refuses to follow the
record into somebody else's later turn, the same "never guess" rule as everywhere
else.

It is worth stating plainly: the instrument found this in its own logic before
it found it in the spec's, which is what a measurement milestone is for.

## 7. Unmeasured, and the verdict on §14

`§14` says no design decision in M1+ may assume an unmeasured channel. The state
of each channel after M0:

| Channel | State |
| --- | --- |
| the lock file and the `PROC` axis | **measured** — verdicts, pid identity, start-time, zero signals |
| project resolution | **measured** — collision real; record-carried `projectRoot` identified as the better key |
| active-conversation resolution | **measured** — shape test, 2.14-day drift |
| `chat-messages.json` reads | **measured** — snapshot, delta, resync, shrink, and a corrected watermark rule |
| turn completion | **measured** — `isComplete`, with the two-signal fallback |
| the SESS axis | **measured** — via `msg`, event/mention separated |
| **torn reads** | **not observed.** 0 in ~1,600 polls across four runs. The hazard is real by construction (a 2 MB file rewritten in place) and the reader has a retry, but "not observed" is not "does not happen". |
| **boot time / ready frame** | **not re-measured here** (≈12.3 s from `live_mcp_probe.py`). |
| **second-launch lock semantics (Q1, Q2)** | **unmeasured** — needs two clean exits. |
| **`--continue` resume speed** | **unmeasured.** |
| **M4's `bro__*` tools over MCP** | untouched by M0; still the earlier `inconclusive` probe. |

M1 may proceed on the measured channels. It may **not** assume a boot time, a
torn-read rate, `--continue` behaviour, or that an exit cleanly releases the lock.
