/* Durable at-least-once delivery; server receipt makes retries idempotent. */
(function (root) {
  const delay = (attempt) => Math.min(3600000, 1000 * 2 ** Math.min(attempt, 12));
  function open() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open("toolspy-outbox", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("batches", { keyPath: "id" });
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  async function operation(mode, action) {
    const db = await open();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction("batches", mode);
        const request = action(tx.objectStore("batches"));
        tx.oncomplete = () => resolve(request.result);
        tx.onerror = tx.onabort = () => reject(tx.error || new Error("Outbox transaction aborted"));
      });
    } finally { db.close(); }
  }
  const store = {
    all: () => operation("readonly", (s) => s.getAll()),
    count: () => operation("readonly", (s) => s.count()),
    enqueue: (row) => operation("readwrite", (s) => {
      const r = s.get(row.id);
      r.onsuccess = () => { if (!r.result) s.add(row); };
      return r;
    }),
    put: (row) => operation("readwrite", (s) => s.put(row)),
    remove: (id) => operation("readwrite", (s) => s.delete(id)),
  };
  // The server refused the batch itself (bad payload, key reused with other data, too large): retrying never helps.
  // The row is kept (dead) so nothing is silently lost; auth / rate-limit / server errors keep retrying.
  const PERMANENT = new Set([400, 404, 409, 413, 422]);
  // ponytail: a row cap, not a byte quota — 5,000 batches of ≤20 ads stay far below the browser's IndexedDB budget
  const MAX_ROWS = 5000;
  async function queue(storage, row) {
    if ((await storage.count()) >= MAX_ROWS) {
      throw new Error("Hàng đợi offline đầy (" + MAX_ROWS + " batch) — kiểm tra kết nối tới ToolSpy rồi bấm Gửi lại");
    }
    await storage.enqueue(row);
  }
  // "Gửi lại ngay" in the popup: refused (dead) and backed-off rows are sent on the next drain
  async function retry(storage) {
    let n = 0;
    for (const row of await storage.all()) {
      if (row.dead || row.nextAt > 0) { row.dead = false; row.nextAt = 0; await storage.put(row); n++; }
    }
    return n;
  }
  async function stats(storage) {
    const rows = await storage.all();
    return { pending: rows.filter((r) => !r.dead).length, dead: rows.filter((r) => r.dead).length };
  }
  async function drain(storage, send, now = Date.now, rand = Math.random) {
    for (const row of await storage.all()) {
      if (row.dead || row.nextAt > now()) continue;
      try {
        await send(row);
        await storage.remove(row.id); // only a server ACK removes durable data
      } catch (error) {
        row.attempts = (row.attempts || 0) + 1;
        row.nextAt = now() + Math.round(delay(row.attempts) * (0.8 + 0.4 * rand())); // ±20% jitter: tabs do not retry in lockstep
        row.error = String(error.message || error).slice(0, 200);
        if (PERMANENT.has(error.status)) row.dead = true;
        await storage.put(row);
      }
    }
  }
  root.ToolSpyOutbox = { store, drain, delay, queue, retry, stats, MAX_ROWS };
  if (typeof module !== "undefined") module.exports = root.ToolSpyOutbox;
})(globalThis);
