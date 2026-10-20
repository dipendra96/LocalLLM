# Capability conventions

Restart the app after adding trusted tool modules or skills.

## Python tools

Add a module under `tools/` with `build_tools()` returning Python callables or
ADK `FunctionTool` objects. Type annotations describe arguments; docstrings
explain when the agent should call the tool.

```python
def lookup_fact(subject: str) -> dict:
    """Look up a fact in the application's local reference source."""
    return {"subject": subject, "result": "example"}


def build_tools():
    return [lookup_fact]
```

Names must be unique across Python, native skill tools, and MCP tools. Only add
trusted code: these modules run in the app's process.

## Native ADK skills

Each skill lives in a directory whose kebab-case name matches its frontmatter:

```text
skills/web-research/SKILL.md
```

```markdown
---
name: web-research
description: Search the web and read sources for current information.
metadata:
  required_tools: [search, fetch_content]
---

Search only when needed. Read sources and cite their URLs.
```

The native SkillToolset exposes names/descriptions in the initial catalog, and
`load_skill` loads detailed instructions when selected. It also supplies
`load_skill_resource` and `run_skill_script`. These names are reserved. Skill
scripts are executable capabilities; only install trusted skills. No scripts
are bundled with web research.

Full skill files are parsed at startup, but full instructions are not all sent
in the initial model prompt. Adding conventional skills does not require editing
the agent runtime. Native activated skills persist in ADK session state.

Clients may send `requested_skills: ["web-research"]`. The app validates required
tools before generation, activates the skills with ADK, and supplies their full
instructions for that turn. Autonomous selection remains model-driven; metadata
requirements are enforced by our explicit-selection preflight, not by ADK's
native loader. Loading a procedure does not guarantee perfect model adherence.

## MCP servers

`mcp.toml` supports multiple local stdio servers. Use `servers = []` to disable
all servers. Each entry becomes an ADK `McpToolset`:

```toml
[[servers]]
name = "web"
command = "{venv_bin}/duckduckgo-mcp-server"
timeout_seconds = 60
```

`{python}` expands to the app interpreter, and `{venv_bin}` to its executable
directory. `args` and `env` are optional. Commands launch directly without a
shell. Keep credentials out of committed files. Set `LOCALCHAT_MCP_CONFIG` for
another config path. Run from the project root.

Servers are checked at startup and closed on shutdown, including after partial
startup failure. Duplicate server/tool names prevent startup. Remote HTTP MCP
transport is a future extension. `/capabilities` reports native, Python, and MCP
tools, with the originating server for MCP tools.
