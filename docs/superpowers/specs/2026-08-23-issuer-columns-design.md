# Two issuer columns on `pass_state` — design

**Date:** 2026-08-23
**Status:** decided (A. Loechel)
**Follows:** [the handoff of the same day](../handoff-2026-08-23-issuer-columns.md).
That document recorded what was found and what was still open; this one records the
answers.

A record of a decision at a point in time. It is not rewritten later — a different
decision gets a new record.

## What changes

`public.pass_state` gains two columns:

| Column | Type | Filled from |
| --- | --- | --- |
| `issuing_service` | `varchar(64)`, nullable | the `edutap-producer` header of the `create` command |
| `schac_home_organization` | `varchar(64)`, nullable | a field in the `pass.command` payload |

Neither is indexed. Both are written once, and only by `edutap-action: create`.

An operator can then chart issued passes by the service that issued them and by the
home organization of the person they were issued to, next to the breakdowns the table
already supports.

## Two columns, not three

The handoff described the issuer along two axes: the issuing *service* and the issuing
*institution*. The second axis turned out to be two different questions wearing one
word, and only one of them is worth a column.

**The issuing authority** — the body in whose name a pass is issued, `Tenant` in
`edutap.pass_builder` — gets no column. In an eduTAP deployment exactly one authority
is active, so the value would be constant per database and tell a report nothing. It
becomes interesting only where several issuers are aggregated together, and that is a
different arrangement with a different question; it can have its own record.

**The home organization** — which institution the pass holder belongs to — does get
one, and it is the axis the request was actually about. A deployment serving a
canteen carries fifteen or more of them, and how many passes each one accounts for is
a question nobody can answer today.

## Why the name is `schac_home_organization`

`lmu_edutap_full_view` already writes this value into `person_view.data` under
exactly this name, with the value range of a domain — `lmu.de`, not
"Ludwig-Maximilians-Universität München".

One concept gets one name. Two columns meaning the same thing under different names is
how the two come apart, and the standard prefix is doing work rather than decorating:
it fixes the value range. A column called `home_institution` invites the first writer
to enter a display name, and then the report shows `lmu.de` and `LMU München` as two
organizations.

This matters more here than it would elsewhere, because of this package's own rule:

> Note that a **rename renders as drop + add** and therefore stops the deploy.

A column named wrongly today is not renamed tomorrow. It is a stopped deploy, resolved
by hand.

## Why the value is a snapshot, not the current state

`schac_home_organization` records which institution the holder belonged to **at
issuance**. It is never corrected afterwards.

The alternative was to store nothing and have the report join `pass_state.person_uid`
against `person_view` — always current, no column, no contract question. It was
rejected for two reasons, and the second is the decisive one:

* A person who moves from one institution to another would retroactively move every
  pass ever issued to them. The report would then answer "whose members hold a pass
  today", where the question asked is "how many passes were issued on behalf of each
  institution".
* **`person_view` is not present in every deployment.** A deployment without a
  full-view spooler holds no such row, so the join has nothing to join against. The
  value has exactly one source: the backend, which knows it from the authenticated
  session at the moment it issues the command.

## Where the values come from

**`issuing_service`** is the `edutap-producer` header of the `create` command. That
header states what a service claims rather than proving it, which is written down
where it is defined and is accepted here: the column records who said they issued the
pass, and who was permitted to write to the topic at all is a separate control.

**`schac_home_organization`** rides in the payload of `pass.command`, set by the
producer of the `create` action from its authenticated session. It is a property of
the person the message is about, not of the transmission, which is why it belongs in
the payload rather than in the envelope.

The payload contract stays `pass-command/v1`. The versioning rule in the consumer says
a `pass-command/v2` is not a `v1` with extra fields to be ignored — that rule is aimed
at a consumer meeting an unknown contract, and it is not in play here. There is no
written `v1` payload anywhere to bump: the version exists as a string literal in the
consumer's `SCHEMAS` and nowhere else. A `v2` would assert a difference from a `v1`
that nobody can look up.

```{important}
That missing payload contract is a real gap, and it is not closed here. A model for
`pass-command/v1` belongs in `edutap.data_models`, and the field named above has no
written home until it exists. Whoever reads the column description below and goes
looking for the field will not find it.
```

## The write rule

Both columns are set **once**, and only by `edutap-action: create`.

Not by `update`, not by `deactivate`, not by a `pass.state` report, not by a
`device.registration`. Those upserts must not carry the columns in their `EXCLUDED`
list at all — not even when the stored value is `NULL`.

The reason is the same for both columns. They are statements about the *issuance*. The
producers differ per action: a `create` comes from a backend or the HEIDI webhook, an
`update` from a scheduler or the template manager. Letting a later action fill a
`NULL` would write the scheduler into `issuing_service`, and it would look exactly
like a correct value in the report. A scheduled `update` also has no authenticated
person and cannot know the home organization at all.

`NULL` is therefore the right answer and not the second-best one: it says "not
recorded for this row", which is the truth for everything written before the columns
existed, for a row a report created without a preceding command, and for a deployment
whose producers do not send the field.

The rule is held by the writer, as:

```sql
ON CONFLICT (pass_id) DO UPDATE SET
    issuing_service = COALESCE(pass_state.issuing_service, EXCLUDED.issuing_service)
```

and by the column descriptions, which are the contract text — the same role
`last_event_at` already plays for the watermark. **Not** by a trigger: this package
declares structure and holds no behaviour, it has no trigger anywhere, and the rule
would in any case only need holding for a single writer.

## Column shape

`varchar(64)`, nullable, no index — following the table's own precedents.

* **Text, not a native enum**, as `wallet_type` states in its own description: a new
  provider must not force a migration. The argument is stronger for a list of services
  and stronger again for a list of institutions.
* **64 rather than 32**, the length `pass_template` uses. A service name such as
  `apple_wallet_vas_web_service` is 28 characters and the next one may be longer;
  a home organization is a domain.
* **Nullable rather than `NOT NULL` with a server default.** Rows already exist in
  development and demonstration databases. A `NOT NULL` column without a default
  cannot be added to a populated table, and a default would write an assertion into
  every existing row that nobody checked.
* **No index.** The reporting reads are grouped aggregates over the whole table, which
  no index on a low-cardinality column improves. The existing indexes serve point
  lookups, which these are not.

## What this record does not decide

* **The `pass-command/v1` payload contract.** It does not exist as a model. That is
  its own piece of work, in `edutap.data_models`.
* **The write path.** `lmu_edutap_worker` is a scaffold that validates envelopes and
  writes nothing. Its own repository, its own change — and the timing is in our
  favour: the columns exist before the writer that fills them, rather than being
  retrofitted around a running one.
* **The producers.** A backend has to put the field into the `create` payload. Its own
  repository, its own change.
* **The issuing authority axis**, parked until several issuers are aggregated.
