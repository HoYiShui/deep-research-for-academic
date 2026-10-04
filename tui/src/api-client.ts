export class ApiError extends Error {
  constructor(message: string, readonly statusCode?: number, readonly code = "client_error") {
    super(message);
  }
}

export type SseEvent = { event: string; data: unknown; id?: string };

export class ResearchApiClient {
  constructor(readonly baseUrl: string) {}

  async health(): Promise<Record<string, unknown>> { return this.request("GET", "/health"); }
  async createResearch(query: string): Promise<Record<string, unknown>> { return this.request("POST", "/research", { query }); }
  async message(id: string, content: string): Promise<Record<string, unknown>> { return this.request("POST", `/research/${id}/messages`, { content }); }
  async confirm(id: string, accepted: boolean, feedback?: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/research/${id}/confirm`, feedback ? { accepted, feedback } : { accepted });
  }
  async status(id: string): Promise<Record<string, unknown>> { return this.request("GET", `/research/${id}`); }
  async report(id: string): Promise<Record<string, unknown>> { return this.request("GET", `/research/${id}/report`); }
  async cancel(id: string): Promise<Record<string, unknown>> { return this.request("POST", `/research/${id}/cancel`, {}); }

  async *events(url: string): AsyncGenerator<SseEvent> {
    const response = await fetch(this.resolve(url), { headers: { Accept: "text/event-stream" } });
    if (!response.ok) throw await this.error(response);
    if (!response.body) throw new ApiError("SSE response has no body", response.status, "invalid_sse_response");
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const chunk = await reader.read(); if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const frames = buffer.split("\n\n"); buffer = frames.pop() ?? "";
      for (const frame of frames) {
        let event = "message"; let id: string | undefined; const data: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim() || "message";
          if (line.startsWith("id:")) id = line.slice(3).trim();
          if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        }
        if (data.length) { const raw = data.join("\n"); yield { event, id, data: parseJson(raw) }; }
      }
    }
  }

  private async request(method: string, path: string, body?: object): Promise<Record<string, unknown>> {
    let response: Response;
    try { response = await fetch(this.resolve(path), { method, headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined }); }
    catch (error) { throw new ApiError(String(error), undefined, "network_error"); }
    if (!response.ok) throw await this.error(response);
    const payload: unknown = await response.json().catch(() => { throw new ApiError("API response was not JSON", response.status, "invalid_json_response"); });
    if (!isRecord(payload)) throw new ApiError("API response must be an object", response.status, "invalid_response");
    return payload;
  }
  private async error(response: Response): Promise<ApiError> {
    const payload: unknown = await response.json().catch(() => null);
    const error = isRecord(payload) && isRecord(payload.error) ? payload.error : {};
    return new ApiError(String(error.message ?? response.statusText), response.status, String(error.code ?? "http_error"));
  }
  private resolve(path: string): string { return new URL(path, `${this.baseUrl.replace(/\/$/, "")}/`).toString(); }
}
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const parseJson = (value: string): unknown => { try { return JSON.parse(value); } catch { return value; } };
