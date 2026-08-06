from fastapi import FastAPI


def create_app() -> FastAPI:
    """W0 health skeleton for the MCP service slot.

    Deliberately not an MCP server yet. The five read-only capabilities and the
    Streamable HTTP transport arrive in W3; standing up the SDK now would add a
    dependency with no exercised code and make the Compose file look like it
    serves tools it does not have.
    """
    app = FastAPI(title="SpecPilot MCP", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
