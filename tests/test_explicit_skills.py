import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from localchat import main
from localchat.agent.runtime import AgentRuntime
from conftest import FakeModel, create


@pytest.mark.parametrize('endpoint', ['/chat', '/chat/stream'])
@pytest.mark.parametrize('name', ['unknown', 'web-research'])
def test_invalid_or_unavailable_skill_prevents_generation(client, endpoint, name):
    identifier = create(client)
    result = client.post(endpoint, json={'conversation_id': identifier, 'message': 'Research', 'requested_skills': [name]})
    assert result.status_code == 422
    assert client.model._requests == []
    assert client.get(f'/conversations/{identifier}').json()['messages'] == []


def test_explicit_skill_load_and_request_scope(client):
    runtime = client.app.state.runtime
    runtime.tool_names.update({'search', 'fetch_content'})
    identifier = create(client)
    result = client.post('/chat', json={'conversation_id': identifier, 'message': 'Hello',
                                      'requested_skills': ['web-research', 'web-research']})
    assert result.status_code == 200, result.text
    prompt = str(client.model._requests[-1].config.system_instruction)
    assert 'Explicitly loaded skill: web-research' in prompt
    assert 'Never invent search results' in prompt
    assert client.post('/chat', json={'conversation_id': identifier, 'message': 'Hello again'}).status_code == 200
    # The explicit selection flag is per invocation; ADK may retain activated skills.
    assert 'Explicitly loaded skill: web-research' not in str(client.model._requests[-1].config.system_instruction)
    assert 'Active skill: web-research' in str(client.model._requests[-1].config.system_instruction)


def test_sqlite_survives_runtime_restart(tmp_path):
    config = replace(main.settings, data_dir=tmp_path)
    from localchat.storage.conversations import save_conversation, fetch_conversation
    async def check():
        first = AgentRuntime(config, model=FakeModel())
        conversation = await save_conversation(first.sessions, 'Persistent')
        await first.reply(conversation['id'], 'Remember ORCHID')
        await first.close()
        second = AgentRuntime(config, model=FakeModel())
        try:
            assert len((await fetch_conversation(second.sessions, conversation['id']))['messages']) == 2
            assert await second.reply(conversation['id'], 'What word?') == 'ORCHID'
        finally:
            await second.close()
    asyncio.run(check())
