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
    enqueue: (row) => operation("readwrite", (s) => {
      const r = s.get(row.id);
      r.onsuccess = () => { if (!r.result) s.add(row); };
      return r;
    }),
    put: (row) => operation("readwrite", (s) => s.put(row)),
    remove: (id) => operation("readwrite", (s) => s.delete(id)),
  };
  async function drain(storage, send, now = Date.now) {
    for (const row of await storage.all()) {
      if (row.nextAt > now()) continue;
      try {
        await send(row);
        await storage.remove(row.id); // only a server ACK removes durable data
      } catch (error) {
        row.attempts = (row.attempts || 0) + 1;
        row.nextAt = now() + delay(row.attempts);
        row.error = String(error.message || error).slice(0, 200);
        await storage.put(row);
      }
    }
  }
  root.ToolSpyOutbox = { store, drain, delay };
  if (typeof module !== "undefined") module.exports = root.ToolSpyOutbox;
})(globalThis);
