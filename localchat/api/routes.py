"""FastAPI contracts; ADK owns agent execution and persistence."""

import asyncio
import json
import logging
from contextlib import aclosing
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, File, Form
from pydantic import BaseModel, Field
from litellm import APIConnectionError, APIError, Timeout
from fastapi.responses import StreamingResponse

from localchat.storage.attachments import AttachmentError, MAX_IMAGE_BYTES
from localchat.config import settings
from localchat.models.ollama import fetch_model_details, fetch_models
from localchat.models.manager import ModelSelectionError
from localchat.registry.catalog import SkillLoadError
from localchat.storage.conversations import (
    fetch_conversation,
    fetch_conversations,
    remove_conversation,
    save_conversation,
)

router = APIRouter()

@router.get("/health", tags=["System"])
def health():
    return {"status": "ok"}


class ChatRequest(BaseModel):
    conversation_id: UUID
    message: str = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    requested_skills: list[str] = Field(default_factory=list)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=4)


def generation_error(exc):
    if isinstance(exc, ModelSelectionError):
        return exc.status, str(exc)
    if isinstance(exc, Timeout):
        return 504, "The model took too long to respond."
    if isinstance(exc, APIConnectionError):
        return 503, "Cannot reach Ollama. Check that it is running."
    if isinstance(exc, APIError):
        return 502, "Ollama rejected the generation request."
    return 500, "The response could not be completed. Check the server logs."


async def check_conversation(runtime, identifier):
    if await fetch_conversation(runtime.sessions, identifier) is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")


def check_skills(runtime, names):
    try:
        return runtime.prepare_skills(names)
    except SkillLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def check_attachments(runtime, identifier, ids, selection):
    if not ids and not await runtime.has_retained_images(identifier):
        return
    try:
        await runtime.attachments.references(identifier, ids)
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    if selection.capabilities is not None:
        if "vision" not in selection.capabilities:
            raise HTTPException(status_code=422, detail="The selected model does not support images still present in this conversation. Choose a vision model or start a new chat.")
        return
    try:
        details = await fetch_model_details(selection.name)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Cannot check the model's image support in Ollama.") from exc
    if "vision" not in details.get("capabilities", []):
        raise HTTPException(status_code=422, detail="The selected model does not support images.")


async def select_model(runtime, identifier, name, requested_skills):
    conversation = await fetch_conversation(runtime.sessions, identifier)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    try:
        selection = await runtime.model_manager.resolve(name or conversation.get("model"))
    except ModelSelectionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    if requested_skills and selection.capabilities is not None and 'tools' not in selection.capabilities:
        raise HTTPException(status_code=422, detail="The selected model does not support tools or skills.")
    return selection


@router.post("/attachments", tags=["Attachments"], status_code=201)
async def upload_attachment(request: Request, conversation_id: UUID = Form(...), file: UploadFile = File(...)):
    """Upload a PNG, JPEG, or WebP into an existing conversation."""
    runtime = request.app.state.runtime
    identifier = str(conversation_id)
    try:
        async with runtime.lock(identifier):
            await check_conversation(runtime, identifier)
            data = await file.read(MAX_IMAGE_BYTES + 1)
            try:
                return await runtime.attachments.save(identifier, data, file.filename)
            except AttachmentError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    finally:
        await file.close()


@router.get("/conversations/{conversation_id}/attachments/{attachment_id}", tags=["Attachments"])
async def get_attachment(request: Request, conversation_id: UUID, attachment_id: UUID):
    runtime = request.app.state.runtime
    async with runtime.lock(str(conversation_id)):
        await check_conversation(runtime, str(conversation_id))
        try:
            part = await runtime.attachments.load(str(conversation_id), str(attachment_id))
        except AttachmentError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return Response(content=part.inline_data.data, media_type=part.inline_data.mime_type,
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.post("/chat", tags=["Chat"])
async def chat(request: ChatRequest, http_request: Request):
    """Run a turn; ADK persists execution events as they occur."""
    runtime = http_request.app.state.runtime
    identifier = str(request.conversation_id)
    check_skills(runtime, request.requested_skills)
    async with runtime.lock(identifier):
        selection = await select_model(runtime, identifier, request.model, request.requested_skills)
        await check_attachments(runtime, identifier, request.attachment_ids, selection)
        try:
            reply = await runtime.reply(identifier, request.message, request.requested_skills,
                                        attachment_ids=request.attachment_ids, model_selection=selection)
        except Exception as exc:
            logging.getLogger(__name__).exception("Chat failed")
            status, message = generation_error(exc)
            raise HTTPException(status_code=status, detail=message) from exc
    return {"conversation_id": identifier, "reply": reply}


@router.post("/chat/stream", tags=["Chat"])
async def stream_chat(request: ChatRequest, http_request: Request):
    """Stream visible text and tool activity. Failed turns can leave ADK events."""
    runtime = http_request.app.state.runtime
    identifier = str(request.conversation_id)
    selected = check_skills(runtime, request.requested_skills)
    selection = await select_model(runtime, identifier, request.model, request.requested_skills)
    await check_attachments(runtime, identifier, request.attachment_ids, selection)

    def encode(event):
        return f"data: {json.dumps(event)}\n\n"

    async def events():
        try:
            async with runtime.lock(identifier):
                # Deletion may have happened while this stream waited for a turn.
                await check_conversation(runtime, identifier)
                await check_attachments(runtime, identifier, request.attachment_ids, selection)
                yield encode({"type": "started", "conversation_id": identifier})
                for skill in selected:
                    yield encode({"type": "skill_loaded", "name": skill.frontmatter.name})
                async with aclosing(runtime.stream(identifier, request.message, request.requested_skills,
                                                   attachment_ids=request.attachment_ids, model_selection=selection)) as stream:
                    async for event in stream:
                        if event["type"] == "generation_finished":
                            yield encode({"type": "done", "conversation_id": identifier, "reply": event["reply"]})
                        else:
                            yield encode(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("Streaming chat failed")
            _, message = generation_error(exc)
            yield encode({"type": "error", "message": message})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/models", tags=["Models"])
async def list_models():
    """List models installed in Ollama; does not load a model."""
    try:
        return await fetch_models()
    except httpx.HTTPError:
        raise HTTPException(
            status_code=503,
            detail="Cannot retrieve models from Ollama. Check that it is running.",
        )


@router.get("/models/selected", tags=["Models"])
async def get_selected_model(request: Request, model: str | None = None):
    """Report the configured chat model and its capabilities."""
    model = model or settings.model

    try:
        details = await fetch_model_details(model)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model}' is not installed.",
            )

        raise HTTPException(
            status_code=502,
            detail="Ollama could not return the model details.",
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Cannot reach Ollama. Check that it is running.",
        )

    budget = {}
    if "completion" in details.get("capabilities", []):
        selection = request.app.state.runtime.model_manager.from_details(model, details)
        budget = {"context_tokens": selection.context_tokens, "compaction_tokens": selection.compaction_tokens}
    return {
        "model": model,
        "capabilities": details.get("capabilities", []),
        "details": details.get("details", {}),
        **budget,
    }


@router.get("/capabilities", tags=["Capabilities"])
async def list_capabilities(request: Request):
    runtime = request.app.state.runtime
    skill_tools = await runtime.skills.get_tools()
    return {
        "tools": [{"name": tool.name, "description": tool.description}
                  for tool in [*skill_tools, *runtime.registry.tools]] + runtime.mcp_tools,
        "skills": runtime.registry.describe_skills(),
    }


class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)


@router.post("/conversations", tags=["Conversations"], status_code=201)
async def create_conversation(request: ConversationCreate, http_request: Request):
    """Create a saved conversation and return its generated ID and metadata."""
    return await save_conversation(http_request.app.state.runtime.sessions, request.title)


@router.get("/conversations", tags=["Conversations"])
async def list_conversations(request: Request):
    """List saved conversation summaries for the chat sidebar."""
    return {"conversations": await fetch_conversations(request.app.state.runtime.sessions)}


@router.get("/conversations/{conversation_id}", tags=["Conversations"])
async def get_conversation(conversation_id: UUID, request: Request):
    """Retrieve one conversation and its messages using its ID."""
    conversation = await fetch_conversation(request.app.state.runtime.sessions, str(conversation_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.delete("/conversations/{conversation_id}", tags=["Conversations"], status_code=204)
async def delete_conversation(conversation_id: UUID, request: Request):
    """Delete the ADK session and its events, after any active turn finishes."""
    runtime = request.app.state.runtime
    async with runtime.lock(str(conversation_id)):
        await check_conversation(runtime, str(conversation_id))
        await runtime.attachments.delete_conversation(str(conversation_id))
        await remove_conversation(runtime.sessions, str(conversation_id))
    return Response(status_code=204)
