"""Model selection and shared-agent coordination without network inference."""
import asyncio
from dataclasses import replace

import httpx
import pytest

from conftest import FakeModel, create
from localchat.api import routes
from localchat.config import settings
from localchat.models import manager
from localchat.models.manager import ModelManager, ModelSelection, ModelSelectionError


def details(limit=131072, caps=('completion','tools','vision')):
    return {'capabilities': list(caps), 'model_info': {'general.architecture':'test', 'test.context_length':limit}}


def test_context_and_compaction_capped_for_smaller_models(monkeypatch):
    async def fetch(name):
        return details(8192)
    monkeypatch.setattr(manager, 'fetch_model_details', fetch)
    async def check():
        selection = await ModelManager(settings).resolve('small:latest')
        assert selection.context_tokens == 8192
        assert selection.compaction_tokens == 4096
        selection = await ModelManager(replace(settings, compaction_tokens=0)).resolve('small:latest')
        assert selection.compaction_tokens == 0
    asyncio.run(check())


def test_model_maximum_does_not_raise_requested_context(monkeypatch):
    async def fetch(name):
        return details(262144)
    monkeypatch.setattr(manager, 'fetch_model_details', fetch)
    async def check():
        selection = await ModelManager(settings).resolve('larger')
        assert selection.context_tokens == 131072
        assert selection.compaction_tokens == 50000
    asyncio.run(check())


@pytest.mark.parametrize('kind,status', [('missing',422), ('offline',503), ('embedding',422)])
def test_invalid_selection(monkeypatch, kind, status):
    async def fetch(name):
        if kind == 'offline': raise httpx.ConnectError('offline')
        if kind == 'missing': raise httpx.HTTPStatusError('missing', request=httpx.Request('POST','http://localhost/api/show'), response=httpx.Response(404))
        return details(caps=('embedding',))
    monkeypatch.setattr(manager, 'fetch_model_details', fetch)
    async def check():
        with pytest.raises(ModelSelectionError) as error:
            await ModelManager(settings).resolve('bad')
        assert error.value.status == status
    asyncio.run(check())


def test_switch_waits_for_generation_and_reuses_adapter(monkeypatch):
    created = []
    def build(**kwargs):
        created.append(kwargs)
        return FakeModel(model=kwargs['model'])
    monkeypatch.setattr(manager, 'LiteLlm', build)
    async def check():
        instance = ModelManager(settings)
        first = ModelSelection(settings.model,131072,50000)
        second = ModelSelection('other:latest',8192,4096)
        entered, release = asyncio.Event(), asyncio.Event()
        async def one():
            async with instance.use(first) as model:
                assert model.model == f'ollama_chat/{settings.model}'
                entered.set()
                await release.wait()
        async def two():
            await entered.wait()
            async with instance.use(second) as model:
                assert model.model == 'ollama_chat/other:latest'
        a,b = asyncio.create_task(one()),asyncio.create_task(two())
        await entered.wait()
        await asyncio.sleep(0)
        assert len(created) == 1  # The second turn cannot change the active adapter.
        release.set()
        await asyncio.gather(a,b)
        assert len(created) == 2
        assert created[-1]['num_ctx'] == 8192
        async with instance.use(second): pass
        assert len(created) == 2  # Selecting the same model reuses the client.
    asyncio.run(check())


def test_cancelled_waiter_preserves_active_adapter():
    async def check():
        instance = ModelManager(settings, model_override=FakeModel())
        selection = ModelSelection(settings.model,131072,50000)
        async with instance.use(selection):
            async def wait():
                async with instance.use(ModelSelection('other',4096,2048)): pass
            task = asyncio.create_task(wait())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        assert not instance.lock.locked()
        assert instance._active == selection
        assert instance._llm is instance._override
    asyncio.run(check())


def test_request_model_is_saved_and_reused(client, monkeypatch):
    seen = []
    async def resolve(name=None):
        seen.append(name)
        return ModelSelection(name or settings.model,8192,4096,frozenset({'completion','tools','vision'}))
    monkeypatch.setattr(client.app.state.runtime.model_manager, 'resolve', resolve)
    identifier = create(client)
    response = client.post('/chat',json={'conversation_id':identifier,'message':'Hello','model':'other:latest'})
    assert response.status_code == 200, response.text
    assert client.get(f'/conversations/{identifier}').json()['model'] == 'other:latest'
    assert client.post('/chat',json={'conversation_id':identifier,'message':'Hello again'}).status_code == 200
    assert seen == ['other:latest','other:latest']


def test_text_only_model_rejects_images_in_retained_history(client, monkeypatch):
    from test_attachments import upload
    identifier = create(client)
    attachment = upload(client,identifier).json()
    async def vision(model): return details()
    monkeypatch.setattr(routes,'fetch_model_details',vision)
    assert client.post('/chat',json={'conversation_id':identifier,'message':'Describe','attachment_ids':[attachment['id']]}).status_code == 200
    before = len(client.model._requests)
    async def resolve(name=None): return ModelSelection('text-only',8192,4096,frozenset({'completion','tools'}))
    monkeypatch.setattr(client.app.state.runtime.model_manager,'resolve',resolve)
    for endpoint in ['/chat','/chat/stream']:
        response = client.post(endpoint,json={'conversation_id':identifier,'message':'Hello','model':'text-only'})
        assert response.status_code == 422, response.text
    assert len(client.model._requests) == before


def test_model_details_query_is_inspection_only(client, monkeypatch):
    seen = []
    async def fetch(name):
        seen.append(name)
        return details(8192)
    monkeypatch.setattr(routes,'fetch_model_details',fetch)
    response = client.get('/models/selected?model=other:latest')
    assert response.status_code == 200
    assert response.json()['context_tokens'] == 8192
    assert response.json()['compaction_tokens'] == 4096
    assert seen == ['other:latest']
    assert client.app.state.runtime.config.model == settings.model


def test_ui_is_served_without_changing_api_docs(client, tmp_path, monkeypatch):
    from localchat import main
    build = tmp_path / 'dist'
    build.mkdir()
    (build / 'index.html').write_text('<title>LocalChat</title><script src="/assets/app.js"></script>')
    (build / 'favicon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    monkeypatch.setattr(main, 'frontend_dist', build)
    response = client.get('/')
    assert response.status_code == 200
    assert 'LocalChat' in response.text
    assert '/assets/' in response.text
    assert client.get('/favicon.svg').status_code == 200
    assert client.get('/docs').status_code == 200


def test_switch_reaches_the_new_adk_model_not_a_cached_model(client, monkeypatch):
    runtime = client.app.state.runtime
    runtime.model_manager._override = None
    used = []
    def build(**kwargs):
        model = FakeModel(model=kwargs['model'])
        used.append(model)
        return model
    async def resolve(name=None):
        return ModelSelection(name or settings.model,8192,0,frozenset({'completion','tools'}))
    monkeypatch.setattr(manager,'LiteLlm',build)
    monkeypatch.setattr(runtime.model_manager,'resolve',resolve)
    identifier = create(client)
    for name in ['first:latest','second:latest']:
        result = client.post('/chat',json={'conversation_id':identifier,'message':'Hello','model':name})
        assert result.status_code == 200, result.text
    assert [model.model for model in used] == ['ollama_chat/first:latest','ollama_chat/second:latest']
    assert [len(model._requests) for model in used] == [1,1]
    assert len(used[1]._requests[0].contents) == 3  # The next model receives saved history.


def test_models_without_tools_can_chat_but_cannot_request_skills(client, monkeypatch):
    async def resolve(name=None):
        return ModelSelection(settings.model,8192,0,frozenset({'completion'}))
    monkeypatch.setattr(client.app.state.runtime.model_manager,'resolve',resolve)
    identifier = create(client)
    result = client.post('/chat',json={'conversation_id':identifier,'message':'Hello'})
    assert result.status_code == 200
    assert not client.model._requests[-1].tools_dict
    client.app.state.runtime.tool_names.update({'search','fetch_content'})
    result = client.post('/chat',json={'conversation_id':identifier,'message':'Research', 'requested_skills':['web-research']})
    assert result.status_code == 422
