import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const EMPTY = { name: "", provider: "anthropic", model: "claude-sonnet-5-5", api_key: "", base_url: "", region: "", project_id: "", ca_bundle: "" };

export default function Settings() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [msg, setMsg] = useState(null); // {ok, text}
  const [busy, setBusy] = useState(false);

  const load = () => api.llm().then(setData).catch((e) => setMsg({ ok: false, text: e.message }));
  useEffect(() => { load(); }, []);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const wrap = async (fn, okText) => {
    setBusy(true); setMsg(null);
    try { const r = await fn(); setMsg({ ok: true, text: okText?.(r) ?? "완료" }); await load(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
    setBusy(false);
  };

  const save = () => {
    const { name, ...body } = form;
    if (!name.trim()) return setMsg({ ok: false, text: "프로필 이름을 입력하세요" });
    wrap(() => api.llmPut(name.trim(), body), () => "저장됨").then(() => setForm((f) => ({ ...f, api_key: "" })));
  };
  const edit = (p) => setForm({ ...EMPTY, ...p, api_key: "", base_url: p.base_url || "", region: p.region || "", project_id: p.project_id || "", ca_bundle: p.ca_bundle || "" });

  if (!data) return <p>{msg ? <span className="error">{msg.text}</span> : "불러오는 중…"}</p>;
  return (
    <section className="settings">
      <h2>LLM 연결 프로필</h2>
      <p className="muted">사내/사외 환경별로 프로필을 등록해 두고 활성 프로필만 바꿔 쓰세요. API 키는 서버(<code>data/secrets.json</code>, 0600)에만 저장되며 화면에는 마스킹되어 표시됩니다.</p>

      <ul className="cards">
        {data.profiles.length === 0 && <li className="muted">등록된 프로필이 없습니다. 아래에서 추가하세요.</li>}
        {data.profiles.map((p) => (
          <li key={p.name}>
            <b>{p.name}</b> {data.active === p.name && <span className="badge s-reviewed">활성</span>}
            <div className="muted">{p.provider} · {p.model}{p.base_url ? ` · ${p.base_url}` : ""}{p.region ? ` · ${p.region}` : ""} · 키: {p.has_api_key ? p.api_key_masked : "없음"}</div>
            <div className="row">
              {data.active !== p.name && <button disabled={busy} onClick={() => wrap(() => api.llmActive(p.name))}>활성화</button>}
              <button onClick={() => edit(p)}>편집</button>
              <button className="danger" onClick={() => confirm(`${p.name} 삭제?`) && wrap(() => api.llmDel(p.name))}>삭제</button>
            </div>
          </li>
        ))}
      </ul>
      <button disabled={busy || !data.active} onClick={() => wrap(api.llmTest, (r) => `연결 성공 (${r.profile}): ${r.reply}`)}>활성 프로필 연결 테스트</button>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      <h3>프로필 추가 / 수정</h3>
      <div className="form">
        <label>이름<input value={form.name} onChange={set("name")} placeholder="예: external / corp" /></label>
        <label>Provider
          <select value={form.provider} onChange={set("provider")}>{data.providers.map((p) => <option key={p}>{p}</option>)}</select>
        </label>
        <label>모델<input value={form.model} onChange={set("model")} /></label>
        <label>API 키 <span className="muted">(수정 시 비우면 기존 키 유지)</span><input type="password" autoComplete="off" value={form.api_key} onChange={set("api_key")} /></label>
        {form.provider === "anthropic" && <label>Base URL <span className="muted">(사내 게이트웨이/프록시, 비우면 공식 API)</span><input value={form.base_url} onChange={set("base_url")} placeholder="https://llm.mycorp.com" /></label>}
        {form.provider !== "anthropic" && <label>Region<input value={form.region} onChange={set("region")} /></label>}
        {form.provider === "vertex" && <label>GCP Project ID<input value={form.project_id} onChange={set("project_id")} /></label>}
        <label>사내 CA 번들 경로 <span className="muted">(서버 상의 PEM 파일, 선택)</span><input value={form.ca_bundle} onChange={set("ca_bundle")} placeholder="/etc/ssl/corp-ca.pem" /></label>
        <button disabled={busy} onClick={save}>저장</button>
      </div>
    </section>
  );
}
