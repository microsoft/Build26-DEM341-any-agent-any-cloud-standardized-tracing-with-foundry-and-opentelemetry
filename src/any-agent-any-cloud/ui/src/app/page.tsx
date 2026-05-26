"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  type Edge,
  type Node,
} from "reactflow";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type AgentId = "orchestrator" | "seattle" | "bangalore" | "xian" | "copilot";
type Status = "idle" | "active" | "done" | "error";

type AgentMeta = {
  label: string;
  sublabel: string;
  cloud: string;
  emoji: string;
};

const AGENTS: Record<AgentId, AgentMeta> = {
  orchestrator: {
    label: "Orchestrator",
    sublabel: "MS Agent Framework",
    cloud: "Azure AI Foundry",
    emoji: "🧭",
  },
  seattle: {
    label: "Seattle",
    sublabel: "LangGraph + Foundry model",
    cloud: "AWS Lambda",
    emoji: "🦞",
  },
  bangalore: {
    label: "Bangalore",
    sublabel: "Google ADK + Vertex AI",
    cloud: "GCP",
    emoji: "☕",
  },
  xian: {
    label: "Xi'an",
    sublabel: "Foundry Prompt Agent",
    cloud: "Azure AI Foundry",
    emoji: "🥟",
  },
  copilot: {
    label: "Copilot fallback",
    sublabel: "GitHub Copilot SDK",
    cloud: "Claude Sonnet 4.5",
    emoji: "✨",
  },
};

const POSITIONS: Record<AgentId, { x: number; y: number }> = {
  orchestrator: { x: 280, y: 40 },
  seattle: { x: 0, y: 240 },
  bangalore: { x: 280, y: 240 },
  xian: { x: 560, y: 240 },
  copilot: { x: 280, y: 440 },
};

const EDGE_DEFS: { id: string; source: AgentId; target: AgentId }[] = [
  { id: "o-seattle", source: "orchestrator", target: "seattle" },
  { id: "o-bangalore", source: "orchestrator", target: "bangalore" },
  { id: "o-xian", source: "orchestrator", target: "xian" },
  { id: "o-copilot", source: "orchestrator", target: "copilot" },
];

type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};

function AgentNode({ data }: { data: { meta: AgentMeta; status: Status } }) {
  const { meta, status } = data;
  return (
    <div className={`react-flow__node-agent ${status}`}>
      <div className="flex items-center gap-2">
        <span className="text-xl">{meta.emoji}</span>
        <div className="flex-1">
          <div className="font-semibold text-sm">{meta.label}</div>
          <div className="text-[10px] text-zinc-400">{meta.cloud}</div>
        </div>
        <StatusDot status={status} />
      </div>
      <div className="mt-1.5 text-[11px] text-zinc-400 leading-tight">
        {meta.sublabel}
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: Status }) {
  const color =
    status === "active"
      ? "bg-blue-400 animate-pulse"
      : status === "done"
      ? "bg-emerald-400"
      : status === "error"
      ? "bg-red-400"
      : "bg-zinc-600";
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${color}`} />;
}

const nodeTypes = { agent: AgentNode };

function getStringField(data: unknown, key: string): string | null {
  if (typeof data !== "object" || data === null) {
    return null;
  }

  const value = (data as Record<string, unknown>)[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function getBooleanField(data: unknown, key: string): boolean | null {
  if (typeof data !== "object" || data === null) {
    return null;
  }

  const value = (data as Record<string, unknown>)[key];
  return typeof value === "boolean" ? value : null;
}

export default function Page() {
  const [statuses, setStatuses] = useState<Record<AgentId, Status>>({
    orchestrator: "idle",
    seattle: "idle",
    bangalore: "idle",
    xian: "idle",
    copilot: "idle",
  });
  const [edgeStatuses, setEdgeStatuses] = useState<Record<string, Status>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [liveSummary, setLiveSummary] = useState("");
  const summaryRef = useRef("");
  const sessionIdRef = useRef("");

  const nodes: Node[] = useMemo(
    () =>
      (Object.keys(AGENTS) as AgentId[]).map((id) => ({
        id,
        type: "agent",
        position: POSITIONS[id],
        data: { meta: AGENTS[id], status: statuses[id] },
      })),
    [statuses]
  );

  const edges: Edge[] = useMemo(
    () =>
      EDGE_DEFS.map((e) => ({
        ...e,
        animated: edgeStatuses[e.id] === "active",
        className: edgeStatuses[e.id] ?? "",
        markerEnd: { type: MarkerType.ArrowClosed },
      })),
    [edgeStatuses]
  );

  const edgeForAgent = (agent: string) => {
    const map: Record<string, string> = {
      seattle: "o-seattle",
      bangalore: "o-bangalore",
      xian: "o-xian",
      copilot: "o-copilot",
    };
    return map[agent];
  };

  const resetGraph = () => {
    setStatuses({
      orchestrator: "idle",
      seattle: "idle",
      bangalore: "idle",
      xian: "idle",
      copilot: "idle",
    });
    setEdgeStatuses({});
    setTraceId(null);
    setLiveSummary("");
    summaryRef.current = "";
  };

  const handleEvent = useCallback((eventName: string, payload: unknown) => {
    if (eventName === "token") {
      const text = getStringField(payload, "text") ?? "";
      if (!text) {
        return;
      }
      summaryRef.current += text;
      setLiveSummary(summaryRef.current);
      return;
    }

    if (eventName === "tool_start") {
      const agent = getStringField(payload, "agent") as AgentId | null;
      if (!agent) {
        return;
      }
      setStatuses((s) => ({ ...s, [agent]: "active" }));
      const edgeId = edgeForAgent(agent);
      if (edgeId) {
        setEdgeStatuses((es) => ({ ...es, [edgeId]: "active" }));
      }
      return;
    }

    if (eventName === "tool_end") {
      const agent = getStringField(payload, "agent") as AgentId | null;
      const ok = getBooleanField(payload, "ok") ?? true;
      const nextTraceId = getStringField(payload, "trace_id");
      if (nextTraceId) {
        setTraceId(nextTraceId);
      }
      if (!agent) {
        return;
      }
      setStatuses((s) => ({ ...s, [agent]: ok ? "done" : "error" }));
      const edgeId = edgeForAgent(agent);
      if (edgeId) {
        setEdgeStatuses((es) => ({ ...es, [edgeId]: ok ? "done" : "error" }));
      }
      return;
    }

    if (eventName === "done") {
      const nextTraceId = getStringField(payload, "trace_id");
      if (nextTraceId) {
        setTraceId(nextTraceId);
      }
      setStatuses((s) => ({ ...s, orchestrator: "done" }));
      const finalContent = summaryRef.current || "(no response)";
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: finalContent,
        },
      ]);
      setLiveSummary("");
      summaryRef.current = "";
      return;
    }

    if (eventName === "error") {
      const message = getStringField(payload, "message") ?? "Unknown error";
      const nextTraceId = getStringField(payload, "trace_id");
      if (nextTraceId) {
        setTraceId(nextTraceId);
      }
      setStatuses((s) => ({ ...s, orchestrator: "error" }));
      setMessages((m) => [
        ...m,
        { role: "system", content: `Error: ${message}` },
      ]);
    }
  }, []);

  const send = async () => {
    if (!input.trim() || streaming) return;

    if (!sessionIdRef.current) {
      sessionIdRef.current = crypto.randomUUID();
    }

    const message = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content: message }]);
    resetGraph();
    setStatuses((s) => ({ ...s, orchestrator: "active" }));
    setStreaming(true);

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          message,
          sessionId: sessionIdRef.current,
        }),
      });

      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";

        for (const part of parts) {
          const lines = part.split("\n");
          let eventName = "message";
          const dataLines: string[] = [];

          for (const line of lines) {
            if (line.startsWith("event:")) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              dataLines.push(line.slice(5).trimStart());
            }
          }

          const json = dataLines.join("\n");
          if (!json) {
            continue;
          }

          try {
            handleEvent(eventName, JSON.parse(json) as unknown);
          } catch (error) {
            console.error("bad SSE event", error, json);
          }
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatuses((s) => ({ ...s, orchestrator: "error" }));
      setMessages((m) => [
        ...m,
        { role: "system", content: `Request failed: ${message}` },
      ]);
    } finally {
      setStreaming(false);
    }
  };

  const appInsightsResourceId =
    process.env.NEXT_PUBLIC_APPLICATIONINSIGHTS_RESOURCE_ID;
  const traceLink =
    traceId && appInsightsResourceId
      ? `https://portal.azure.com/#blade/AppInsightsExtension/DetailsV2Blade/ComponentId/${encodeURIComponent(
          appInsightsResourceId
        )}/DataModel/%7B%22eventId%22:%22${traceId}%22%7D`
      : null;

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* Left: chat */}
      <div className="w-[42%] min-w-[420px] flex flex-col border-r border-zinc-800">
        <header className="px-5 py-4 border-b border-zinc-800">
          <h1 className="text-xl font-semibold tracking-tight">
            Any Agent, Any Cloud
          </h1>
          <p className="text-xs text-zinc-400 mt-0.5">
            Foundry Observability across LangGraph (AWS) · Google ADK (GCP) ·
            Foundry Prompt Agent · MS Agent Framework + Copilot SDK
          </p>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {messages.length === 0 && !streaming && (
            <div className="text-sm text-zinc-500 space-y-2">
              <p>Try a prompt:</p>
              <div className="flex flex-wrap gap-2">
                {[
                  "3 days in Seattle, coffee + waterfront",
                  "1 day in Xi'an, must-see sights",
                  "Bangalore coffee and culture day",
                  "7-day trip: Seattle, Bangalore, then Xi'an",
                ].map((p) => (
                  <button
                    key={p}
                    onClick={() => setInput(p)}
                    className="px-2.5 py-1 text-xs rounded-md bg-zinc-800 hover:bg-zinc-700 text-zinc-200"
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <MessageBubble key={i} m={m} />
          ))}

          {streaming && liveSummary && (
            <MessageBubble
              m={{ role: "assistant", content: liveSummary }}
              live
            />
          )}

          {streaming && !liveSummary && (
            <div className="text-xs text-zinc-500 italic">
              orchestrating…
            </div>
          )}
        </div>

        <div className="border-t border-zinc-800 p-3">
          <div className="flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              disabled={streaming}
              placeholder="Plan a trip…"
              className="flex-1 rounded-md bg-zinc-900 border border-zinc-800 px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={send}
              disabled={streaming || !input.trim()}
              className="px-4 py-2 rounded-md bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:text-zinc-400 text-white text-sm font-medium"
            >
              Send
            </button>
          </div>
        </div>
      </div>

      {/* Right: graph */}
      <div className="flex-1 flex flex-col bg-[#0f0f17]">
        <header className="px-5 py-4 border-b border-zinc-800 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold">Live agent flow</div>
            <div className="text-[11px] text-zinc-400 mt-0.5">
              Single distributed trace · OTel GenAI semantic conventions
            </div>
          </div>
          {traceId && (
            <div className="text-right">
              <div className="text-[10px] text-zinc-500 uppercase tracking-wider">
                Trace ID
              </div>
              <code className="text-xs text-blue-300">
                {traceId.slice(0, 16)}…
              </code>
              {traceLink && (
                <a
                  href={traceLink}
                  target="_blank"
                  rel="noreferrer"
                  className="block text-[10px] text-blue-400 underline mt-0.5"
                >
                  open in Foundry / App Insights →
                </a>
              )}
            </div>
          )}
        </header>
        <div className="flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            proOptions={{ hideAttribution: true }}
            nodesDraggable={false}
            nodesConnectable={false}
          >
            <Background color="#1f1f2c" gap={20} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <footer className="px-5 py-2 border-t border-zinc-800 flex gap-4 text-[11px] text-zinc-400">
          <span className="flex items-center gap-1.5">
            <StatusDot status="active" /> in-flight
          </span>
          <span className="flex items-center gap-1.5">
            <StatusDot status="done" /> done
          </span>
          <span className="flex items-center gap-1.5">
            <StatusDot status="error" /> error
          </span>
          <span className="flex items-center gap-1.5">
            <StatusDot status="idle" /> idle
          </span>
        </footer>
      </div>
    </div>
  );
}

function MessageBubble({
  m,
  live,
}: {
  m: ChatMessage;
  live?: boolean;
}) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-blue-600 text-white px-3.5 py-2 text-sm">
          {m.content}
        </div>
      </div>
    );
  }
  if (m.role === "system") {
    return (
      <div className="text-xs text-red-400 italic">{m.content}</div>
    );
  }
  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[92%] rounded-2xl rounded-bl-sm bg-zinc-900 border border-zinc-800 px-3.5 py-2.5 text-sm markdown-body ${
          live ? "opacity-80" : ""
        }`}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
        {live && (
          <span className="inline-block w-2 h-3.5 bg-blue-400 animate-pulse ml-0.5" />
        )}
      </div>
    </div>
  );
}
