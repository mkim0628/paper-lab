import React, { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { renderMd } from "./markdown.js";

/** alphaXiv 에 이미 생성되어 있는 'AI Overview' 를 가져와 보여준다 (LLM API 키 불필요). 외부 AI 요약이므로 출처/미검증을 항상 표시. */
export default function AlphaxivPanel({ id, auto, onAutoUsed, onCite, onAdopt }) {
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const started = useRef(false);

  const load = () => api.alphaxiv(id).then(setD).catch((e) => setErr(e.message));
  const fetchNow = async () => {
    setBusy(true); setErr("");
    try { setD(await api.alphaxivFetch(id)); } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  useEffect(() => { load(); }, [id]);
  useEffect(() => {   // 리뷰 시작 화면에서 눌렀다면 저장본이 없을 때 바로 가져온다
    if (auto && d && !d.fetched && !started.current) { started.current = true; onAutoUsed?.(); fetchNow(); }
  }, [auto, d]);

  if (!d) return <p className="pad muted">{err || "불러오는 중…"}</p>;
  const date = d.fetched_at ? d.fetched_at.replace("T", " ").slice(0, 16) + " UTC" : "";

  if (!d.fetched) {
    return (
      <div className="pad ax-intro">
        <h3>🅰 alphaXiv 개요 가져오기</h3>
        <p>LLM API 키 없이, <b>alphaXiv가 이미 만들어 둔 AI 개요</b>를 가져와 볼 수 있습니다. 논문 내용에 대한 근거 페이지(<code>[p.N]</code>)와 그림이 포함되어 있습니다.</p>
        <ul className="muted left">
          <li>alphaXiv에 개요가 이미 생성된 논문(주로 널리 읽힌 논문)에서만 동작합니다. 없으면 없다고 알려드립니다.</li>
          <li>버튼을 누를 때만 alphaXiv에 <b>요청 1건</b>을 보내고, 결과는 이 PC에만 저장합니다. 영어로 제공됩니다.</li>
          <li>외부 AI가 생성한 요약이라 <b>검증되지 않았습니다</b>. 수치와 인용은 원문과 대조하세요.</li>
        </ul>
        <button className="primary" disabled={busy} onClick={fetchNow}>{busy ? "가져오는 중…" : "alphaXiv에서 개요 가져오기"}</button>
        {err && <p className="error">{err}</p>}
      </div>
    );
  }
  return (
    <div className="ax-panel">
      <div className="ax-head">
        <div className="muted">출처: <a href={d.source_url} target="_blank" rel="noreferrer">alphaXiv</a> · {date} 에 가져옴</div>
        <div className="row">
          <button disabled={busy} onClick={fetchNow}>{busy ? "가져오는 중…" : "🔄 다시 가져오기"}</button>
          {d.available && <button className="primary" onClick={() => onAdopt(d)} title="리뷰 문서의 시작점으로 복사합니다 (출처와 미검증 표시가 함께 들어갑니다)">리뷰의 시작점으로 사용</button>}
        </div>
        {err && <p className="error">{err}</p>}
      </div>
      {d.available ? (
        <>
          <div className="banner block">⚠️ alphaXiv의 <b>외부 AI가 생성한 개요</b>입니다. 이 앱이 검증한 내용이 아니며 오류가 있을 수 있습니다. <b>[p.N]</b>을 누르면 원문 해당 페이지로 이동합니다.</div>
          <div className="md ax-doc" onClick={(e) => { const p = e.target.dataset?.page; if (p) { e.preventDefault(); onCite(+p); } }}
            dangerouslySetInnerHTML={{ __html: renderMd(d.markdown, { id, figs: [] }) }} />
        </>
      ) : (
        <div className="pad">
          <p><b>이 논문의 alphaXiv 개요가 아직 없습니다.</b></p>
          <p className="muted">{d.reason} alphaXiv에서 직접 생성을 요청하실 수도 있고, 이 앱의 <b>🤖 AI 리뷰 작성</b>(LLM 키 필요)을 쓰실 수도 있습니다.</p>
          <a className="btn" href={d.source_url} target="_blank" rel="noreferrer">alphaXiv에서 열기</a>
        </div>
      )}
    </div>
  );
}
