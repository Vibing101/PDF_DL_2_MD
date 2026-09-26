/**
 * Framing for the sidecar's stdout.
 *
 * Tauri's shell plugin may hand us whole lines with the newline already
 * stripped, or raw stream chunks that split a message anywhere — including in
 * the middle of a multi-byte character or a 1 MB parse result. This reassembles
 * either shape into complete JSON messages.
 */

export class LineFramer {
  #buffer = "";

  /**
   * Feed one chunk of output.
   *
   * @param {string} chunk
   * @returns {{messages: object[], broken: string[]}} complete messages, plus
   *   any line that was not valid JSON.
   */
  push(chunk) {
    const messages = [];
    const broken = [];
    this.#buffer += chunk ?? "";

    const parts = this.#buffer.split("\n");
    this.#buffer = parts.pop() ?? "";
    for (const part of parts) {
      const text = part.trim();
      if (!text) continue;
      try {
        messages.push(JSON.parse(text));
      } catch {
        broken.push(text);
      }
    }

    // In line mode there is no trailing newline, so whatever is left may
    // already be a whole message. If it does not parse, it is an incomplete
    // stream chunk and we keep it until more arrives.
    const rest = this.#buffer.trim();
    if (rest) {
      try {
        messages.push(JSON.parse(rest));
        this.#buffer = "";
      } catch {
        // Wait for the rest of the message.
      }
    }
    return { messages, broken };
  }

  /** Anything buffered but never completed. */
  get pending() {
    return this.#buffer;
  }
}
