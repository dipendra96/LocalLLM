"""Image validation and conversation-scoped storage using ADK artifacts."""
from io import BytesIO
import errno
from uuid import UUID, uuid4

from google.adk.artifacts import FileArtifactService
from google.genai import types
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from localchat.storage import APP_NAME, USER_ID

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ATTACHMENT_PREFIX = "localchat://attachments/"
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class AttachmentError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def validate_image(data):
    if not data:
        raise AttachmentError(422, "The image is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise AttachmentError(413, "Images must be no larger than 10 MiB.")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in FORMATS:
                raise AttachmentError(415, "Use a PNG, JPEG, or WebP image.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise AttachmentError(413, "Images must be no larger than 20 megapixels.")
            if getattr(image, "is_animated", False):
                raise AttachmentError(415, "Animated images are not supported yet.")
            image.load()
            return FORMATS[image.format], image.width, image.height
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        if isinstance(exc, AttachmentError):
            raise
        raise AttachmentError(422, "The uploaded file is not a valid supported image.") from exc


def attachment_id(part):
    if not part.file_data or not part.file_data.file_uri.startswith(ATTACHMENT_PREFIX):
        return None
    return str(UUID(part.file_data.file_uri.removeprefix(ATTACHMENT_PREFIX)))


def describe_attachment(conversation_id, identifier, mime_type):
    return {"id": identifier, "mime_type": mime_type,
            "url": f"/conversations/{conversation_id}/attachments/{identifier}"}


class AttachmentStore:
    def __init__(self, root):
        self.service = FileArtifactService(root_dir=root)

    async def save(self, conversation_id, data, filename):
        mime, width, height = await run_in_threadpool(validate_image, data)
        identifier = str(uuid4())
        # Original names are display metadata only; storage paths are generated IDs.
        filename = (filename or "image").replace("\\", "/").split("/")[-1][:200]
        await self.service.save_artifact(
            app_name=APP_NAME, user_id=USER_ID, session_id=conversation_id,
            filename=identifier, artifact=types.Part.from_bytes(data=data, mime_type=mime),
            custom_metadata={"filename": filename, "width": width, "height": height},
        )
        return {**describe_attachment(conversation_id, identifier, mime),
                "filename": filename, "size": len(data), "width": width, "height": height}

    async def load(self, conversation_id, identifier):
        identifier = str(UUID(identifier))
        part = await self.service.load_artifact(
            app_name=APP_NAME, user_id=USER_ID, session_id=conversation_id,
            filename=identifier, version=0,
        )
        if part is None or not part.inline_data:
            raise AttachmentError(404, "Attachment not found in this conversation.")
        return part

    async def references(self, conversation_id, identifiers):
        parts = []
        for identifier in dict.fromkeys(str(value) for value in identifiers):
            part = await self.load(conversation_id, identifier)
            parts.append(types.Part(file_data=types.FileData(
                file_uri=f"{ATTACHMENT_PREFIX}{identifier}", mime_type=part.inline_data.mime_type,
            )))
        return parts

    async def hydrate(self, callback_context, llm_request):
        # Request content may share objects with persisted session events. Copy
        # before replacing refs so image bytes never become history payloads.
        llm_request.contents = [content.model_copy(deep=True) for content in llm_request.contents]
        loaded = {}
        for content in llm_request.contents:
            for index, part in enumerate(content.parts or []):
                identifier = attachment_id(part)
                if identifier is not None:
                    if identifier not in loaded:
                        loaded[identifier] = await self.load(callback_context.session.id, identifier)
                    content.parts[index] = loaded[identifier].model_copy(deep=True)

    async def delete_conversation(self, conversation_id):
        conversation_id = str(UUID(conversation_id))
        keys = await self.service.list_artifact_keys(
            app_name=APP_NAME, user_id=USER_ID, session_id=conversation_id,
        )
        for filename in keys:
            await self.service.delete_artifact(
                app_name=APP_NAME, user_id=USER_ID, session_id=conversation_id, filename=filename,
            )
        await run_in_threadpool(self._remove_empty_conversation_dir, conversation_id)

    def _remove_empty_conversation_dir(self, conversation_id):
        # FileArtifactService's layout in the pinned ADK version. Leave shared
        # parent directories intact and never recursively delete leftover files.
        directory = (self.service.root_dir / "apps" / APP_NAME / "users" / USER_ID
                     / "sessions" / conversation_id)
        for path in (directory / "artifacts", directory):
            try:
                path.rmdir()
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST):
                    raise
