"""Select Ollama models and protect shared agent configuration during turns."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from google.adk.models.lite_llm import LiteLlm

from localchat.models.ollama import fetch_model_details


class ModelSelectionError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


@dataclass(frozen=True)
class ModelSelection:
    name: str
    context_tokens: int
    compaction_tokens: int
    capabilities: frozenset[str] | None = None


class ModelManager:
    def __init__(self, config, model_override=None):
        self.config = config
        self.lock = asyncio.Lock()
        self._override = model_override
        self._active = None
        self._llm = model_override

    async def resolve(self, name=None):
        name = name or self.config.model
        if self._override is not None and name == self.config.model:
            return ModelSelection(name, self.config.context_tokens, self.config.compaction_tokens)
        try:
            details = await fetch_model_details(name)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ModelSelectionError(422, f"Model '{name}' is not installed in Ollama.") from exc
            raise ModelSelectionError(502, "Ollama could not inspect the selected model.") from exc
        except httpx.RequestError as exc:
            raise ModelSelectionError(503, "Cannot reach Ollama to inspect the selected model.") from exc
        return self.from_details(name, details)

    def from_details(self, name, details):
        caps = frozenset(details.get('capabilities', []))
        if 'completion' not in caps:
            raise ModelSelectionError(422, "Choose a chat/completion model, not an embedding-only model.")
        info = details.get('model_info', {})
        architecture = info.get('general.architecture')
        limit = info.get(f'{architecture}.context_length') if architecture else None
        if not isinstance(limit, int) or limit <= 0:
            limits = [v for k, v in info.items() if k.endswith('.context_length')
                      and isinstance(v, int) and v > 0 and 'vision' not in k and 'audio' not in k]
            limit = min(limits) if limits else self.config.context_tokens
        context = min(self.config.context_tokens, limit)
        threshold = min(self.config.compaction_tokens, max(1, context // 2)) if self.config.compaction_tokens else 0
        return ModelSelection(name, context, threshold, caps)

    @asynccontextmanager
    async def use(self, selection):
        async with self.lock:
            if self._active != selection or self._llm is None:
                self._llm = self._override or LiteLlm(
                    model=f'ollama_chat/{selection.name}', api_base=self.config.ollama_url,
                    reasoning_effort='none', temperature=0, timeout=120,
                    num_ctx=selection.context_tokens,
                )
                self._active = selection
            # Ollama manages model loading and residency on inference requests.
            yield self._llm
