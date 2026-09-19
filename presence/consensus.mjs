// consensus.mjs — one person, one voice. Questions the circle deliberates on.
// Votes are append-only (provenance), tallies are honest (never forced to 100%),
// and a member can change their mind but the history stays.

import { randomUUID } from "node:crypto";

export function createQuestion({ title, choices = ["agree", "disagree", "abstain"], proposer = "unknown", at = Date.now() }) {
  return {
    id: randomUUID(),
    title,
    choices,
    votes: {}, // memberId -> { choice, stance, at }
    log: [],   // [{ memberId, choice, stance, at, change }] — append-only
    proposer,
    at,
    closed: false,
  };
}

export function vote(question, { memberId, choice, stance = null, at = Date.now() }) {
  if (question.closed) throw new Error("question is closed");
  if (!question.choices.includes(choice)) throw new Error(`invalid choice: ${choice}`);
  const prev = question.votes[memberId];
  question.votes[memberId] = { choice, stance, at };
  question.log.push({
    memberId,
    choice,
    stance,
    at,
    change: prev ? prev.choice !== choice : true,
  });
  return question;
}

export function tally(question) {
  const counts = {};
  for (const c of question.choices) counts[c] = 0;
  for (const v of Object.values(question.votes)) counts[v.choice] = (counts[v.choice] || 0) + 1;
  return counts;
}

// Honest state: how many voices were heard out of how many eligible, the real
// agreement share, the distribution, and the full log. Never rounded up to unity.
export function state(question, eligibleCount) {
  const counts = tally(question);
  const heard = Object.keys(question.votes).length;
  const top = Math.max(...Object.values(counts), 0);
  const agreement = heard ? Math.round((top / heard) * 100) : 0;
  return {
    id: question.id,
    title: question.title,
    choices: question.choices,
    counts,
    heard,
    eligible: eligibleCount,
    agreement,
    closed: question.closed,
    proposer: question.proposer,
    at: question.at,
    log: question.log,
  };
}