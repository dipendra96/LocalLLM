"""HTTP conversation views backed exclusively by ADK session events."""
from datetime import datetime, timezone
from uuid import uuid4

from localchat.storage import APP_NAME, USER_ID
from localchat.storage.attachments import attachment_id, describe_attachment


def summary(session):
    return {
        "id": session.id,
        "title": session.state.get("title", "New chat"),
        "created_at": session.state["created_at"],
        **({"model": session.state["selected_model"]} if session.state.get("selected_model") else {}),
    }


async def save_conversation(service, title):
    session = await service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=str(uuid4()),
        state={"title": title, "created_at": datetime.now(timezone.utc).isoformat()},
    )
    return summary(session)


async def fetch_conversations(service):
    result = await service.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    return sorted([summary(s) for s in result.sessions],
                  key=lambda s: (s["created_at"], s["id"]), reverse=True)


async def fetch_conversation(service, conversation_id):
    session = await service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=conversation_id,
    )
    if session is None:
        return None
    messages = []
    calls_by_id = {}
    for event in session.events:
        if event.partial or not event.content or event.actions.compaction:
            continue
        text = "".join(part.text or "" for part in event.content.parts or [] if not part.thought)
        attachments = [describe_attachment(conversation_id, attachment_id(part), part.file_data.mime_type)
                       for part in event.content.parts or [] if attachment_id(part) is not None]
        calls = []
        for part in event.content.parts or []:
            if part.function_call:
                call = part.function_call
                item = {"id": call.id or f"{event.id}:{call.name}", "name": call.name, "state": "stopped"}
                calls.append(item)
                calls_by_id[item["id"]] = item
            if part.function_response:
                result = part.function_response
                call_id = result.id or next((key for key, value in calls_by_id.items()
                                             if value["name"] == result.name and value["state"] == "stopped"), None)
                if call_id in calls_by_id:
                    calls_by_id[call_id]["state"] = "returned"
        if not text and not attachments and not calls:
            continue
        messages.append({
            "id": event.id, "conversation_id": conversation_id,
            "role": "user" if event.author == "user" else "assistant",
            "content": text,
            **({"attachments": attachments} if attachments else {}),
            **({"tool_calls": calls} if calls else {}),
            "created_at": datetime.fromtimestamp(event.timestamp, timezone.utc).isoformat(),
        })
    return {**summary(session), "messages": messages}


async def remove_conversation(service, conversation_id):
    """Delete a session after the caller checks it under the conversation lock."""
    await service.delete_session(app_name=APP_NAME, user_id=USER_ID,
                                 session_id=conversation_id)
