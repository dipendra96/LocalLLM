import { AttachmentPrimitive, ComposerPrimitive, MessagePrimitive, ThreadPrimitive, useAui, useAuiState } from '@assistant-ui/react';
import { MarkdownTextPrimitive } from '@assistant-ui/react-markdown';
import { ArrowDown, ArrowUp, Check, Compass, Globe2, ImagePlus, Lightbulb, Paperclip, Square, X } from 'lucide-react';
import { useChat } from '../runtime/ChatProvider';
import { ToolActivity } from './ToolActivity';

function AttachmentImage() {
  const image = useAuiState(s => s.attachment.content?.find(part => part.type === 'image'));
  const name = useAuiState(s => s.attachment.name);
  return image?.type === 'image' ? <div className="attachment-image"><img src={image.image} alt={name}/></div> : null;
}
// Adapt assistant-ui's message-part props to the context-based Markdown primitive.
const MarkdownText = () => <MarkdownTextPrimitive/>;
function ImageAttachment() {
  return <AttachmentPrimitive.Root className="image-attachment"><AttachmentImage/><span className="sr-only"><AttachmentPrimitive.Name/></span></AttachmentPrimitive.Root>;
}
function PendingImage() {
  return <AttachmentPrimitive.Root className="pending-image"><AttachmentImage/>
    <span className="pending-name"><AttachmentPrimitive.Name/></span>
    <AttachmentPrimitive.Remove className="remove-image" aria-label="Remove image"><X size={12}/></AttachmentPrimitive.Remove>
  </AttachmentPrimitive.Root>;
}
function UserMessage() {
  return <MessagePrimitive.Root className="message user-message"><div className="user-bubble">
    <div className="message-images"><MessagePrimitive.Attachments components={{Image:ImageAttachment}}/></div>
    <MessagePrimitive.Parts/>
  </div><span className="user-avatar" aria-hidden="true">You</span></MessagePrimitive.Root>;
}
function AssistantMessage() {
  return <MessagePrimitive.Root className="message assistant-message"><div className="assistant-avatar" aria-hidden="true"><Compass size={19}/></div>
    <div className="assistant-body"><div className="message-author">LocalChat</div>
      <MessagePrimitive.Parts components={{Text:MarkdownText, tools:{Fallback:ToolActivity}}}/>
    </div></MessagePrimitive.Root>;
}
function Welcome() {
  const aui = useAui();
  const suggestions = [
    {icon:Lightbulb, title:'Untangle an idea', detail:'Make the complex feel simple', prompt:'Explain a challenging idea with a simple example.'},
    {icon:Globe2, title:'Explore a question', detail:'Follow your curiosity', prompt:'Help me explore a topic. Ask me what I am curious about.'},
    {icon:ImagePlus, title:'Bring an image', detail:'Look closer, together', prompt:'What kinds of questions can I ask about an image?'},
  ];
  return <div className="welcome"><div className="welcome-icon"><Compass size={32} strokeWidth={1.2}/></div>
    <span className="eyebrow">A LITTLE SPACE TO THINK</span><h1>What are we exploring?</h1>
    <p>Ask a question, work through an idea, or bring an image.<br/>Your conversation starts here.</p>
    <div className="suggestions">{suggestions.map(({icon:Icon, title, detail, prompt}) =>
      <button key={title} onClick={() => aui.thread().composer().setText(prompt)}><Icon size={19}/><strong>{title}</strong><span>{detail}</span><ArrowUp size={13} className="suggestion-arrow"/></button>)}</div>
  </div>;
}
export function ChatWorkspace() {
  const chat = useChat();
  const hasText = useAuiState(s => s.composer.text.trim().length > 0);
  const canUseTools = chat.modelInfo?.capabilities.includes('tools') !== false;
  const canUseImages = chat.modelInfo?.capabilities.includes('vision') === true;
  return <ThreadPrimitive.Root className="thread"><ThreadPrimitive.Viewport className="thread-viewport">
    <ThreadPrimitive.Empty><Welcome/></ThreadPrimitive.Empty>
    <div className="message-list"><ThreadPrimitive.Messages components={{UserMessage, AssistantMessage}}/></div>
    {chat.isLoading && <div className="loading-conversation">Opening your conversation…</div>}
  </ThreadPrimitive.Viewport>
    <div className="composer-area">
      <ThreadPrimitive.ScrollToBottom className="scroll-bottom" aria-label="Scroll to latest message"><ArrowDown size={16}/></ThreadPrimitive.ScrollToBottom>
      {chat.error && <div className="error-banner" role="alert"><span>{chat.error}</span><button onClick={chat.clearError} aria-label="Dismiss error"><X size={15}/></button></div>}
      <div className="generation-status" aria-live="polite">{chat.isUploading ? <><span className="status-pulse"/>Uploading images…</> : chat.isRunning ? <><span className="status-pulse"/>{chat.status || 'Working…'}</> : chat.status}</div>
      <ComposerPrimitive.Root className="composer">
        <div className="pending-images"><ComposerPrimitive.Attachments components={{Image:PendingImage}}/></div>
        <ComposerPrimitive.Input className="composer-input" placeholder="Message LocalChat…" aria-label="Message" disabled={chat.isLoading || !chat.model} autoFocus={window.matchMedia('(min-width: 701px)').matches}/>
        <div className="composer-toolbar"><div className="composer-options">
          <ComposerPrimitive.AddAttachment className="icon-button attach-button" aria-label="Attach images" title={canUseImages ? 'Attach images' : 'Select a vision model to attach images'} disabled={!canUseImages || chat.isRunning || chat.isUploading || chat.isLoading}><Paperclip size={18}/></ComposerPrimitive.AddAttachment>
          {chat.webAvailable && <button type="button" className={`web-toggle ${chat.webResearch ? 'selected' : ''}`} aria-pressed={chat.webResearch} disabled={!canUseTools || chat.isRunning || chat.isUploading} onClick={() => chat.setWebResearch(!chat.webResearch)}><Globe2 size={14}/>Web research{chat.webResearch && <Check size={12}/>}</button>}
        </div>
        {chat.isRunning || chat.isUploading ? <ComposerPrimitive.Cancel className="send-button stop-button" aria-label="Stop response"><Square size={14} fill="currentColor"/></ComposerPrimitive.Cancel> : <ComposerPrimitive.Send className="send-button" aria-label="Send message" disabled={!hasText || !chat.model || chat.isLoading || chat.modelInfo?.capabilities.includes('completion') === false}><ArrowUp size={19}/></ComposerPrimitive.Send>}
        </div>
      </ComposerPrimitive.Root>
      <div className="composer-footnote"><span>Local models can make mistakes. Check important details.</span><span>↵ to send · Shift ↵ for a new line</span></div>
    </div>
  </ThreadPrimitive.Root>;
}
