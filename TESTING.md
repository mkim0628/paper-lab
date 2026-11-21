# 테스트 가이드 (서버에서 한 번에 확인하기)

샌드박스(개발 환경)에서는 arXiv·Docker·실제 브라우저 PDF 렌더링·실제 Claude API에 접근할 수 없어서 **아래 항목만 서버에서 직접 확인**이 필요합니다. 그 외(저장 로직, API, UI 동작, LLM 호출 경로)는 자동 테스트와 mock 서버 E2E로 이미 검증했습니다.

| 영역 | 샌드박스 검증 | 서버에서 확인 필요 |
|---|---|---|
| 파일 저장 / 이력 / API | ✅ pytest | – |
| arXiv Atom 파싱, ID/URL 파싱 | ✅ fixture | **실제 arXiv 응답, PDF 다운로드** |
| 본문 추출(PyMuPDF), 로컬 전문 검색 | ✅ 합성 PDF | 실제 논문 PDF (수식·2단 레이아웃) 품질 |
| LLM 호출 (스트리밍, base_url, 프로필, 키 마스킹) | ✅ mock Messages API | **실제 API 키 / 사내 게이트웨이** |
| UI (검색→추가→편집→AI 초안→인용 링크→검색) | ✅ 헤드리스 브라우저 + mock | 실제 브라우저의 **PDF 뷰어 렌더링, `#page=N` 이동** |
| Docker 빌드/실행 | ❌ | **전부** |

---

## 0. 준비

```bash
git clone https://github.com/mkim0628/paper-lab && cd paper-lab
git checkout ccr-6a4316ee-2qvcx4
docker compose up -d --build        # ① 빌드가 성공하는지
docker compose ps                    # ② paperlab 이 Up 인지
```
로컬 PC에서 접속: `ssh -L 8000:localhost:8000 <서버>` → 브라우저에서 http://localhost:8000
(서버 브라우저/Tailscale을 쓰면 터널 불필요. 컨테이너는 `127.0.0.1:8000`에만 바인딩됨)

## 1. 자동 점검 (1분)

```bash
./scripts/smoke.sh
```
서버 응답 → **실제 arXiv 검색/URL 조회** → 논문 추가 → PDF 다운로드 → 본문 추출 → 메모 저장 → 로컬 검색을 확인하고 PASS/FAIL을 출력합니다. (테스트 논문은 끝나면 삭제, 원래 있던 경우는 건드리지 않음)

- 모두 ✅ 이면 아래 2번(브라우저 확인)만 하면 됩니다.
- ❌ 가 나오면 출력된 `→` 힌트와 `docker compose logs --tail=100 paperlab` 를 보내 주세요.

LLM 프로필 등록(3번) 후에는 `SMOKE_LLM=1 ./scripts/smoke.sh` 로 연결 테스트까지 포함해 다시 실행할 수 있습니다.

## 2. 브라우저 수동 체크리스트 — Phase 1

| # | 동작 | 기대 결과 |
|---|---|---|
| 1 | "arXiv 검색 / 추가"에서 `attention is all you need` 검색 | 결과 목록 + 총 건수 표시 |
| 2 | 검색창에 `https://arxiv.org/abs/1706.03762` 붙여넣기 | 단건 결과 |
| 3 | `+ 추가` → "라이브러리에서 열기" | 논문 화면. 왼쪽에 PDF가 **실제로 렌더링**됨 (※ 샌드박스 미검증) |
| 4 | 리뷰 편집 후 2초 대기 | 우측 상단 "저장됨". `data/papers/1706.03762/review.md` 에 반영 |
| 5 | "미리보기" 전환 | 마크다운 렌더링 |
| 6 | 상태/평점/태그(쉼표+Enter) 변경 후 라이브러리로 이동 | 목록에 반영 |
| 7 | 페이지 새로고침 / 탭 닫았다 열기 | 내용 유지 |
| 8 | "이력" 버튼 | 편집 이력 목록 (1분 이상 간격으로 변경한 경우 생김) |

## 3. 브라우저 수동 체크리스트 — Phase 2 (LLM)

**3-1. 프로필 등록 (설정 탭)**

| 환경 | 설정 |
|---|---|
| 사외 | Provider `anthropic`, Base URL 비움, API 키 입력, 모델명 (기본 `claude-sonnet-5-5`) |
| 사내 | Provider `anthropic` + **Base URL = 사내 게이트웨이 주소** + 사내 키. 사내 TLS 인증서가 필요하면 "사내 CA 번들 경로"에 **컨테이너 안에서 보이는** PEM 경로 입력 (docker-compose.yml 의 volumes 로 마운트 필요) |
| 사내(클라우드 경유) | Provider `bedrock` / `vertex` + region (이 경우 이미지에 `anthropic[bedrock]` 또는 `[vertex]` 추가 설치 필요 — 필요하면 요청) |

체크:
- [ ] 저장 후 키가 `••••••••XXXX` 로만 보이고, 페이지 소스/네트워크 응답 어디에도 평문 키가 없음
- [ ] `ls -l data/secrets.json` 권한이 `-rw-------`
- [ ] "활성 프로필 연결 테스트" → "연결 성공"
- [ ] 프로필 두 개(사외/사내) 등록 후 "활성화"로 전환 가능

> 참고: 개발 환경의 anthropic SDK(2.12)는 키를 `Authorization: Bearer <key>` 헤더로 보냅니다. 사내 게이트웨이가 `x-api-key` 헤더를 요구해서 401이 나면 알려 주세요 (헤더 방식 옵션을 추가).

**3-2. AI 초안**
- [ ] 논문 추가 직후 몇 초 뒤 "🤖 AI 초안" 버튼이 활성화됨 (본문 추출 완료)
- [ ] 클릭 → 확인창 → 1~2분 뒤 리뷰가 12개 섹션으로 채워짐, 상단에 노란 "AI 초안" 배너
- [ ] 미리보기에서 `p.N` 파란 뱃지 클릭 → **PDF가 해당 페이지로 이동** (※ 브라우저 PDF 뷰어에 따라 동작이 다를 수 있음. 안 되면 알려주세요)
- [ ] 인용된 페이지/수치를 원문과 대조해 정확한지 확인 ← **품질 평가의 핵심**
- [ ] "이력"에 생성 직전의 내 리뷰가 보관되어 있음 (덮어쓰기 안전장치)
- [ ] 섹션 선택 → "섹션 재생성" 으로 해당 섹션만 교체됨
- [ ] "검증 완료로 표시" 클릭 시 배너가 사라짐

**3-3. 전문 검색**
- [ ] 라이브러리에서 "본문·리뷰·메모 포함 검색" 체크 → 논문 본문에 있는 용어 검색 시 일치 필드와 발췌문 표시

## 4. 개발자용 자동 테스트 (선택)

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && pytest -q   # 22개
```

## 5. 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| 검색 시 `arXiv 요청 실패` | 서버에서 `curl -I https://export.arxiv.org/api/query` 확인. 사내망이면 프록시 필요 → `docker-compose.yml` 에 `HTTPS_PROXY` 환경변수 추가 |
| PDF "다운로드 실패" | `https://arxiv.org/pdf/<id>` 접근 확인. 논문 화면의 "재시도" 버튼 |
| "본문 재추출" 버튼이 보임 | 스캔본 PDF 등 텍스트가 거의 없음. AI 초안 사용 불가 (OCR은 미지원) |
| PDF 영역이 비어 있음 | 브라우저의 PDF 뷰어 설정/확장 확인. `/api/papers/<id>/pdf` 를 새 탭에서 열어 보기 |
| LLM 연결 실패 `APIConnectionError` | 사내: Base URL, CA 번들, 컨테이너→게이트웨이 네트워크 확인 |
| LLM 401/403 | 키/모델명/헤더 방식 확인 (위 참고) |
| 초안이 너무 일반적/수치 오류 | 정상적인 LLM 한계. 인용 페이지를 대조해 수정하세요 (이 도구의 설계 전제) |

## 6. 결과 공유 양식

```
smoke.sh: PASS=__ FAIL=__ (FAIL 항목 + → 힌트)
2번 체크리스트: 실패 번호 __
3번 체크리스트: 실패 항목 __
docker compose logs 에러 (있으면 붙여넣기)
```
