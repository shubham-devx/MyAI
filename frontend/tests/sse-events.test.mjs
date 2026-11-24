import assert from 'node:assert/strict'
import test from 'node:test'
import { readSseEvents } from '../src/lib/sse-events.js'

function streamFromChunks(chunks) {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk)
      controller.close()
    },
  })
}

test('reads frames split across chunks and preserves UTF-8 characters', async () => {
  const values = []
  const text = 'data: {"type":"token","token":"नमस्ते 🌱"}\r\n\r\ndata: {"type":"done"}\r\n\r\n'
  const bytes = new TextEncoder().encode(text)
  const chunks = Array.from(bytes, (byte) => new Uint8Array([byte]))
  for await (const value of readSseEvents(streamFromChunks(chunks))) values.push(value)
  assert.deepEqual(values, ['{"type":"token","token":"नमस्ते 🌱"}', '{"type":"done"}'])
})

test('dispatches a final event when the connection closes without a blank line', async () => {
  const values = []
  for await (const value of readSseEvents(streamFromChunks(['data: {"type":"done"}']))) values.push(value)
  assert.deepEqual(values, ['{"type":"done"}'])
})

test('supports multiline data fields and ignores SSE comments', async () => {
  const values = []
  const input = ': heartbeat\ndata: first\ndata: second\n\n'
  for await (const value of readSseEvents(streamFromChunks([input]))) values.push(value)
  assert.deepEqual(values, ['first\nsecond'])
})

test('cancels the underlying reader when the consumer stops early', async () => {
  let cancelled = false
  const stream = new ReadableStream({
    pull(controller) { controller.enqueue(new TextEncoder().encode('data: one\n\n')) },
    cancel() { cancelled = true },
  })
  const iterator = readSseEvents(stream)[Symbol.asyncIterator]()
  await iterator.next()
  await iterator.return()
  assert.equal(cancelled, true)
})
