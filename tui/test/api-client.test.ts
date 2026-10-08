import assert from "node:assert/strict";
import { createServer, type RequestListener } from "node:http";
import test from "node:test";
import { ApiError, ResearchApiClient } from "../src/api-client.js";
import { ResearchSession } from "../src/session.js";

async function fixture(handler: RequestListener): Promise<{ baseUrl: string; close(): Promise<void> }> {
  const server = createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  return { baseUrl: `http://127.0.0.1:${address.port}`, close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())) };
}

test("anonymous research requests preserve the public path and body", async () => {
  const server = await fixture((request, response) => {
    assert.equal(request.url, "/research"); assert.equal(request.method, "POST"); assert.equal(request.headers.authorization, undefined); assert.equal(request.headers.cookie, undefined);
    assert.equal(typeof request.headers["idempotency-key"], "string");
    let body = ""; request.on("data", (chunk) => body += chunk); request.on("end", () => { assert.deepEqual(JSON.parse(body), { query: "compare methods", sources: ["papers", "web"], knowledge_base_ids: [] }); response.setHeader("content-type", "application/json"); response.end(JSON.stringify({ session_id: "s1", status: "ask", brief_version: 1 })); });
  });
  try { assert.deepEqual(await new ResearchApiClient(server.baseUrl).createResearch("compare methods", { categories: ["papers", "web"], knowledge_base_ids: [] }), { session_id: "s1", status: "ask", brief_version: 1 }); } finally { await server.close(); }
});

test("SSE frames preserve their event order", async () => {
  const server = await fixture((_, response) => { response.writeHead(200, { "content-type": "text/event-stream" }); response.end('event: phase\ndata: {"phase":"research"}\n\nevent: done\ndata: {"status":"done"}\n\n'); });
  try { const events = []; for await (const event of new ResearchApiClient(server.baseUrl).events("/events")) events.push(event); assert.deepEqual(events, [{ event: "phase", data: { phase: "research" }, id: undefined }, { event: "done", data: { status: "done" }, id: undefined }]); } finally { await server.close(); }
});

test("structured failures become ApiError", async () => {
  const server = await fixture((_, response) => { response.writeHead(401, { "content-type": "application/json" }); response.end(JSON.stringify({ error: { code: "unauthenticated", message: "No auth" } })); });
  try { await assert.rejects(() => new ResearchApiClient(server.baseUrl).createResearch("q", { categories: ["papers"], knowledge_base_ids: [] }), (error: unknown) => error instanceof ApiError && error.statusCode === 401 && error.code === "unauthenticated"); } finally { await server.close(); }
});

for (const statusCode of [200, 503]) test(`lost ${statusCode} response body preserves the mutation key for explicit retry`, async () => {
  const calls: { key: unknown; body: unknown }[] = [];
  const server = await fixture((request, response) => {
    let body = "";
    request.on("data", chunk => body += chunk);
    request.on("end", () => {
      calls.push({ key: request.headers["idempotency-key"], body: JSON.parse(body) });
      if (calls.length === 1) {
        // A mutation may have committed; the response body is not delivered.
        response.writeHead(statusCode, { "content-type": "application/json", "content-length": "10000" });
        response.flushHeaders(); response.write('{"session_id":');
        setTimeout(() => response.destroy(), 10);
      } else {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ session_id: "same-session", status: "ask", brief_version: 1 }));
      }
    });
  });
  const current = new ResearchSession(new ResearchApiClient(server.baseUrl));
  try {
    await assert.rejects(() => current.send("original query"), e => e instanceof ApiError && e.code === "network_error" && e.retryable);
    assert.equal(calls.length, 1); assert.equal(current.view, undefined);
    current.sources.categories = ["web"];
    const replay = await current.retry();
    assert.equal(calls.length, 2); assert.deepEqual(calls[0], calls[1]);
    assert.equal(replay.session_id, "same-session");
    await assert.rejects(() => current.retry());
  } finally { await server.close(); }
});

test("complete malformed JSON is a contract error, not a retryable transport error", async () => {
  const server = await fixture((_, response) => {
    response.writeHead(200, { "content-type": "application/json" }); response.end("{invalid-json}");
  });
  try {
    await assert.rejects(() => new ResearchApiClient(server.baseUrl).status("session"),
      e => e instanceof ApiError && e.code === "contract_error" && !e.retryable);
  } finally { await server.close(); }
});

test("typed source selection preserves knowledge-base scope without claiming backend support", async () => {
  const id = "00000000-0000-4000-8000-000000000002";
  const server = await fixture((request, response) => {
    let body = "";
    request.on("data", chunk => body += chunk);
    request.on("end", () => {
      assert.deepEqual(JSON.parse(body), { query: "public research", sources: ["knowledge_base"], knowledge_base_ids: [id] });
      response.writeHead(404, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: { code: "knowledge_base_not_found", message: "Unavailable scope", retryable: false } }));
    });
  });
  try {
    await assert.rejects(() => new ResearchApiClient(server.baseUrl).createResearch("public research", {
      categories: ["knowledge_base"], knowledge_base_ids: [id],
    }), error => error instanceof ApiError && error.code === "knowledge_base_not_found");
  } finally { await server.close(); }
});

test("SSE handles CRLF split across chunks and retains JSON unicode", async () => {
  const server = await fixture((_, response) => {
    response.writeHead(200, { "content-type": "text/event-stream" });
    response.write('event: progress\r');
    setImmediate(() => response.end('\ndata: {"message":"研究进度"}\r\n\r\n'));
  });
  try {
    const result = [];
    for await (const event of new ResearchApiClient(server.baseUrl).events("/events")) result.push(event);
    assert.deepEqual(result, [{ event: "progress", id: undefined, data: { message: "研究进度" } }]);
  } finally { await server.close(); }
});

test("SSE rejects non-SSE response and foreign-origin URLs before I/O", async () => {
  const server = await fixture((_, response) => response.end("{}"));
  const api = new ResearchApiClient(server.baseUrl);
  try {
    await assert.rejects(async () => { for await (const _ of api.events("/events")) {} }, e => e instanceof ApiError && e.code === "contract_error");
    await assert.rejects(async () => { for await (const _ of api.events("https://foreign.invalid/events")) {} }, e => e instanceof ApiError && e.code === "contract_error");
  } finally { await server.close(); }
});
