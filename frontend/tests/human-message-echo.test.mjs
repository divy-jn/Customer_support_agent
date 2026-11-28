import assert from "node:assert/strict";
import test from "node:test";
import { closeTranscriptSocket, humanReplyFromEvent, mergeHumanReply, mergeTranscript } from "../app/lib/transcript.mjs";

const echo = {
  type: "human_message",
  message: "I can help with that.",
  timestamp: "2026-11-28T12:00:00.000Z",
  message_id: "first-reply",
};

test("a dashboard send and repeated WebSocket echo leave one renderable bubble", () => {
  const reply = humanReplyFromEvent(echo);
  const history = mergeTranscript([reply], [reply]);
  const bubbles = mergeHumanReply(history, humanReplyFromEvent(echo));

  assert.equal(bubbles.length, 1);
  assert.equal(bubbles[0].content, echo.message);
  assert.equal(bubbles[0].message_id, echo.message_id);
});

test("a current echo upgrades a matching legacy history entry", () => {
  const legacy = humanReplyFromEvent({
    ...echo, message_id: undefined, timestamp: "2026-11-28T11:59:59.990Z",
  });
  const bubbles = mergeTranscript([legacy], [humanReplyFromEvent(echo)]);

  assert.equal(bubbles.length, 1);
  assert.equal(bubbles[0].message_id, echo.message_id);
});

test("two intentionally identical replies with different IDs produce two bubbles", () => {
  const replies = [
    humanReplyFromEvent(echo),
    humanReplyFromEvent({ ...echo, message_id: "second-reply" }),
  ];

  assert.deepEqual(mergeTranscript(replies), replies);
});

test("closing a socket detaches all handlers before close can invoke them", () => {
  const listener = () => assert.fail("A closed socket callback fired");
  const socket = {
    readyState: 1, onopen: listener, onmessage: listener, onclose: listener, onerror: listener,
    close() {
      assert.equal(this.onopen, null);
      assert.equal(this.onmessage, null);
      assert.equal(this.onclose, null);
      assert.equal(this.onerror, null);
      this.readyState = 2;
    },
  };

  closeTranscriptSocket(socket);
  assert.equal(socket.readyState, 2);
});
