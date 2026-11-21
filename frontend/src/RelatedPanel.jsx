import React, { useEffect, useMemo, useState } from "react";
import { api } from "./api.js";

const TIER_CLASS = { foundation: "s-reviewed", method: "s-reproducing", baseline: "s-reviewing", classic: "", background: "" };
const SORTS = {
  score: ["읽기 우선순위", (a, b) => b.score - a.score],
  citations: ["전역 인용 수", (a, b) => b.citations - a.citations],
  velocity: ["연평균 인용", (a, b) => (b.cites_per_year || 0) - (a.cites_per_year || 0)],
  local: ["이 논문 내 인용 횟수", (a, b) => b.n_contexts - a.n_contexts],
  year: ["최신순", (a, b) => (b.year || 0) - (a.year || 0)],
};
const pct = (x) => Math.round(x * 100);

function PaperRow({ p, tiers, onAdd, busy }) {
  return (
    <div className="cand">
      <div>
        {p.tier && <span className={`badge ${TIER_CLASS[p.tier]}`}>{tiers[p.tier]}</span>}{" "}
        {p.url ? <a className="title" href={p.url} target="_blank" rel="noreferrer">{p.title}</a> : <b>{p.title}</b>}
        <span className="muted"> {p.year || "?"}{p.venue ? ` · ${p.venue}` : ""}</span>
      </div>
      <div className="muted">
        인용 {p.citations.toLocaleString()}{p.cites_per_year != null ? ` (연 ${p.cites_per_year.toLocaleString()})` : ""}
        {p.suspect_count && <span title="Semantic Scholar의 중복/미병합 레코드로 인용 수가 실제보다 낮게 나왔을 수 있습니다" className="error"> ⚠ 인용 수 신뢰 불가</span>}
        {p.is_influential && <span title="Semantic Scholar가 '이 논문에 큰 영향을 준 인용'으로 분류"> · ✓ 영향력 있는 인용</span>}
        {p.n_contexts > 0 && <span> · 본문에서 {p.n_contexts}회 인용</span>}
        {p.intents?.length > 0 && <span> · {p.intents.join("/")}</span>}
        {p.role_hints?.length > 0 && <span> · 맥락: {p.role_hints.join(", ")}</span>}
      </div>
      {p.score_parts && (
        <div className="muted" title="점수 = 0.3×전역 + 0.1×속도 + 0.6×이 논문 내 중요도. 값은 이 논문의 참고문헌들 중 백분위(전역·속도)와 0~1 점수(논문 내).">
          점수 {p.score.toFixed(2)} = 전역 상위 {100 - pct(p.score_parts.global_pct)}% · 속도 상위 {100 - pct(p.score_parts.velocity_pct)}% · 논문 내 중요도 {p.score_parts.local.toFixed(2)}
        </div>
      )}
      {p.contexts?.length > 0 && (
        <details><summary className="muted">인용 맥락 ({p.n_contexts})</summary>
          <ul className="ev">{p.contexts.map((c, i) => <li key={i}>“{c}”</li>)}</ul>
        </details>
      )}
      <div className="row">
        {p.in_library ? <a className="btn" href={`#/paper/${encodeURIComponent(p.arxiv_id)}`}>라이브러리에서 열기</a>
          : p.arxiv_id ? <button disabled={busy} onClick={() => onAdd(p.arxiv_id)}>+ 라이브러리에 추가</button>
          : <span className="muted">arXiv 없음</span>}
      </div>
    </div>
  );
}

function Guide({ g, byKey, stale }) {
  const name = (k) => (byKey[k] ? `${byKey[k].title.slice(0, 60)}${byKey[k].title.length > 60 ? "…" : ""} (${byKey[k].year || "?"})` : k);
  const ref = (k) => (byKey[k]?.url ? <a href={byKey[k].url} target="_blank" rel="noreferrer">{name(k)}</a> : name(k));
  return (
    <div className="cand sel">
      <div><b>🧭 읽기 가이드</b> <span className="badge">AI 서술 · {g.model}</span>
        {stale && <span className="badge" title="가이드 생성 이후 관련 연구 데이터가 갱신되었습니다">데이터 갱신됨 — 재생성 권장</span>}
        <div className="muted">논문 제목·연도·인용 수는 데이터베이스 값이며, AI는 묶음과 이유만 서술합니다. {g.dropped_unknown_refs > 0 && `(제공되지 않은 논문 참조 ${g.dropped_unknown_refs}건은 제거됨)`}</div></div>
      <h4>집중할 기술 축</h4>
      {g.axes.map((a, i) => (
        <div key={i} className="cand">
          <b>{i + 1}. {a.name}</b><div className="abstract">{a.summary}</div>
          <div>시작: {ref(a.start_with)}</div>
          <ul className="ev">{a.papers.filter((k) => k !== a.start_with).map((k) => <li key={k}>{ref(k)}</li>)}</ul>
        </div>
      ))}
      {g.reading_path.length > 0 && <><h4>읽는 순서</h4>
        <ol className="ev">{g.reading_path.map((s, i) => <li key={i}>{ref(s.key)} <span className="muted">— {s.why}</span></li>)}</ol></>}
      {g.after_reading.length > 0 && <><h4>읽은 뒤에</h4>
        <ul className="ev">{g.after_reading.map((s, i) => <li key={i}>{ref(s.key)} <span className="muted">— {s.why}</span></li>)}</ul></>}
      {g.caveats && <div className="muted">⚠ {g.caveats}</div>}
    </div>
  );
}

export default function RelatedPanel({ id, meta, onMeta }) {
  const [data, setData] = useState(null);
  const [view, setView] = useState("refs");
  const [tier, setTier] = useState("all");
  const [sort, setSort] = useState("score");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = () => api.related(id).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [id, meta.related_status]);

  const run = async (fn) => {
    setBusy(true); setErr("");
    try { const r = await fn(); await load(); onMeta(await api.get(id)); return r; }
    catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };
  const add = async (aid) => { setBusy(true); try { await api.add(aid); await load(); } catch (e) { setErr(e.message); } setBusy(false); };

  const byKey = useMemo(() => {
    const m = {}; if (!data) return m;
    [...data.references, ...data.citing.top, ...data.citing.recent, ...data.similar].forEach((p) => { m[p.key] = p; });
    return m;
  }, [data]);
  if (!data) return <p className="pad muted">{err || "불러오는 중…"}</p>;

  const fetching = meta.related_status === "fetching";
  const counts = data.references.reduce((c, r) => ({ ...c, [r.tier]: (c[r.tier] || 0) + 1 }), {});
  const refs = data.references.filter((r) => tier === "all" || r.tier === tier).sort(SORTS[sort][1]);
  const stale = data.guide && data.guide.based_on !== data.fetched_at;

  return (
    <div className="code-panel">
      <div className="row">
        <button disabled={busy || fetching} onClick={() => run(() => api.relatedRefresh(id))}>{fetching ? "조회 중…" : "🔄 다시 조회"}</button>
        <button disabled={busy || fetching || !data.references.length} onClick={() => run(() => api.relatedGuide(id))} title="활성 LLM 프로필로 읽기 가이드를 생성합니다">
          {busy ? "처리 중…" : data.guide ? "🧭 가이드 재생성" : "🧭 읽기 가이드 생성 (AI)"}</button>
        {data.fetched_at && <span className="muted">조회 {data.fetched_at.replace("T", " ").slice(0, 16)} UTC</span>}
      </div>
      {err && <p className="error">{err}</p>}
      {data.errors?.length > 0 && <div className="banner">⚠️ 일부 소스 문제 (결과가 불완전할 수 있음):<br />{data.errors.map((e, i) => <div key={i} className="muted">· {e}</div>)}</div>}
      <details><summary className="muted">결과를 해석할 때 주의</summary>
        <ul className="ev muted"><li>인용 수는 <b>저명도</b> 지표이지 품질이 아닙니다. 분야·연도마다 편차가 큽니다.</li><li>최근 논문은 인용이 적게 나오므로 "연평균 인용"과 함께 보세요.</li>
          <li>Semantic Scholar/OpenAlex에 누락·중복 레코드가 있을 수 있습니다(⚠ 표시).</li><li>티어와 점수는 규칙 기반이며 구성요소를 그대로 공개합니다. 최종 판단은 본인의 몫입니다.</li></ul></details>

      {data.guide && <Guide g={data.guide} byKey={byKey} stale={stale} />}

      <div className="tabs">
        <button className={view === "refs" ? "on" : ""} onClick={() => setView("refs")}>선행 연구 {data.references.length}</button>
        <button className={view === "citing" ? "on" : ""} onClick={() => setView("citing")}>후속 연구</button>
        <button className={view === "similar" ? "on" : ""} onClick={() => setView("similar")}>유사 논문 {data.similar.length}</button>
      </div>

      {view === "refs" && <>
        {!data.references.length && <p className="muted">{fetching ? "조회 중…" : "참고문헌 데이터가 없습니다."}</p>}
        <div className="row">
          <button className={tier === "all" ? "on" : ""} onClick={() => setTier("all")}>전체 {data.references.length}</button>
          {Object.entries(data.tiers).map(([k, v]) => counts[k] ? <button key={k} className={tier === k ? "on" : ""} onClick={() => setTier(k)}>{v} {counts[k]}</button> : null)}
          <select value={sort} onChange={(e) => setSort(e.target.value)}>{Object.entries(SORTS).map(([k, [v]]) => <option key={k} value={k}>정렬: {v}</option>)}</select>
        </div>
        {refs.map((p) => <PaperRow key={p.key} p={p} tiers={data.tiers} onAdd={add} busy={busy} />)}
      </>}

      {view === "citing" && <>
        <p className="muted">이 논문을 인용한 논문 {data.citing.total != null ? `총 ${data.citing.total.toLocaleString()}편` : ""}{data.citing.sample ? " (S2 표본 — 상위 인용 논문이 빠졌을 수 있음)" : ""}. 이후 분야가 어디로 움직였는지 보여줍니다.</p>
        <h4>가장 많이 인용된 후속 연구</h4>
        {data.citing.top.map((p) => <PaperRow key={p.key} p={p} tiers={data.tiers} onAdd={add} busy={busy} />)}
        {data.citing.recent.length > 0 && <><h4>최근 2년 주요 후속 연구</h4>{data.citing.recent.map((p) => <PaperRow key={p.key} p={p} tiers={data.tiers} onAdd={add} busy={busy} />)}</>}
        {!data.citing.top.length && <p className="muted">{fetching ? "조회 중…" : "후속 연구 데이터가 없습니다 (OpenAlex API 키 설정 필요할 수 있음)."}</p>}
      </>}

      {view === "similar" && <>
        <p className="muted">참고문헌에는 없지만 주제가 가까운 논문 (Semantic Scholar 추천).</p>
        {data.similar.map((p) => <PaperRow key={p.key} p={p} tiers={data.tiers} onAdd={add} busy={busy} />)}
      </>}
    </div>
  );
}
