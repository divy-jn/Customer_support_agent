export function humanReplyFromEvent(event) {
  return {
    role: "agent",
    content: event.message,
    agent_name: event.agent_name || "Support Agent",
    human_agent: true,
    timestamp: event.timestamp,
    message_id: event.message_id,
  };
}

export function mergeHumanReply(messages, reply) {
  const duplicate = messages.findIndex((entry) => {
    if (entry.role !== "agent" || !entry.human_agent) return false;
    if (entry.message_id && reply.message_id) return entry.message_id === reply.message_id;

    // Older servers emitted a fresh timestamp for the live echo.
    const elapsed = Math.abs(Date.parse(entry.timestamp) - Date.parse(reply.timestamp));
    return elapsed <= 250 && entry.content === reply.content
      && (entry.agent_name || "Support Agent") === (reply.agent_name || "Support Agent");
  });
  if (duplicate === -1) return [...messages, reply];
  if (messages[duplicate].message_id || !reply.message_id) return messages;
  return messages.map((entry, index) => index === duplicate ? { ...entry, ...reply } : entry);
}

export function mergeTranscript(history, earlyReplies = []) {
  return [...history, ...earlyReplies].reduce((messages, entry) => {
    if (entry.role === "agent" && entry.human_agent) return mergeHumanReply(messages, entry);
    messages.push(entry);
    return messages;
  }, []);
}

export function closeTranscriptSocket(socket) {
  if (!socket) return;
  socket.onopen = null;
  socket.onmessage = null;
  socket.onerror = null;
  socket.onclose = null;
  if (socket.readyState < 2) socket.close();
}
