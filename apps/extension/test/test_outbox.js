const assert = require("node:assert/strict");
const { test } = require("node:test");
const { drain, delay, queue, retry, stats, MAX_ROWS } = require("../outbox.js");
function memory() {
  const rows = new Map();
  return { rows, all: async () => structuredClone([...rows.values()]), count: async () => rows.size,
    enqueue: async (r) => { if (!rows.has(r.id)) rows.set(r.id, structuredClone(r)); },
    put: async (r) => rows.set(r.id, structuredClone(r)), remove: async (id) => rows.delete(id) };
}
test("offline failure survives worker restart, backoff and lost ACK reuse the same key", async () => {
  const store = memory();
  await store.put({ id: "stable", body: { idempotency_key: "stable" }, nextAt: 0 });
  let now = 100, attempts = 0;
  await drain(store, async () => { attempts++; throw Error("offline"); }, () => now);
  assert.equal(store.rows.get("stable").attempts, 1);
  await drain(store, async () => { attempts++; }, () => now);
  assert.equal(attempts, 1);
  now = store.rows.get("stable").nextAt;
  const saved = new Set();
  await drain(store, async (r) => { saved.add(r.body.idempotency_key); throw Error("ACK lost"); }, () => now);
  now = store.rows.get("stable").nextAt;
  // A new drain invocation models a restarted worker reading persisted rows.
  await drain(store, async (r) => { saved.add(r.body.idempotency_key); }, () => now);
  assert.equal(saved.size, 1);
  assert.equal(store.rows.size, 0);
});
test("bounded exponential retry and rejected batches retained", async () => {
  assert.ok(delay(3) > delay(2)); assert.equal(delay(100), 3600000);
  const store = memory(); await store.put({ id: "bad", nextAt: 0 });
  await drain(store, async () => { throw Error("401"); }, () => 0);
  assert.equal(store.rows.size, 1); assert.equal(store.rows.get("bad").error, "401");
});
test("permanent rejections are kept but never retried; auth errors keep retrying", async () => {
  const store = memory();
  await store.put({ id: "bad", nextAt: 0 }); await store.put({ id: "auth", nextAt: 0 });
  let calls = 0;
  const send = async (r) => { calls++; throw Object.assign(Error(r.id), { status: r.id === "bad" ? 422 : 401 }); };
  await drain(store, send, () => 0);
  assert.equal(store.rows.get("bad").dead, true); assert.ok(!store.rows.get("auth").dead);
  calls = 0;
  await drain(store, send, () => 10 ** 13);
  assert.equal(calls, 1); assert.equal(store.rows.size, 2);
});
test("retry times are jittered within ±20% so many tabs do not hit the API together", async () => {
  for (const [rand, lo, hi] of [[0, 0.8, 0.8], [1, 1.2, 1.2], [0.5, 1, 1]]) {
    const store = memory(); await store.put({ id: "j", nextAt: 0 });
    await drain(store, async () => { throw Error("offline"); }, () => 0, () => rand);
    const wait = store.rows.get("j").nextAt;
    assert.ok(wait >= delay(1) * lo && wait <= delay(1) * hi, `${rand}: ${wait}`);
  }
});
test("a full outbox refuses the new batch loudly and keeps every stored one", async () => {
  const store = memory();
  for (let i = 0; i < MAX_ROWS; i++) store.rows.set("r" + i, { id: "r" + i });
  await assert.rejects(queue(store, { id: "new" }), /đầy/);
  assert.equal(store.rows.size, MAX_ROWS); assert.ok(!store.rows.has("new"));
  store.rows.delete("r0");
  await queue(store, { id: "new" });
  assert.ok(store.rows.has("new"));
});
test("retry-now revives refused and waiting batches and reports what is queued", async () => {
  const store = memory();
  await store.put({ id: "dead", dead: true, nextAt: 99, attempts: 4 }); await store.put({ id: "wait", nextAt: 10 ** 13, attempts: 2 });
  assert.deepEqual(await stats(store), { pending: 1, dead: 1 });
  assert.equal(await retry(store), 2);
  const rows = [...store.rows.values()];
  assert.ok(rows.every((r) => !r.dead && r.nextAt === 0)); assert.deepEqual(await stats(store), { pending: 2, dead: 0 });
});
