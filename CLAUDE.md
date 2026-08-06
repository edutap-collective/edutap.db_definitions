# CLAUDE.md — edutap.db_definitions

Repository-specific rules. They take precedence over the global defaults.

## Language

**English only.** This repository belongs to eduTAP proper, not to any single
institution: README, changelog, documentation, docstrings, code comments, commit
messages, pull request titles and bodies, and replies to review comments.

The language follows the repository, not the conversation. A discussion held in
German still produces English artefacts here.

## What this package is

A development-time tool that renders the SQL schema of the installed eduTAP packages.
It has no runtime role, no image and no `Dockerfile`.

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

## Confidentiality

No vendor internals from Apple or NXP — not in files, not in commit messages. What a
platform's behaviour *means for us* is documentable ("the platform enforces a
deadline, it is self-healing, it is outside our control"); the mechanics, concrete
values and rule sets behind it are not.

Contract and regulatory material is fine and wanted: eduPersonAssurance, GÉANT and
eduGAIN terms.

## Working practice

Branch first, never commit on `main`. Push only when asked. Lint and tests green
before opening a pull request.

Design records under `docs/superpowers/` record a decision at a point in time — do
not rewrite them to match a later state; write a new one.
