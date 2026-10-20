"""Exercise ADK's real compactor rather than mocking session summaries."""
import asyncio
import os
from copy import deepcopy
from dataclasses import replace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from conftest import FakeModel
from localchat.agent.runtime import AgentRuntime
from localchat.config import settings
from localchat.storage.conversations import save_conversation, fetch_conversation


class SummaryModel(FakeModel):
    async def generate_content_async(self, llm_request, stream=False):
        self._requests.append(deepcopy(llm_request))
        text = ' '.join(p.text or '' for c in llm_request.contents for p in c.parts or [])
        if not stream:
            answer = 'Conversation Language: English. Reference word: ORCHID. No tools used.'
        else:
            answer = 'ORCHID' if 'ORCHID' in text else 'Hello!'
        yield LlmResponse(
            content=types.Content(role='model', parts=[types.Part(text=answer)]),
            partial=False, turn_complete=True,
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=max(1, len(text) // 4),
            ),
        )


@pytest.mark.parametrize('field', ['compaction_tokens', 'compaction_retention'])
def test_negative_configuration_rejected(field):
    with pytest.raises(ValueError, match='must be zero or positive'):
        replace(settings, **{field: -1})


def test_compaction_reduces_model_history_and_survives_restart(tmp_path):
    config = replace(settings, data_dir=tmp_path, compaction_tokens=200, compaction_retention=2)
    async def check():
        model = SummaryModel()
        runtime = AgentRuntime(config, model=model)
        identifier = (await save_conversation(runtime.sessions, 'Compaction'))['id']
        first_message = 'Reference word: ORCHID. OLDFILLER ' + ('irrelevant trees and rivers. ' * 100)
        try:
            await runtime.reply(identifier, first_message)
            await runtime.reply(identifier, 'Continue briefly.')
            session = await runtime.sessions.get_session(app_name='localchat', user_id='local', session_id=identifier)
            summaries = [e for e in session.events if e.actions.compaction]
            assert summaries
            assert 'ORCHID' in str(summaries[-1].actions.compaction.compacted_content)
            # Compaction changes prompt assembly, not the original transcript.
            assert (await fetch_conversation(runtime.sessions, identifier))['messages'][0]['content'] == first_message
        finally:
            await runtime.close()
        next_model = SummaryModel()
        runtime = AgentRuntime(config, model=next_model)
        try:
            assert await runtime.reply(identifier, 'What reference word did I give you?') == 'ORCHID'
            request = next_model._requests[-1]
            prompt = str(request.contents)
            assert 'ORCHID' in prompt
            assert 'OLDFILLER' not in prompt
            assert len(prompt) < len(first_message)
        finally:
            await runtime.close()
    asyncio.run(check())


def test_disabled_compaction_preserves_raw_model_history(tmp_path):
    async def check():
        model = SummaryModel()
        runtime = AgentRuntime(replace(settings, data_dir=tmp_path, compaction_tokens=0), model=model)
        try:
            identifier = (await save_conversation(runtime.sessions, 'Disabled'))['id']
            await runtime.reply(identifier, 'ORCHID ' + 'OLDFILLER ' * 400)
            await runtime.reply(identifier, 'Recall the word.')
            session = await runtime.sessions.get_session(app_name='localchat', user_id='local', session_id=identifier)
            assert not any(e.actions.compaction for e in session.events)
            assert 'OLDFILLER' in str(model._requests[-1].contents)
        finally:
            await runtime.close()
    asyncio.run(check())


def test_summarizer_failure_does_not_fail_chat(tmp_path):
    class FailingSummarizer(SummaryModel):
        async def generate_content_async(self, llm_request, stream=False):
            if not stream:
                raise RuntimeError('summary failed')
            async for result in super().generate_content_async(llm_request, stream):
                yield result
    async def check():
        runtime = AgentRuntime(replace(settings, data_dir=tmp_path, compaction_tokens=200,
                                       compaction_retention=2), model=FailingSummarizer())
        try:
            identifier = (await save_conversation(runtime.sessions, 'Failure'))['id']
            await runtime.reply(identifier, 'ORCHID ' + 'trees and rivers. ' * 100)
            assert await runtime.reply(identifier, 'Recall the word.') == 'ORCHID'
            session = await runtime.sessions.get_session(app_name='localchat', user_id='local', session_id=identifier)
            assert not any(e.actions.compaction for e in session.events)
        finally:
            await runtime.close()
    asyncio.run(check())


@pytest.mark.skipif(os.getenv('LOCALCHAT_LIVE') != '1', reason='Opt-in real Gemma summarization')
def test_live_compaction_preserves_reference_word(tmp_path):
    async def check():
        # Force a small threshold in an isolated session to exercise the feature.
        runtime = AgentRuntime(replace(settings, data_dir=tmp_path,
                                       compaction_tokens=400, compaction_retention=2))
        try:
            identifier = (await save_conversation(runtime.sessions, 'Live compaction'))['id']
            await runtime.reply(identifier,
                'Remember the reference word ORCHID. It is a test label. '
                + 'Unimportant background: trees grow beside rivers. ' * 80
                + ' Reply briefly and remember the reference word.')
            await runtime.reply(identifier, 'My favorite color is indigo. Reply briefly.')
            session = await runtime.sessions.get_session(app_name='localchat', user_id='local', session_id=identifier)
            summaries = [e for e in session.events if e.actions.compaction]
            assert summaries, 'No summary was persisted'
            assert 'ORCHID' in str(summaries[-1].actions.compaction.compacted_content)
            answer = await runtime.reply(identifier, 'What reference word did I give you? Reply only with the word.')
            assert 'orchid' in answer.lower(), answer
        finally:
            await runtime.close()
    asyncio.run(check())
