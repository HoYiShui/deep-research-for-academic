import chalk from "chalk";
import { CancellableLoader, CombinedAutocompleteProvider, Editor, Key, Markdown, matchesKey, Spacer, Text, type EditorTheme, type MarkdownTheme, type TUI } from "@earendil-works/pi-tui";
import { ApiError, ResearchApiClient, isRecord } from "./api-client.js";
import { ResearchSession } from "./session.js";

const editorTheme: EditorTheme = { borderColor: chalk.cyan, selectList: { selectedPrefix: chalk.cyan, selectedText: chalk.inverse, description: chalk.dim, scrollInfo: chalk.dim, noMatch: chalk.dim } };
const markdownTheme: MarkdownTheme = { heading: chalk.bold.cyan, link: chalk.blue.underline, linkUrl: chalk.blue.underline, code: s => chalk.bgHex("#1e1e1e").white(s), codeBlock: s => chalk.bgHex("#1e1e1e").white(s), codeBlockBorder: chalk.dim, quote: chalk.dim, quoteBorder: () => chalk.dim("│ "), hr: () => chalk.dim("─".repeat(48)), listBullet: s => chalk.dim(`  ${s}`), bold: chalk.bold, italic: chalk.italic, strikethrough: chalk.strikethrough, underline: chalk.underline };
const json = (value: unknown) => "\`\`\`json\n" + JSON.stringify(value, null, 2) + "\n\`\`\`";
const commands = [
  ["confirm", "确认当前 Brief"], ["patch", "补齐字段（JSON）"], ["status", "读取持久状态"],
  ["report", "读取报告"], ["cancel", "请求取消"], ["resume", "显式恢复失败运行"],
  ["retry", "原幂等键重试失败请求"], ["open", "恢复会话 <UUID>"], ["session", "会话与 CLI 提示"],
  ["sources", "下个研究的来源 papers,web"], ["new", "新研究"], ["connect", "切换后端 origin"],
  ["watch", "只读查询并重新观察"], ["help", "用法"],
];

export class Dr4aApp {
  private readonly status = new Text();
  private readonly editor: Editor;
  private readonly current: ResearchSession;
  private loader?: CancellableLoader;
  private shownReport?: string;
  constructor(private readonly tui: TUI, api: ResearchApiClient) {
    this.current = new ResearchSession(api);
    tui.addChild(new Text(chalk.bold.cyan("DR4A") + chalk.dim(" — academic deep research")));
    tui.addChild(this.status); tui.addChild(new Text(chalk.dim("/help · /confirm 或 Ctrl+Enter 确认 · Ctrl+C 退出（不取消服务端）")));
    tui.addChild(new Spacer(1));
    this.editor = new Editor(tui, editorTheme, { paddingX: 1 });
    this.editor.setAutocompleteProvider(new CombinedAutocompleteProvider(commands.map(([name, description]) => ({ name, description })), process.cwd()));
    this.editor.onSubmit = value => void this.submit(value.trim());
    tui.addChild(this.editor); tui.setFocus(this.editor);
    tui.addInputListener(data => {
      if (matchesKey(data, Key.ctrl("c"))) { this.current.stop(); this.doneLoading(); tui.stop(); process.exit(0); }
      if (matchesKey(data, Key.ctrl("enter"))) { void this.action(() => this.current.confirm()); return { consume: true }; }
      if (matchesKey(data, Key.ctrl("r"))) { this.text("在 confirm 阶段输入反馈并按 Enter，即退回修改。"); return { consume: true }; }
      return undefined;
    });
    this.refresh();
  }
  private beforeEditor(component: Text | Markdown | Spacer | CancellableLoader): void {
    this.tui.children.splice(this.tui.children.indexOf(this.editor), 0, component);
  }
  private text(message: string): void { this.beforeEditor(new Text(message)); this.tui.requestRender(); }
  private markdown(label: string, content: string, color = chalk.cyan): void {
    this.beforeEditor(new Text(color(label))); this.beforeEditor(new Markdown(content, 1, 0, markdownTheme));
    this.beforeEditor(new Spacer(1)); this.tui.requestRender();
  }
  private refresh(): void {
    const view = this.current.view;
    this.status.setText(chalk.dim(`API ${this.current.api.baseUrl} │ session ${view?.session_id ?? "—"} │ ${view?.status ?? "—"} / ${view?.phase ?? "—"} │ brief v${view?.brief_version ?? "—"} │ seq ${view?.checkpoint_seq ?? "—"}`));
    this.tui.requestRender();
  }
  private loading(): void {
    this.loader = new CancellableLoader(this.tui, chalk.cyan, chalk.dim, "Waiting for API...");
    this.loader.onAbort = () => this.text("Esc 不取消已发送的变更；请等待响应，网络失败可 /retry。");
    this.beforeEditor(this.loader); this.editor.disableSubmit = true;
  }
  private doneLoading(): void {
    if (this.loader) { this.tui.removeChild(this.loader); this.loader.dispose(); this.loader = undefined; }
    this.editor.disableSubmit = false; this.tui.requestRender();
  }
  private async action(request: () => Promise<unknown>): Promise<void> {
    if (this.current.busy || this.loader) return this.text(chalk.yellow("请等待当前请求结束。"));
    this.loading();
    try { await request(); this.present(); }
    catch (error) { this.error(error); this.refresh(); }
    finally { this.doneLoading(); }
  }
  private async submit(value: string): Promise<void> {
    if (!value) return;
    this.editor.setText("");
    if (value.startsWith("/")) return this.command(value);
    this.markdown("You", value, chalk.green);
    await this.action(() => this.current.send(value));
  }
  private present(): void {
    const view = this.current.view; this.refresh();
    if (!view) return;
    if (view.status === "ask") {
      this.markdown("Clarify", json({ questions: view.questions, missing_fields: view.missing_fields, brief_draft: view.brief_draft }), chalk.yellow);
      if (view.clarification_limit_reached) this.text("已达自动澄清上限；用 /patch {字段:值} 明确补齐十字段。");
    } else if (view.status === "confirm") {
      this.markdown("Research Brief", json(view.research_brief));
      this.text("/confirm 或 Ctrl+Enter 接受；输入反馈退回修改。");
    } else {
      if (view.status === "ready") this.text("Brief/Run 已保存，等待执行器领取。可 /status、/cancel；/session 获取 CLI 调试命令。");
      this.watch();
    }
  }
  private watch(): void {
    void this.current.observe(event => this.markdown(`Event · ${event.event}`, json(event.data), chalk.dim),
      view => { this.refresh(); if (view.status === "completed") void this.showReport(); else if (view.status === "failed") this.text(chalk.yellow(`运行失败：${isRecord(view.failure) ? view.failure.code : "unknown"}；/status 查看原因，/session 导出 checkpoint 调试。`)); },
      error => this.error(error));
  }
  private async showReport(): Promise<void> {
    const id = this.current.view?.session_id;
    if (!id || this.shownReport === id) return;
    this.shownReport = id;
    try {
      const value = await this.current.api.report(id);
      if (this.current.view?.session_id !== id) return;
      if (typeof value.report !== "string") throw new ApiError("报告响应缺少 Markdown", undefined, "contract_error");
      this.markdown("Report", value.report, chalk.green);
    } catch (error) { this.shownReport = undefined; this.error(error); }
  }
  private async command(raw: string): Promise<void> {
    const [name, ...parts] = raw.slice(1).split(/\s+/); const arg = parts.join(" ");
    try {
      if (name === "help") return this.markdown("Help", commands.map(([n, d]) => `- /${n} — ${d}`).join("\n"));
      if (name === "session") return this.text(`session: ${this.current.view?.session_id ?? "—"}\n独立调试库：uv run python -m cli dump ${this.current.view?.session_id ?? "<UUID>"} --debug-db --json\n普通后端库则去掉 --debug-db。将结果的 state 字段保存为 phase 输入；phase 不写回此会话。`);
      if (name === "new") { this.current.reset(); this.shownReport = undefined; this.refresh(); return; }
      if (name === "sources") {
        if (this.current.view) throw new ApiError("先 /new；来源在创建前设置");
        const values = [...new Set(arg.split(","))];
        if (!values.length || values.some(v => v !== "papers" && v !== "web")) throw new ApiError("用 /sources papers 或 /sources papers,web；KB 暂不接入");
        this.current.sources = { categories: values as ("papers" | "web")[], knowledge_base_ids: [] };
        return this.text(`下个研究来源：${values.join(",")}`);
      }
      if (name === "connect") {
        const api = new ResearchApiClient(arg); await api.health(); this.current.reset(); this.current.api = api;
        this.shownReport = undefined; this.refresh(); return;
      }
      if (name === "open") {
        if (!/^[0-9a-f-]{36}$/i.test(arg)) throw new ApiError("/open 需要 Session UUID");
        await this.current.open(arg); this.shownReport = undefined; this.present(); return;
      }
      if (name === "status") { this.markdown("Status", json(await this.current.refresh()), chalk.dim); this.refresh(); return; }
      if (name === "watch") { this.watch(); return; }
      if (name === "report") { this.shownReport = undefined; await this.showReport(); return; }
      if (name === "confirm") return this.action(() => this.current.confirm());
      if (name === "cancel") return this.action(() => this.current.cancel());
      if (name === "resume") return this.action(() => this.current.resume());
      if (name === "retry") return this.action(() => this.current.retry());
      if (name === "patch") {
        const patch: unknown = JSON.parse(arg); if (!isRecord(patch)) throw new ApiError("/patch 需要 JSON 对象");
        return this.action(() => this.current.send("用户明确补齐任务书字段", patch));
      }
      throw new ApiError("未知命令；/help 查看用法");
    } catch (error) { this.error(error); }
  }
  private error(value: unknown): void {
    if (value instanceof ApiError && value.code === "aborted") return;
    if (value instanceof ApiError && value.code === "network_error")
      return this.text(chalk.red(`无法连接后端：${this.current.api.baseUrl}`) + chalk.dim("\n检查后端；失败的变更用 /retry 原键重试，SSE 重连不会启动新研究。"));
    if (value instanceof ApiError && value.statusCode === 401)
      return this.text(chalk.yellow("后端启用了认证；当前 TUI 使用匿名开发模式，不发送 token/cookie。"));
    const detail = value instanceof ApiError ? `${value.statusCode ?? "请求"} ${value.code}: ${value.message}${value.requestId ? " [request " + value.requestId + "]" : ""}` : "客户端输入或处理失败";
    this.text(chalk.red(detail));
  }
}
