# Handoff: two issuer columns on `pass_state` (2026-08-23)

**A snapshot of 2026-08-23.** It records the request and what was found on that
day. It is not kept up to date; whoever decides something else writes a new one.

## The request in one sentence

`public.pass_state` should record **who issued a pass** — along two axes, the
issuing *service* and the issuing *institution* — so that a deployment can report
issued passes broken down by either.

## Where it comes from

An operator wants to chart issued passes by issuing service and by issuing
institution, next to the breakdowns the table already supports. Reading the
current columns, three of the four axes are there:

| Axis | Column | Status |
| --- | --- | --- |
| Pass kind | `pass_template` (+ `pass_template_variant`) | present |
| Wallet | `wallet_type` | present |
| Life cycle | `issuance_state`, `holder_state` | present |
| **Issuer** | — | **absent, both senses** |

The reporting itself is none of this package's business; it reads from a
replica and aggregates. What it cannot do is aggregate over a column that does
not exist.

## The two columns are not equally hard

**The issuing service is already known at write time.** Every message carries a
mandatory `producer` header — see `Envelope.producer` in the pass-state
consumer's `headers.py`, where a missing or blank value refuses the message
outright. The value exists on every event that reaches the writer; it is simply
not persisted. Adding the column is plumbing.

**The issuing institution exists nowhere.** Not in the envelope, not in the
payload contracts. Before a column can hold it, someone has to decide where the
value comes from — and that decision reaches further than this package:

* A new envelope header is a **contract change**. `SCHEMAS` in the consumer is
  versioned on purpose, with the reasoning spelled out there: a `pass-state/v2`
  is not a `v1` with extra fields to be ignored. Adding a header means a new
  schema version and every producer moving to it.
* Deriving it from something already present — the producer, the template, a
  tenant notion — avoids the contract change but bakes an assumption into the
  writer that will be wrong for the first deployment that does not share it.

Do not let the easy half drag the hard half along. The service column can land
on its own and be useful; the institution column should wait for its answer.

## Naming is a one-way door here

This is the constraint that shapes everything else, and it is this repository's
own rule (`CLAUDE.md`, *What this package is*):

> Note that a **rename renders as drop + add** and therefore stops the deploy.

A column named wrongly today is not renamed tomorrow. It is a stopped deploy,
resolved by hand, in a repository whose whole point is that nothing destructive
runs unattended. **The names have to be right the first time.**

The house style in `pass_state` is a plain noun compound in snake case —
`pass_template`, `wallet_type`, `issuance_state`, `holder_state`. Candidates in
that shape are `issuing_service` / `issuer_service` and `issuing_institution` /
`issuer_institution`, but the choice is a domain question and is **deliberately
not made here**.

Worth settling at the same time: whether *institution* is the right word at all.
It has to hold for a deployment where passes are issued on behalf of several
distinct bodies, and the word that fits one such arrangement may not fit the
next.

## Column shape

Nothing here is decided either, but the precedents in the table are clear:

* **Text, not a native enum.** `wallet_type` states the reason in its own
  description — a new provider must not force a migration. The same argument
  applies with more force to a service list, which grows as services are added.
* **Nullable versus `NOT NULL` with a server default.** Rows already exist in
  development and demonstration databases. A `NOT NULL` column without a default
  cannot be added to a populated table; a default writes an assertion into every
  existing row that nobody checked. Nullable says "not recorded for this row",
  which is the truth for everything written before the column existed.
* **An index is probably not warranted.** The reporting reads are grouped
  aggregates over the whole table, which no index on a low-cardinality column
  improves. The existing indexes serve point lookups, which these are not.

## What works in our favour

Two added columns are an **additive** change, and this package applies those.
The rule that stops a deploy — no `DROP TABLE`, `DROP COLUMN`, `DROP CONSTRAINT`
or `DROP INDEX` — is not in the way here. That is exactly why the naming matters
so much: the one thing that would turn this into a destructive change is
changing our minds about a name later.

It also helps that the writer is not finished. The pass-state consumer describes
itself as a scaffold — three consumers running, messages logged, nothing written
yet. The column can be in place before the write path that fills it, rather than
being retrofitted around a running writer.

## Open questions

1. **The two names.** A domain-modelling question, not a schema question. It
   should be answered with the vocabulary, not in a migration.
2. **Where the institution value comes from** — a new envelope header (contract
   change, new schema version) or a derivation (no contract change, an assumption
   baked in).
3. **Nullable or defaulted**, given existing rows.
4. **Who writes them.** The service value is in the envelope the consumer already
   reads. The institution value has no source until question 2 is answered.

## Suggested order

1. Settle the vocabulary for both axes, including whether *institution* is the
   right word.
2. Answer question 2. It is the only one that reaches outside this package.
3. Add the service column, which needs neither.
4. Add the institution column once its source is decided.

Steps 3 and 4 are small. Steps 1 and 2 are the work.
