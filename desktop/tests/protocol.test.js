import assert from "node:assert/strict";
import test from "node:test";

import { LineFramer } from "../src/protocol.js";

test("parses whole lines that still carry their newline", () => {
  const framer = new LineFramer();
  const { messages } = framer.push('{"a":1}\n{"b":2}\n');
  assert.deepEqual(messages, [{ a: 1 }, { b: 2 }]);
  assert.equal(framer.pending, "");
});

test("parses a line whose newline was already stripped", () => {
  const framer = new LineFramer();
  assert.deepEqual(framer.push('{"event":"ready"}').messages, [{ event: "ready" }]);
  assert.deepEqual(framer.push('{"id":1,"ok":true}').messages, [{ id: 1, ok: true }]);
});

test("reassembles a message split across chunks", () => {
  const framer = new LineFramer();
  assert.deepEqual(framer.push('{"id":1,"res').messages, []);
  assert.deepEqual(framer.push('ult":{"count":2}}').messages, [{ id: 1, result: { count: 2 } }]);
});

test("reassembles a big message split mid-way, then keeps going", () => {
  const framer = new LineFramer();
  const links = Array.from({ length: 500 }, (_, index) => ({ id: index, url: `u${index}` }));
  const payload = JSON.stringify({ id: 2, ok: true, result: { links } }) + "\n";
  const cut = Math.floor(payload.length / 2);
  assert.deepEqual(framer.push(payload.slice(0, cut)).messages, []);
  const { messages } = framer.push(payload.slice(cut) + '{"event":"progress"}\n');
  assert.equal(messages.length, 2);
  assert.equal(messages[0].result.links.length, 500);
  assert.deepEqual(messages[1], { event: "progress" });
});

test("several messages in one chunk all come through", () => {
  const framer = new LineFramer();
  const { messages } = framer.push('{"a":1}\n{"b":2}\n{"c":3}');
  assert.deepEqual(messages, [{ a: 1 }, { b: 2 }, { c: 3 }]);
});

test("blank lines are ignored", () => {
  assert.deepEqual(new LineFramer().push('\n\n{"a":1}\n\n').messages, [{ a: 1 }]);
});

test("a line that is not JSON is reported, not thrown", () => {
  const framer = new LineFramer();
  const { messages, broken } = framer.push('oops not json\n{"a":1}\n');
  assert.deepEqual(messages, [{ a: 1 }]);
  assert.deepEqual(broken, ["oops not json"]);
});

test("unicode survives being split between chunks", () => {
  const framer = new LineFramer();
  const payload = JSON.stringify({ title: "ΘΕΩΡΗΤΙΚΗ · Α · Αισθητική" });
  assert.deepEqual(framer.push(payload.slice(0, 20)).messages, []);
  const { messages } = framer.push(payload.slice(20) + "\n");
  assert.equal(messages[0].title, "ΘΕΩΡΗΤΙΚΗ · Α · Αισθητική");
});
