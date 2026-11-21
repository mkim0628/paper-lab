"""LLM 연결 프로필(사내/사외 전환) 저장 + 호출.

프로필은 data/secrets.json (0600)에 저장되며 api_key는 API 응답에 절대 평문으로 나가지 않는다.
테스트에서는 `complete`를 monkeypatch 한다.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import config

PROVIDERS = ("anthropic", "bedrock", "vertex")
_lock = threading.Lock()


class LLMError(Exception):
    pass


def _path() -> Path:
    return config.DATA_DIR / "secrets.json"


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {"active": None, "profiles": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # 생성 시점부터 0600
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def _mask(key: str) -> str:
    return "" if not key else ("•" * 8 + key[-4:] if len(key) > 8 else "•" * len(key))


def _public(name: str, p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "api_key"}
    out.update(name=name, api_key_masked=_mask(p.get("api_key", "")), has_api_key=bool(p.get("api_key")))
    return out


def list_profiles() -> dict:
    with _lock:
        d = _load()
    return {"active": d["active"], "profiles": [_public(n, p) for n, p in d["profiles"].items()], "providers": list(PROVIDERS)}


def upsert_profile(name: str, fields: dict) -> None:
    if fields.get("provider", "anthropic") not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    with _lock:
        d = _load()
        old = d["profiles"].get(name, {})
        new = {**old, **{k: v for k, v in fields.items() if v is not None}}
        if not fields.get("api_key"):  # 빈 값이면 기존 키 유지
            new["api_key"] = old.get("api_key", "")
        new.setdefault("provider", "anthropic")
        d["profiles"][name] = new
        if d["active"] is None:
            d["active"] = name
        _save(d)


def delete_profile(name: str) -> None:
    with _lock:
        d = _load()
        d["profiles"].pop(name, None)
        if d["active"] == name:
            d["active"] = next(iter(d["profiles"]), None)
        _save(d)


def set_active(name: str) -> None:
    with _lock:
        d = _load()
        if name not in d["profiles"]:
            raise KeyError(name)
        d["active"] = name
        _save(d)


def active_profile() -> dict:
    with _lock:
        d = _load()
    name = d["active"]
    if not name or name not in d["profiles"]:
        raise LLMError("활성 LLM 프로필이 없습니다. Settings에서 프로필을 등록하세요.")
    return {**d["profiles"][name], "name": name}


def _client(p: dict):
    import anthropic

    try:  # anthropic SDK 2.x 는 httpx2 를 사용 (httpx 객체는 거부됨)
        import httpx2 as httpx
    except ImportError:  # 구버전 SDK
        import httpx
    kw: dict = {"max_retries": 2, "timeout": 600}
    if p.get("ca_bundle"):  # 사내 프록시 CA
        import ssl

        ctx = ssl.create_default_context(cafile=p["ca_bundle"])
        ctx.load_default_certs()  # 시스템 CA도 유지 (사내 CA는 추가)
        kw["http_client"] = httpx.Client(verify=ctx, timeout=600)
    prov = p.get("provider", "anthropic")
    if prov == "anthropic":
        if not p.get("api_key"):
            raise LLMError("API 키가 없습니다")
        if p.get("base_url"):
            kw["base_url"] = p["base_url"]
        return anthropic.Anthropic(api_key=p["api_key"], **kw)
    if prov == "bedrock":
        extra = {"aws_region": p["region"]} if p.get("region") else {}
        if p.get("api_key"):
            extra["api_key"] = p["api_key"]
        return anthropic.AnthropicBedrock(**extra, **kw)
    if prov == "vertex":
        return anthropic.AnthropicVertex(region=p.get("region") or "us-east5", project_id=p.get("project_id"), **kw)
    raise LLMError(f"unknown provider {prov}")


def complete(system: str, user: str, max_tokens: int = 8000, profile: dict | None = None) -> str:
    """동기 호출 (스트리밍으로 받아 최종 텍스트 반환). 라우트에서는 to_thread로 호출."""
    p = profile or active_profile()
    try:
        client = _client(p)
        with client.messages.stream(
            model=p["model"], max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}]
        ) as stream:
            msg = stream.get_final_message()
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001  SDK 예외 → 사용자 메시지 (키는 포함되지 않음)
        raise LLMError(f"LLM 호출 실패 ({type(e).__name__}): {e}") from e
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
