export type Attachment = { id: string; mime_type: string; url: string; filename?: string };
export type ToolCall = { id: string; name: string; state: 'running' | 'returned' | 'stopped' };
export type ChatMessage = { id: string; role: 'user' | 'assistant'; content: string; created_at?: string; attachments?: Attachment[]; tool_calls?: ToolCall[] };
export type Conversation = { id: string; title: string; created_at: string; model?: string; messages?: ChatMessage[] };
export type ModelInfo = { model: string; capabilities: string[]; context_tokens?: number; compaction_tokens?: number };
export type StreamEvent = { type: string; message?: string; text?: string; reply?: string; name?: string; call_id?: string; model?: string; context_tokens?: number; compaction_tokens?: number };
export async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item: {msg: string}) => item.msg).join('. ');
    } catch { /* Keep the HTTP status if the server did not return JSON. */ }
    throw new Error(message);
  }
  return response.json();
}
export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  return readJson(await fetch(path, { signal }));
}
export async function createConversation(): Promise<Conversation> {
  return readJson(await fetch('/conversations', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({}) }));
}
export async function uploadImage(conversationId: string, file: File, signal?: AbortSignal): Promise<Attachment> {
  const body = new FormData();
  body.append('conversation_id', conversationId);
  body.append('file', file);
  return readJson(await fetch('/attachments', { method: 'POST', body, signal }));
}
