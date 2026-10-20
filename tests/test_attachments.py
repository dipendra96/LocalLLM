"""Image validation, artifact references, API isolation, and optional real vision."""
import asyncio
import os
from dataclasses import replace
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from conftest import FakeModel, create
from localchat import main
from localchat.agent.runtime import AgentRuntime
from localchat.api import routes
from localchat.config import settings
from localchat.storage.attachments import AttachmentError, validate_image
from localchat.storage.conversations import save_conversation, fetch_conversation


def image_bytes(format='PNG'):
    buffer = BytesIO()
    image = Image.new('RGB', (256, 256), 'white')
    ImageDraw.Draw(image).rectangle((24, 24, 232, 232), fill='red')
    image.save(buffer, format=format)
    return buffer.getvalue()


def upload(client, identifier, data=None, filename='picture.png'):
    return client.post('/attachments', data={'conversation_id': identifier},
                       files={'file': (filename, image_bytes() if data is None else data, 'image/png')})


@pytest.fixture
def vision(monkeypatch):
    async def details(model):
        return {'capabilities': ['completion', 'vision', 'tools']}
    monkeypatch.setattr(routes, 'fetch_model_details', details)


@pytest.mark.parametrize('format,mime', [('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('WEBP', 'image/webp')])
def test_supported_formats_and_preview(client, format, mime):
    identifier = create(client)
    data = image_bytes(format)
    result = upload(client, identifier, data, '../../picture')
    assert result.status_code == 201, result.text
    attachment = result.json()
    assert attachment['mime_type'] == mime  # Decoded format wins over supplied MIME.
    assert attachment['filename'] == 'picture'
    assert attachment['width'] == attachment['height'] == 256
    response = client.get(attachment['url'])
    assert response.content == data
    assert response.headers['content-type'] == mime
    assert response.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.parametrize('data,status', [(b'', 422), (b'not an image', 422),
    (b'<svg></svg>', 422), (b'x' * (10 * 1024 * 1024 + 1), 413)])
def test_invalid_images(client, data, status):
    assert upload(client, create(client), data).status_code == status


def test_pixel_limit():
    buffer = BytesIO()
    Image.new('1', (5000, 4001)).save(buffer, format='PNG')
    with pytest.raises(AttachmentError) as error:
        validate_image(buffer.getvalue())
    assert error.value.status == 413


def test_animation_rejected(client):
    buffer = BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, format='PNG', save_all=True,
        append_images=[Image.new('RGB', (4, 4), 'blue')], duration=100, loop=0)
    assert upload(client, create(client), buffer.getvalue()).status_code == 415


def test_missing_conversation_and_cross_chat_attachment(client, vision):
    assert upload(client, str(uuid4())).status_code == 404
    first, second = create(client), create(client)
    attachment = upload(client, first).json()
    assert client.get(f"/conversations/{second}/attachments/{attachment['id']}").status_code == 404
    for endpoint in ['/chat', '/chat/stream']:
        result = client.post(endpoint, json={'conversation_id': second, 'message': 'Describe this image',
                                           'attachment_ids': [attachment['id']]})
        assert result.status_code == 404
    assert not client.model._requests


@pytest.mark.parametrize('endpoint', ['/chat', '/chat/stream'])
def test_image_model_capability_preflight(client, monkeypatch, endpoint):
    identifier = create(client)
    attachment = upload(client, identifier).json()
    async def details(model):
        return {'capabilities': ['completion']}
    monkeypatch.setattr(routes, 'fetch_model_details', details)
    response = client.post(endpoint, json={'conversation_id': identifier, 'message': 'Describe the image',
                                          'attachment_ids': [attachment['id']]})
    assert response.status_code == 422
    assert not client.model._requests
    assert client.get(f'/conversations/{identifier}').json()['messages'] == []


def test_capability_lookup_failure(client, monkeypatch):
    identifier = create(client)
    attachment = upload(client, identifier).json()
    async def offline(model):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(routes, 'fetch_model_details', offline)
    response = client.post('/chat', json={'conversation_id': identifier, 'message': 'Describe',
                                         'attachment_ids': [attachment['id']]})
    assert response.status_code == 503
    assert not client.model._requests


def test_image_chat_stream_and_followup_do_not_inline_history(client, vision):
    identifier = create(client)
    attachment = upload(client, identifier).json()
    response = client.post('/chat/stream', json={'conversation_id': identifier, 'message': 'Describe the image',
                                                'attachment_ids': [attachment['id'], attachment['id']]})
    from test_streaming import decode
    assert decode(response)[-1]['type'] == 'done', response.text
    request = client.model._requests[-1]
    images = [part for content in request.contents for part in content.parts or [] if part.inline_data]
    assert len(images) == 1 and images[0].inline_data.data == image_bytes()
    assert client.post('/chat', json={'conversation_id': identifier, 'message': 'Explain more'}).status_code == 200
    assert any(part.inline_data for content in client.model._requests[-1].contents for part in content.parts or [])
    history = client.get(f'/conversations/{identifier}').json()['messages']
    assert history[0]['attachments'] == [{key: attachment[key] for key in ['id', 'mime_type', 'url']}]
    async def read():
        return await client.app.state.runtime.sessions.get_session(app_name='localchat', user_id='local', session_id=identifier)
    session = client.portal.call(read)
    assert not any(part.inline_data for event in session.events if event.content for part in event.content.parts or [])
    assert any(part.file_data for event in session.events if event.content for part in event.content.parts or [])
    assert client.delete(f'/conversations/{identifier}').status_code == 204
    assert client.get(attachment['url']).status_code == 404
    async def keys():
        return await client.app.state.runtime.attachments.service.list_artifact_keys(
            app_name='localchat', user_id='local', session_id=identifier)
    assert client.portal.call(keys) == []


def test_attachment_request_validation(client):
    identifier = create(client)
    assert client.post('/chat', json={'conversation_id': identifier, 'message': 'Describe',
                                     'attachment_ids': ['../../file']}).status_code == 422
    assert client.post('/chat', json={'conversation_id': identifier, 'message': 'Describe',
                                     'attachment_ids': [str(uuid4()) for _ in range(5)]}).status_code == 422


def test_images_survive_restart(tmp_path):
    async def check():
        config = replace(settings, data_dir=tmp_path)
        runtime = AgentRuntime(config, model=FakeModel())
        identifier = (await save_conversation(runtime.sessions, 'Images'))['id']
        attachment = await runtime.attachments.save(identifier, image_bytes(), 'picture.png')
        await runtime.reply(identifier, 'Describe', attachment_ids=[attachment['id']])
        await runtime.close()
        model = FakeModel()
        runtime = AgentRuntime(config, model=model)
        try:
            await runtime.reply(identifier, 'What else is visible?')
            assert any(p.inline_data for c in model._requests[-1].contents for p in c.parts or [])
            assert (await fetch_conversation(runtime.sessions, identifier))['messages'][0]['attachments'][0]['id'] == attachment['id']
        finally:
            await runtime.close()
    asyncio.run(check())


@pytest.mark.skipif(os.getenv('LOCALCHAT_VISION_LIVE') != '1', reason='Opt-in Ollama vision')
def test_live_image_understanding(monkeypatch, tmp_path):
    config = tmp_path / 'mcp.toml'
    config.write_text('servers = []\n')
    monkeypatch.setattr(main, 'settings', replace(main.settings, data_dir=tmp_path, mcp_config=config))
    with TestClient(main.app) as client:
        identifier = create(client, 'Vision verification')
        attachment = upload(client, identifier).json()
        response = client.post('/chat/stream', json={'conversation_id': identifier,
            'message': 'What color is the large square in the image? Reply only with its color.',
            'attachment_ids': [attachment['id']]})
        from test_streaming import decode
        events = decode(response)
        assert events[-1]['type'] == 'done', events
        assert 'red' in events[-1]['reply'].lower(), events[-1]
        assert client.get(attachment['url']).content == image_bytes()


def test_delete_removes_only_target_conversation_directory(client):
    deleted = create(client)
    retained = create(client)
    upload(client, deleted)
    remaining = upload(client, retained).json()
    root = client.app.state.runtime.attachments.service.root_dir
    sessions = root / 'apps' / 'localchat' / 'users' / 'local' / 'sessions'
    assert (sessions / deleted).is_dir()
    assert client.delete(f'/conversations/{deleted}').status_code == 204
    assert not (sessions / deleted).exists()
    assert (sessions / retained).is_dir()
    assert client.get(remaining['url']).content == image_bytes()


def test_cleanup_preserves_unexpected_files(client):
    identifier = create(client)
    upload(client, identifier)
    root = client.app.state.runtime.attachments.service.root_dir
    directory = root / 'apps' / 'localchat' / 'users' / 'local' / 'sessions' / identifier
    extra = directory / 'keep.txt'
    extra.write_text('preserve')
    assert client.delete(f'/conversations/{identifier}').status_code == 204
    assert extra.read_text() == 'preserve'
