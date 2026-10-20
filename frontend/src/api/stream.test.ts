import { describe, expect, it } from 'vitest';
import { readEvents } from './stream';

function streamChunks(chunks: Uint8Array[], onCancel?: () => void) {
  return new ReadableStream<Uint8Array>({start(controller) {for (const chunk of chunks) controller.enqueue(chunk);controller.close();}, cancel:onCancel});
}
const bytes = (text: string) => new TextEncoder().encode(text);
describe('SSE reader', () => {
  it('preserves UTF-8 split across individual bytes and multiple frames', async () => {
    const wire = bytes('data: {"type":"text_delta","text":"🌿 café"}\n\ndata: {"type":"done","reply":"🌿 café"}\n\n');
    const events = [];
    for await (const event of readEvents(streamChunks([...wire].map(byte => new Uint8Array([byte]))))) events.push(event);
    expect(events).toEqual([{type:'text_delta', text:'🌿 café'}, {type:'done', reply:'🌿 café'}]);
  });
  it('accepts CRLF frames and ignores SSE comments', async () => {
    const events = [];
    for await (const event of readEvents(streamChunks([bytes(': keep alive\r\n\r\ndata: {"type":"status",\r\ndata: "message":"Working"}\r\n\r\n')]))) events.push(event);
    expect(events).toEqual([{type:'status',message:'Working'}]);
  });
  it('rejects truncated frames instead of claiming success', async () => {
    const read = async () => {for await (const event of readEvents(streamChunks([bytes('data: {"type":"done"}')]))){void event;}};
    await expect(read()).rejects.toThrow('incomplete event');
  });
  it('cancels an open body when the consumer stops early', async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({start(controller){controller.enqueue(bytes('data: {"type":"status"}\n\n'));},cancel(){cancelled = true;}});
    for await (const event of readEvents(body)) {expect(event.type).toBe('status');break;}
    expect(cancelled).toBe(true);
  });
});
