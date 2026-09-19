# SELF_IMPROVEMENT_LOG

Machine-appended by `rag_diagnose.py`. Each entry is one full
DETECT → RESEARCH → DESIGN → IMPLEMENT → TEST → REGISTER cycle over the RAG
pipeline. Entries are appended, never edited: a later cycle that contradicts
an earlier one leaves both on the record.

## 2026-09-14T07:57:27+00:00 — RAG tuning cycle

### 1. DETECT
Incumbent: `fusion=? density=0.00 budget=0 overlap=0`

Measured: span_coverage=1.0000, recall@k=0.8571, mrr=0.6667, objective=1.5238, chunks=112

### 2. RESEARCH — 3 stage-attributed finding(s)
- **retrieval** [high] 2/14 questions have no gold-bearing chunk in the top 6
  - evidence: `rag-g04; rag-g11`
  - lever: `fusion, dense_weight, chunk_budget_words, tokenisation`
  - hypothesis: either the query shares no vocabulary with the source sentence (lexical gap) or the fused score buried it
- **ranking** [high] MRR 0.667 is below floor 0.70 — gold chunks retrieved but ranked low
  - evidence: `rag-g03@rank4; rag-g10@rank4; rag-g12@rank3; rag-g13@rank2`
  - lever: `fusion mode (rrf vs linear), bm25_weight, bm25_k1`
  - hypothesis: RRF discards score magnitude, so a decisive BM25 lead is compressed into a near-tie
- **embedding** [medium] the hashed-dense channel found 0 gold chunks on its own
  - evidence: `attribution both=17 lexical_only=6 dense_only=0`
  - lever: `dense_weight, char_weight, dim`
  - hypothesis: a signed hashing vectoriser is not semantic; if it contributes no unique recall it should be downweighted rather than trusted

### 3. DESIGN
Bounded search over fusion × chunk budget × chunk overlap × dense weight (56 candidates). Hard gate: span_coverage=1.00, recall@k>=0.85, mrr>=0.70.
Objective: recall@k + mrr, tie-break top-1 source accuracy, then fewer chunks.

### 4. IMPLEMENT / 5. TEST
| fusion | dense | budget | overlap | chunks | span | recall@k | mrr | top1 | gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| linear | 1.00 | 100 | 20 | 177 | 1.000 | 0.929 | 0.729 | 0.643 | pass |
| bm25 | 0.00 | 100 | 20 | 177 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| linear | 0.00 | 100 | 20 | 177 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| rrf | 0.00 | 100 | 20 | 177 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| linear | 1.00 | 100 | 33 | 186 | 1.000 | 0.929 | 0.714 | 0.643 | pass |
| linear | 1.00 | 240 | 80 | 87 | 1.000 | 0.929 | 0.707 | 0.714 | pass |
| linear | 0.50 | 100 | 20 | 177 | 1.000 | 0.929 | 0.699 | 0.643 | near-floor |
| linear | 0.50 | 120 | 24 | 156 | 1.000 | 0.929 | 0.696 | 0.643 | near-floor |
| linear | 0.50 | 120 | 40 | 167 | 1.000 | 0.929 | 0.696 | 0.643 | near-floor |
| linear | 0.50 | 240 | 48 | 79 | 1.000 | 0.929 | 0.691 | 0.714 | near-floor |
| rrf | 0.50 | 240 | 48 | 79 | 1.000 | 0.929 | 0.687 | 0.714 | near-floor |
| linear | 0.50 | 100 | 33 | 186 | 1.000 | 0.929 | 0.687 | 0.643 | near-floor |
| rrf | 1.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.750 | 0.714 | pass |
| bm25 | 0.00 | 120 | 24 | 156 | 1.000 | 0.929 | 0.679 | 0.643 | near-floor |
| linear | 0.00 | 120 | 24 | 156 | 1.000 | 0.929 | 0.679 | 0.643 | near-floor |
| rrf | 0.00 | 120 | 24 | 156 | 1.000 | 0.929 | 0.679 | 0.643 | near-floor |
| bm25 | 0.00 | 100 | 33 | 186 | 1.000 | 0.929 | 0.669 | 0.643 | near-floor |
| linear | 0.00 | 100 | 33 | 186 | 1.000 | 0.929 | 0.669 | 0.643 | near-floor |
| rrf | 0.00 | 100 | 33 | 186 | 1.000 | 0.929 | 0.669 | 0.643 | near-floor |
| bm25 | 0.00 | 120 | 40 | 167 | 1.000 | 0.929 | 0.667 | 0.643 | near-floor |
| linear | 0.00 | 120 | 40 | 167 | 1.000 | 0.929 | 0.667 | 0.643 | near-floor |
| rrf | 0.00 | 120 | 40 | 167 | 1.000 | 0.929 | 0.667 | 0.643 | near-floor |
| rrf | 0.50 | 120 | 24 | 156 | 1.000 | 0.929 | 0.663 | 0.714 | near-floor |
| linear | 1.00 | 120 | 24 | 156 | 1.000 | 0.857 | 0.720 | 0.714 | pass |
| linear | 1.00 | 120 | 40 | 167 | 1.000 | 0.857 | 0.720 | 0.786 | pass |
| rrf | 0.50 | 120 | 40 | 167 | 1.000 | 0.929 | 0.645 | 0.714 | near-floor |
| linear | 1.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.714 | 0.714 | pass |
| rrf | 0.50 | 100 | 20 | 177 | 1.000 | 0.929 | 0.636 | 0.643 | near-floor |
| bm25 | 0.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.673 | 0.714 | near-floor |
| linear | 0.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.673 | 0.714 | near-floor |
| rrf | 0.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.673 | 0.714 | near-floor |
| bm25 | 0.00 | 240 | 80 | 87 | 1.000 | 0.857 | 0.669 | 0.714 | near-floor |
| linear | 0.00 | 240 | 80 | 87 | 1.000 | 0.857 | 0.669 | 0.714 | near-floor |
| rrf | 0.00 | 240 | 80 | 87 | 1.000 | 0.857 | 0.669 | 0.714 | near-floor |
| linear | 1.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.667 | 0.643 | near-floor |
| rrf | 0.50 | 100 | 33 | 186 | 1.000 | 0.929 | 0.594 | 0.643 | near-floor |
| linear | 0.50 | 240 | 80 | 87 | 1.000 | 0.857 | 0.663 | 0.714 | near-floor |
| rrf | 1.00 | 240 | 80 | 87 | 1.000 | 0.857 | 0.663 | 0.714 | near-floor |
| linear | 0.50 | 170 | 34 | 112 | 1.000 | 0.857 | 0.663 | 0.643 | near-floor |
| rrf | 1.00 | 120 | 24 | 156 | 1.000 | 0.857 | 0.663 | 0.643 | near-floor |
| rrf | 1.00 | 120 | 40 | 167 | 1.000 | 0.857 | 0.661 | 0.714 | near-floor |
| rrf | 0.50 | 240 | 80 | 87 | 1.000 | 0.857 | 0.659 | 0.714 | near-floor |
| bm25 | 0.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.643 | 0.786 | near-floor |
| linear | 0.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.643 | 0.786 | near-floor |
| rrf | 0.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.643 | 0.786 | near-floor |
| rrf | 1.00 | 100 | 20 | 177 | 1.000 | 0.857 | 0.643 | 0.714 | near-floor |
| rrf | 1.00 | 100 | 33 | 186 | 1.000 | 0.857 | 0.637 | 0.714 | near-floor |
| rrf | 0.50 | 170 | 34 | 112 | 1.000 | 0.857 | 0.619 | 0.643 | near-floor |
| linear | 0.50 | 170 | 56 | 126 | 1.000 | 0.857 | 0.600 | 0.643 | near-floor |
| linear | 1.00 | 170 | 56 | 126 | 1.000 | 0.857 | 0.600 | 0.643 | near-floor |
| rrf | 1.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.570 | 0.643 | near-floor |
| rrf | 0.50 | 170 | 56 | 126 | 1.000 | 0.857 | 0.552 | 0.643 | near-floor |
| rrf | 1.00 | 170 | 56 | 126 | 1.000 | 0.857 | 0.517 | 0.714 | near-floor |
| bm25 | 0.00 | 170 | 56 | 126 | 1.000 | 0.786 | 0.576 | 0.714 | near-floor |
| linear | 0.00 | 170 | 56 | 126 | 1.000 | 0.786 | 0.576 | 0.714 | near-floor |
| rrf | 0.00 | 170 | 56 | 126 | 1.000 | 0.786 | 0.576 | 0.714 | near-floor |

Best gated candidate: `fusion=linear density=1.00 budget=100 overlap=20` → objective 1.6571 (recall 0.929, mrr 0.729, 177 chunks).

Confidence gate (`min_query_support`) — the step that decides whether the corpus can answer at all, swept at the winning retrieval config:

| min_query_support | refusal_acc | false_answer | false_refusal | citation_fid | groundedness |
| --- | --- | --- | --- | --- | --- |
| 0.35 | 0.824 | 1.00 | 0.000 | 0.986 | 0.357 |
| 0.40 | 0.824 | 1.00 | 0.000 | 0.986 | 0.357 |
| 0.45 | 0.882 | 0.67 | 0.000 | 0.986 | 0.357 |
| 0.50 | 0.882 | 0.67 | 0.000 | 0.986 | 0.357 |
| 0.55 **<- chosen** | 1.000 | 0.00 | 0.000 | 0.986 | 0.357 |
| 0.60 | 0.941 | 0.00 | 0.071 | 0.986 | 0.405 |
| 0.65 | 0.882 | 0.00 | 0.143 | 0.986 | 0.476 |
| 0.70 | 0.824 | 0.00 | 0.214 | 1.000 | 0.500 |
| 0.75 | 0.706 | 0.00 | 0.357 | 1.000 | 0.619 |

### 6. REGISTER
retrieval: objective 1.5238 -> 1.6571 (recall 0.857 -> 0.929, mrr 0.667 -> 0.729); confidence gate: min_query_support=0.55 (refusal_accuracy=1.000, false_answer=0.00, false_refusal=0.00) — registered to rag_config.json

**Outcome:** `changed=True`

*Caveat: the golden set holds 14 answerable items, so one item is ~7% of recall. A config sitting on a floor is `near-floor`, not proven better.*


## 2026-09-14T08:23:30+00:00 — RAG tuning cycle

### 1. DETECT
Incumbent: `fusion=? density=0.00 budget=0 overlap=0`

Measured: span_coverage=1.0000, recall@k=0.9286, mrr=0.7286, objective=1.6571, chunks=177

### 2. RESEARCH — 2 stage-attributed finding(s)
- **retrieval** [high] 1/14 questions have no gold-bearing chunk in the top 6
  - evidence: `rag-g11`
  - lever: `fusion, dense_weight, chunk_budget_words, tokenisation`
  - hypothesis: either the query shares no vocabulary with the source sentence (lexical gap) or the fused score buried it
- **embedding** [medium] the hashed-dense channel found 0 gold chunks on its own
  - evidence: `attribution both=14 lexical_only=8 dense_only=0`
  - lever: `dense_weight, char_weight, dim`
  - hypothesis: a signed hashing vectoriser is not semantic; if it contributes no unique recall it should be downweighted rather than trusted

### 3. DESIGN
Bounded search over fusion × chunk budget × chunk overlap × dense weight (56 candidates). Hard gate: span_coverage=1.00, recall@k>=0.85, mrr>=0.70.
Objective: recall@k + mrr, tie-break top-1 source accuracy, then fewer chunks.

### 4. IMPLEMENT / 5. TEST
| fusion | dense | budget | overlap | chunks | span | recall@k | mrr | top1 | gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| linear | 1.00 | 100 | 20 | 177 | 1.000 | 0.929 | 0.729 | 0.643 | pass |
| bm25 | 0.00 | 100 | 20 | 177 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| linear | 0.00 | 100 | 20 | 177 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| rrf | 0.00 | 100 | 20 | 177 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| linear | 1.00 | 100 | 33 | 186 | 1.000 | 0.929 | 0.714 | 0.643 | pass |
| linear | 1.00 | 240 | 80 | 87 | 1.000 | 0.929 | 0.707 | 0.714 | pass |
| linear | 0.50 | 100 | 20 | 177 | 1.000 | 0.929 | 0.699 | 0.643 | near-floor |
| linear | 0.50 | 120 | 24 | 156 | 1.000 | 0.929 | 0.696 | 0.643 | near-floor |
| linear | 0.50 | 120 | 40 | 167 | 1.000 | 0.929 | 0.696 | 0.643 | near-floor |
| linear | 0.50 | 240 | 48 | 79 | 1.000 | 0.929 | 0.691 | 0.714 | near-floor |
| rrf | 0.50 | 240 | 48 | 79 | 1.000 | 0.929 | 0.687 | 0.714 | near-floor |
| linear | 0.50 | 100 | 33 | 186 | 1.000 | 0.929 | 0.687 | 0.643 | near-floor |
| rrf | 1.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.750 | 0.714 | pass |
| bm25 | 0.00 | 120 | 24 | 156 | 1.000 | 0.929 | 0.679 | 0.643 | near-floor |
| linear | 0.00 | 120 | 24 | 156 | 1.000 | 0.929 | 0.679 | 0.643 | near-floor |
| rrf | 0.00 | 120 | 24 | 156 | 1.000 | 0.929 | 0.679 | 0.643 | near-floor |
| bm25 | 0.00 | 100 | 33 | 186 | 1.000 | 0.929 | 0.669 | 0.643 | near-floor |
| linear | 0.00 | 100 | 33 | 186 | 1.000 | 0.929 | 0.669 | 0.643 | near-floor |
| rrf | 0.00 | 100 | 33 | 186 | 1.000 | 0.929 | 0.669 | 0.643 | near-floor |
| bm25 | 0.00 | 120 | 40 | 167 | 1.000 | 0.929 | 0.667 | 0.643 | near-floor |
| linear | 0.00 | 120 | 40 | 167 | 1.000 | 0.929 | 0.667 | 0.643 | near-floor |
| rrf | 0.00 | 120 | 40 | 167 | 1.000 | 0.929 | 0.667 | 0.643 | near-floor |
| rrf | 0.50 | 120 | 24 | 156 | 1.000 | 0.929 | 0.663 | 0.714 | near-floor |
| linear | 1.00 | 120 | 24 | 156 | 1.000 | 0.857 | 0.720 | 0.714 | pass |
| linear | 1.00 | 120 | 40 | 167 | 1.000 | 0.857 | 0.720 | 0.786 | pass |
| rrf | 0.50 | 120 | 40 | 167 | 1.000 | 0.929 | 0.645 | 0.714 | near-floor |
| linear | 1.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.714 | 0.714 | pass |
| rrf | 0.50 | 100 | 20 | 177 | 1.000 | 0.929 | 0.636 | 0.643 | near-floor |
| bm25 | 0.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.673 | 0.714 | near-floor |
| linear | 0.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.673 | 0.714 | near-floor |
| rrf | 0.00 | 240 | 48 | 79 | 1.000 | 0.857 | 0.673 | 0.714 | near-floor |
| bm25 | 0.00 | 240 | 80 | 87 | 1.000 | 0.857 | 0.669 | 0.714 | near-floor |
| linear | 0.00 | 240 | 80 | 87 | 1.000 | 0.857 | 0.669 | 0.714 | near-floor |
| rrf | 0.00 | 240 | 80 | 87 | 1.000 | 0.857 | 0.669 | 0.714 | near-floor |
| linear | 1.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.667 | 0.643 | near-floor |
| rrf | 0.50 | 100 | 33 | 186 | 1.000 | 0.929 | 0.594 | 0.643 | near-floor |
| linear | 0.50 | 240 | 80 | 87 | 1.000 | 0.857 | 0.663 | 0.714 | near-floor |
| rrf | 1.00 | 240 | 80 | 87 | 1.000 | 0.857 | 0.663 | 0.714 | near-floor |
| linear | 0.50 | 170 | 34 | 112 | 1.000 | 0.857 | 0.663 | 0.643 | near-floor |
| rrf | 1.00 | 120 | 24 | 156 | 1.000 | 0.857 | 0.663 | 0.643 | near-floor |
| rrf | 1.00 | 120 | 40 | 167 | 1.000 | 0.857 | 0.661 | 0.714 | near-floor |
| rrf | 0.50 | 240 | 80 | 87 | 1.000 | 0.857 | 0.659 | 0.714 | near-floor |
| bm25 | 0.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.643 | 0.786 | near-floor |
| linear | 0.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.643 | 0.786 | near-floor |
| rrf | 0.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.643 | 0.786 | near-floor |
| rrf | 1.00 | 100 | 20 | 177 | 1.000 | 0.857 | 0.643 | 0.714 | near-floor |
| rrf | 1.00 | 100 | 33 | 186 | 1.000 | 0.857 | 0.637 | 0.714 | near-floor |
| rrf | 0.50 | 170 | 34 | 112 | 1.000 | 0.857 | 0.619 | 0.643 | near-floor |
| linear | 0.50 | 170 | 56 | 126 | 1.000 | 0.857 | 0.600 | 0.643 | near-floor |
| linear | 1.00 | 170 | 56 | 126 | 1.000 | 0.857 | 0.600 | 0.643 | near-floor |
| rrf | 1.00 | 170 | 34 | 112 | 1.000 | 0.857 | 0.570 | 0.643 | near-floor |
| rrf | 0.50 | 170 | 56 | 126 | 1.000 | 0.857 | 0.552 | 0.643 | near-floor |
| rrf | 1.00 | 170 | 56 | 126 | 1.000 | 0.857 | 0.517 | 0.714 | near-floor |
| bm25 | 0.00 | 170 | 56 | 126 | 1.000 | 0.786 | 0.576 | 0.714 | near-floor |
| linear | 0.00 | 170 | 56 | 126 | 1.000 | 0.786 | 0.576 | 0.714 | near-floor |
| rrf | 0.00 | 170 | 56 | 126 | 1.000 | 0.786 | 0.576 | 0.714 | near-floor |

Best gated candidate: `fusion=linear density=1.00 budget=100 overlap=20` → objective 1.6571 (recall 0.929, mrr 0.729, 177 chunks).

Confidence gate (`min_query_support`) — the step that decides whether the corpus can answer at all, swept at the winning retrieval config:

| min_query_support | refusal_acc | false_answer | false_refusal | citation_fid | groundedness |
| --- | --- | --- | --- | --- | --- |
| 0.35 | 0.824 | 1.00 | 0.000 | 1.000 | 1.000 |
| 0.40 | 0.824 | 1.00 | 0.000 | 1.000 | 1.000 |
| 0.45 | 0.882 | 0.67 | 0.000 | 1.000 | 1.000 |
| 0.50 | 0.882 | 0.67 | 0.000 | 1.000 | 1.000 |
| 0.55 **<- chosen** | 1.000 | 0.00 | 0.000 | 1.000 | 1.000 |
| 0.60 | 0.941 | 0.00 | 0.071 | 1.000 | 1.000 |
| 0.65 | 0.882 | 0.00 | 0.143 | 1.000 | 1.000 |
| 0.70 | 0.824 | 0.00 | 0.214 | 1.000 | 1.000 |
| 0.75 | 0.706 | 0.00 | 0.357 | 1.000 | 1.000 |

### 6. REGISTER
retrieval: best candidate objective 1.6571 does not beat incumbent 1.6571; confidence gate: min_query_support=0.55 (refusal_accuracy=1.000, false_answer=0.00, false_refusal=0.00)

**Outcome:** `changed=False`

*Caveat: the golden set holds 14 answerable items, so one item is ~7% of recall. A config sitting on a floor is `near-floor`, not proven better.*



## 2026-09-14T08:30:58+00:00 — autonomy loop hardening (tool-use, limit detection, triggers)

**Stage:** execution · **Severity:** info

1. **DETECT** — scripted pathology suite through the real run_goal: 4/9 correct before hardening — four distinct faults all returned the same bare reason=step budget exceeded
2. **RESEARCH** — candidate levers from the pathology catalogue: per-call repeat signatures, per-step failure streaks, unknown-tool counting, conversation size budget, wall-clock ceiling
3. **DESIGN** — loop_guard.LoopGuard: observe-per-step, named reason codes with a stage and severity, stop on detection, compact over-budget context
4. **IMPLEMENT** — wired into agent_runtime.run_goal; _emit_step now records loop evidence so failures are explainable after the fact
5. **TEST** — loop_guard_test.py (30 tests) + loop_audit.py A/B suite: 9/9 correct after
6. **REGISTER** — thresholds -> loop_guard.json

**Evidence:** `{"baseline_correct": "4/9", "hardened_correct": "9/9", "pathologies": 9, "steps_burned_baseline": 41, "steps_burned_hardened": 20}`

**Loop summary at fault:** `{"compactions": 3, "peak_context_chars": 30823, "self_improvement_triggers": ["model_unavailable", "no_progress", "tool_error_storm", "unknown_tool_storm"]}`

## 2026-09-14T11:05:32+00:00 — autonomy loop hardening (tool-use, limit detection, triggers)

**Stage:** execution · **Severity:** info

1. **DETECT** — 11 scripted pathologies through the real run_goal, guard OFF: 6/11 correct. Two are new and legitimate — a model that revisits what it already read — and the first hardening's cumulative repeat rule false-positived on both. Separately, tool failures were emitted with two different prefixes, so a storm of file-tool failures was not counted at all
2. **RESEARCH** — two candidate levers: (1) one canonical failure constructor with a contract test over every registered tool, rather than renaming the odd prefix out; (2) result-aware repeat detection — judge a repeat on consecutive identical steps AND identical results, not on a signature count across the whole run
3. **DESIGN** — loop_guard.LoopGuard: observe-per-step, named reason codes with a stage and severity, stop on detection, compact over-budget context; repeat detection is result-aware (a repeated call whose result changed is progress), shipped alongside the original cumulative rule so the two are measured against the same pathologies
4. **IMPLEMENT** — wired into agent_runtime.run_goal; the failure protocol is canonical (loop_guard.fail) and every registered tool is proven to emit it; _emit_step records loop evidence so failures are explainable after the fact
5. **TEST** — loop_guard_test.py (52 tests) + loop_audit.py A/B suite: 11/11 correct after
6. **REGISTER** — thresholds -> loop_guard.json

**Evidence:** `{"baseline_correct": "6/11", "hardened_correct": "11/11", "legit_revisit_false_positives": {"cumulative": 2, "streak": 0}, "pathologies": 11, "repeat_mode_rule": "streak", "steps_burned_baseline": 41, "steps_burned_hardened": 20}`

**Loop summary:** `{"compactions": 3, "peak_context_chars": 30823, "self_improvement_triggers": ["model_unavailable", "no_progress", "tool_error_storm", "unknown_tool_storm"]}`

## 2026-09-17T06:48:35+00:00 — A1: plan + per-step verification + a gated completion

**Stage:** verification · **Severity:** info

1. **DETECT** — the loop's success signal was the model's own `FINAL:` claim. loop_audit measured the cost on scripted models: the reflex loop claimed success in 4/4 runs and collected evidence in 0/4, because a reply containing only a PLAN block has no `TOOL:` line and so was returned as the answer. Separately, no instrument could report a completion rate at all — 222 passing tests, every one of them component-scoped
2. **RESEARCH** — three candidate levers: (1) require a plan with a declared observable per step and check it against tool output; (2) accept `FINAL:` only when a goal condition holds — and decide *over evidence the loop collected*, never over the model's prose, since a checker the model can talk past is not a checker; (3) build the task-level suite first, so any claim is refutable
3. **DESIGN** — autonomy.py: a small decidable expectation grammar (ok/nonempty/contains/absent/regex/lines/chars), plans whose steps declare expectations *before* acting, promotion of only verified output to evidence, a completion gate, attributed deviation with a tolerated-note path, and per-task budgets the loop must refuse to exceed. Weak goal conditions (`ok`, `nonempty`) are refused at plan time rather than warned about: a goal gated on one would pass every run ever made. Detection is not duplicated — the existing LoopGuard sees every step, so all fifteen of its reason codes apply
4. **IMPLEMENT** — autonomy.py (new); loop_guard gains 6 reason codes with a stage and severity, 4 thresholds, and a public `note()` for tolerated findings that is deliberately not `_trip` and never opens a cycle; agent_runtime --goal now runs the verified loop and exits non-zero on an unmet goal, with --reflex keeping the old path reachable for measurement
5. **TEST** — autonomy_test.py (113 tests with loop_guard_test.py) + autonomy_suite.py 20 tasks (all 7 floors pass, 2 consecutive runs with identical verdicts) + loop_audit.py 10/10 correct on the goal arm, 0 false successes, no regression in the 11-pathology detection arm
6. **REGISTER** — thresholds -> loop_guard.json; pre-registered floors live in autonomy_suite.FLOORS (moving one to make a run pass would make the suite a rubber stamp)

**Evidence:** `{"budget_compliance": 1.0, "detection_regression": "11/11", "deterministic": true, "false_success_rate": 0.0, "false_success_reflex": 4, "false_success_verified": 0, "floors": {"budget_compliance": {"bound": 1.0, "dir": "min"}, "false_success_rate": {"bound": 0.0, "dir": "max"}, "mean_replans_per_completion": {"bound": 1.0, "dir": "max"}, "mean_tokens_per_task": {"bound": 120.0, "dir": "max"}, "refusal_accuracy": {"bound": 1.0, "dir": "min"}, "task_accuracy": {"bound": 1.0, "dir": "min"}, "verified_completion_rate": {"bound": 1.0, "dir": "min"}}, "goal_arm_correct": "10/10", "goal_arm_false_refusals": ["goal_only_in_unverified_step"], "goal_arm_false_successes": 0, "refusal_accuracy": 1.0, "repeat_runs": 2, "suite_tasks": 20, "task_accuracy": "20/20", "tests": 113, "verified_completion_rate": 1.0}`

**Loop summary:** `{"false_success_rate": 0.0, "mean_replans_per_completion": 0.1111111111111111, "mean_tokens_per_task": 25.6, "suite": "20/20"}`

## 2026-09-17T06:49:35+00:00 — autonomy loop fault: `unverified_completion` (opened)

**Stage:** verification · **Severity:** critical

1. **DETECT** — unverified_completion (step 2): the model claimed the goal was met but the goal condition did not hold over verified evidence
2. **ROUTED_TO** — goal condition strength, evidence promotion, system prompt
3. **HYPOTHESIS** — the model is asserting completion rather than collecting the evidence that proves it
4. **NEXT** — no RESEARCH/DESIGN/IMPLEMENT/TEST/REGISTER step has run for this fault yet; a closed cycle is appended separately once one has

**Evidence:** `{"check_detail": {"hit": false, "kind": "contains", "needle": "CAPABILITY_INVENTORY.md", "spec": "contains:CAPABILITY_INVENTORY.md"}, "claim": "PLAN:\n1. tool: list_dir .\n   expect: contains:CAPABILITY_INVENTORY.md\nGOAL-CHECK: contains:CAPABILITY_INVENTORY.md", "evidence_chars": 0, "goal_check": "contains:CAPABILITY_INVENTORY.md", "unmet_expectations": 0, "verified_steps": 0}`

**Loop summary:** `{"calls": 0, "compactions": 0, "consecutive_tool_errors": 0, "distinct_calls": 0, "max_steps": 8, "outcome": "failed", "peak_context_chars": 0, "repeat_mode": "streak", "repeat_streak": 0, "self_improvement_triggers": ["unverified_completion"], "steps_used": 2, "tolerated_notes": {}, "tool_steps": 0, "unknown_tools": []}`

## 2026-09-17T06:50:49+00:00 — autonomy loop fault: `empty_response` (opened)

**Stage:** transport · **Severity:** high

1. **DETECT** — empty_response (step 2): the model returned an empty response
2. **ROUTED_TO** — provider health, max_tokens
3. **HYPOTHESIS** — the provider truncated or refused the completion
4. **NEXT** — no RESEARCH/DESIGN/IMPLEMENT/TEST/REGISTER step has run for this fault yet; a closed cycle is appended separately once one has

**Evidence:** `{"provider": "groq"}`

**Loop summary:** `{"calls": 0, "compactions": 0, "consecutive_tool_errors": 0, "distinct_calls": 0, "max_steps": 8, "outcome": "failed", "peak_context_chars": 0, "repeat_mode": "streak", "repeat_streak": 0, "self_improvement_triggers": ["empty_response"], "steps_used": 2, "tolerated_notes": {}, "tool_steps": 0, "unknown_tools": []}`

## 2026-09-17T06:54:21+00:00 — hardening from the first live runs of the verified loop

**Stage:** verification · **Severity:** info

1. **DETECT** — three faults that no scripted pathology had produced, found by running the verified loop against a hosted model (openai/gpt-oss-120b via groq, after the local server and a retired model both failed over): (1) the model re-emitted its PLAN block instead of executing step 1, and a reply with no `TOOL:` line was reported as a false completion claim; (2) it proposed `GOAL-CHECK: regex:.+`, which is strong by *kind* and meaningless in effect — the run printed `[verified] goal check `regex:.+` held over 2 verified step(s)`; (3) it replied `FINAL:` with nothing after it and the run reported success with an empty answer, because the goal condition held
2. **RESEARCH** — levers for each, in dependency order: a targeted nudge for a reply that neither acts nor claims (do not fold it into the gate — that misattributes an ignored directive as dishonesty); a goal-strength test that catches a wildcard pattern (*not* a threshold over N probes, since this repo requires thresholds to be registered and measured — so a two-probe partial check is shipped and its gap named); and treating a verified goal with no answer text as its own fault rather than a success
3. **DESIGN** — `no_action` (nudge once, then attribute), `empty_answer` (checked only after the goal verifies, so a genuine gate refusal keeps its more informative reason), and wildcard detection via `spec_strength`: a regex goal that accepts two maximally dissimilar generic probes accepts essentially any evidence. Documented as partial — a pattern that accepts one probe but not the other still slips through
4. **IMPLEMENT** — loop_guard gains 3 reason codes (16 total) and the `no_action_limit` threshold; autonomy gains the nudge path, the goal-strength test, and the empty-answer check; loop_audit gains 4 pathologies for these shapes
5. **TEST** — autonomy_test.py (120 tests with loop_guard_test.py) + loop_audit.py 14/14 on the goal arm (0 false successes, 1 documented false refusal) + autonomy_suite.py 20/20 with all 7 floors and 2 consecutive identical runs + the same live goal now exits 0 with a verified goal
6. **REGISTER** — thresholds -> loop_guard.json

**Loop summary:** `{"false_success_rate": 0.0, "mean_replans_per_completion": 0.1111111111111111, "mean_tokens_per_task": 25.6, "suite": "20/20"}`

## 2026-09-17T07:28:05+00:00 — REPLACED (see the entry below)

A tombstone, kept so the timestamp is accounted for rather than silently reused.
A defective generator wrote the weakness-closure cycle under A1's title, because
the title was hardcoded and only overridden for one cycle kind. The body was
removed and re-registered under its own title (`07:28:45` below) rather than left
on the record with a title that contradicts its content. No phases are reproduced
here — a second copy would double-count the cycle.

## 2026-09-17T07:28:45+00:00 — closing the recorded weaknesses (vacuous goals, budgets, dropped replies)

**Stage:** verification · **Severity:** info

1. **DETECT** — six weaknesses recorded in CAPABILITY_INVENTORY.md after the A1 overhaul, five of which were real holes rather than notes: (1) a goal could be satisfied by a *vacuum* — `absent:X` holds over an empty string, so a run that promoted no evidence could pass a gate it never fed; (2) wildcard-goal detection was a two-probe check, so `lines:1` — `nonempty` with arithmetic — was accepted as a goal; (3) a single dropped reply ended the run as `empty_response` and threw away a plan already paid for; (4) nothing measured whether a task budget was *warranted*, only that it was not exceeded; (5) the audit's ground truth used a hardcoded marker token rather than each pathology's own goal, so `vacuous_goal` looked like a satisfied goal the gate had wrongly refused
2. **RESEARCH** — for each, the fix with the smallest claim: a probe set is only sound if weakness requires accepting *all* of it (monotone, so no calibrated count) and needle-carrying kinds are excluded from probing entirely; a completion is a claim about evidence, so it cannot be verified without any; a retry is the remedy for a transient and the note is what makes it visible rather than silent; budget adequacy is a claim about *reproducibility*, so it is exact integer slack rather than a tuned margin
3. **DESIGN** — kind-based strength (`contains`/`absent` strong by construction, everything else probed against 11 diverse non-empty strings); a non-empty-evidence requirement on the completion gate; `empty_response_retries` with a `transient_empty_response` note; a `budget_slack_rate` floor; and an audit whose ground truth is each pathology's own goal, with vacuity computed separately so a correct refusal of a vacuous goal is not counted as a gate error
4. **IMPLEMENT** — autonomy.py (strength rule, non-empty evidence, retries on both plan and step calls); loop_guard gains `transient_empty_response` and `empty_response_retries`; loop_audit gains 4 pathologies (18 total) and per-pathology goal extraction; autonomy_suite gains the slack floor and reports the tightest success
5. **TEST** — autonomy_test.py (132 tests with loop_guard_test.py) + loop_audit.py 18/18 on the goal arm (0 false successes, 1 documented false refusal, 1 vacuous refusal separated) + autonomy_suite.py 20/20 with all 8 floors + detection arm still 11/11
6. **REGISTER** — thresholds -> loop_guard.json

**Loop summary:** `{"false_success_rate": 0.0, "mean_replans_per_completion": 0.1111111111111111, "mean_tokens_per_task": 25.6, "suite": "20/20"}`

## 2026-09-17T20:56:32+00:00 — RAG tuning cycle

### 1. DETECT
Incumbent: `fusion=linear density=1.0 budget=120 overlap=24 gate=0.5475`

Measured: span_coverage=1.0000, recall@k=0.9286, mrr=0.7679, objective=1.6964, chunks=170

### 2. RESEARCH — 1 stage-attributed finding(s)
- **retrieval** [high] 1/14 questions have no gold-bearing chunk in the top 6
  - evidence: `rag-g11`
  - lever: `fusion, dense_weight, chunk_budget_words, tokenisation`
  - hypothesis: either the query shares no vocabulary with the source sentence (lexical gap) or the fused score buried it

### 3. DESIGN
Bounded search over fusion × chunk budget × chunk overlap × dense weight (56 candidates). Hard gate: span_coverage=1.00, recall@k>=0.85, mrr>=0.70.
Objective: recall@k + mrr, tie-break top-1 source accuracy, then fewer chunks.

### 4. IMPLEMENT / 5. TEST
| fusion | dense | budget | overlap | chunks | span | recall@k | mrr | top1 | gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| linear | 1.00 | 120 | 24 | 170 | 1.000 | 0.929 | 0.768 | 0.714 | pass |
| linear | 1.00 | 120 | 40 | 180 | 1.000 | 0.929 | 0.768 | 0.714 | pass |
| linear | 0.50 | 100 | 20 | 193 | 1.000 | 0.929 | 0.735 | 0.643 | pass |
| linear | 1.00 | 100 | 20 | 193 | 1.000 | 0.929 | 0.729 | 0.643 | pass |
| linear | 1.00 | 240 | 48 | 87 | 1.000 | 0.929 | 0.726 | 0.714 | pass |
| linear | 0.50 | 100 | 33 | 202 | 1.000 | 0.929 | 0.723 | 0.643 | pass |
| bm25 | 0.00 | 100 | 20 | 193 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| linear | 0.00 | 100 | 20 | 193 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| rrf | 0.00 | 100 | 20 | 193 | 1.000 | 0.929 | 0.717 | 0.643 | pass |
| rrf | 1.00 | 120 | 24 | 170 | 1.000 | 0.929 | 0.711 | 0.714 | pass |
| rrf | 1.00 | 120 | 40 | 180 | 1.000 | 0.929 | 0.708 | 0.714 | pass |
| linear | 1.00 | 100 | 33 | 202 | 1.000 | 0.929 | 0.708 | 0.643 | pass |
| linear | 0.50 | 120 | 24 | 170 | 1.000 | 0.929 | 0.699 | 0.643 | near-floor |
| linear | 0.50 | 120 | 40 | 180 | 1.000 | 0.929 | 0.699 | 0.643 | near-floor |
| linear | 0.50 | 240 | 48 | 87 | 1.000 | 0.929 | 0.687 | 0.714 | near-floor |
| rrf | 0.50 | 240 | 48 | 87 | 1.000 | 0.929 | 0.687 | 0.714 | near-floor |
| rrf | 0.50 | 120 | 24 | 170 | 1.000 | 0.929 | 0.684 | 0.714 | near-floor |
| rrf | 0.50 | 120 | 40 | 180 | 1.000 | 0.929 | 0.681 | 0.643 | near-floor |
| bm25 | 0.00 | 100 | 33 | 202 | 1.000 | 0.929 | 0.681 | 0.643 | near-floor |
| linear | 0.00 | 100 | 33 | 202 | 1.000 | 0.929 | 0.681 | 0.643 | near-floor |
| rrf | 0.00 | 100 | 33 | 202 | 1.000 | 0.929 | 0.681 | 0.643 | near-floor |
| bm25 | 0.00 | 120 | 24 | 170 | 1.000 | 0.929 | 0.663 | 0.643 | near-floor |
| linear | 0.00 | 120 | 24 | 170 | 1.000 | 0.929 | 0.663 | 0.643 | near-floor |
| rrf | 0.00 | 120 | 24 | 170 | 1.000 | 0.929 | 0.663 | 0.643 | near-floor |
| bm25 | 0.00 | 120 | 40 | 180 | 1.000 | 0.929 | 0.663 | 0.643 | near-floor |
| linear | 0.00 | 120 | 40 | 180 | 1.000 | 0.929 | 0.663 | 0.643 | near-floor |
| rrf | 0.00 | 120 | 40 | 180 | 1.000 | 0.929 | 0.663 | 0.643 | near-floor |
| rrf | 0.50 | 100 | 20 | 193 | 1.000 | 0.929 | 0.639 | 0.643 | near-floor |
| rrf | 1.00 | 240 | 48 | 87 | 1.000 | 0.857 | 0.679 | 0.714 | near-floor |
| linear | 1.00 | 170 | 34 | 121 | 1.000 | 0.857 | 0.663 | 0.714 | near-floor |
| rrf | 0.50 | 100 | 33 | 202 | 1.000 | 0.929 | 0.592 | 0.643 | near-floor |
| linear | 0.50 | 170 | 34 | 121 | 1.000 | 0.857 | 0.661 | 0.714 | near-floor |
| linear | 0.50 | 240 | 80 | 96 | 1.000 | 0.857 | 0.657 | 0.714 | near-floor |
| rrf | 0.50 | 240 | 80 | 96 | 1.000 | 0.857 | 0.657 | 0.714 | near-floor |
| rrf | 1.00 | 240 | 80 | 96 | 1.000 | 0.857 | 0.657 | 0.786 | near-floor |
| rrf | 1.00 | 100 | 20 | 193 | 1.000 | 0.857 | 0.649 | 0.643 | near-floor |
| bm25 | 0.00 | 240 | 48 | 87 | 1.000 | 0.857 | 0.637 | 0.714 | near-floor |
| linear | 0.00 | 240 | 48 | 87 | 1.000 | 0.857 | 0.637 | 0.714 | near-floor |
| rrf | 0.00 | 240 | 48 | 87 | 1.000 | 0.857 | 0.637 | 0.714 | near-floor |
| rrf | 1.00 | 100 | 33 | 202 | 1.000 | 0.857 | 0.631 | 0.643 | near-floor |
| rrf | 0.50 | 170 | 34 | 121 | 1.000 | 0.857 | 0.627 | 0.643 | near-floor |
| bm25 | 0.00 | 240 | 80 | 96 | 1.000 | 0.857 | 0.616 | 0.714 | near-floor |
| linear | 0.00 | 240 | 80 | 96 | 1.000 | 0.857 | 0.616 | 0.714 | near-floor |
| rrf | 0.00 | 240 | 80 | 96 | 1.000 | 0.857 | 0.616 | 0.714 | near-floor |
| rrf | 1.00 | 170 | 34 | 121 | 1.000 | 0.857 | 0.616 | 0.643 | near-floor |
| linear | 1.00 | 240 | 80 | 96 | 1.000 | 0.786 | 0.681 | 0.714 | near-floor |
| linear | 1.00 | 170 | 56 | 136 | 1.000 | 0.857 | 0.600 | 0.714 | near-floor |
| linear | 0.50 | 170 | 56 | 136 | 1.000 | 0.857 | 0.598 | 0.714 | near-floor |
| bm25 | 0.00 | 170 | 34 | 121 | 1.000 | 0.786 | 0.637 | 0.714 | near-floor |
| linear | 0.00 | 170 | 34 | 121 | 1.000 | 0.786 | 0.637 | 0.714 | near-floor |
| rrf | 0.00 | 170 | 34 | 121 | 1.000 | 0.786 | 0.637 | 0.714 | near-floor |
| rrf | 0.50 | 170 | 56 | 136 | 1.000 | 0.857 | 0.561 | 0.643 | near-floor |
| rrf | 1.00 | 170 | 56 | 136 | 1.000 | 0.857 | 0.552 | 0.643 | near-floor |
| bm25 | 0.00 | 170 | 56 | 136 | 1.000 | 0.786 | 0.579 | 0.643 | near-floor |
| linear | 0.00 | 170 | 56 | 136 | 1.000 | 0.786 | 0.579 | 0.643 | near-floor |
| rrf | 0.00 | 170 | 56 | 136 | 1.000 | 0.786 | 0.579 | 0.643 | near-floor |

Best gated candidate: `fusion=linear density=1.00 budget=120 overlap=24` → objective 1.6964 (recall 0.929, mrr 0.768, 170 chunks).

Confidence gate (`min_query_support`) — the step that decides whether the corpus can answer at all, swept at the winning retrieval config:

| min_query_support | refusal_acc | false_answer | false_refusal | citation_fid | groundedness |
| --- | --- | --- | --- | --- | --- |
| 0.35 | 0.824 | 1.00 | 0.000 | 1.000 | 1.000 |
| 0.40 | 0.882 | 0.67 | 0.000 | 1.000 | 1.000 |
| 0.45 | 0.882 | 0.67 | 0.000 | 1.000 | 1.000 |
| 0.50 | 0.882 | 0.67 | 0.000 | 1.000 | 1.000 |
| 0.55 **<- chosen** | 1.000 | 0.00 | 0.000 | 1.000 | 1.000 |
| 0.55 | 1.000 | 0.00 | 0.000 | 1.000 | 1.000 |
| 0.60 | 0.941 | 0.00 | 0.071 | 1.000 | 1.000 |
| 0.65 | 0.941 | 0.00 | 0.071 | 1.000 | 1.000 |
| 0.70 | 0.824 | 0.00 | 0.214 | 1.000 | 1.000 |
| 0.75 | 0.765 | 0.00 | 0.286 | 1.000 | 1.000 |

### 6. REGISTER
retrieval: best candidate objective 1.6964 does not beat incumbent 1.6964; confidence gate: min_query_support=0.5475 (refusal_accuracy=1.000, false_answer=0.00, false_refusal=0.00) — registered to rag_config.json

**Outcome:** `changed=True`

*Caveat: the golden set holds 14 answerable items, so one item is ~7% of recall. A config sitting on a floor is `near-floor`, not proven better.*

## 2026-09-18T21:19:47+00:00 — A3: an adversarial verifier — independent confirmation before promotion

**Stage:** verification · **Severity:** info

1. **DETECT** — A1 checks the expectation the *model declared* for a step, so the model writes the test it is graded on. loop_audit's new arm put a number on the cost: of 4 unsupported step claims, all 4 were promoted to evidence — an expectation of `nonempty` holds on the tool's own failure line, so a failed call was counted as a verified observation, and `regex:.+` held on anything at all. The one claim that could not be re-observed was promoted too, and the run reported `[verified]` for it
2. **RESEARCH** — the strongest confirmation available locally is deterministic: re-derive the expectation from the raw output rather than trust the caller's earlier verdict; refuse an expectation that the *empty string or the tool's own failure line* would satisfy, since it cannot distinguish a usable observation from nothing; and re-run a deterministic tool to require the claim to reproduce. A second model is the 'where not' clause, not the default — it is unmeasured live here, so it ships off
3. **DESIGN** — verifier.py: four named checks (`expectation_held`, `not_failure`, `not_vacuous`, `reproduced`) whose names are carried into `how_verified`, because AGENT-INTEGRITY requires a promotion to name the check that ran. Levels: a claim that reproduces is `invariant`, one on a tool that cannot be re-run is `observed` and is labelled as such, anything else is refused and the step does not advance. `assess()` encodes the exit criterion: a verifier that detects nothing is reported as `no_verifier`, never as a pass
4. **IMPLEMENT** — autonomy.py promotes through `verifier.confirm` (A1's behaviour stays reachable as `verify_mode: off`, for the A/B); loop_guard gains the `claim_rejected` reason code and two thresholds; loop_audit gains the arm and `audit_ok` now requires the verifier's verdict to be `useful` — a red audit cannot be bought by confirming everything
5. **TEST** — verifier_test.py (172 tests across verifier_test, autonomy_test and loop_guard_test) + loop_audit.py lying control 4/4 with honest control 0/2 (verdict `useful`) + autonomy_suite.py 20/20 with all 8 floors + detection arm 11/11 and goal arm 18/18 unchanged
6. **REGISTER** — thresholds -> loop_guard.json

**Evidence:** `{"budget_compliance": 1.0, "detection_regression": "11/11", "deterministic": true, "false_success_rate": 0.0, "false_success_reflex": 5, "false_success_verified": 0, "floors": {"budget_compliance": {"bound": 1.0, "dir": "min"}, "budget_slack_rate": {"bound": 1.0, "dir": "min"}, "false_success_rate": {"bound": 0.0, "dir": "max"}, "mean_replans_per_completion": {"bound": 1.0, "dir": "max"}, "mean_tokens_per_task": {"bound": 120.0, "dir": "max"}, "refusal_accuracy": {"bound": 1.0, "dir": "min"}, "task_accuracy": {"bound": 1.0, "dir": "min"}, "verified_completion_rate": {"bound": 1.0, "dir": "min"}}, "goal_arm_correct": "18/18", "goal_arm_false_refusals": ["goal_only_in_unverified_step"], "goal_arm_false_successes": 0, "hollow_promotions_after": 0, "hollow_promotions_before": 4, "refusal_accuracy": 1.0, "repeat_runs": 2, "suite_tasks": 20, "task_accuracy": "20/20", "tests": 172, "verified_completion_rate": 1.0, "verifier_detection": "4/4", "verifier_detection_rate": 1.0, "verifier_false_accusation_rate": 0.0, "verifier_false_accusations": "0/2", "verifier_verdict": "useful", "verify_mode": "confirm"}`

**Loop summary:** `{"false_success_rate": 0.0, "mean_replans_per_completion": 0.1111111111111111, "mean_tokens_per_task": 25.6, "suite": "20/20"}`

## 2026-09-19T09:19:45+00:00 — A3 live coverage: the refusal path against a real model — and the four defects only a live run could find

**Stage:** verification · **Severity:** info

1. **DETECT** — A3's refusal path had only ever been driven by scripted replies, so nothing recorded what a real model does with the message. Driving it live found four defects, none visible from a stub: a correct PLAN: block discarded for missing the `REPLAN:` keyword (three times in one run); a rejection note whose first suggested repair ("re-run the step") cannot change an expectation the plan holds; a supplied `plan=` parsed and enforced but never shown to the model, so it was asked to execute a plan it had never seen; and an empty reply that had consumed its whole cap retried at the same cap
2. **RESEARCH** — Each fix is the measured one, not the plausible one. For the empty reply: one tool-step prompt returned 256/256, 512/512 and 1024/1024 tokens of reasoning with no content and then the correct directive in 12 tokens at 2048, so a retry has to reach 2048 to be worth making (`empty_reply_token_ceiling`, registered). For the plan block: the system prompt teaches the `PLAN:` block and mentions `REPLAN:` once, so the block is the form a model produces; a plan block can only mean "replace the plan", and a re-sent identical one is still the echo `no_action` describes
3. **DESIGN** — `escalated_cap` raises a retry only when the reply actually billed its cap; `is_plan_reply` + `_same_plan` accept a plan block as a replacement when it differs from the current plan; a supplied plan is echoed into the context exactly as a model-written one is; `REFUSAL_FIXES` gives each refusing check its own remedy and the note asks for a corrected plan. `live_refusal_probe.py` records the transcript verbatim and can reprint the report without spending requests again
4. **IMPLEMENT** — autonomy.py (escalated_cap, is_plan_reply, _same_plan, REFUSAL_FIXES, the supplied-plan echo, `stop_when` forwarded to the injected seam); loop_guard.py (the `empty_reply_token_ceiling` threshold and the `replan_unlabelled` reason code); live_refusal_probe.py + live_refusal_probe_test.py
5. **TEST** — 192 tests across the modules this cycle touches; goal arm 18/18; verifier controls 4/4 detected with 0/2 honest claims refused (`useful`). Live: 2 of 3 arms reached a refusal, the checks reached were `not_failure`, `not_vacuous`, and the model repaired on `failed_call`, `non_discriminating` — its corrected expectation was accepted, the step re-executed, and the goal then verified over 3 `invariant` steps. The model-planned arm reached no refusal: it wrote the pathological `expect: nonempty` itself but the plan gate refused its weak GOAL-CHECK first
6. **REGISTER** — self — this record, with the transcript it rests on

**Evidence:** `{"arm_outcomes": {"failed_call": "replan_storm", "natural": "plan_invalid", "non_discriminating": "empty_answer"}, "arms_repaired_by_the_model": ["failed_call", "non_discriminating"], "arms_that_did_not": ["natural"], "arms_that_reached_a_refusal": 2, "evidence_levels": {"failed_call": {"invariant": 1}, "non_discriminating": {"invariant": 3}}, "goal_arm_correct": "18/18", "live_arms": 3, "refusals_by_check": ["not_failure", "not_vacuous"], "served_by": ["groq#2/qwen/qwen3.8-27b", "groq/qwen/qwen3.8-27b"], "summary": "live-refusal-summary.json", "tests": 192, "threshold_empty_reply_token_ceiling": 2048, "transcript": "live-refusal-transcript.jsonl", "verifier_detection": "4/4", "verifier_false_accusations": "0/2", "verifier_verdict": "useful"}`

**Loop summary:** `{"arms": 3, "model_repaired": 2, "refusals_reached": 4}`

## 2026-09-19T09:28:32+00:00 — the cascade classified a dead server as a paid wall, because it read numbers as substrings

**Stage:** execution · **Severity:** medium

1. **DETECT** — A regression run went red once in ~17 and passed on every re-run. Caught by repeating it: `test_probe_call_reaches_a_real_provider_with_one_token` asserted a dead local hop classifies as `server` and got `payment` — because `classify` substring-matched the numeric codes and the test's randomly chosen closed port contained `402`
2. **RESEARCH** — Any incidental number in an error message has the same shape (a port, a timeout in ms, a byte count, a temp path), so the fix cannot be one spelling. Word boundaries alone are insufficient — a port can be exactly `402`. URLs are therefore stripped before scanning, which removes the class rather than an instance, while the transports' own formats (`HTTP 402`, `"status":402`, `code=403`, `error code: 1010`) survive
3. **DESIGN** — `codes_in(text)` strips URL-shaped text, then matches the five codes plus Cloudflare's `1010` as whole tokens; `classify` reads codes from it and keeps its text patterns. The residual (a bare number outside a URL equal to a code) is stated in the docstring rather than hidden, with the rejected alternative named
4. **IMPLEMENT** — brain_cascade.py — `_CODES_RE`, `_URL_RE`, `codes_in`; `classify` reads it
5. **TEST** — Property, not instance: all 65,535 ports classify `server` (0 bad after the fix); the real code formats still classify; the exact failing string is a test case now. `brain_cascade_test.py` Ran 53 tests in 0.250s OK, `agent_runtime_test.py` Ran 35 tests in 5.564s OK — the module that flaked is green on 12 consecutive runs
6. **REGISTER** — self — this record

**Evidence:** `{"agent_runtime_test": "OK", "brain_cascade_test": "OK", "codes": ["1010", "401", "402", "403", "404", "429"], "flake_rate_observed": "1 failure in 17 runs", "ports_misclassified_after": 0, "ports_misclassified_before_examples": [401, 402, 403, 429, 1010, 14025, 40233]}`

**Loop summary:** `{"agent_runtime_test": "OK", "brain_cascade_test": "OK", "ports_misclassified_after": 0}`

## 2026-09-19T09:34:56+00:00 — RAG tuning cycle

### 1. DETECT
Incumbent: `fusion=linear density=1.0 budget=120 overlap=24 gate=0.5475`

Measured: span_coverage=1.0000, recall@k=0.9286, mrr=0.7321, objective=1.6607, chunks=206

### 2. RESEARCH — 1 stage-attributed finding(s)
- **retrieval** [high] 1/14 questions have no gold-bearing chunk in the top 6
  - evidence: `rag-g11`
  - lever: `fusion, dense_weight, chunk_budget_words, tokenisation`
  - hypothesis: either the query shares no vocabulary with the source sentence (lexical gap) or the fused score buried it

### 3. DESIGN
Bounded search over fusion × chunk budget × chunk overlap × dense weight (56 candidates). Hard gate: span_coverage=1.00, recall@k>=0.85, mrr>=0.70.
Objective: recall@k + mrr, tie-break top-1 source accuracy, then fewer chunks.

### 4. IMPLEMENT / 5. TEST
| fusion | dense | budget | overlap | chunks | span | recall@k | mrr | top1 | gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| linear | 1.00 | 120 | 24 | 206 | 1.000 | 0.929 | 0.732 | 0.714 | pass |
| linear | 1.00 | 120 | 40 | 216 | 1.000 | 0.929 | 0.729 | 0.714 | pass |
| bm25 | 0.00 | 100 | 20 | 236 | 1.000 | 0.929 | 0.702 | 0.643 | pass |
| linear | 0.00 | 100 | 20 | 236 | 1.000 | 0.929 | 0.702 | 0.643 | pass |
| rrf | 0.00 | 100 | 20 | 236 | 1.000 | 0.929 | 0.702 | 0.643 | pass |
| linear | 0.50 | 120 | 24 | 206 | 1.000 | 0.929 | 0.699 | 0.643 | near-floor |
| linear | 0.50 | 120 | 40 | 216 | 1.000 | 0.929 | 0.699 | 0.643 | near-floor |
| linear | 0.50 | 100 | 20 | 236 | 1.000 | 0.929 | 0.696 | 0.643 | near-floor |
| linear | 0.50 | 100 | 33 | 245 | 1.000 | 0.929 | 0.684 | 0.643 | near-floor |
| bm25 | 0.00 | 100 | 33 | 245 | 1.000 | 0.929 | 0.655 | 0.714 | near-floor |
| linear | 0.00 | 100 | 33 | 245 | 1.000 | 0.929 | 0.655 | 0.714 | near-floor |
| rrf | 0.00 | 100 | 33 | 245 | 1.000 | 0.929 | 0.655 | 0.714 | near-floor |
| rrf | 0.50 | 120 | 24 | 206 | 1.000 | 0.929 | 0.645 | 0.643 | near-floor |
| rrf | 0.50 | 120 | 40 | 216 | 1.000 | 0.929 | 0.645 | 0.643 | near-floor |
| linear | 1.00 | 100 | 20 | 236 | 1.000 | 0.857 | 0.717 | 0.643 | pass |
| linear | 1.00 | 100 | 33 | 245 | 1.000 | 0.857 | 0.702 | 0.714 | pass |
| rrf | 1.00 | 240 | 48 | 105 | 1.000 | 0.857 | 0.699 | 0.643 | near-floor |
| bm25 | 0.00 | 120 | 24 | 206 | 1.000 | 0.857 | 0.681 | 0.643 | near-floor |
| linear | 0.00 | 120 | 24 | 206 | 1.000 | 0.857 | 0.681 | 0.643 | near-floor |
| rrf | 0.00 | 120 | 24 | 206 | 1.000 | 0.857 | 0.681 | 0.643 | near-floor |
| bm25 | 0.00 | 120 | 40 | 216 | 1.000 | 0.857 | 0.681 | 0.643 | near-floor |
| linear | 0.00 | 120 | 40 | 216 | 1.000 | 0.857 | 0.681 | 0.643 | near-floor |
| rrf | 0.00 | 120 | 40 | 216 | 1.000 | 0.857 | 0.681 | 0.643 | near-floor |
| rrf | 0.50 | 100 | 20 | 236 | 1.000 | 0.929 | 0.600 | 0.643 | near-floor |
| linear | 1.00 | 170 | 34 | 147 | 1.000 | 0.857 | 0.663 | 0.643 | near-floor |
| linear | 0.50 | 170 | 34 | 147 | 1.000 | 0.857 | 0.659 | 0.714 | near-floor |
| rrf | 0.50 | 100 | 33 | 245 | 1.000 | 0.929 | 0.588 | 0.643 | near-floor |
| rrf | 1.00 | 170 | 34 | 147 | 1.000 | 0.857 | 0.627 | 0.571 | near-floor |
| linear | 1.00 | 240 | 48 | 105 | 1.000 | 0.786 | 0.696 | 0.714 | near-floor |
| rrf | 0.50 | 170 | 34 | 147 | 1.000 | 0.857 | 0.619 | 0.571 | near-floor |
| rrf | 1.00 | 120 | 40 | 216 | 1.000 | 0.786 | 0.684 | 0.714 | near-floor |
| rrf | 0.50 | 240 | 80 | 116 | 1.000 | 0.786 | 0.681 | 0.714 | near-floor |
| bm25 | 0.00 | 170 | 34 | 147 | 1.000 | 0.857 | 0.604 | 0.714 | near-floor |
| linear | 0.00 | 170 | 34 | 147 | 1.000 | 0.857 | 0.604 | 0.714 | near-floor |
| rrf | 0.00 | 170 | 34 | 147 | 1.000 | 0.857 | 0.604 | 0.714 | near-floor |
| rrf | 1.00 | 240 | 80 | 116 | 1.000 | 0.786 | 0.669 | 0.714 | near-floor |
| linear | 1.00 | 170 | 56 | 164 | 1.000 | 0.857 | 0.598 | 0.643 | near-floor |
| linear | 0.50 | 170 | 56 | 164 | 1.000 | 0.857 | 0.594 | 0.714 | near-floor |
| linear | 0.50 | 240 | 48 | 105 | 1.000 | 0.786 | 0.661 | 0.714 | near-floor |
| rrf | 0.50 | 240 | 48 | 105 | 1.000 | 0.786 | 0.661 | 0.786 | near-floor |
| bm25 | 0.00 | 170 | 56 | 164 | 1.000 | 0.857 | 0.588 | 0.643 | near-floor |
| linear | 0.00 | 170 | 56 | 164 | 1.000 | 0.857 | 0.588 | 0.643 | near-floor |
| rrf | 0.00 | 170 | 56 | 164 | 1.000 | 0.857 | 0.588 | 0.643 | near-floor |
| rrf | 1.00 | 120 | 24 | 206 | 1.000 | 0.786 | 0.649 | 0.714 | near-floor |
| bm25 | 0.00 | 240 | 48 | 105 | 1.000 | 0.786 | 0.645 | 0.714 | near-floor |
| linear | 0.00 | 240 | 48 | 105 | 1.000 | 0.786 | 0.645 | 0.714 | near-floor |
| rrf | 0.00 | 240 | 48 | 105 | 1.000 | 0.786 | 0.645 | 0.714 | near-floor |
| linear | 0.50 | 240 | 80 | 116 | 1.000 | 0.786 | 0.645 | 0.643 | near-floor |
| linear | 1.00 | 240 | 80 | 116 | 1.000 | 0.786 | 0.645 | 0.714 | near-floor |
| bm25 | 0.00 | 240 | 80 | 116 | 1.000 | 0.786 | 0.643 | 0.786 | near-floor |
| linear | 0.00 | 240 | 80 | 116 | 1.000 | 0.786 | 0.643 | 0.786 | near-floor |
| rrf | 0.00 | 240 | 80 | 116 | 1.000 | 0.786 | 0.643 | 0.786 | near-floor |
| rrf | 0.50 | 170 | 56 | 164 | 1.000 | 0.857 | 0.550 | 0.643 | near-floor |
| rrf | 1.00 | 170 | 56 | 164 | 1.000 | 0.857 | 0.549 | 0.571 | near-floor |
| rrf | 1.00 | 100 | 20 | 236 | 1.000 | 0.786 | 0.610 | 0.714 | near-floor |
| rrf | 1.00 | 100 | 33 | 245 | 1.000 | 0.786 | 0.601 | 0.714 | near-floor |

Best gated candidate: `fusion=linear density=1.00 budget=120 overlap=24` → objective 1.6607 (recall 0.929, mrr 0.732, 206 chunks).

Confidence gate (`min_query_support`) — the step that decides whether the corpus can answer at all, swept at the winning retrieval config:

| min_query_support | refusal_acc | false_answer | false_refusal | citation_fid | groundedness |
| --- | --- | --- | --- | --- | --- |
| 0.35 | 0.824 | 1.00 | 0.000 | 1.000 | 1.000 |
| 0.40 | 0.824 | 1.00 | 0.000 | 1.000 | 1.000 |
| 0.45 | 0.882 | 0.67 | 0.000 | 1.000 | 1.000 |
| 0.50 | 0.882 | 0.67 | 0.000 | 1.000 | 1.000 |
| 0.55 **<- chosen** | 1.000 | 0.00 | 0.000 | 1.000 | 1.000 |
| 0.60 | 1.000 | 0.00 | 0.000 | 1.000 | 1.000 |
| 0.61 | 1.000 | 0.00 | 0.000 | 1.000 | 1.000 |
| 0.65 | 1.000 | 0.00 | 0.000 | 1.000 | 1.000 |
| 0.70 | 0.824 | 0.00 | 0.214 | 1.000 | 1.000 |
| 0.75 | 0.824 | 0.00 | 0.214 | 1.000 | 1.000 |

### 6. REGISTER
retrieval: best candidate objective 1.6607 does not beat incumbent 1.6607; context: no threshold separated the support sets at top_k=6; at top_k=7 the gap is +0.1402 (min answerable 0.6846 vs max unanswerable 0.5444) — the smallest context that works; confidence gate: min_query_support=0.5500 (refusal_accuracy=1.000, false_answer=0.00, false_refusal=0.00) — registered to rag_config.json

**Outcome:** `changed=True`

*Caveat: the golden set holds 14 answerable items, so one item is ~7% of recall. A config sitting on a floor is `near-floor`, not proven better.*


## 2026-09-19T10:06:14+00:00 — A2: episodic memory with provenance — recall that cannot launder a claim

**Stage:** verification · **Severity:** info

1. **DETECT** — A2's absence was a measurement, not a matter of taste. The ledger was write-only — 32 records in freebrain-residence/ledger.jsonl that nothing read back — so every run began from zero knowledge even when an earlier run had already established the fact it needed. AGENT-INTEGRITY.md had also recorded the shape of the failure to avoid: cross-instance memory was untyped in practice (every `memorize()` call site wrote type `observation`), so the one bucket that should hold invariants — `memory.facts` — was never written by anyone at all
2. **RESEARCH** — two candidate levers: (1) recall by lexical overlap, stdlib-only, in the same spirit as rag_core.py — the binding constraint measured for retrieval was verification, not ranking, and AUTONOMY-UPGRADE §6 says not to add an embedding dependency without a measurement that says lexical is what is failing; (2) provenance first: make the level a property of the record's birth and make the renderer *structurally* unable to put a non-invariant in a fact position, rather than asking a prompt to be careful
3. **DESIGN** — memory.py: the three levels taken verbatim from AGENT-INTEGRITY.md (invariant / observed / volatile), an append-only JSONL store, recall scored as the fraction of the goal's content words a record's own text covers, and a render whose split is structural — the facts section is built from `facts()` alone and `unverified()` is its exact complement, so no argument to `render` puts a claim above the header. A3 decides the level at birth: confirmed and reproduced -> `invariant`, real but not re-observed -> `observed`, the model's own expectation with the verifier off -> `volatile`. That last row is the boundary — without it, switching the verifier off would be a way to launder a claim into a remembered fact. `promote()` refuses a volatile record outright and refuses an unnamed check; `audit()` re-checks a rendered block against the records it claims to render and reports values too short to search for rather than counting them clean
4. **IMPLEMENT** — autonomy.run_goal_verified gains `memory_store` (None by default, so every existing caller behaves identically): recall is read once at plan time into the *system* message, which compaction never touches; each independently confirmed step is written back at its verified level; and memory is kept out of the completion gate — a goal that would hold over recalled facts alone is refused with `recall_gap` in the diagnosis rather than promoted to this run's evidence. loop_guard registers 5 recall thresholds; loop_audit gains the arm (one task, four cases) and `audit_ok` now requires the memory verdict to be `useful`, so recall that changes no outcome fails the audit
5. **TEST** — memory_test.py (233 tests across memory_test, verifier_test, autonomy_test and loop_guard_test) + loop_audit.py: the same task refused with recall off and succeeded with it on (1 fact offered; the planner named the file only when a fact was in front of it), the never-confirmed value seeded with the verifier off stored as `volatile` and stayed unusable, false recall 0 with 1 value(s) actually checked for leakage (verdict `useful`) + autonomy_suite.py 20/20 with all 8 floors + detection arm 11/11 and goal arm 18/18 unchanged
6. **REGISTER** — thresholds -> loop_guard.json

**Evidence:** `{"budget_compliance": 1.0, "detection_regression": "11/11", "deterministic": true, "false_success_rate": 0.0, "false_success_reflex": 5, "false_success_verified": 0, "floors": {"budget_compliance": {"bound": 1.0, "dir": "min"}, "budget_slack_rate": {"bound": 1.0, "dir": "min"}, "false_success_rate": {"bound": 0.0, "dir": "max"}, "mean_replans_per_completion": {"bound": 1.0, "dir": "max"}, "mean_tokens_per_task": {"bound": 120.0, "dir": "max"}, "refusal_accuracy": {"bound": 1.0, "dir": "min"}, "task_accuracy": {"bound": 1.0, "dir": "min"}, "verified_completion_rate": {"bound": 1.0, "dir": "min"}}, "goal_arm_correct": "18/18", "goal_arm_false_refusals": ["goal_only_in_unverified_step"], "goal_arm_false_successes": 0, "hollow_promotions_after": 0, "hollow_promotions_before": 4, "memory_adversarial_value_usable": false, "memory_cold_case_reached_it": false, "memory_false_recall": 0, "memory_recall_changed_outcome": true, "memory_store_levels_seeded_with_verifier_off": ["volatile"], "memory_store_levels_seeded_with_verifier_on": ["invariant"], "memory_tests": 42, "memory_values_checked_for_leakage": 1, "memory_values_too_short_to_check": 0, "memory_verdict": "useful", "recall_enabled": 1, "refusal_accuracy": 1.0, "repeat_runs": 2, "suite_tasks": 20, "task_accuracy": "20/20", "tests": 191, "verified_completion_rate": 1.0, "verifier_detection": "4/4", "verifier_detection_rate": 1.0, "verifier_false_accusation_rate": 0.0, "verifier_false_accusations": "0/2", "verifier_verdict": "useful", "verify_mode": "confirm"}`

**Loop summary:** `{"false_success_rate": 0.0, "mean_replans_per_completion": 0.1111111111111111, "mean_tokens_per_task": 25.6, "suite": "20/20"}`

## 2026-09-19T10:07:17+00:00 — the reflex loop's evidence writes were the one ungated path

**Stage:** evidence · **Severity:** high

1. **DETECT** — A2's verification ran the full suite with a before/after checksum over the published records, and the Q1 evidence log grew by 5 rows. They were stub-provider rows with a plausible token rate, so nothing about them looked wrong in the file — the same signature as the leak `test_support.py` was built to stop, which means it had a second source. Bisected by class: `autonomy_test.AuditInstrumentTest`, whose reflex arm runs the unverified loop 5 times. That loop called `_emit_step` unconditionally, so it was the one path in the runtime whose evidence writes did not follow the guard's emit flag — every other writer (autonomy._call) is gated, and `_emit_loop_diagnosis` only prints. Measured cumulative damage in the published log: 7033 artifact rows against 386 real measurements.
2. **RESEARCH** — two candidate fixes, and the difference matters. (1) Add `test_support.isolate_residence()` to autonomy_test.py — done as well, but on its own it is a per-suite patch: the next suite that runs the reflex loop leaks again, and this module read as safe precisely because its guards are built with emit=False. (2) Gate the write at the source, on the same flag the rest of the runtime already uses. The rejected alternative was gating on `EVIDENCE_FILE` being set: that is a redirect, not a switch, and it would make an unset variable mean 'write to the published log', which is the assumption that caused the leak.
3. **DESIGN** — `run_goal` writes its step evidence only when `guard.emit` is true, and the flag already resolves from `LOOP_GUARD_EMIT` when the caller does not pass one, so no signature changes and no call site has to be updated. The CLI paths keep their `setdefault("LOOP_GUARD_EMIT", "1")`, so a recorded run still records: the gate is a gate, not a deletion. `autonomy_test.py` also isolates its residence, because a module that reads as safe should still be safe when the loop changes under it.
4. **IMPLEMENT** — agent_runtime.py — both `_emit_step` call sites in `run_goal` guarded, with the measurement that found the leak written into the docstring; autonomy_test.py — test_support.isolate_residence() at import; agent_runtime_test.py — a regression test in each direction.
5. **TEST** — the published log adds 0 rows across a suite run that previously added 5, asserted in both directions by `ReflexEvidenceGateTest` (a library run writes nothing; a recorded run still writes its record) + the full suite: 440 tests across the ten modules, all passing, with the published records unchanged (0 rows added).
6. **REGISTER** — self — this record. The 7033 rows the leak left are kept in `freebrain-residence/evidence/test-artifacts.jsonl` rather than deleted: they are the record of the bug.

**Evidence:** `{"cli_behaviour_unchanged": "agent_runtime --goal/--reflex still setdefault LOOP_GUARD_EMIT=1", "measured_rows_kept": 386, "regression_tests": ["ReflexEvidenceGateTest.test_a_library_run_writes_no_evidence", "ReflexEvidenceGateTest.test_a_recorded_run_still_writes_evidence"], "rows_added_by_one_autonomy_test_run_after": 0, "rows_added_by_one_autonomy_test_run_before": 5, "rows_quarantined": 7033, "suites_that_isolate_their_residence": 6, "writers_investigated": ["agent_runtime._emit_step", "agent_runtime.run_goal", "autonomy._call", "loop_guard.LoopGuard.emit"]}`

**Loop summary:** `{"measurement_rows_kept": 386, "rows_added_by_a_suite_run_after": 0, "rows_quarantined": 7033}`
