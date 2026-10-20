"""Opt-in real Ollama, session memory, streaming, and MCP research checks."""
import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from localchat import main
from conftest import create


@pytest.mark.skipif(os.getenv('LOCALCHAT_LIVE') != '1', reason='Opt-in Ollama integration')
def test_live_all_endpoints_and_memory(monkeypatch, tmp_path):
    config = tmp_path / 'mcp.toml'
    config.write_text('servers = []\n')
    monkeypatch.setattr(main, 'settings', replace(main.settings, data_dir=tmp_path, mcp_config=config))
    with TestClient(main.app) as client:
        assert client.get('/models').status_code == 200
        assert client.get('/models/selected').status_code == 200
        identifier = create(client)
        for message in ['Remember the reference word ORCHID for this conversation. It is a test label. Reply briefly.', 'What reference word did I give you? Reply only with the word.']:
            result = client.post('/chat', json={'conversation_id': identifier, 'message': message})
            assert result.status_code == 200, result.text
        assert 'orchid' in result.json()['reply'].lower()
        assert len(client.get(f'/conversations/{identifier}').json()['messages']) == 4
        response = client.post('/chat/stream', json={'conversation_id': identifier, 'message': 'Say hello briefly.'})
        from test_streaming import decode
        events = decode(response)
        assert events[-1]['type'] == 'done'
        assert any(e['type'] == 'text_delta' for e in events)
        assert client.delete(f'/conversations/{identifier}').status_code == 204


@pytest.mark.skipif(os.getenv('LOCALCHAT_WEB_LIVE') != '1', reason='Opt-in public web and Ollama integration')
def test_live_web_research(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'settings', replace(main.settings, data_dir=tmp_path))
    with TestClient(main.app) as client:
        identifier = create(client)
        response = client.post('/chat/stream', json={
            'conversation_id': identifier,
            'requested_skills': ['web-research'],
            'message': 'Call search to find Python 3.15.0 release date. Then call fetch_content to read https://www.python.org/downloads/release/python-3150/ . Answer with the release date and source URL.',
        })
        from test_streaming import decode
        events = decode(response)
        assert events[-1]['type'] == 'done', events
        assert events[1] == {'type': 'skill_loaded', 'name': 'web-research'}
        starts = [e for e in events if e['type'] == 'tool_started']
        ends = [e for e in events if e['type'] == 'tool_finished']
        assert {'search', 'fetch_content'} <= {e['name'] for e in starts}
        assert {e['call_id'] for e in starts} == {e['call_id'] for e in ends}
        assert 'python.org' in events[-1]['reply']
