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

test("late GET cannot replace a newer checkpoint or return stale state to its caller", async () => {
  const api = new ResearchApiClient("http://localhost:8000");
  let release!: (value: Session) => void; let calls = 0;
  api.status = async () => ++calls === 1 ? new Promise<Session>(resolve => release = resolve) : { ...view("running"), checkpoint_seq: 5 };
  const current = new ResearchSession(api); current.view = view("running");
  const old = current.refresh();
  assert.equal((await current.refresh()).checkpoint_seq, 5);
  release({ ...view("running"), checkpoint_seq: 2 });
  assert.equal((await old).checkpoint_seq, 5); assert.equal(current.view.checkpoint_seq, 5);
});

test("late observer bootstrap preserves the checkpoint obtained by manual status", async () => {
  const api = new ResearchApiClient("http://localhost:8000");
  let release!: (value: Session) => void; let calls = 0;
  api.status = async () => {
    if (++calls === 1) return new Promise<Session>(resolve => release = resolve);
    return { ...view(calls === 2 ? "running" : "completed"), checkpoint_seq: calls === 2 ? 5 : 6 };
  };
  api.events = async function* () {
    yield { event: "done", data: { session_id: "session", run_id: "run", checkpoint_seq: 6, status: "completed" } };
  };
  const current = new ResearchSession(api); current.view = view("running");
  const sequences: unknown[] = [];
  const observing = current.observe(() => {}, state => sequences.push(state.checkpoint_seq), error => assert.fail(String(error)));
  await current.refresh(); release({ ...view("running"), checkpoint_seq: 2 });
  await observing;
  assert.deepEqual(sequences, [5, 6]); assert.equal(current.view.status, "completed");
});

test("late status cannot revert Brief version or remove a frozen Run", async () => {
  const api = new ResearchApiClient("http://localhost:8000");
  const current = new ResearchSession(api); current.view = view("running", 3);
  api.status = async () => view("ask", 2);
  assert.equal((await current.refresh()).brief_version, 3);
  api.status = async () => ({ ...view("confirm", 3), run_id: null, checkpoint_seq: null });
  assert.equal((await current.refresh()).status, "running");
});

test("stale GET after done cannot erase its seq or establish an older terminal state", async () => {
  const api = new ResearchApiClient("http://localhost:8000"); let calls = 0;
  api.status = async () => ++calls < 3 ? view("running") : { ...view("completed"), checkpoint_seq: 2 };
  api.events = async function* () {
    yield { event: "done", data: { session_id: "session", run_id: "run", checkpoint_seq: 2, status: "completed" } };
  };
  const current = new ResearchSession(api); current.view = view("running");
  const sequences: unknown[] = [], errors: unknown[] = [];
  await current.observe(() => {}, state => sequences.push(state.checkpoint_seq), error => errors.push(error), 1);
  assert.deepEqual(sequences, [1, 2, 2]); assert.equal(current.view.status, "completed");
  assert.equal(calls, 3); assert.equal(errors.length, 1);
});

test("status identity mismatches do not replace the current Session or Run", async () => {
  const api = new ResearchApiClient("http://localhost:8000");
  const current = new ResearchSession(api); current.view = view("running");
  for (const update of [{ session_id: "other" }, { run_id: "other-run" }]) {
    api.status = async () => ({ ...view("running"), ...update });
    await assert.rejects(() => current.refresh(), e => e instanceof ApiError && e.code === "contract_error");
    assert.equal(current.view.run_id, "run"); assert.equal(current.view.session_id, "session");
  }
  await assert.rejects(() => current.open("other"));
  assert.equal(current.view.session_id, "session");
});

test("a refresh completing after session switch is abandoned, not used by resume", async () => {
  const api = new ResearchApiClient("http://localhost:8000");
  let release!: (value: Session) => void; let mutations = 0;
  api.status = async () => new Promise<Session>(resolve => release = resolve);
  api.resume = async () => { mutations++; return view("ready"); };
  const current = new ResearchSession(api); current.view = view("failed");
  const resuming = current.resume();
  current.reset(); current.view = { ...view("ask"), session_id: "other" };
  release({ ...view("failed"), resume_allowed: true });
  await assert.rejects(() => resuming, e => e instanceof ApiError && e.code === "aborted");
  assert.equal(mutations, 0); assert.equal(current.view.session_id, "other");
});
