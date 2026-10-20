import { useEffect, useRef, useState } from 'react';
import { useAui } from '@assistant-ui/react';
import { ChevronDown, Compass, MessageSquare, PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { ChatProvider, useChat } from './runtime/ChatProvider';
import { ChatWorkspace } from './components/ChatWorkspace';

function Workspace() {
  const chat = useChat();
  const aui = useAui();
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 700);
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 700);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 700px)');
    const update = () => { setIsMobile(media.matches); setSidebarOpen(!media.matches); };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (!isMobile || !sidebarOpen) return;
    const sidebar = sidebarRef.current;
    sidebar?.querySelector<HTMLButtonElement>('button')?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarOpen(false);
      if (event.key !== 'Tab') return;
      const buttons = sidebar?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
      if (!buttons?.length) return;
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      requestAnimationFrame(() => menuRef.current?.focus());
    };
  }, [isMobile, sidebarOpen]);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const current = chat.conversations.find(c => c.id === chat.conversationId);
  const switchConversation = (id?: string) => {
    aui.thread().composer().reset();
    if (id) void chat.openConversation(id); else chat.newConversation();
    if (isMobile) setSidebarOpen(false);
  };
  return <div className={`app-shell ${sidebarOpen ? '' : 'sidebar-hidden'}`}>
    {isMobile && sidebarOpen && <button className="sidebar-backdrop" aria-label="Close conversations" onClick={() => setSidebarOpen(false)} tabIndex={-1}/>}
    <aside ref={sidebarRef} id="conversations-sidebar" className="sidebar" aria-label="Conversations" role={isMobile && sidebarOpen ? 'dialog' : undefined} aria-modal={isMobile && sidebarOpen ? true : undefined}>
      <div className="sidebar-brand"><span className="brand-mark"><Compass size={22}/></span><span>LocalChat<small>YOUR OWN CORNER</small></span><button className="sidebar-collapse" onClick={() => setSidebarOpen(false)} aria-label="Hide sidebar"><PanelLeftClose size={17}/></button></div>
      <button className="new-chat" disabled={chat.isRunning || chat.isUploading} onClick={() => switchConversation()}><Plus size={17}/>New conversation</button>
      <div className="sidebar-section-label">CONVERSATIONS <span>{chat.conversations.length || ''}</span></div>
      <nav className="conversation-list">{chat.conversations.map(conversation => <div key={conversation.id} className={`conversation-row ${chat.conversationId === conversation.id ? 'active' : ''}`}>
        <button className="conversation-link" disabled={chat.isRunning || chat.isUploading} onClick={() => switchConversation(conversation.id)}><MessageSquare size={14}/><span>{conversation.title}</span></button>
        <button className="delete-chat" disabled={chat.isRunning || chat.isUploading} aria-label={`Delete ${conversation.title}`} onClick={() => setDeleteId(conversation.id)}><Trash2 size={13}/></button>
      </div>)}{!chat.conversations.length && <p className="sidebar-empty">Good conversations<br/>deserve a place to stay.</p>}</nav>
      <div className="sidebar-footer"><div className="local-status"><span className={chat.connected ? 'connected-dot' : 'offline-dot'}/><span>{chat.connected ? 'Ollama connected' : 'Ollama offline'}<small>Running locally</small></span><button onClick={() => void chat.refreshModels()} aria-label="Refresh models"><RefreshCw size={14}/></button></div><div className="sidebar-signoff"><span>LOCAL BY NATURE</span><span>✦</span></div></div>
    </aside>
    <main className="main-panel" inert={isMobile && sidebarOpen}><header className="topbar"><div className="topbar-left">{!sidebarOpen && <button ref={menuRef} className="icon-button" onClick={() => setSidebarOpen(true)} aria-label="Show sidebar" aria-controls="conversations-sidebar" aria-expanded={sidebarOpen}><PanelLeftOpen size={18}/></button>}<span className="tab-active">Chat</span><span className="header-divider"/><span className="conversation-title">{current?.title ?? 'New conversation'}</span></div>
      <div className="model-control"><div className="model-select-wrap"><span className="model-dot"/><select aria-label="Chat model" value={chat.model} disabled={chat.isRunning || chat.isUploading || chat.isLoading} onChange={event => {chat.setModel(event.target.value); chat.clearError();}}>
        {!chat.models.includes(chat.model) && <option value={chat.model}>{chat.model || 'Select a model'}</option>}{chat.models.map(name => <option key={name} value={name}>{name}</option>)}
      </select><ChevronDown size={13}/></div><div className="model-caption">{chat.modelInfo ? [chat.modelInfo.capabilities.includes('vision') && 'Vision', chat.modelInfo.capabilities.includes('tools') && 'Tools', chat.modelInfo.context_tokens && `${Math.round(chat.modelInfo.context_tokens / 1024)}k context`].filter(Boolean).join(' · ') : 'Checking model…'}</div></div>
    </header><ChatWorkspace/></main>
    {deleteId && <div className="modal-backdrop" role="presentation" onClick={() => setDeleteId(null)}><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title" onClick={event => event.stopPropagation()}><div className="dialog-icon"><Trash2 size={22}/></div><h2 id="delete-title">Delete this conversation?</h2><p>Its messages and uploaded images will be removed.</p><div><button className="secondary-button" autoFocus onClick={() => setDeleteId(null)}>Keep conversation</button><button className="danger-button" onClick={() => {void chat.deleteConversation(deleteId);setDeleteId(null);}}>Delete</button></div></section></div>}
  </div>;
}
export default function App() { return <ChatProvider><Workspace/></ChatProvider>; }
