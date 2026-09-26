/**
 * Client for the bundled `pdf2md-service` sidecar.
 *
 * The sidecar speaks one JSON object per line over stdin/stdout: requests get a
 * reply with the same `id`, and anything with an `event` key arrives
 * unsolicited (progress, log lines). See pdf2md/service.py.
 */

import { Command } from "@tauri-apps/plugin-shell";

import { LineFramer } from "./protocol.js";

const SIDECAR = "binaries/pdf2md-service";

export class ServiceError extends Error {
  constructor(message, kind = "error") {
    super(message);
    this.name = "ServiceError";
    this.kind = kind;
  }
}

export class Service {
  #child = null;
  #pending = new Map();
  #nextId = 1;
  #framer = new LineFramer();
  #listeners = new Map();
  #stderr = [];

  /** Spawn the sidecar and wait for its `ready` event. */
  async start() {
    if (this.#child) return;
    const command = Command.sidecar(SIDECAR, [], {
      env: { PDF2MD_LOG_LEVEL: "INFO" },
    });
    command.stdout.on("data", (chunk) => this.#onChunk(chunk));
    command.stderr.on("data", (line) => this.#onStderr(line));
    command.on("close", ({ code }) => this.#onClose(code));
    command.on("error", (error) => this.#onClose(null, error));

    const ready = this.#once("ready", 60_000);
    this.#child = await command.spawn();
    return ready;
  }

  /** Send a request and resolve with its result, or throw a ServiceError. */
  request(method, params = undefined, { timeout = 0 } = {}) {
    if (!this.#child) return Promise.reject(new ServiceError("the converter is not running"));
    const id = this.#nextId++;
    const payload = params === undefined ? { id, method } : { id, method, params };
    return new Promise((resolve, reject) => {
      const entry = { resolve, reject, timer: null };
      if (timeout > 0) {
        entry.timer = setTimeout(() => {
          this.#pending.delete(id);
          reject(new ServiceError(`${method} timed out`, "timeout"));
        }, timeout);
      }
      this.#pending.set(id, entry);
      this.#child.write(JSON.stringify(payload) + "\n").catch((error) => {
        this.#settle(id, { ok: false, error: { kind: "io", message: String(error) } });
      });
    });
  }

  /** Subscribe to an event kind; returns a function that unsubscribes. */
  on(event, handler) {
    const handlers = this.#listeners.get(event) ?? new Set();
    handlers.add(handler);
    this.#listeners.set(event, handlers);
    return () => handlers.delete(handler);
  }

  /** The last lines the sidecar wrote to stderr — handy when something breaks. */
  get log() {
    return this.#stderr;
  }

  async stop() {
    if (!this.#child) return;
    const child = this.#child;
    this.#child = null;
    try {
      await child.write(JSON.stringify({ id: 0, method: "shutdown" }) + "\n");
    } catch {
      // The process is already gone; killing it below is enough.
    }
    try {
      await child.kill();
    } catch {
      // Nothing to kill.
    }
  }

  // ------------------------------------------------------------------ internals

  /** Turn a chunk of stdout into whole messages and deliver them. */
  #onChunk(chunk) {
    const { messages, broken } = this.#framer.push(chunk);
    for (const message of messages) this.#deliver(message);
    for (const line of broken) {
      console.warn("pdf2md: unreadable line from the converter", line.slice(0, 200));
    }
  }

  #deliver(message) {
    if (message.event) {
      this.#emit(message.event, message.data ?? {});
      return;
    }
    if (message.id !== undefined && this.#pending.has(message.id)) {
      this.#settle(message.id, message);
    }
  }

  #settle(id, message) {
    const entry = this.#pending.get(id);
    if (!entry) return;
    this.#pending.delete(id);
    if (entry.timer) clearTimeout(entry.timer);
    if (message.ok) entry.resolve(message.result ?? {});
    else {
      const error = message.error ?? {};
      entry.reject(new ServiceError(error.message ?? "the converter failed", error.kind));
    }
  }

  #emit(event, data) {
    for (const handler of this.#listeners.get(event) ?? []) {
      try {
        handler(data);
      } catch (error) {
        console.error(`pdf2md: ${event} handler failed`, error);
      }
    }
  }

  #once(event, timeout) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        off();
        reject(new ServiceError(`the converter did not start within ${timeout / 1000}s`, "timeout"));
      }, timeout);
      const off = this.on(event, (data) => {
        clearTimeout(timer);
        off();
        resolve(data);
      });
    });
  }

  #onStderr(line) {
    this.#stderr.push(String(line));
    if (this.#stderr.length > 200) this.#stderr.shift();
    this.#emit("stderr", { line: String(line) });
  }

  #onClose(code, error) {
    this.#child = null;
    const message = error
      ? `the converter stopped: ${error}`
      : `the converter stopped unexpectedly (exit code ${code})`;
    for (const id of [...this.#pending.keys()]) {
      this.#settle(id, { ok: false, error: { kind: "closed", message } });
    }
    this.#emit("closed", { code, message });
  }
}
