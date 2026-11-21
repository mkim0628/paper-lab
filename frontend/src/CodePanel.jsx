import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const CLS = {
  official: ["공식(추정 확실)", "s-reviewed"],
  "likely-official": ["공식 가능성 높음", "s-reproducing"],
  community: ["커뮤니티/비공식", "s-reviewing"],
  weak: ["관련성 낮음", ""],
};

export default function CodePanel({ id, meta, onMeta }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [addErr, setAddErr] = useState("");
  const [url, setUrl] = useState("");
  const [showWeak, setShowWeak] = useState(false);

  const load = () => api.code(id).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [id]);
  // 백그라운드 탐색이 끝나면 목록 갱신
  useEffect(() => { if (meta.code_status === "done") load(); }, [meta.code_status]);

  const run = async (fn) => {
    setBusy(true); setErr("");
    try { await fn(); await load(); onMeta(await api.get(id)); } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  if (!data) return <p className="pad muted">{err || "불러오는 중…"}</p>;
  const searching = meta.code_status === "searching";
  const cands = data.candidates.filter((c) => showWeak || c.classification !== "weak");
  const weakN = data.candidates.length - data.candidates.filter((c) => c.classification !== "weak").length;

  return (
    <div className="code-panel">
      <div className="row">
        <button disabled={busy || searching} onClick={() => run(() => api.codeSearch(id))}>{busy || searching ? "탐색 중…" : "🔎 다시 탐색"}</button>
        <button className={data.no_official ? "on" : ""} disabled={busy} onClick={() => run(() => api.codeSelect(id, null, !data.no_official))}>
          {data.no_official ? "✓ 공개 코드 없음 (직접 구현)" : "공개 코드 없음 → 직접 구현"}
        </button>
        {data.searched_at && <span className="muted">마지막 탐색 {data.searched_at.replace("T", " ").slice(0, 16)} UTC</span>}
      </div>
      {err && <p className="error">{err}</p>}
      {data.errors?.length > 0 && <div className="banner block">⚠️ 일부 소스 실패 (결과가 불완전할 수 있음):<br />{data.errors.map((e, i) => <div key={i} className="muted">· {e}</div>)}</div>}
      {!data.searched_at && !searching && <p className="muted">아직 탐색하지 않았습니다. "다시 탐색"을 눌러 주세요.</p>}
      {data.searched_at && data.candidates.length === 0 && !data.errors?.length && <p className="muted">후보를 찾지 못했습니다. 공개 코드가 없을 가능성이 있습니다 — 아래에서 URL을 직접 추가하거나 '직접 구현'을 선택하세요.</p>}

      {cands.map((c) => {
        const [label, badge] = CLS[c.classification];
        const sel = data.selected?.toLowerCase() === c.full_name.toLowerCase();
        return (
          <div key={c.full_name} className={`cand ${sel ? "sel" : ""}`}>
            <div>
              <a href={c.url} target="_blank" rel="noreferrer" className="title">{c.full_name}</a>{" "}
              <span className={`badge ${badge}`}>{label}</span>
              {c.fork && <span className="badge">fork</span>}
              <span className="muted"> ★{c.stars.toLocaleString()} · {c.license || "라이선스 없음"} · 최근 push {c.days_since_push != null ? `${c.days_since_push}일 전` : "?"}</span>
            </div>
            {c.description && <div className="muted">{c.description}</div>}
            <ul className="ev">{c.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
            <div className="row">
              {sel ? <button onClick={() => run(() => api.codeSelect(id, null))}>✓ 선택됨 (해제)</button>
                : <button disabled={busy} onClick={() => run(() => api.codeSelect(id, c.full_name))}>이 repo 선택</button>}
              <span className="muted">출처: {c.found_via.join(", ")}</span>
            </div>
          </div>
        );
      })}
      {weakN > 0 && <button onClick={() => setShowWeak(!showWeak)}>{showWeak ? "관련성 낮은 후보 숨기기" : `관련성 낮은 후보 ${weakN}건 보기`}</button>}

      {data.hf_models?.length > 0 && (
        <div className="cand">
          <b>🤗 이 논문을 태그한 Hugging Face 모델</b> <span className="muted">(인용한 모델 전체 포함 — 이 논문의 공식 체크포인트가 아닐 수 있음)</span>
          <ul className="ev">{data.hf_models.map((m) => <li key={m.id}><a href={`https://huggingface.co/${m.id}`} target="_blank" rel="noreferrer">{m.id}</a> <span className="muted">↓{m.downloads.toLocaleString()}</span></li>)}</ul>
        </div>
      )}

      {addErr && <p className="error">{addErr}</p>}
      <form className="toolbar" onSubmit={(e) => {
        e.preventDefault(); if (!url.trim()) return;
        setAddErr(""); setBusy(true);
        api.codeAdd(id, url.trim()).then(() => { setUrl(""); return load().then(() => api.get(id)).then(onMeta); })
          .catch((er) => setAddErr(er.message)).finally(() => setBusy(false));
      }}>
        <input placeholder="직접 추가: GitHub URL 또는 owner/repo" value={url} onChange={(e) => setUrl(e.target.value)} />
        <button disabled={busy}>추가</button>
      </form>
    </div>
  );
}
