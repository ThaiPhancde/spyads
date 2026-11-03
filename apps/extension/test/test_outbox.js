const assert = require("node:assert/strict");
const { test } = require("node:test");
const { drain, delay } = require("../outbox.js");
function memory() {
  const rows = new Map();
  return { rows, all: async () => structuredClone([...rows.values()]),
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
