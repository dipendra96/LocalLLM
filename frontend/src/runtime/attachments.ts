import { createId } from './id';
import type { AttachmentAdapter } from '@assistant-ui/react';
import { uploadImage } from '../api/client';
import type { Attachment } from '../api/client';

/** Browser previews are temporary; durable IDs come from the artifact service. */
type ImageAdapter = AttachmentAdapter & {cancelUploads: () => void; dispose: () => void};
export function imageAdapter(ensureConversation: () => Promise<string>, onError: (message: string) => void, uploadChanged: (delta: number) => void): ImageAdapter {
  const previews = new Map<string, string>();
  const controllers = new Set<AbortController>();
  const uploaded = new WeakMap<File, {conversation: string; attachment: Attachment}>();
  return {
    accept: 'image/png,image/jpeg,image/webp',
    cancelUploads() {for (const controller of controllers) controller.abort();},
    dispose() {for (const controller of controllers) controller.abort(); for (const url of previews.values()) URL.revokeObjectURL(url); previews.clear();},
    async add({ file }) {
      if (previews.size >= 4) {onError('Attach up to four images per message.'); throw new Error('Attach up to four images per message.');}
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
        const error = 'Choose a PNG, JPEG, or WebP image up to 10 MiB.';
        onError(error);
        throw new Error(error);
      }
      const id = createId();
      const preview = URL.createObjectURL(file);
      previews.set(id, preview);
      return { id, type: 'image', name: file.name, contentType: file.type, file,
        content: [{ type: 'image', image: preview }], status: { type: 'requires-action', reason: 'composer-send' } };
    },
    async send(attachment, options) {
      const abort = new AbortController();
      const cancel = () => abort.abort();
      controllers.add(abort); uploadChanged(1);
      if (options?.signal?.aborted) abort.abort();
      options?.signal?.addEventListener('abort', cancel, {once:true});
      try {
        abort.signal.throwIfAborted();
        const conversation = await ensureConversation();
        const cached = uploaded.get(attachment.file);
        const saved = cached?.conversation === conversation ? cached.attachment : await uploadImage(conversation, attachment.file, abort.signal);
        uploaded.set(attachment.file, { conversation, attachment: saved });
        const preview = previews.get(attachment.id);
        if (preview) URL.revokeObjectURL(preview);
        previews.delete(attachment.id);
        return { id: saved.id, type: 'image', name: attachment.name, contentType: saved.mime_type,
          content: [{ type: 'image', image: saved.url }], status: {type: 'complete'} };
      } catch (error) {
        if (!(error instanceof DOMException && error.name === 'AbortError')) onError(error instanceof Error ? error.message : 'Image upload failed.');
        throw error;
      } finally {
        controllers.delete(abort); uploadChanged(-1);
        options?.signal?.removeEventListener('abort', cancel);
      }
    },
    async remove(attachment) {
      const preview = previews.get(attachment.id);
      if (preview) URL.revokeObjectURL(preview);
      previews.delete(attachment.id);
    },
  };
}
