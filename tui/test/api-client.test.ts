import assert from "node:assert/strict";
import { createServer, type RequestListener } from "node:http";
import test from "node:test";
import { ApiError, ResearchApiClient } from "../src/api-client.js";

async function fixture(handler: RequestListener): Promise<{ baseUrl: string; close(): Promise<void> }> {
  const server = createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  return { baseUrl: `http://127.0.0.1:${address.port}`, close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())) };
}

test("anonymous research requests preserve the public path and body", async () => {
  const server = await fixture((request, response) => {
    assert.equal(request.url, "/research"); assert.equal(request.method, "POST"); assert.equal(request.headers.authorization, undefined); assert.equal(request.headers.cookie, undefined);
    let body = ""; request.on("data", (chunk) => body += chunk); request.on("end", () => { assert.deepEqual(JSON.parse(body), { query: "compare methods" }); response.setHeader("content-type", "application/json"); response.end(JSON.stringify({ session_id: "s1", status: "ask" })); });
  });
  try { assert.deepEqual(await new ResearchApiClient(server.baseUrl).createResearch("compare methods"), { session_id: "s1", status: "ask" }); } finally { await server.close(); }
});

test("SSE frames preserve their event order", async () => {
  const server = await fixture((_, response) => { response.writeHead(200, { "content-type": "text/event-stream" }); response.end('event: phase\ndata: {"phase":"research"}\n\nevent: done\ndata: {"status":"done"}\n\n'); });
  try { const events = []; for await (const event of new ResearchApiClient(server.baseUrl).events("/events")) events.push(event); assert.deepEqual(events, [{ event: "phase", data: { phase: "research" }, id: undefined }, { event: "done", data: { status: "done" }, id: undefined }]); } finally { await server.close(); }
});

test("structured failures become ApiError", async () => {
  const server = await fixture((_, response) => { response.writeHead(401, { "content-type": "application/json" }); response.end(JSON.stringify({ error: { code: "unauthenticated", message: "No auth" } })); });
  try { await assert.rejects(() => new ResearchApiClient(server.baseUrl).createResearch("q"), (error: unknown) => error instanceof ApiError && error.statusCode === 401 && error.code === "unauthenticated"); } finally { await server.close(); }
});
