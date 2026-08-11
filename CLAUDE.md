# CLAUDE.md — edutap.db_definitions

Repository-specific rules. They take precedence over the global defaults.

## Language

**English only.** This repository belongs to eduTAP proper, not to any single
institution: README, changelog, documentation, docstrings, code comments, commit
messages, pull request titles and bodies, and replies to review comments.

The language follows the repository, not the conversation. A discussion held in
German still produces English artefacts here.

## What this package is

A tool that renders the SQL schema of the installed eduTAP packages, and applies it
where it is allowed to. It serves a developer preparing a change and a **migration
container that runs once per deploy** and exits.

**It may add and may not take away.** A diff containing `DROP TABLE`, `DROP COLUMN`,
`DROP CONSTRAINT` or `DROP INDEX` must end the run with a non-zero status, not be
applied around — `render_diff` already classifies these. An additive change that goes
wrong leaves a table that already exists; a destructive one deletes something that is
not coming back, and no deploy at three in the morning gets to decide that.

Note that a **rename renders as drop + add** and therefore stops the deploy. That is
the correct outcome, and it will still surprise someone.

```{note}
Until 2026-08-11 this package stated it had no runtime role, no image and no
`Dockerfile`. The reason it gave was sound; the reason it missed was that rendering on
one machine and applying on another assumes both hold the same package versions.
[The design record](docs/superpowers/specs/2026-08-11-migration-container-design.md)
carries the argument and the limits.
```

## Guard rails

**Never own a schema.** This package collects definitions through the
`edutap.db_definitions` entry point; the definition itself always belongs to the
package that owns the tables. That ownership is what makes exactly one package
answerable for one schema — moving definitions here would dissolve it.

**The contract checks are the point of the tool**, not a side feature: table
collisions across packages, version-table collisions, naming-convention deviations,
undeclared cross-package foreign keys. A check that becomes inconvenient is a signal
about the schema, not about the check.

**Connect read-only for `diff` and `check`.** Only `apply` writes, and it is meant to
run as a privileged role that no service holds.

## Sources and confidentiality

**No vendor internals — from any vendor, not just the ones currently in play.**
Neither in files nor in commit messages.

The standard is academic: a statement counts as reliable only where it can be
evidenced from public information, with a link. Everything else was obtained either
by our own testing or through insider knowledge, and the three are not
interchangeable:

* **Documented** — public source, linked. May be written as fact.
* **Verified, not citable** — obtained by a person from an access-protected area and
  checked there; the reference is recorded internally but must not be published; and
  the statement has been reduced to what is not confidential. May be written as fact,
  carrying this label. It is the rule journalism uses for source protection: the claim
  stands, we know where it comes from, the reader does not get the source.

  The four conditions hold together. A statement for which nobody can name the
  internal reference does not fall here — that is insider knowledge.
* **Measured** — established by our own tests. May be written down, but always marked
  as such, because it describes what a platform did on the day we looked, not what it
  guarantees. It can change with the next release, without notice and without an
  entry in any changelog.
* **Insider knowledge** — is not written down at all.

What a platform's behaviour *means for us* stays documentable even where the
mechanism does not: "the platform enforces a deadline, it is self-healing, it is
outside our control" carries the design consequence without disclosing anything.

Contract and regulatory material is wanted and citable: eduPersonAssurance, GÉANT and
eduGAIN terms, published wallet programme obligations.

## Working practice

Branch first, never commit on `main`. Push only when asked. Lint and tests green
before opening a pull request.

Design records under `docs/superpowers/` record a decision at a point in time — do
not rewrite them to match a later state; write a new one.
