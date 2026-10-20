import asyncio
import json
from dataclasses import replace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from conftest import FakeModel, create
from localchat.agent.runtime import AgentRuntime
from localchat.config import settings
from localchat.storage.conversations import save_conversation, fetch_conversation
from localchat.registry.catalog import Registry
from google.adk.tools import FunctionTool


def decode(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_sse_text_final_and_saved_history(client):
    identifier = create(client)
    response = client.post('/chat/stream', json={'conversation_id': identifier, 'message': 'Hello'})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    events = decode(response)
    assert [e['type'] for e in events] == ['started', 'status', 'model_selected', 'status', 'text_delta', 'done']
    assert events[-1]['reply'] == 'Hello!'
    assert 'hidden thought' not in response.text
    assert len(client.get(f'/conversations/{identifier}').json()['messages']) == 2


def test_sse_error_does_not_emit_done(client, monkeypatch):
    async def fail(*args, **kwargs):
        yield {'type': 'text_delta', 'text': 'partial'}
        raise RuntimeError('sensitive error')
    monkeypatch.setattr(client.app.state.runtime, 'stream', fail)
    events = decode(client.post('/chat/stream', json={'conversation_id': create(client), 'message': 'Hello'}))
    assert events[-1]['type'] == 'error'
    assert not any(e['type'] == 'done' for e in events)
    assert 'sensitive error' not in str(events)


def test_same_conversation_turns_serialize(client):
    runtime = client.app.state.runtime
    async def check():
        entered = asyncio.Event()
        release = asyncio.Event()
        order = []
        async def first():
            async with runtime.lock('one'):
                order.append('first')
                entered.set()
                await release.wait()
        async def second():
            await entered.wait()
            async with runtime.lock('one'):
                order.append('second')
        a, b = asyncio.create_task(first()), asyncio.create_task(second())
        await entered.wait()
        await asyncio.sleep(0)
        assert order == ['first']
        assert not runtime.lock('other').locked()
        release.set()
        await asyncio.gather(a, b)
        assert order == ['first', 'second']
    asyncio.run(check())


def test_tool_events_precede_execution_and_results_persist(tmp_path):
    markers = []
    def lookup(query: str) -> dict:
        """Look up a test fact."""
        markers.append('executed')
        return {'fact': 'ORCHID'}
    class ToolModel(FakeModel):
        async def generate_content_async(self, llm_request, stream=False):
            if any(p.function_response for c in llm_request.contents for p in c.parts or []):
                async for result in super().generate_content_async(llm_request, stream):
                    yield result
            else:
                call = types.Part(function_call=types.FunctionCall(name='lookup', id='lookup-1', args={'query': 'word'}))
                yield LlmResponse(content=types.Content(role='model', parts=[call]), partial=False)
    async def check():
        runtime = AgentRuntime(replace(settings, data_dir=tmp_path),
                               registry=Registry([FunctionTool(lookup)], {}), model=ToolModel())
        try:
            identifier = (await save_conversation(runtime.sessions, 'Tools'))['id']
            events = []
            async for event in runtime.stream(identifier, 'Look up the word'):
                if event['type'] == 'tool_started':
                    assert markers == []
                events.append(event)
            starts = [e for e in events if e['type'] == 'tool_started']
            ends = [e for e in events if e['type'] == 'tool_finished']
            assert len(starts) == len(ends) == 1
            assert starts[0]['call_id'] == ends[0]['call_id']
            session = await runtime.sessions.get_session(app_name='localchat', user_id='local', session_id=identifier)
            assert any(p.function_response for e in session.events if e.content for p in e.content.parts or [])
            assert events[-1]['reply'] == 'ORCHID'
        finally:
            await runtime.close()
    asyncio.run(check())


def test_stream_close_releases_model_and_keeps_partial_history(tmp_path):
    closed = []
    class WaitingModel(FakeModel):
        async def generate_content_async(self, llm_request, stream=False):
            try:
                yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='partial')]), partial=True)
                await asyncio.Event().wait()
            finally:
                closed.append(True)
    async def check():
        runtime = AgentRuntime(replace(settings, data_dir=tmp_path), model=WaitingModel())
        try:
            identifier = (await save_conversation(runtime.sessions, 'Cancel'))['id']
            stream = runtime.stream(identifier, 'Hello')
            async for event in stream:
                if event['type'] == 'text_delta':
                    break
            await stream.aclose()
            # ADK/model generators unwind asynchronously on cancellation.
            for _ in range(20):
                if closed:
                    break
                await asyncio.sleep(0)
            assert closed
            history = await fetch_conversation(runtime.sessions, identifier)
            assert [m['role'] for m in history['messages']] == ['user']
        finally:
            await runtime.close()
    asyncio.run(check())
