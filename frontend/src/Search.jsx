import React, { useState } from "react";
import { api } from "./api.js";

export default function Search() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState({});

  const run = async (start = 0) => {
    if (!q.trim()) return;
    setBusy(true); setErr("");
    try {
      const r = await api.search(q.trim(), start);
      setRes(start ? { ...r, items: [...res.items, ...r.items] } : r);
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const add = async (id) => {
    setAdding((a) => ({ ...a, [id]: true }));
    try {
      await api.add(id);
      setRes((r) => ({ ...r, items: r.items.map((i) => (i.arxiv_id === id ? { ...i, in_library: true } : i)) }));
    } catch (e) { setErr(e.message); }
    setAdding((a) => ({ ...a, [id]: false }));
  };

  return (
    <section>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <input autoFocus placeholder="키워드, 또는 arXiv URL / ID (예: https://arxiv.org/abs/1706.03762)" value={q} onChange={(e) => setQ(e.target.value)} />
        <button disabled={busy}>{busy ? "검색 중…" : "검색"}</button>
      </form>
      <p className="muted">고급: <code>ti:transformer AND au:vaswani</code> 같은 arXiv 쿼리 문법 사용 가능. arXiv API 정책상 요청 간 3초 간격이 적용됩니다.</p>
      {err && <p className="error">{err}</p>}
      {res && <p className="muted">총 {res.total.toLocaleString()}건</p>}
      <ul className="cards">
        {res?.items.map((p) => (
          <li key={p.arxiv_id}>
            <a className="title" href={p.abs_url} target="_blank" rel="noreferrer">{p.title}</a>
            <div className="muted">{p.authors.slice(0, 5).join(", ")}{p.authors.length > 5 ? " et al." : ""} · {p.published.slice(0, 10)} · {p.primary_category} · arXiv:{p.arxiv_id}</div>
            <p className="abstract">{p.abstract.length > 400 ? p.abstract.slice(0, 400) + "…" : p.abstract}</p>
            {p.in_library
              ? <a className="btn" href={`#/paper/${encodeURIComponent(p.arxiv_id)}`}>라이브러리에서 열기</a>
              : <button disabled={adding[p.arxiv_id]} onClick={() => add(p.arxiv_id)}>{adding[p.arxiv_id] ? "추가 중…" : "+ 추가"}</button>}
          </li>
        ))}
      </ul>
      {res && res.items.length < res.total && <button disabled={busy} onClick={() => run(res.items.length)}>더 보기</button>}
    </section>
  );
}
