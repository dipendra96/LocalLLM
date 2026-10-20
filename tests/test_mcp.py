"""Check multi-server setup and cleanup without launching external services."""

import asyncio
from types import SimpleNamespace

import pytest

from localchat.registry import mcp


def test_multiple_servers_metadata_and_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "mcp.toml"
    path.write_text('''
[[servers]]
name = "web"
command = "{python}"
args = ["web.py"]
[[servers]]
name = "documents"
command = "{python}"
args = ["documents.py"]
''')
    events = []

    class FakeServer:
        def __init__(self, connection_params, **kwargs):
            self.name = connection_params.server_params.args[0].removesuffix(".py")
        async def close(self):
            events.append("close:" + self.name)
        async def get_tools(self):
            events.append("open:" + self.name)
            return [SimpleNamespace(name=self.name + "_tool", description="Example")]

    monkeypatch.setattr(mcp, "McpToolset", FakeServer)

    async def check():
        async with mcp.connect_mcp_servers(path, {"load_skill"}) as connected:
            assert len(connected.servers) == 2
            assert [tool["name"] for tool in connected.tools] == ["web_tool", "documents_tool"]
    asyncio.run(check())
    assert events == ["open:web", "open:documents", "close:documents", "close:web"]


def test_name_collision_cleans_up_open_servers(tmp_path, monkeypatch):
    path = tmp_path / "mcp.toml"
    path.write_text('[[servers]]\nname="web"\ncommand="example"\n')
    closed = []

    class FakeServer:
        def __init__(self, **kwargs):
            pass
        async def close(self):
            closed.append(True)
        async def get_tools(self):
            return [SimpleNamespace(name="load_skill", description="Collision")]

    monkeypatch.setattr(mcp, "McpToolset", FakeServer)

    async def check():
        with pytest.raises(ValueError, match="Duplicate tool"):
            async with mcp.connect_mcp_servers(path, {"load_skill"}):
                pytest.fail("Collision should prevent startup")
    asyncio.run(check())
    assert closed == [True]


def test_connection_failure_closes_previous_server(tmp_path, monkeypatch):
    path = tmp_path / 'mcp.toml'
    path.write_text('[[servers]]\nname="first"\ncommand="first"\n[[servers]]\nname="broken"\ncommand="broken"\n')
    closed = []
    class FakeServer:
        def __init__(self, connection_params):
            self.name = connection_params.server_params.command
        async def get_tools(self):
            if self.name == 'broken':
                raise RuntimeError('Cannot connect')
            return []
        async def close(self):
            closed.append(self.name)
    monkeypatch.setattr(mcp, 'McpToolset', FakeServer)
    async def check():
        with pytest.raises(RuntimeError, match='Cannot connect'):
            async with mcp.connect_mcp_servers(path, set()):
                pytest.fail('Connection should fail')
    asyncio.run(check())
    assert closed == ['broken', 'first']


def test_duplicate_server_names_fail_before_connecting(tmp_path):
    path = tmp_path / 'mcp.toml'
    path.write_text('[[servers]]\nname="same"\ncommand="one"\n[[servers]]\nname="same"\ncommand="two"\n')
    async def check():
        with pytest.raises(ValueError, match='unique'):
            async with mcp.connect_mcp_servers(path, set()):
                pytest.fail('Duplicate names should fail')
    asyncio.run(check())
