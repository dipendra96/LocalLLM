"""Exercise the real ADK runner and SQLite service without network inference."""
from dataclasses import replace
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from localchat import main
from localchat.agent.runtime import AgentRuntime


class FakeModel(BaseLlm):
    model: str = "offline-test"
    _requests: list = PrivateAttr(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self._requests.append(deepcopy(llm_request))
        text = " ".join((p.text or "") + (str(p.function_response.response) if p.function_response else "") for c in llm_request.contents for p in c.parts or [])
        answer = "ORCHID" if "ORCHID" in text else "Hello!"
        if stream:
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(text="hidden thought", thought=True), types.Part(text=answer),
            ]), partial=True)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=answer)]),
                          partial=False, turn_complete=True)


@pytest.fixture
def client(monkeypatch, tmp_path):
    config = tmp_path / "mcp.toml"
    config.write_text("servers = []\n")
    monkeypatch.setattr(main, "settings", replace(main.settings, data_dir=tmp_path, mcp_config=config))
    model = FakeModel()
    def build(*args, **kwargs):
        return AgentRuntime(*args, **kwargs, model=model)
    monkeypatch.setattr(main, "AgentRuntime", build)
    with TestClient(main.app) as client:
        client.model = model
        yield client


def create(client, title="Test chat"):
    result = client.post("/conversations", json={"title": title})
    assert result.status_code == 201, result.text
    return result.json()["id"]
