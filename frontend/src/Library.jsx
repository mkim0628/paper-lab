import React, { useEffect, useMemo, useState } from "react";
import { api, STATUSES } from "./api.js";

export default function Library() {
  const [papers, setPapers] = useState(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [err, setErr] = useState("");
  const [deep, setDeep] = useState(false); // 본문/리뷰/메모 전문 검색
  const [hits, setHits] = useState(null);

  useEffect(() => { api.list().then(setPapers).catch((e) => setErr(e.message)); }, []);

  useEffect(() => {
    if (!deep || q.trim().length < 2) { setHits(null); return; }
    const t = setTimeout(() => api.localSearch(q.trim()).then(setHits).catch((e) => setErr(e.message)), 300);
    return () => clearTimeout(t);
  }, [deep, q]);

  const shown = useMemo(() => {
    const t = q.toLowerCase();
    return (papers || []).filter(
      (p) =>
        (!status || p.status === status) &&
        (!t || [p.title, p.arxiv_id, p.authors.join(" "), (p.tags || []).join(" ")].join(" ").toLowerCase().includes(t))
    );
  }, [papers, q, status]);

  if (err) return <p className="error">{err}</p>;
  if (!papers) return <p>불러오는 중…</p>;

  return (
    <section>
      <div className="toolbar">
        <input placeholder="제목 / 저자 / 태그 / ID 필터" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="muted"><input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} /> 본문·리뷰·메모 포함 검색</label>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">모든 상태</option>
          {Object.entries(STATUSES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      {papers.length === 0 && <p className="muted">아직 논문이 없습니다. <a href="#/search">arXiv 검색 / 추가</a>에서 시작하세요.</p>}
      {deep && hits && (
        <ul className="cards">
          {hits.length === 0 && <li className="muted">검색 결과가 없습니다.</li>}
          {hits.map((h) => (
            <li key={h.arxiv_id}>
              <a href={`#/paper/${encodeURIComponent(h.arxiv_id)}`} className="title">{h.title}</a>
              <div className="muted">일치: {h.matched_in.join(", ")}</div>
              {h.snippet && <p className="abstract">{h.snippet}</p>}
            </li>
          ))}
        </ul>
      )}
      <ul className="cards" hidden={deep && hits !== null}>
        {shown.map((p) => (
          <li key={p.arxiv_id}>
            <a href={`#/paper/${encodeURIComponent(p.arxiv_id)}`} className="title">{p.title}</a>
            <div className="muted">{p.authors.slice(0, 4).join(", ")}{p.authors.length > 4 ? " et al." : ""} · arXiv:{p.arxiv_id}</div>
            <div>
              <span className={`badge s-${p.status}`}>{STATUSES[p.status]}</span>
              {(p.tags || []).map((t) => <span key={t} className="tag">{t}</span>)}
              {p.rating && <span className="muted"> ★{p.rating}</span>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
