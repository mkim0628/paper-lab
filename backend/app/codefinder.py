"""논문의 공개 코드 탐색 + 신뢰도 평가.

소스: (1) 논문 본문/초록/코멘트 안의 GitHub 링크, (2) Hugging Face Papers 의 연결 repo,
      (3) GitHub 검색(arXiv ID / 제목으로 README 검색).
각 후보는 GitHub API로 실존/메타/README/루트 파일을 확인하고, 규칙 기반으로 분류한다
(official / likely-official / community / weak). 분류 근거(evidence)를 항상 함께 저장해 사용자가 판단할 수 있게 한다.
"""
from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone

import httpx

from . import config, storage

MAX_CANDIDATES = 8
ENV_FILES = {"dockerfile", "requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "docker-compose.yml", "conda.yaml"}

_GH = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9_.-]*)/([A-Za-z0-9_.-]+)", re.I)
# 'our code', 'code is available', 'we release' 처럼 *이 논문의* 코드임을 시사하는 문맥
_CTX = re.compile(
    r"(\bour\b[^.\n]{0,60}(code|implementation|model|repo|release)|code[^.\n]{0,50}(available|released|publicly|at)\b|"
    r"\bwe (release|open-?source|make[^.\n]{0,40}available|provide)\b|available (at|on|from)\b|(project|code|implementation)\s*(page)?\s*[:：]\s*$)",
    re.I,
)
_NOT_REPO_OWNERS = {"features", "topics", "about", "pricing", "login", "sponsors", "orgs", "settings", "marketplace", "collections", "explore", "apps"}


def _client() -> httpx.AsyncClient:  # 테스트에서 MockTransport 로 교체
    return httpx.AsyncClient(timeout=30, follow_redirects=True)


def _gh_headers(raw: bool = False) -> dict:
    h = {"Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json", "User-Agent": "paper-lab/0.1"}
    if config.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return h


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _clean_repo(owner: str, repo: str) -> tuple[str, str] | None:
    repo = re.sub(r"\.git$", "", repo.rstrip(".,;:)]}'\""))
    owner = owner.strip()
    if not repo or owner.lower() in _NOT_REPO_OWNERS or repo.lower() in ("", "issues", "pulls"):
        return None
    return owner, repo


# ---------------------------------------------------------------- 1. 논문 텍스트에서 링크 추출
def extract_paper_links(meta: dict, fulltext: str) -> dict[str, dict]:
    """{ 'owner/repo': {'pages': [..], 'ctx': bool, 'ctx_early': bool} }"""
    out: dict[str, dict] = {}

    def scan(text: str, page: int, force_ctx: bool = False) -> None:
        # PDF 줄바꿈으로 끊긴 URL 복구: 'github.com/owner/\nrepo'
        text = re.sub(r"(github\.com/[\w.-]+/)\s*\n\s*(?=[\w])", r"\1", text, flags=re.I)
        for m in _GH.finditer(text):
            cr = _clean_repo(m.group(1), m.group(2))
            if not cr:
                continue
            key = f"{cr[0]}/{cr[1]}"
            before = text[max(0, m.start() - 200) : m.start()]
            ctx = force_ctx or bool(_CTX.search(before))
            e = out.setdefault(key, {"pages": [], "ctx": False, "ctx_early": False})
            if page not in e["pages"]:
                e["pages"].append(page)
            e["ctx"] |= ctx
            e["ctx_early"] |= ctx and page <= 3

    # 초록 / arXiv 코멘트는 저자가 직접 쓴 메타 정보 → 문맥 가정
    scan(" ".join([meta.get("comment", ""), meta.get("journal_ref", "")]), 1, force_ctx=True)
    scan(meta.get("abstract", ""), 1)
    if fulltext:
        for pm in re.finditer(r'<page n="(\d+)">(.*?)</page>', fulltext, re.S):
            scan(pm.group(2), int(pm.group(1)))
        if "<page" not in fulltext:
            scan(fulltext, 1)
    return out


# ---------------------------------------------------------------- 2/3. 외부 소스
async def _hf_paper(c: httpx.AsyncClient, arxiv_id: str) -> tuple[list[str], list[dict]]:
    """HF Papers가 연결한 GitHub repo와, 논문을 태그한 HF 모델(체크포인트 후보)."""
    repos: list[str] = []
    models: list[dict] = []
    r = await c.get(f"{config.HF_API}/papers/{arxiv_id}")
    if r.status_code == 200:
        gh = (r.json() or {}).get("githubRepo")
        if gh:
            m = _GH.search(gh if "github.com" in gh else f"github.com/{gh}")
            cr = m and _clean_repo(m.group(1), m.group(2))
            if cr:
                repos.append(f"{cr[0]}/{cr[1]}")
    elif r.status_code != 404:
        r.raise_for_status()
    r = await c.get(f"{config.HF_API}/models", params={"filter": f"arxiv:{arxiv_id}", "sort": "downloads", "limit": 5})
    if r.status_code == 200:
        models = [{"id": m.get("id") or m.get("modelId"), "downloads": m.get("downloads", 0)} for m in (r.json() or [])[:5] if m.get("id") or m.get("modelId")]
    return repos, models


async def _gh_search(c: httpx.AsyncClient, q: str) -> list[str]:
    r = await c.get(f"{config.GITHUB_API}/search/repositories", params={"q": q, "per_page": 5}, headers=_gh_headers())
    if r.status_code in (403, 429):
        raise RuntimeError("GitHub 검색 rate limit (PAPERLAB_GITHUB_TOKEN 설정 권장)")
    r.raise_for_status()
    return [i["full_name"] for i in r.json().get("items", [])]


async def _enrich(c: httpx.AsyncClient, full_name: str) -> dict | None:
    """repo 실존 확인 + 메타/README/루트 파일. 존재하지 않으면 None."""
    r = await c.get(f"{config.GITHUB_API}/repos/{full_name}", headers=_gh_headers())
    if r.status_code in (404, 451):
        return None
    if r.status_code in (403, 429):
        raise RuntimeError("GitHub API rate limit (PAPERLAB_GITHUB_TOKEN 설정 권장)")
    r.raise_for_status()
    j = r.json()
    info = {
        "full_name": j.get("full_name", full_name),
        "url": j.get("html_url", f"https://github.com/{full_name}"),
        "owner": (j.get("owner") or {}).get("login", full_name.split("/")[0]),
        "description": j.get("description") or "",
        "stars": j.get("stargazers_count", 0),
        "pushed_at": j.get("pushed_at"),
        "license": (j.get("license") or {}).get("spdx_id"),
        "archived": bool(j.get("archived")),
        "fork": bool(j.get("fork")),
        "default_branch": j.get("default_branch", "main"),
        "readme": "",
        "env_files": [],
    }
    rr, rc = await asyncio.gather(
        c.get(f"{config.GITHUB_API}/repos/{full_name}/readme", headers=_gh_headers(raw=True)),
        c.get(f"{config.GITHUB_API}/repos/{full_name}/contents/", headers=_gh_headers()),
    )
    if rr.status_code == 200:
        info["readme"] = rr.text[:200_000]
    if rc.status_code == 200 and isinstance(rc.json(), list):
        info["env_files"] = sorted(f["name"] for f in rc.json() if f.get("name", "").lower() in ENV_FILES)
    return info


# ---------------------------------------------------------------- 평가
def _author_match(owner: str, authors: list[str]) -> bool:
    o = re.sub(r"[-_]", "", owner.lower())
    for a in authors:
        parts = [re.sub(r"[^a-z]", "", p.lower()) for p in a.split()]
        if parts and len(parts[-1]) >= 4 and parts[-1] in o:
            return True
    return False


def evaluate(info: dict, meta: dict, paper_link: dict | None, hf_linked: bool) -> dict:
    ev: list[str] = []
    score = 0
    readme = info.pop("readme", "")
    aid = meta["arxiv_id"]
    sig = {"ctx_early": False, "paper_link": bool(paper_link), "hf": hf_linked, "readme_arxiv": False, "readme_title": False, "author": False}

    if paper_link:
        pg = ", ".join(f"p.{p}" for p in sorted(paper_link["pages"])[:4])
        if paper_link["ctx_early"]:
            sig["ctx_early"] = True
            score += 60
            ev.append(f"논문 초반({pg})에 '우리 코드' 문맥으로 링크됨")
        elif paper_link["ctx"]:
            score += 35
            ev.append(f"논문({pg})에 코드 공개 문맥으로 링크됨")
        else:
            score += 12
            ev.append(f"논문 본문({pg})에서 링크됨 (역할 불명 — 베이스라인/라이브러리일 수 있음)")
    if hf_linked:
        score += 35
        ev.append("Hugging Face Papers 가 이 논문의 GitHub repo 로 연결")
    if re.search(re.escape(aid), readme):
        sig["readme_arxiv"] = True
        score += 30
        ev.append(f"README 가 arXiv:{aid} 를 언급")
    t = _norm_title(meta["title"])
    if len(t.split()) >= 4 and t in _norm_title(readme):
        sig["readme_title"] = True
        score += 15
        ev.append("README 가 논문 제목을 언급")
    if _author_match(info["owner"], meta.get("authors", [])):
        sig["author"] = True
        score += 10
        ev.append(f"소유자 '{info['owner']}' 가 저자 성과 일치")
    score += min(10, round(math.log10(info["stars"] + 1) * 3))
    if info["fork"]:
        score -= 15
        ev.append("⚠ fork 저장소 (원본을 확인하세요)")
    if info["archived"]:
        score -= 3
        ev.append("보관(archived)됨")
    if info["pushed_at"]:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(info["pushed_at"].replace("Z", "+00:00"))).days
        info["days_since_push"] = age
        score += 2 if age < 730 else 0
    if info["env_files"]:
        score += 2
        ev.append("환경 파일: " + ", ".join(info["env_files"]))
    info["checkpoint_hint"] = bool(re.search(r"checkpoint|pre-?trained|model zoo|weights|huggingface\.co|drive\.google", readme, re.I))
    if info["checkpoint_hint"]:
        score += 2
        ev.append("README 에 체크포인트/가중치 언급")

    # 분류 (규칙 기반, 점수는 정렬용)
    if (sig["ctx_early"] or (sig["hf"] and sig["readme_arxiv"])) and not info["fork"]:
        cls = "official"
    elif sig["hf"] or (sig["paper_link"] and sig["readme_arxiv"]) or (sig["readme_arxiv"] and sig["author"] and not info["fork"]):
        cls = "likely-official"
    elif sig["readme_arxiv"] or sig["readme_title"]:
        cls = "community"
    else:
        cls = "weak"
    if info["fork"] and (cls in ("official", "likely-official") or sig["ctx_early"]):
        cls = "community"  # fork 는 원본이 아닐 수 있으므로 official 로 올리지 않는다
    return {**info, "classification": cls, "score": score, "evidence": ev, "signals": sig}


# ---------------------------------------------------------------- 전체 실행
async def find(arxiv_id: str) -> dict:
    meta = storage.get_meta(arxiv_id)
    fulltext = storage.read_text(arxiv_id, "fulltext.md")
    errors: list[str] = []
    paper_links = extract_paper_links(meta, fulltext)
    hf_repos: list[str] = []
    hf_models: list[dict] = []
    found: dict[str, dict] = {}  # full_name(lower) -> {'name':..., 'via': set}

    def add(name: str, via: str) -> None:
        found.setdefault(name.lower(), {"name": name, "via": set()})["via"].add(via)

    for name in paper_links:
        add(name, "paper")

    async with _client() as c:
        try:
            hf_repos, hf_models = await _hf_paper(c, arxiv_id)
            for n in hf_repos:
                add(n, "hf")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Hugging Face: {e}")
        for label, q in (("arXiv ID", f'"{arxiv_id}" in:readme'), ("제목", f'"{meta["title"]}" in:readme,description,name')):
            try:
                for n in await _gh_search(c, q):
                    add(n, "search")
            except Exception as e:  # noqa: BLE001
                errors.append(f"GitHub 검색({label}): {e}")

        # 후보가 많으면 근거가 강한 것 우선 (paper > hf > search)
        order = sorted(found.values(), key=lambda f: (0 if "hf" in f["via"] else 1 if "paper" in f["via"] else 2))[:MAX_CANDIDATES * 2]
        results = await asyncio.gather(*[_enrich(c, f["name"]) for f in order], return_exceptions=True)

    cands = []
    for f, info in zip(order, results):
        if isinstance(info, Exception):
            errors.append(f"{f['name']}: {info}")
            continue
        if info is None:  # 존재하지 않는 repo (링크 오추출 등)
            continue
        pl = next((v for k, v in paper_links.items() if k.lower() == f["name"].lower()), None)
        cand = evaluate(info, meta, pl, hf_linked=f["name"].lower() in (h.lower() for h in hf_repos))
        cand["found_via"] = sorted(f["via"])
        cands.append(cand)

    # 초반 '우리 코드' 링크가 여러 개면 모호 → 다른 근거가 없는 쪽은 likely-official 로 강등
    top = [c for c in cands if c["classification"] == "official" and c["signals"]["ctx_early"] and not (c["signals"]["hf"] or c["signals"]["readme_arxiv"])]
    if len(top) > 1:
        for c in top:
            c["classification"] = "likely-official"
            c["evidence"].append("초반 코드 링크가 여러 개라 단정할 수 없음")

    cands = sorted(cands, key=lambda c: -c["score"])[:MAX_CANDIDATES]
    prev = load(arxiv_id)
    names = {c["full_name"].lower() for c in cands}  # 사용자가 수동으로 추가한 후보는 재탐색해도 유지
    cands += [c for c in prev.get("candidates", []) if c.get("found_via") == ["manual"] and c["full_name"].lower() not in names]
    return {
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidates": cands,
        "hf_models": hf_models,
        "errors": errors,
        "selected": prev.get("selected"),
        "no_official": prev.get("no_official", False),
    }


# ---------------------------------------------------------------- 저장
def load(arxiv_id: str) -> dict:
    import json

    t = storage.read_text(arxiv_id, "code_candidates.json")
    return json.loads(t) if t else {}


def save(arxiv_id: str, data: dict) -> None:
    import json

    storage.write_text(arxiv_id, "code_candidates.json", json.dumps(data, ensure_ascii=False, indent=2))


def parse_repo_url(text: str) -> str | None:
    m = _GH.search(text.strip() if "github.com" in text else f"github.com/{text.strip()}")
    cr = m and _clean_repo(m.group(1), m.group(2))
    return f"{cr[0]}/{cr[1]}" if cr else None


async def add_manual(arxiv_id: str, repo: str) -> dict:
    """사용자가 직접 준 repo 를 같은 방식으로 검증/평가해 후보에 추가."""
    meta = storage.get_meta(arxiv_id)
    async with _client() as c:
        info = await _enrich(c, repo)
    if info is None:
        raise LookupError(f"GitHub 에서 {repo} 를 찾을 수 없습니다")
    cand = evaluate(info, meta, None, hf_linked=False)
    cand["found_via"] = ["manual"]
    data = load(arxiv_id) or {"candidates": [], "hf_models": [], "errors": [], "selected": None, "no_official": False}
    data["candidates"] = [c for c in data["candidates"] if c["full_name"].lower() != cand["full_name"].lower()] + [cand]
    data["candidates"].sort(key=lambda c: -c["score"])
    save(arxiv_id, data)
    return cand
