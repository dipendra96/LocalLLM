from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from localchat.agent.runtime import AgentRuntime
from localchat.api.routes import router
from localchat.config import settings
from localchat.registry.catalog import RESERVED_TOOL_NAMES, load_registry
from localchat.registry.mcp import connect_mcp_servers


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry = load_registry()
    names = RESERVED_TOOL_NAMES | {tool.name for tool in registry.tools}
    async with connect_mcp_servers(settings.mcp_config, names) as connected:
        runtime = AgentRuntime(settings, registry, connected.servers, connected.tools)
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.close()


app = FastAPI(lifespan=lifespan)
app.include_router(router)


# The Vite build is static; deployed chat needs only this Python server.
frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
app.mount("/assets", StaticFiles(directory=frontend_dist / "assets", check_dir=False), name="ui-assets")


@app.get("/", include_in_schema=False)
async def chat_page():
    index = frontend_dist / "index.html"
    if not index.is_file():
        return HTMLResponse("Build the UI first: cd frontend &amp;&amp; pnpm install --frozen-lockfile &amp;&amp; pnpm build", status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    path = frontend_dist / "favicon.svg"
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/svg+xml")
