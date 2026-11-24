/** Read Server-Sent Events data fields, including a final frame without a blank line. */
export async function* readSseEvents(stream) {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed = false

  const dataFromBlock = (block) => {
    const data = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(':')) continue
      if (!line.startsWith('data:')) continue
      const value = line.slice(5)
      data.push(value.startsWith(' ') ? value.slice(1) : value)
    }
    return data.length ? data.join('\n') : null
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) {
        completed = true
        buffer += decoder.decode()
      } else {
        buffer += decoder.decode(value, { stream: true })
      }

      let boundary
      while ((boundary = /\r?\n\r?\n/.exec(buffer)) !== null) {
        const block = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        const data = dataFromBlock(block)
        if (data !== null) yield data
      }

      if (done) {
        if (buffer.trim()) {
          const data = dataFromBlock(buffer)
          if (data !== null) yield data
        }
        return
      }
    }
  } finally {
    if (!completed) await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
