# SpecPilot

SpecPilot is a safety-first foundation for specification intelligence.

## Local development

Use Python 3.12 through 3.14 and run `make setup` to create a repository-local
virtual environment with development dependencies. Then run `make unit`,
`make lint`, and `make typecheck`.

Copy `.env.example` to `.env` only for local settings. Never commit credentials,
provider account metadata, or restricted source material.
