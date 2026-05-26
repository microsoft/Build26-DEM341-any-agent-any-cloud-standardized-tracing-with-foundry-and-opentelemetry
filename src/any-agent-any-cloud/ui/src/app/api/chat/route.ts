import { DefaultAzureCredential } from "@azure/identity";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type AgentId = "seattle" | "bangalore" | "xian" | "copilot";

type ChatRequestBody = {
  message?: unknown;
  sessionId?: unknown;
};

type ResponseMetadata = Record<string, unknown>;

type ResponseItem = {
  type?: string;
  name?: string;
  call_id?: string;
  id?: string;
  status?: string;
  metadata?: ResponseMetadata | null;
};

type ResponseEnvelope = {
  metadata?: ResponseMetadata | null;
  usage?: {
    total_tokens?: number | null;
  } | null;
};

type ResponsesStreamEvent = {
  type?: string;
  delta?: string;
  item?: ResponseItem | null;
  response?: ResponseEnvelope | null;
  error?: { message?: string } | string | null;
};

const FOUNDRY_PROJECT_ENDPOINT =
  process.env.FOUNDRY_PROJECT_ENDPOINT ??
  "https://<account>.services.ai.azure.com/api/projects/<project>";
const FOUNDRY_ORCHESTRATOR_AGENT_NAME =
  process.env.FOUNDRY_ORCHESTRATOR_AGENT_NAME ?? "foundry-orchestrator";
const FOUNDRY_ORCHESTRATOR_AGENT_VERSION =
  process.env.FOUNDRY_ORCHESTRATOR_AGENT_VERSION ?? "19";
const FOUNDRY_RESPONSE_API_VERSION =
  process.env.FOUNDRY_RESPONSE_API_VERSION ?? "2025-11-15-preview";

const credential = new DefaultAzureCredential();
const MARKER_PATTERN = /^\[city:(seattle|bangalore|xian|copilot)\]\s*\n?/i;

function asAgentId(value: unknown): AgentId | null {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (
    normalized === "seattle" ||
    normalized === "bangalore" ||
    normalized === "xian" ||
    normalized === "copilot"
  ) {
    return normalized;
  }

  return null;
}

function toolNameToAgent(name: unknown): AgentId | null {
  switch (name) {
    case "call_seattle":
      return "seattle";
    case "call_bangalore":
      return "bangalore";
    case "call_xian":
      return "xian";
    case "ask_copilot":
      return "copilot";
    default:
      return null;
  }
}

function getString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function getNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function getMetadataValue(
  metadata: ResponseMetadata | null | undefined,
  key: string
): string | null {
  if (!metadata) {
    return null;
  }

  return getString(metadata[key]);
}

function inferAgentFromText(text: string): AgentId | null {
  const markerMatch = text.match(MARKER_PATTERN);
  if (markerMatch) {
    return asAgentId(markerMatch[1]);
  }

  const normalized = text.toLowerCase();
  if (normalized.includes("### seattle")) {
    return "seattle";
  }
  if (normalized.includes("bangalore") || normalized.includes("bengaluru")) {
    return "bangalore";
  }
  if (normalized.includes("xi'an") || normalized.includes("xian") || normalized.includes("xi an")) {
    return "xian";
  }
  if (normalized.includes("copilot fallback")) {
    return "copilot";
  }

  return null;
}

function extractTraceId(evt: ResponsesStreamEvent): string | null {
  return (
    getMetadataValue(evt.response?.metadata, "trace_id") ??
    getMetadataValue(evt.item?.metadata, "trace_id")
  );
}

function extractTotalTokens(evt: ResponsesStreamEvent): number | null {
  return getNumber(evt.response?.usage?.total_tokens);
}

function extractRoutedAgent(evt: ResponsesStreamEvent): AgentId | null {
  return (
    asAgentId(getMetadataValue(evt.response?.metadata, "routed_to")) ??
    asAgentId(getMetadataValue(evt.item?.metadata, "routed_to")) ??
    toolNameToAgent(evt.item?.name)
  );
}

function formatSse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

async function openFoundryStream(payload: string, bearerToken: string) {
  const versionedBase = `${FOUNDRY_PROJECT_ENDPOINT}/agents/${FOUNDRY_ORCHESTRATOR_AGENT_NAME}/versions/${FOUNDRY_ORCHESTRATOR_AGENT_VERSION}`;
  const candidates = [
    `${versionedBase}?api-version=${FOUNDRY_RESPONSE_API_VERSION}`,
    `${versionedBase}/endpoint/protocols/openai/responses?api-version=${FOUNDRY_RESPONSE_API_VERSION}`,
    `${FOUNDRY_PROJECT_ENDPOINT}/agents/${FOUNDRY_ORCHESTRATOR_AGENT_NAME}/endpoint/protocols/openai/responses?api-version=${FOUNDRY_RESPONSE_API_VERSION}`,
  ];

  const failures: string[] = [];

  for (const url of candidates) {
    const upstream = await fetch(url, {
      method: "POST",
      headers: {
        authorization: `Bearer ${bearerToken}`,
        "content-type": "application/json",
        accept: "text/event-stream",
      },
      body: payload,
      cache: "no-store",
    });

    if (upstream.ok && upstream.body) {
      return { upstream, attemptedUrl: url };
    }

    const details = await upstream.text().catch(() => "");
    failures.push(`${upstream.status} ${url}${details ? ` :: ${details}` : ""}`);

    // Continue trying alternative routes on common "wrong path" responses.
    if (![400, 404, 405].includes(upstream.status)) {
      break;
    }
  }

  return {
    upstream: null,
    attemptedUrl: null,
    error: failures.join("\n"),
  };
}

export async function POST(req: NextRequest) {
  let body: ChatRequestBody;

  try {
    body = (await req.json()) as ChatRequestBody;
  } catch {
    return new Response("invalid JSON body", { status: 400 });
  }

  const message = typeof body.message === "string" ? body.message.trim() : "";
  const sessionId =
    typeof body.sessionId === "string" && body.sessionId.trim().length > 0
      ? body.sessionId.trim()
      : null;

  if (!message) {
    return new Response("message required", { status: 400 });
  }

  const token = await credential.getToken("https://ai.azure.com/.default");
  if (!token?.token) {
    return new Response("could not acquire Azure token", { status: 500 });
  }

  const payload = JSON.stringify({
    input: message,
    stream: true,
    ...(sessionId ? { metadata: { session_id: sessionId } } : {}),
  });

  const { upstream, error } = await openFoundryStream(payload, token.token);
  if (!upstream?.body) {
    return new Response(error ?? "unable to reach Foundry orchestrator", {
      status: 502,
    });
  }

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const emit = (event: string, data: unknown) => {
        controller.enqueue(encoder.encode(formatSse(event, data)));
      };

      const reader = upstream.body!.getReader();
      let buffer = "";
      let routedAgent: AgentId | null = null;
      let toolStarted = false;
      let toolEnded = false;
      let markerResolved = false;
      let markerBuffer = "";
      let accumulatedText = "";
      let traceId: string | null = null;
      let totalTokens: number | null = null;

      const emitToolStart = (agent: AgentId) => {
        routedAgent = agent;
        if (!toolStarted) {
          toolStarted = true;
          emit("tool_start", { agent });
        }
      };

      const emitToolEnd = (ok: boolean) => {
        if (!routedAgent || toolEnded) {
          return;
        }
        toolEnded = true;
        emit("tool_end", {
          agent: routedAgent,
          trace_id: traceId,
          ok,
        });
      };

      const emitToken = (text: string) => {
        if (!text) {
          return;
        }
        accumulatedText += text;
        emit("token", { text });
      };

      const resolveMarker = (force: boolean) => {
        const ready = force || markerBuffer.includes("\n") || markerBuffer.length >= 64;
        if (!ready || !markerBuffer) {
          return;
        }

        const match = markerBuffer.match(MARKER_PATTERN);
        let textToEmit = markerBuffer;

        if (match) {
          const detectedAgent = asAgentId(match[1]);
          if (detectedAgent) {
            emitToolStart(detectedAgent);
          }
          textToEmit = markerBuffer.slice(match[0].length);
        } else if (!routedAgent) {
          const inferredAgent = inferAgentFromText(markerBuffer);
          if (inferredAgent) {
            emitToolStart(inferredAgent);
          }
        }

        markerBuffer = "";
        markerResolved = true;
        emitToken(textToEmit);
      };

      const handleEvent = (payloadText: string) => {
        let evt: ResponsesStreamEvent;

        try {
          evt = JSON.parse(payloadText) as ResponsesStreamEvent;
        } catch {
          return;
        }

        traceId = extractTraceId(evt) ?? traceId;
        totalTokens = extractTotalTokens(evt) ?? totalTokens;

        const metadataAgent = extractRoutedAgent(evt);
        if (metadataAgent) {
          emitToolStart(metadataAgent);
        }

        switch (evt.type) {
          case "response.output_text.delta": {
            const delta = getString(evt.delta) ?? "";
            if (!delta) {
              return;
            }

            if (!markerResolved) {
              markerBuffer += delta;
              resolveMarker(false);
              return;
            }

            emitToken(delta);
            return;
          }
          case "response.output_item.added":
          case "response.output_item.done":
            return;
          case "response.completed": {
            resolveMarker(true);
            if (!routedAgent) {
              const inferredAgent = inferAgentFromText(accumulatedText);
              if (inferredAgent) {
                emitToolStart(inferredAgent);
              }
            }
            emitToolEnd(true);
            emit("done", {
              trace_id: traceId,
              total_tokens: totalTokens,
            });
            return;
          }
          case "response.failed":
          case "error": {
            resolveMarker(true);
            emitToolEnd(false);
            const message =
              typeof evt.error === "string"
                ? evt.error
                : evt.error?.message ?? "Foundry response failed";
            emit("error", { message, trace_id: traceId });
            return;
          }
          default:
            return;
        }
      };

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }

          buffer += decoder.decode(value, { stream: true });

          let boundaryIndex = buffer.indexOf("\n\n");
          while (boundaryIndex !== -1) {
            const rawEvent = buffer.slice(0, boundaryIndex);
            buffer = buffer.slice(boundaryIndex + 2);

            const dataLines: string[] = [];
            for (const line of rawEvent.split("\n")) {
              if (line.startsWith("data:")) {
                dataLines.push(line.slice(5).trimStart());
              }
            }

            const payloadText = dataLines.join("\n");
            if (payloadText && payloadText !== "[DONE]") {
              handleEvent(payloadText);
            }

            boundaryIndex = buffer.indexOf("\n\n");
          }
        }

        if (buffer.trim()) {
          const dataLines = buffer
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart());
          const payloadText = dataLines.join("\n");
          if (payloadText && payloadText !== "[DONE]") {
            handleEvent(payloadText);
          }
        }
      } catch (error) {
        resolveMarker(true);
        emitToolEnd(false);
        emit("error", {
          message: error instanceof Error ? error.message : String(error),
          trace_id: traceId,
        });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
