import { randomUUID } from "node:crypto";

export class ApiError extends Error {
  constructor(message: string, readonly statusCode?: number, readonly code = "client_error",
    readonly retryable = false, readonly requestId?: string) { super(message); }
}
export type RecordValue = Record<string, unknown>;
export type SourceSelection = { categories: ("papers" | "web")[]; knowledge_base_ids: string[] };
export type Session = RecordValue & { session_id: string; status: string; brief_version: number };
export type SseEvent = { event: string; data: RecordValue; id?: string };
export type RequestOptions = { key?: string; signal?: AbortSignal };
export const isRecord = (value: unknown): value is RecordValue =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const statuses = new Set(["ask", "confirm", "ready", "running", "cancelling", "completed", "failed", "cancelled"]);
const briefFields = ["task_type", "decision_goal", "research_object", "scope", "comparison_scope", "claims_to_verify", "evidence_requirements", "conclusion_boundary", "deliverable", "assumptions"];
export function session(value: RecordValue): Session {
  if (typeof value.session_id !== "string" || !statuses.has(String(value.status)) ||
      !Number.isInteger(value.brief_version) || Number(value.brief_version) < 1)
    throw new ApiError("会话响应缺少合法身份、状态或 Brief 版本", undefined, "contract_error");
  if (value.status === "confirm" && (!isRecord(value.research_brief) ||
      briefFields.some(key => typeof (value.research_brief as RecordValue)[key] !== "string")))
    throw new ApiError("确认响应缺少完整十字段 Brief", undefined, "contract_error");
  return value as Session;
}
export class ResearchApiClient {
  readonly baseUrl: string;
  constructor(baseUrl: string) {
    const url = new URL(baseUrl);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
        url.search || url.hash || url.pathname !== "/") throw new ApiError("API 地址必须是 HTTP(S) origin");
    this.baseUrl = url.origin;
  }
  health(): Promise<RecordValue> { return this.request("GET", "/health"); }
  async createResearch(query: string, sources: SourceSelection, options: RequestOptions = {}): Promise<Session> {
    return session(await this.request("POST", "/research", { query, sources: sources.categories, knowledge_base_ids: sources.knowledge_base_ids }, options));
  }
  async message(id: string, content: string, version: number, patch?: RecordValue, options: RequestOptions = {}): Promise<Session> {
    return session(await this.request("POST", `/research/${id}/messages`, { content, brief_version: version, ...(patch ? { brief_patch: patch } : {}) }, options));
  }
  async confirm(id: string, accepted: boolean, version: number, feedback?: string, options: RequestOptions = {}): Promise<Session> {
    return session(await this.request("POST", `/research/${id}/confirm`, { accepted, brief_version: version, ...(accepted ? {} : { feedback }) }, options));
  }
  async status(id: string): Promise<Session> { return session(await this.request("GET", `/research/${id}`)); }
  report(id: string): Promise<RecordValue> { return this.request("GET", `/research/${id}/report`); }
  cancel(id: string, options: RequestOptions = {}): Promise<RecordValue> { return this.request("POST", `/research/${id}/cancel`, {}, options); }
  async resume(id: string, seq: number, options: RequestOptions = {}): Promise<Session> {
    return session(await this.request("POST", `/research/${id}/resume`, { checkpoint_seq: seq }, options));
  }
  async *events(url: string, signal?: AbortSignal): AsyncGenerator<SseEvent> {
    const target = this.resolve(url);
    let response: Response;
    try { response = await fetch(target, { headers: { Accept: "text/event-stream" }, signal, credentials: "omit" }); }
    catch { throw new ApiError("SSE 连接失败", undefined, signal?.aborted ? "aborted" : "network_error", true); }
    if (!response.ok) throw await this.error(response);
    if (!response.headers.get("content-type")?.startsWith("text/event-stream") || !response.body)
      throw new ApiError("响应不是 SSE 流", response.status, "contract_error");
    const reader = response.body.getReader(); const decoder = new TextDecoder("utf-8", { fatal: true }); let buffer = "";
    try {
      while (true) {
        const chunk = await reader.read(); if (chunk.done) break;
        buffer = (buffer + decoder.decode(chunk.value, { stream: true })).replace(/\r\n/g, "\n");
        if (buffer.length > 512 * 1024) throw new ApiError("SSE 帧超出调试上限", undefined, "contract_error");
        const frames = buffer.split("\n\n"); buffer = frames.pop() ?? "";
        for (const frame of frames) {
          let event = "message"; let id: string | undefined; const data: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim() || "message";
            if (line.startsWith("id:")) id = line.slice(3).trim();
            if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
          }
          if (!data.length) continue;
          let value: unknown;
          try { value = JSON.parse(data.join("\n")); } catch { throw new ApiError("SSE 数据不是 JSON", undefined, "contract_error"); }
          if (!isRecord(value)) throw new ApiError("SSE 数据不是对象", undefined, "contract_error");
          yield { event, id, data: value };
        }
      }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  }
  private async request(method: string, path: string, body?: object, options: RequestOptions = {}): Promise<RecordValue> {
    const target = this.resolve(path);
    let response: Response;
    try {
      response = await fetch(target, { method, signal: options.signal ?? AbortSignal.timeout(240000), credentials: "omit",
        headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json", "Idempotency-Key": options.key ?? randomUUID() } : {}) },
        body: body ? JSON.stringify(body) : undefined });
    } catch { throw new ApiError("请求连接失败", undefined, options.signal?.aborted ? "aborted" : "network_error", true); }
    if (!response.ok) throw await this.error(response);
    const value: unknown = await response.json().catch(() => { throw new ApiError("API 响应不是 JSON", response.status, "contract_error"); });
    if (!isRecord(value)) throw new ApiError("API 响应不是对象", response.status, "contract_error");
    return value;
  }
  private async error(response: Response): Promise<ApiError> {
    const payload: unknown = await response.json().catch(() => null);
    const error = isRecord(payload) && isRecord(payload.error) ? payload.error : {};
    return new ApiError(String(error.message ?? response.statusText), response.status, String(error.code ?? "http_error"),
      error.retryable === true, typeof error.request_id === "string" ? error.request_id : response.headers.get("x-request-id") ?? undefined);
  }
  private resolve(path: string): string {
    const url = new URL(path, `${this.baseUrl}/`);
    if (url.origin !== this.baseUrl || url.username || url.password) throw new ApiError("拒绝跨后端 URL", undefined, "contract_error");
    return url.toString();
  }
}
