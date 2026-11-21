async function req(path, opts = {}) {
  const r = await fetch(`/api${path}`, {
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

export const api = {
  search: (q, start = 0) => req(`/search?q=${encodeURIComponent(q)}&start=${start}`),
  add: (input) => req("/papers", { method: "POST", body: { input } }),
  list: () => req("/papers"),
  get: (id) => req(`/papers/${id}`),
  patch: (id, body) => req(`/papers/${id}`, { method: "PATCH", body }),
  remove: (id) => req(`/papers/${id}`, { method: "DELETE" }),
  review: (id) => req(`/papers/${id}/review`),
  saveReview: (id, content) => req(`/papers/${id}/review`, { method: "PUT", body: { content } }),
  saveNotes: (id, content) => req(`/papers/${id}/notes`, { method: "PUT", body: { content } }),
  history: (id) => req(`/papers/${id}/review/history`),
  historyItem: (id, stamp) => req(`/papers/${id}/review/history/${stamp}`),
  retryPdf: (id) => req(`/papers/${id}/retry-pdf`, { method: "POST" }),
  generate: (id, section) => req(`/papers/${id}/generate-review`, { method: "POST", body: section ? { section } : {} }),
  localSearch: (q) => req(`/local-search?q=${encodeURIComponent(q)}`),
  llm: () => req("/settings/llm"),
  llmPut: (name, body) => req(`/settings/llm/profiles/${encodeURIComponent(name)}`, { method: "PUT", body }),
  llmDel: (name) => req(`/settings/llm/profiles/${encodeURIComponent(name)}`, { method: "DELETE" }),
  llmActive: (name) => req("/settings/llm/active", { method: "POST", body: { name } }),
  llmTest: () => req("/settings/llm/test", { method: "POST" }),
  reextract: (id) => req(`/papers/${id}/reextract`, { method: "POST" }),
  pdfUrl: (id) => `/api/papers/${id}/pdf`,
};

export const STATUSES = { "to-read": "읽을 예정", reviewing: "리뷰 중", reviewed: "리뷰 완료", reproducing: "재현 중" };
