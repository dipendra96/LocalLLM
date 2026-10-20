import type { ToolCallMessagePartComponent } from '@assistant-ui/react';
import { Check, CircleStop, LoaderCircle, Search, FileSearch, Wrench } from 'lucide-react';

export const ToolActivity: ToolCallMessagePartComponent = ({toolName, result}) => {
  const state = result && typeof result === 'object' && 'status' in result ? String(result.status) : 'running';
  const Icon = toolName === 'search' ? Search : toolName === 'fetch_content' ? FileSearch : Wrench;
  const labels: Record<string, string> = {search:'Searching the web', fetch_content:'Reading a page', load_skill:'Loading a skill', load_skill_resource:'Reading skill resources'};
  return <div className={`tool-activity ${state}`} role="status">
    <Icon size={14}/><span>{labels[toolName] ?? toolName.replaceAll('_',' ')}</span>
    {state === 'running' ? <LoaderCircle className="spin" size={13}/> : state === 'stopped' ? <CircleStop size={13}/> : <Check size={13}/>}
    <small>{state === 'running' ? 'Working' : state === 'stopped' ? 'Stopped' : 'Returned'}</small>
  </div>;
};
