from datetime import datetime
from uuid import UUID, uuid4

import httpx
import pytest
from litellm import APIConnectionError, APIError, Timeout

from localchat.api import routes
from conftest import create


def test_health_and_documentation(client):
    assert client.get('/health').json() == {'status': 'ok'}
    assert client.get('/docs').status_code == 200
    assert set(client.get('/openapi.json').json()['paths']) == {
        '/health', '/chat', '/chat/stream', '/models', '/models/selected',
        '/capabilities', '/conversations', '/conversations/{conversation_id}',
        '/attachments', '/conversations/{conversation_id}/attachments/{attachment_id}',
    }


def test_capabilities(client):
    data = client.get('/capabilities').json()
    assert data['skills'][0]['name'] == 'web-research'
    assert data['skills'][0]['required_tools'] == ['search', 'fetch_content']
    assert 'load_skill' in [tool['name'] for tool in data['tools']]


def test_conversation_lifecycle(client):
    assert client.get('/conversations').json() == {'conversations': []}
    first = client.post('/conversations', json={}).json()
    UUID(first['id'])
    assert first['title'] == 'New chat'
    assert datetime.fromisoformat(first['created_at']).utcoffset().total_seconds() == 0
    second = create(client, 'Second')
    assert [c['id'] for c in client.get('/conversations').json()['conversations']] == [second, first['id']]
    assert client.get(f"/conversations/{first['id']}").json() == {**first, 'messages': []}
    response = client.delete(f"/conversations/{first['id']}")
    assert response.status_code == 204 and response.content == b''
    assert client.get(f"/conversations/{first['id']}").status_code == 404
    assert client.delete(f"/conversations/{first['id']}").status_code == 404


@pytest.mark.parametrize('title', ['', 'x' * 201, None, 123])
def test_invalid_title(client, title):
    assert client.post('/conversations', json={'title': title}).status_code == 422


@pytest.mark.parametrize('method', ['get', 'delete'])
@pytest.mark.parametrize('identifier,status', [('invalid', 422), (str(uuid4()), 404)])
def test_missing_conversation(client, method, identifier, status):
    assert getattr(client, method)(f'/conversations/{identifier}').status_code == status


@pytest.mark.parametrize('body', [{}, {'message': 'Hello'},
    {'conversation_id': 'invalid', 'message': 'Hello'},
    {'conversation_id': str(uuid4()), 'message': ''},
    {'conversation_id': str(uuid4()), 'message': None}])
def test_chat_validation(client, body):
    assert client.post('/chat', json=body).status_code == 422


@pytest.mark.parametrize('endpoint', ['/chat', '/chat/stream'])
def test_missing_chat_never_calls_model(client, endpoint):
    assert client.post(endpoint, json={'conversation_id': str(uuid4()), 'message': 'Hello'}).status_code == 404
    assert client.model._requests == []


def test_adk_history_isolation_and_deletion(client):
    first, second = create(client), create(client)
    for identifier, message, answer in [(first, 'Remember ORCHID', 'ORCHID'),
                                       (first, 'What word?', 'ORCHID'),
                                       (second, 'Hello', 'Hello!')]:
        result = client.post('/chat', json={'conversation_id': identifier, 'message': message})
        assert result.status_code == 200, result.text
        assert result.json() == {'conversation_id': identifier, 'reply': answer}
    history = client.get(f'/conversations/{first}').json()['messages']
    assert [m['role'] for m in history] == ['user', 'assistant', 'user', 'assistant']
    assert len({m['id'] for m in history}) == 4
    assert all(m['conversation_id'] == first for m in history)
    assert len(client.model._requests[1].contents) == 3
    assert len(client.model._requests[2].contents) == 1
    assert client.delete(f'/conversations/{first}').status_code == 204
    assert len(client.get(f'/conversations/{second}').json()['messages']) == 2


@pytest.mark.parametrize('error,status', [('timeout', 504), ('connection', 503), ('rejected', 502)])
def test_upstream_failure(client, monkeypatch, error, status):
    async def fail(*args, **kwargs):
        if error == 'timeout':
            raise Timeout(message='slow', model='test', llm_provider='ollama')
        if error == 'connection':
            raise APIConnectionError(message='offline', model='test', llm_provider='ollama')
        raise APIError(status_code=400, message='rejected', model='test', llm_provider='ollama')
    monkeypatch.setattr(client.app.state.runtime, 'reply', fail)
    assert client.post('/chat', json={'conversation_id': create(client), 'message': 'Hello'}).status_code == status


def test_model_endpoints(client, monkeypatch):
    async def models():
        return {'models': [{'name': 'gemma4:e2b'}]}
    async def details(model):
        return {'capabilities': ['completion', 'tools'], 'details': {'family': 'gemma4'}}
    monkeypatch.setattr(routes, 'fetch_models', models)
    monkeypatch.setattr(routes, 'fetch_model_details', details)
    assert client.get('/models').json()['models'][0]['name'] == 'gemma4:e2b'
    assert client.get('/models/selected').json()['capabilities'] == ['completion', 'tools']


@pytest.mark.parametrize('endpoint,upstream,status', [('/models', None, 503),
    ('/models/selected', None, 503), ('/models/selected', 404, 404), ('/models/selected', 500, 502)])
def test_model_errors(client, monkeypatch, endpoint, upstream, status):
    async def fail(*args, **kwargs):
        if upstream is None:
            raise httpx.ConnectError('offline')
        httpx.Response(upstream, request=httpx.Request('POST', 'http://localhost/api/show')).raise_for_status()
    monkeypatch.setattr(routes, 'fetch_models', fail)
    monkeypatch.setattr(routes, 'fetch_model_details', fail)
    assert client.get(endpoint).status_code == status
