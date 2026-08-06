# SpecPilot

SpecPilot is a safety-first foundation for specification intelligence.

## Local development

Use Python 3.12 through 3.14 and run `make setup` to create a repository-local
virtual environment with development dependencies. Then run `make unit`,
`make lint`, and `make typecheck`.

The ledger integration tests need a local PostgreSQL. With Homebrew:

```
brew services start postgresql@17
createdb specpilot_test
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_test make integration-db
```

## Running the stack

Nothing in `compose.yaml` publishes a host port. Reaching the API needs the demo
override, which adds both the loopback port and a routable network:

```
docker compose -f compose.yaml -f compose.demo.yaml --profile demo up --wait
```

`--profile real` publishes nothing at all.

On colima the published port binds to the VM's loopback, not the Mac's, so check
it from inside the VM: `colima ssh -- curl http://127.0.0.1:8000/health`. On
Docker Desktop and on Linux it is reachable from the host directly.

Copy `.env.example` to `.env` only for local settings. Never commit credentials,
provider account metadata, or restricted source material.
