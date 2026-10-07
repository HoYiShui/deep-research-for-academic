import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, ResearchApiClient, type Session, type SseEvent } from "../src/api-client.js";
import { ResearchSession } from "../src/session.js";

const view = (status = "ask", version = 1): Session => ({ session_id: "session", status, brief_version: version, run_id: "run", checkpoint_seq: 1, sse_url: "/events" });

test("failed mutation retry reuses original version/body/key, no automatic retry", async () => {
  const api = new ResearchApiClient("http://localhost:8000"); const calls: unknown[] = [];
  api.message = async (...args) => {
    calls.push(structuredClone(args));
    if (calls.length === 1) throw new ApiError("lost response", undefined, "network_error", true);
    return view("ask", 2);
  };
  const current = new ResearchSession(api); current.view = view();
  await assert.rejects(() => current.send("answer"));
  assert.equal(calls.length, 1);
  await current.retry();
  assert.deepEqual(calls[0], calls[1]); assert.equal(current.view.brief_version, 2);
  await assert.rejects(() => current.retry());
});

test("stale brief reads GET and never confirms an unseen new version", async () => {
  const api = new ResearchApiClient("http://localhost:8000"); let calls = 0;
  api.confirm = async () => { calls++; throw new ApiError("stale", 409, "stale_brief"); };
  api.status = async () => view("confirm", 3);
  const current = new ResearchSession(api); current.view = view("confirm", 2);
  await assert.rejects(() => current.confirm()); assert.equal(calls, 1);
  assert.equal(current.view.brief_version, 3); await assert.rejects(() => current.retry());
});

test("running text cannot silently create another research", async () => {
  const current = new ResearchSession(new ResearchApiClient("http://localhost:8000"));
  current.view = view("running"); await assert.rejects(() => current.send("hello"));
});

test("resume checks latest persisted eligibility and sequence", async () => {
  const api = new ResearchApiClient("http://localhost:8000"); const calls: unknown[] = [];
  api.status = async () => ({ ...view("failed"), checkpoint_seq: 7, resume_allowed: true });
  api.resume = async (...args) => { calls.push(args); return view("ready"); };
  const current = new ResearchSession(api); current.view = view("failed"); await current.resume();
  assert.equal((calls[0] as unknown[])[1], 7);
});

test("SSE dedup is by event ID, not seq; failed done does not fetch report", async () => {
  const api = new ResearchApiClient("http://localhost:8000"); let gets = 0;
  api.status = async () => view(++gets > 1 ? "failed" : "running");
  api.report = async () => { throw new Error("must not fetch report"); };
  const frame = (id: string, event = "progress"): SseEvent => ({
    event, id, data: { session_id: "session", run_id: "run", checkpoint_seq: 1, ...(event === "done" ? { status: "failed" } : {}) },
  });
  api.events = async function* () { yield frame("a"); yield frame("a"); yield frame("b"); yield frame("c", "done"); };
  const current = new ResearchSession(api); current.view = view("running");
  const seen: string[] = []; await current.observe(e => seen.push(e.id!), () => {}, () => {});
  assert.deepEqual(seen, ["a", "b", "c"]); assert.equal(current.view.status, "failed");
});

test("EOF reconnect is read-only and stop abandons old observation", async () => {
  const api = new ResearchApiClient("http://localhost:8000"); let streams = 0, gets = 0;
  api.status = async () => { gets++; return view("running"); };
  api.events = async function* () { streams++; };
  const current = new ResearchSession(api); current.view = view("running");
  const pending = current.observe(() => {}, () => {}, () => current.stop(), 1);
  await pending; assert.equal(streams, 1); assert.equal(gets, 2);
});

test("unknown done state is contract failure, not success", async () => {
  const api = new ResearchApiClient("http://localhost:8000");
  api.status = async () => view("running");
  api.events = async function* () { yield { event: "done", data: { session_id: "session", run_id: "run", checkpoint_seq: 1, status: "done" } }; };
  const current = new ResearchSession(api); current.view = view("running"); const errors: unknown[] = [];
  await current.observe(() => assert.fail("invalid frame"), () => {}, e => errors.push(e));
  assert.ok(errors[0] instanceof ApiError); assert.equal(errors[0].code, "contract_error");
});
