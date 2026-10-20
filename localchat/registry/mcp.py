"""Connect configured local MCP processes and close them at app shutdown."""

import sys
import tomllib
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters
from pydantic import BaseModel, ConfigDict, Field


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)


class MCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    servers: list[ServerConfig] = Field(default_factory=list)


@dataclass
class ConnectedMCP:
    servers: list[McpToolset]
    tools: list[dict[str, str]]


def expand_command(value: str) -> str:
    return value.replace("{python}", sys.executable).replace(
        "{venv_bin}", str(Path(sys.executable).parent)
    )


@asynccontextmanager
async def connect_mcp_servers(config_path: Path, local_tool_names: set[str]):
    config = MCPConfig.model_validate(
        tomllib.loads(config_path.read_text(encoding="utf-8"))
    )
    server_names = [server.name for server in config.servers]
    if len(server_names) != len(set(server_names)):
        raise ValueError("MCP server names must be unique")

    connected = ConnectedMCP(servers=[], tools=[])
    tool_names = set(local_tool_names)

    # ExitStack closes all connected servers, even if a later connection fails.
    async with AsyncExitStack() as stack:
        for entry in config.servers:
            params = {
                "command": expand_command(entry.command),
                "args": [expand_command(arg) for arg in entry.args],
            }
            if entry.env:
                params["env"] = entry.env
            server = McpToolset(connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(**params),
                timeout=entry.timeout_seconds,
            ))
            stack.push_async_callback(server.close)
            for tool in await server.get_tools():
                if tool.name in tool_names:
                    raise ValueError(f"Duplicate tool name '{tool.name}' from MCP server '{entry.name}'")
                tool_names.add(tool.name)
                connected.tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "server": entry.name,
                })
            connected.servers.append(server)

        yield connected
