# Contributing to Space

Space is free software under the [GNU Affero General Public License v3.0](LICENSE). Bug reports, fixes, documentation and features are welcome.

## Before your first pull request

Read and accept the [Contributor License Agreement](CLA.md); section 8 lists the two ways to accept it. You keep the copyright in your contribution, and you grant the rights the project needs to distribute and maintain it, including under other license terms.

## Set up and check

```sh
make setup
make check
```

`make check` runs the whole gate: `ruff check`, `ruff format --check` and `pytest` for the backend, then the website's unit tests, production build and type check. [DEVELOPMENT.md](docs/DEVELOPMENT.md) explains how to run Space from source.

- `make check-database` also runs the database tests. They start disposable `pgvector/pgvector:pg17` containers, so Docker must be running and the image pulled.
- `npm --prefix apps/web run test:e2e` runs the website's browser tests.

Use the project's own environment rather than installing dependencies yourself: `uv run --directory backend --locked ...` for Python and `npm --prefix apps/web ...` for the website. `backend/uv.lock` pins the toolchain, including the formatter, so always format with `uv run --directory backend --locked ruff format`.

## What a change needs

- A focused change with its test, or an explanation of why a test does not apply.
- Evidence that the relevant check passes: the command and its result.
- Documentation updated when behaviour, interfaces or configuration change.
- No credentials, personal data or installation identifiers in code, tests, fixtures or documentation. Use environment variables and example values.

## Reporting a problem

Open an issue with the steps to reproduce, the observed result and the expected result. Report security problems privately through GitHub's "Report a vulnerability" form on the repository's Security tab, not in a public issue.
