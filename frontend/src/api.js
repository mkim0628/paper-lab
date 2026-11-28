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
  code: (id) => req(`/papers/${id}/code`),
  codeSearch: (id) => req(`/papers/${id}/code/search`, { method: "POST" }),
  codeAdd: (id, url) => req(`/papers/${id}/code/add`, { method: "POST", body: { url } }),
  codeSelect: (id, full_name, no_official = false) => req(`/papers/${id}/code/select`, { method: "PUT", body: { full_name, no_official } }),
  figures: (id) => req(`/papers/${id}/figures`),
  figuresExtract: (id) => req(`/papers/${id}/figures/extract`, { method: "POST" }),
  figPatch: (id, fid, body) => req(`/papers/${id}/figures/${fid}`, { method: "PUT", body }),
  figAdd: (id, body) => req(`/papers/${id}/figures`, { method: "POST", body }),
  figDelete: (id, fid) => req(`/papers/${id}/figures/${fid}`, { method: "DELETE" }),
  figUrl: (id, it) => `/api/papers/${id}/figures/${it.id}.png?v=${(it.bbox || []).join("-")}`,
  pageUrl: (id, n, dpi = 100) => `/api/papers/${id}/pages/${n}.png?dpi=${dpi}`,
  related: (id) => req(`/papers/${id}/related`),
  relatedRefresh: (id) => req(`/papers/${id}/related/refresh`, { method: "POST" }),
  relatedGuide: (id) => req(`/papers/${id}/related/guide`, { method: "POST" }),
  chat: (id) => req(`/papers/${id}/chat`),
  chatClear: (id) => req(`/papers/${id}/chat`, { method: "DELETE" }),
  skeleton: (id) => req(`/papers/${id}/review/skeleton`, { method: "POST" }),
  alphaxiv: (id) => req(`/papers/${id}/alphaxiv`),
  alphaxivFetch: (id) => req(`/papers/${id}/alphaxiv/fetch`, { method: "POST" }),
  pdfUrl: (id) => `/api/papers/${id}/pdf`,
};

export const STATUSES = { "to-read": "읽을 예정", reviewing: "리뷰 중", reviewed: "리뷰 완료", reproducing: "재현 중" };
