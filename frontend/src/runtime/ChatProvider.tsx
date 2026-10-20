import { createId } from './id';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { PropsWithChildren } from 'react';
import { AssistantRuntimeProvider, useExternalStoreRuntime } from '@assistant-ui/react';
import type { AppendMessage, ThreadMessageLike } from '@assistant-ui/react';
import { createConversation, get, readJson } from '../api/client';
import type { ChatMessage, Conversation, ModelInfo, ToolCall } from '../api/client';
import { readEvents } from '../api/stream';
import { imageAdapter } from './attachments';

function toMessage(message: ChatMessage): ThreadMessageLike {
  return {
    id: message.id, role: message.role, createdAt: message.created_at ? new Date(message.created_at) : undefined,
    content: [
      ...(message.tool_calls ?? []).map(tool => ({ type: 'tool-call' as const, toolCallId: tool.id, toolName: tool.name, args: {}, argsText: '{}',
        ...(tool.state !== 'running' ? { result: {status: tool.state} } : {}) })),
      { type: 'text', text: message.content },
    ],
    attachments: message.attachments?.map(a => ({ id: a.id, type: 'image', name: a.filename ?? 'Image', contentType: a.mime_type,
      content: [{ type: 'image' as const, image: a.url }], status: {type: 'complete' as const} })),
  };
}

function useChatController() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [connected, setConnected] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const uploadCount = useRef(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [webResearch, setWebResearch] = useState(false);
  const [webAvailable, setWebAvailable] = useState(false);
  const currentId = useRef<string | null>(null);
  const creating = useRef<Promise<string> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const busy = useRef(false);
  const navigation = useRef(0);
  const refreshConversations = useCallback(async () => {
    const result = await get<{conversations: Conversation[]}>('/conversations');
    setConversations(result.conversations);
  }, []);
  const refreshModels = useCallback(async () => {
    try {
      const data = await get<{models: {name: string}[]}>('/models');
      setModels(data.models.map(item => item.name));
      setConnected(true);
    } catch (error) { setConnected(false); setError(String(error instanceof Error ? error.message : error)); }
  }, []);
  useEffect(() => {
    void refreshConversations().catch(error => setError(error.message));
    void refreshModels();
    void get<ModelInfo>('/models/selected').then(info => { setModel(info.model); setModelInfo(info); }).catch(error => setError(error.message));
    void get<{tools: {name: string}[]}>('/capabilities').then(data => setWebAvailable(data.tools.some(tool => tool.name === 'search'))).catch(() => {});
    return () => controller.current?.abort();
  }, [refreshConversations, refreshModels]);
  useEffect(() => {
    if (!model) return;
    const abort = new AbortController();
    setModelInfo(null);
    void get<ModelInfo>(`/models/selected?model=${encodeURIComponent(model)}`, abort.signal).then(info => {setModelInfo(info); if (!info.capabilities.includes('tools')) setWebResearch(false);}).catch(error => {
      if (!abort.signal.aborted) setError(error.message);
    });
    return () => abort.abort();
  }, [model]);

  const ensureConversation = useCallback(async () => {
    if (currentId.current) return currentId.current;
    if (!creating.current) creating.current = createConversation().then(conversation => {
      currentId.current = conversation.id;
      setConversationId(conversation.id);
      setConversations(previous => [conversation, ...previous]);
      return conversation.id;
    }).finally(() => { creating.current = null; });
    return creating.current;
  }, []);
  const attachments = useMemo(() => imageAdapter(ensureConversation, setError, delta => {
    uploadCount.current += delta;
    setIsUploading(uploadCount.current > 0);
  }), [ensureConversation]);
  useEffect(() => () => attachments.dispose(), [attachments]);

  const openConversation = async (id: string) => {
    if (busy.current || uploadCount.current) return;
    const token = ++navigation.current;
    setIsLoading(true); setError('');
    try {
      const conversation = await get<Conversation>(`/conversations/${id}`);
      if (token !== navigation.current) return;
      currentId.current = id; setConversationId(id); setMessages(conversation.messages ?? []);
      if (conversation.model) setModel(conversation.model);
      setStatus('');
    } catch (error) { if (token === navigation.current) setError((error as Error).message); }
    finally { if (token === navigation.current) setIsLoading(false); }
  };
  const newConversation = () => {
    if (busy.current || uploadCount.current) return;
    ++navigation.current; currentId.current = null;
    setConversationId(null); setMessages([]); setError(''); setStatus(''); setIsLoading(false);
  };
  const deleteConversation = async (id: string) => {
    if (busy.current || uploadCount.current) return;
    try {
      const response = await fetch(`/conversations/${id}`, {method: 'DELETE'});
      if (!response.ok) await readJson(response);
      if (currentId.current === id) newConversation();
      await refreshConversations();
    } catch (error) { setError((error as Error).message); }
  };

  const send = async (message: AppendMessage) => {
    if (busy.current || isLoading) return;
    const text = message.content.filter(part => part.type === 'text').map(part => part.text).join('\n').trim();
    if (!text) { setError('Add a question or message with your image.'); return; }
    if ((message.attachments?.length ?? 0) > 4) { setError('Attach up to four images per message.'); return; }
    busy.current = true; setIsRunning(true); setError(''); setStatus('Preparing your message…');
    const abort = new AbortController(); controller.current = abort;
    let id: string | null = null;
    const assistantId = createId();
    let completed = false;
    try {
      id = await ensureConversation();
      const uploaded = (message.attachments ?? []).map(a => ({id: a.id, mime_type: a.contentType ?? 'image/png', filename: a.name,
        url: `/conversations/${id}/attachments/${a.id}`}));
      setMessages(previous => [...previous, {id: createId(), role:'user', content:text, attachments:uploaded},
        {id: assistantId, role:'assistant', content:''}]);
      const response = await fetch('/chat/stream', { method:'POST', headers:{'Content-Type':'application/json'}, signal:abort.signal,
        body:JSON.stringify({conversation_id:id, message:text, model:model || undefined,
          attachment_ids: uploaded.map(a => a.id), requested_skills: webResearch ? ['web-research'] : []}) });
      if (!response.ok) await readJson(response);
      if (!response.body) throw new Error('The server did not return a response stream.');
      for await (const event of readEvents(response.body)) {
        if (event.type === 'error') throw new Error(event.message ?? 'Generation failed.');
        if (event.type === 'status') setStatus(event.message ?? 'Working…');
        if (event.type === 'model_selected') setModelInfo(previous => previous ? {...previous, context_tokens:event.context_tokens, compaction_tokens:event.compaction_tokens} : previous);
        if (event.type === 'tool_started' || event.type === 'tool_finished') {
          const running = event.type === 'tool_started';
          setStatus(running ? `Using ${event.name}…` : 'Writing a response…');
          setMessages(previous => previous.map(item => {
            if (item.id !== assistantId) return item;
            const tools = item.tool_calls ?? [];
            const call: ToolCall = {id:event.call_id!, name:event.name!, state:running ? 'running' : 'returned'};
            return {...item, tool_calls: tools.some(t => t.id === call.id) ? tools.map(t => t.id === call.id ? call : t) : [...tools, call]};
          }));
        }
        if (event.type === 'text_delta') setMessages(previous => previous.map(item => item.id === assistantId ? {...item, content:item.content + event.text} : item));
        if (event.type === 'done') {
          completed = true;
          setMessages(previous => previous.map(item => item.id === assistantId ? {...item, content:event.reply ?? ''} : item));
        }
      }
      if (!completed) throw new Error('The connection closed before the response finished.');
    } catch (error) {
      if (abort.signal.aborted) setStatus('Response stopped.');
      else setError((error as Error).message);
    } finally {
      if (id) {
        // Reconcile with ADK even after failure or cancellation.
        try {
          const saved = await get<Conversation>(`/conversations/${id}`);
          setMessages(saved.messages ?? []);
        } catch { /* Keep the visible partial response when reconciliation fails. */ }
      }
      setMessages(previous => previous.map(item => ({...item, tool_calls:item.tool_calls?.map(tool => tool.state === 'running' ? {...tool, state:'stopped'} : tool)})));
      await refreshConversations().catch(() => {});
      busy.current = false; setIsRunning(false); controller.current = null;
      if (completed) setStatus('');
    }
  };
  return { messages, conversations, conversationId, models, model, setModel, modelInfo, connected, isRunning, isUploading, isLoading,
    error, clearError: () => setError(''), status, webResearch, setWebResearch, webAvailable, attachments,
    send, stop: () => {attachments.cancelUploads(); controller.current?.abort();}, openConversation, newConversation, deleteConversation, refreshModels };
}

type ChatController = ReturnType<typeof useChatController>;
const ChatContext = createContext<ChatController | null>(null);
export function useChat() { const context = useContext(ChatContext); if (!context) throw new Error('Missing chat provider'); return context; }
export function ChatProvider({children}: PropsWithChildren) {
  const chat = useChatController();
  const runtime = useExternalStoreRuntime({messages:chat.messages, isRunning:chat.isRunning, isLoading:chat.isLoading,
    convertMessage:toMessage, onNew:chat.send, onCancel:async () => chat.stop(),
    adapters:{attachments:chat.attachments}});
  return <ChatContext.Provider value={chat}><AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider></ChatContext.Provider>;
}
