# A migration container in the deploy — design

**Date:** 2026-08-11
**Status:** decided (A. Loechel)
**Supersedes:** the "development-time helper only" part of
[2026-07-30](2026-07-30-db-definitions-design.md). That record stands as what was
decided then; this one records what changed and why.

A record of a decision at a point in time. It is not rewritten later — a different
decision gets a new record.

## What changes

This package gets a `Dockerfile`. The image runs once per deploy, as a Swarm service
with `replicas: 1` and `restart_policy: condition: none`, compares the database
against the declarations of the packages installed **in that image**, applies what
only adds, and refuses everything else.

The earlier record said the opposite, in as many words: *"The commands are a
development-time helper, never a deployed service … there is no image in any stack and
no `Dockerfile`."* That was right for the reason it gave and wrong for a reason it did
not consider.

## Why the earlier decision does not hold

**The commands collect schemas from the packages that are installed.** That is the
whole mechanism: an entry point per package, discovered at run time. So the SQL that
comes out is a function of one specific set of installed versions.

Rendering it on a developer machine and applying it at deploy time silently assumes
those two sets are the same. Nothing enforces it. Someone renders the file, reviews
it, commits it — and three weeks later the stack rolls out a package version whose
declarations differ from the ones the file was built from. **Both halves stay
internally consistent, and nothing reports a problem.** The database ends up shaped by
a package version that is no longer deployed anywhere.

A container built from the deploy has, by construction, exactly the versions being
rolled out. The gap closes structurally rather than by discipline.

## Why DDL rights are not the objection they look like

The guard rail this seems to collide with is about **readers**:

> A service that reads data runs with a read-only database role and has no DDL rights
> at all — otherwise a compromised reader would hold schema privileges.

A migration job is not a reader, and the concern does not transfer. What the rail
protects against is a long-lived, network-facing service that holds schema privileges
for months — an attacker who reaches it can rewrite the schema. A container that
starts, migrates and exits holds the same privilege for seconds, has no port, and is
not reachable from anywhere.

The rail stays exactly as it is. Every reading and writing service keeps its role
without DDL. What moves is only *who* applies the change, from "a human with a
privileged role" to "a job with a privileged role, started by the same deploy that
ships the packages".

## What is genuinely lost, and what replaces it

The earlier record's rule was *"prepared here, **reviewed as SQL**, and applied once by
a privileged role"*. The review step is the real cost of this change, and it is not
recovered by being careful.

It is replaced by a narrower rule: **the container may add, and may not take away.**

| The diff contains | The container |
|---|---|
| `CREATE TABLE`, `CREATE INDEX`, `ADD COLUMN`, `CREATE SCHEMA` | applies it |
| `DROP TABLE`, `DROP COLUMN`, `DROP CONSTRAINT`, `DROP INDEX` | **refuses, and fails the deploy** |

This is not a new mechanism. `render_diff` already classifies exactly these four
(`compare.py:33`) and already comments them out unless `--allow-destructive` is
passed. What is missing is only that a marker in the output has to **end the run with
a non-zero status** instead of being applied around.

The asymmetry is the point. An additive change is one whose failure mode is a table
that already exists; a destructive one deletes something that is not coming back, and
no automation should be trusted with that at three in the morning because a deploy
happened to run.

## The limits, written down because they will bite

**A rename renders as drop + add** — `compare.py` says so in the generated header.
Under this rule that means a rename **stops the deploy**, and the person who wanted it
has to do it by hand. That is the correct outcome and it will still surprise someone.

**Some type changes render incompletely.** This is the uncomfortable one: an
incompletely rendered `ALTER … TYPE` may carry no destructive marker and therefore be
applied. The rule protects against deletion, not against every wrong statement.

**An index build takes a write lock.** `CREATE INDEX` counts as additive and will be
applied — on a large populated table that is an outage for the duration. Postgres
solves it with `CREATE INDEX CONCURRENTLY`, which cannot run inside a transaction, and
this package wraps its documents in one. Until that is handled, an index on a table
that is already large is a change to make by hand, outside the deploy.

**Data migrations are out of scope**, as they were before. Nothing here moves a row.

## Operational shape

**One node, one run.** The stack already has this pattern: `kafka-init-topics` runs as
`replicas: 1` with `restart_policy: condition: none`, pinned by a placement
constraint, from an Ansible task with `run_once`. The migration container is a second
user of it, not a new mechanism.

**Ordering.** It has to complete before the services that use the tables start. Swarm
has no dependency edges between services, so the deploy sequences it: the Ansible task
runs the container and waits for it, and only then deploys the stack.

**Concurrency.** `run_once` plus a single replica means one runner. A Postgres advisory
lock around the apply is still worth having — it is three lines, and it turns "we
believe only one is running" into "only one can".

**Failure.** A non-zero exit fails the deploy. That is the intended behaviour for both
cases the container refuses: a destructive diff, and an error while applying. The
document runs in a transaction, so a failure mid-apply leaves nothing half-done.

## What this does not change

* The declarations stay importable without any of this. A service that reads
  `person_view` installs the core and gets SQLAlchemy and the vocabulary, no migration
  engine and no driver — see the README's two-install split.
* No service creates or alters its own tables. That rule is untouched, and this change
  makes it easier to keep: there is now one obvious place for schema changes to happen.
* `create`, `diff` and `check` remain what they were for development.

## Open

- [ ] **The advisory lock.** Which key, and does `apply` take it or the container
      around it.
- [ ] **`CREATE INDEX CONCURRENTLY`.** Whether the renderer should learn to emit index
      statements outside the transaction, or whether large-table indexes stay a manual
      step. Deferred until it first hurts — today `person_view` is empty everywhere.
- [ ] **What the container reports.** A deploy that stops on a destructive diff has to
      show *which* statements it refused, in the deploy log, not only in a file nobody
      opens.
