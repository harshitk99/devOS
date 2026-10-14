import { useEffect, useState } from "react";
import type { StreamMessage } from "../api/types";

/** Subscribes to live progress/log messages for a project over WebSocket. Recent history is
 * replayed by the server on connect. */
export function useProjectStream(projectId: string | null) {
  const [messages, setMessages] = useState<StreamMessage[]>([]);

  useEffect(() => {
    setMessages([]);
    if (!projectId) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/projects/${projectId}`);
    ws.onmessage = (e) => setMessages((prev) => [...prev, JSON.parse(e.data) as StreamMessage]);
    return () => ws.close();
  }, [projectId]);

  return messages;
}
