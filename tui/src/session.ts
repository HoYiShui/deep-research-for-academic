import { randomUUID } from "node:crypto";
import { ApiError, ResearchApiClient, session, type RecordValue, type Session, type SourceSelection, type SseEvent } from "./api-client.js";

export class ResearchSession {
  view?: Session;
  sources: SourceSelection = { categories: ["papers", "web"], knowledge_base_ids: [] };
  busy = false;
  private pending?: () => Promise<RecordValue>;
  private stream?: AbortController;
  constructor(public api: ResearchApiClient) {}
  reset(): void { this.stop(); this.view = undefined; this.pending = undefined; }
  stop(): void { this.stream?.abort(); this.stream = undefined; }
  async open(id: string): Promise<Session> {
    if (this.busy) throw new ApiError("请等待当前请求结束");
    const value = await this.api.status(id);
    if (value.session_id !== id) throw new ApiError("状态响应身份不符", undefined, "contract_error");
    this.stop(); this.pending = undefined; return this.acceptStatus(id, value);
  }
  async refresh(): Promise<Session> {
    if (!this.view) throw new ApiError("没有活动会话");
    const id = this.view.session_id, value = await this.api.status(id);
    if (this.view?.session_id !== id) throw new ApiError("活动会话已切换", undefined, "aborted");
    return this.acceptStatus(id, value);
  }
  private acceptStatus(id: string, value: Session): Session {
    if (value.session_id !== id) throw new ApiError("状态响应身份不符", undefined, "contract_error");
    const current = this.view;
    if (current?.session_id === id) {
      if (value.brief_version < current.brief_version) return current;
      if (typeof current.run_id === "string") {
        if (value.run_id == null) return current; // A pre-freeze GET arrived late.
        if (value.run_id !== current.run_id) throw new ApiError("会话 Run 身份变化", undefined, "contract_error");
        if (Number(value.checkpoint_seq ?? 0) < Number(current.checkpoint_seq ?? 0)) return current;
      }
    }
    this.view = value; return value;
  }
  async send(content: string, patch?: RecordValue): Promise<RecordValue> {
    const view = this.view, api = this.api, options = { key: randomUUID() };
    if (!view) {
      const sources = structuredClone(this.sources);
      return this.perform(() => api.createResearch(content, sources, options));
    }
    if (view.status === "ask") return this.perform(() => api.message(view.session_id, content, view.brief_version, patch, options));
    if (view.status === "confirm" && !patch) return this.perform(() => api.confirm(view.session_id, false, view.brief_version, content, options));
    throw new ApiError("当前阶段不接受聊天输入；用 /new 创建研究或 /open 恢复会话");
  }
  async confirm(): Promise<RecordValue> {
    const view = this.view, api = this.api, options = { key: randomUUID() };
    if (view?.status !== "confirm") throw new ApiError("当前没有可确认的 Brief");
    return this.perform(() => api.confirm(view.session_id, true, view.brief_version, undefined, options));
  }
  async cancel(): Promise<RecordValue> {
    const id = this.view?.session_id, api = this.api, options = { key: randomUUID() };
    if (!id) throw new ApiError("没有活动会话");
    return this.perform(() => api.cancel(id, options));
  }
  async resume(): Promise<RecordValue> {
    const view = await this.refresh(), api = this.api, options = { key: randomUUID() };
    if (view.status !== "failed" || view.resume_allowed !== true || !Number.isInteger(view.checkpoint_seq))
      throw new ApiError("当前运行不可恢复");
    return this.perform(() => api.resume(view.session_id, Number(view.checkpoint_seq), options));
  }
  async retry(): Promise<RecordValue> {
    if (!this.pending) throw new ApiError("没有可重试的请求");
    return this.perform(this.pending);
  }
  private async perform(request: () => Promise<RecordValue>): Promise<RecordValue> {
    if (this.busy) throw new ApiError("当前请求尚未结束");
    this.busy = true; this.pending = request;
    try {
      const result = await request();
      if (result.status === "cancelled" || result.status === "cancelling") {
        if (!this.view || this.view.session_id !== result.session_id) throw new ApiError("取消响应身份不符", undefined, "contract_error");
        this.view = { ...this.view, status: result.status };
      } else this.view = session(result);
      this.pending = undefined; return result;
    } catch (error) {
      if (!(error instanceof ApiError) || (!error.retryable && error.code !== "network_error")) this.pending = undefined;
      if (error instanceof ApiError && error.statusCode === 409 && this.view) await this.refresh().catch(() => {});
      throw error;
    } finally { this.busy = false; }
  }
  async observe(onEvent: (event: SseEvent) => void, onState: (view: Session) => void,
    onDiagnostic: (error: unknown) => void, delayMs = 2000): Promise<void> {
    this.stop();
    const controller = new AbortController(); this.stream = controller;
    const id = this.view?.session_id, api = this.api;
    if (!id) return;
    const seen = new Set<string>(); let attempts = 0;
    try {
      while (!controller.signal.aborted && this.view?.session_id === id) {
        try {
          const received = await api.status(id);
          if (controller.signal.aborted || this.view?.session_id !== id) return;
          const view = this.acceptStatus(id, received); onState(view);
          if (["completed", "failed", "cancelled"].includes(view.status) || typeof view.sse_url !== "string") return;
          for await (const event of api.events(view.sse_url, controller.signal)) {
            if (controller.signal.aborted || this.view?.session_id !== id) return;
            if (!["phase", "progress", "rework", "error", "done"].includes(event.event)) continue;
            if (event.data.session_id !== id || event.data.run_id !== view.run_id ||
                !Number.isInteger(event.data.checkpoint_seq)) throw new ApiError("SSE 身份或序号不符", undefined, "contract_error");
            if (Number(event.data.checkpoint_seq) < Number(this.view.checkpoint_seq ?? 0)) continue;
            if (event.id && seen.has(event.id)) continue;
            if (event.id) { seen.add(event.id); if (seen.size > 1000) seen.delete(seen.values().next().value!); }
            if (event.event === "done" && !["completed", "failed", "cancelled"].includes(String(event.data.status)))
              throw new ApiError("未知 done 状态", undefined, "contract_error");
            this.view = { ...this.view, checkpoint_seq: event.data.checkpoint_seq };
            if (event.event === "phase") {
              if (!["ready", "running", "cancelling"].includes(String(event.data.status)))
                throw new ApiError("未知 phase 状态", undefined, "contract_error");
              this.view = { ...this.view, status: String(event.data.status), phase: event.data.phase };
              onState(this.view);
            }
            onEvent(event);
            if (event.event === "done" || (event.event === "error" && event.data.fatal === true)) break;
          }
          // EOF/fatal/done: only GET can establish the current persisted state.
          const receivedLatest = await api.status(id);
          if (controller.signal.aborted || this.view?.session_id !== id) return;
          const latest = this.acceptStatus(id, receivedLatest); onState(latest);
          if (["completed", "failed", "cancelled"].includes(latest.status)) return;
          throw new ApiError("SSE 已断开，将只读查询后重连", undefined, "network_error", true);
        } catch (error) {
          if (controller.signal.aborted) return;
          onDiagnostic(error);
          if (error instanceof ApiError && (error.statusCode === 401 || error.code === "contract_error")) return;
          if (++attempts >= 5) return;
        }
        if (controller.signal.aborted) return;
        await new Promise<void>(resolve => {
          const finish = () => { clearTimeout(timer); controller.signal.removeEventListener("abort", finish); resolve(); };
          const timer = setTimeout(finish, delayMs); controller.signal.addEventListener("abort", finish, { once: true });
        });
      }
    } finally { if (this.stream === controller) this.stream = undefined; }
  }
}
