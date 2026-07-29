# edutap.db_definitions

Collects the database models and Alembic migrations of every eduTAP package in use
and applies them with a privileged database user.

**Status: planned.**

## Why this exists

No service creates or alters its own tables. A service that reads data runs with a
read-only database role and has no DDL rights at all — otherwise a compromised
reader would hold schema privileges. Schema changes are therefore applied here:
once, centrally, with an account that no running service uses.

`edutap.data_provider` and the HEIDI-Local appliance (a specialised, proprietary
data provider) are the clearest cases: both only read.

## Planned scope

* Discover the model and migration definitions of the configured eduTAP packages.
* Provide one entry point to create and upgrade a deployment's schema.
* Run as a one-shot job in a deployment (compose/Swarm), not as a long-running
  service.
