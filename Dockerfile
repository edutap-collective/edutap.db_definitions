# syntax=docker/dockerfile:1
#
# The migration container: starts once per deploy, brings the schema in line, exits.
#
# This package spent its first weeks stating it would never be deployed. The reason it
# gave was sound -- a reader must not hold DDL rights -- and the reason it missed is
# why this file exists: the commands collect schemas from the packages that are
# *installed*, so the rendered SQL is a function of one particular set of versions.
# Rendering on one machine and applying on another assumes both hold the same set, and
# nothing enforces that. An image built from the deploy has them by construction.
#
# The guard rail is untouched. It protects long-lived, network-facing readers from
# holding schema privileges for months; this container has no port, is reachable from
# nowhere, and lives for seconds. See
# docs/superpowers/specs/2026-08-11-migration-container-design.md.
FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src /app/src

# The packages whose schemas this image should collect, one PEP 508 requirement per
# line. They are what makes the image deployment-specific: the same Dockerfile with a
# different list migrates a different estate.
#
# A file rather than build args, and that is deliberate -- a `--build-arg` ends up in
# the image history, and these lines are direct references that may carry a host and a
# tag somebody would rather not publish. An empty file is valid: the contract schema in
# this package is then all there is to migrate.
COPY packages.txt /app/packages.txt

# git, because the eduTAP requirements are PEP 508 direct references to a repository.
# Purged in the same RUN -- a runtime image has no reason to carry a version control
# system, and splitting it across layers would keep it anyway.
#
# pip, not uv and no venv: the container *is* the environment, and the house rule for
# images says so.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git; \
    pip install --no-cache-dir "/app[cli]"; \
    if [ -s /app/packages.txt ]; then pip install --no-cache-dir -r /app/packages.txt; fi; \
    apt-get purge -y --auto-remove git; \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app
USER app

# `migrate` and nothing else. `apply` would take a file this image does not have, and
# `create`/`diff` write to stdout -- useful at a terminal, pointless as a container's
# reason to exist.
#
# Connection settings come from the environment: EDUTAP_DBDEF_* or the standard PG*
# names. The role needs DDL rights on the schemas it owns and nothing beyond them.
ENTRYPOINT ["edutap-dbdef", "migrate"]
