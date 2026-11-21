#!/usr/bin/env bash
# 실행 중인 Paper Lab에 대한 스모크 테스트 (실제 arXiv 접속 포함).
#   ./scripts/smoke.sh                       # 기본 http://localhost:8000
#   BASE=http://localhost:8000 SMOKE_LLM=1 ./scripts/smoke.sh   # 활성 LLM 프로필 연결 테스트까지
# 필요: bash, curl, python3. 테스트로 추가한 논문(1706.03762)은 끝나면 삭제하되, 원래 있던 경우는 건드리지 않는다.
set -u
BASE="${BASE:-http://localhost:8000}"
ID="1706.03762"
PASS=0; FAIL=0
ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $1"; [ -n "${2:-}" ] && echo "     → $2"; FAIL=$((FAIL+1)); }
jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1" 2>/dev/null; }
req()  { curl -sS -m "${T:-60}" "$@"; }

echo "[1] 서버 응답"
if req -o /dev/null -w "%{http_code}" "$BASE/api/papers" | grep -q 200; then ok "API 응답 (GET /api/papers)"; else bad "API 응답 없음" "docker compose ps / logs 확인. BASE=$BASE"; echo "결과: PASS=$PASS FAIL=$FAIL"; exit 1; fi
req -o /dev/null -w "%{http_code}" "$BASE/" | grep -q 200 && ok "프론트엔드(index.html) 서빙" || bad "프론트엔드 서빙 실패" "Docker 이미지의 frontend/dist 빌드 확인"

echo "[2] arXiv 연동 (실제 외부 접속)"
R=$(T=90 req "$BASE/api/search?q=attention+is+all+you+need&max_results=3")
N=$(echo "$R" | jget "len(d['items'])")
if [ "${N:-0}" -ge 1 ] 2>/dev/null; then ok "키워드 검색: ${N}건 (첫 결과: $(echo "$R" | jget "d['items'][0]['title'][:50]"))"; else bad "arXiv 검색 실패" "$(echo "$R" | head -c 300) / 서버에서 export.arxiv.org:443 접근 가능한지 확인 (프록시/방화벽)"; fi
R=$(T=90 req "$BASE/api/search?q=https://arxiv.org/abs/$ID")
[ "$(echo "$R" | jget "d['items'][0]['arxiv_id']")" = "$ID" ] && ok "URL로 단건 조회" || bad "URL 조회 실패" "$(echo "$R" | head -c 300)"

echo "[3] 논문 추가 → PDF → 본문 추출"
EXISTED=0
if req -f -o /dev/null "$BASE/api/papers/$ID" 2>/dev/null; then EXISTED=1; echo "  (이미 라이브러리에 있음 — 삭제하지 않음)"; else
  CODE=$(T=90 req -o /dev/null -w "%{http_code}" -X POST "$BASE/api/papers" -H 'Content-Type: application/json' -d "{\"input\":\"https://arxiv.org/abs/$ID\"}")
  [ "$CODE" = "201" ] && ok "논문 추가 (201)" || bad "논문 추가 실패 (HTTP $CODE)"
fi
PDF=""; FT=""
for i in $(seq 1 45); do
  M=$(req "$BASE/api/papers/$ID"); PDF=$(echo "$M" | jget "d.get('pdf_status')"); FT=$(echo "$M" | jget "d.get('fulltext_status')")
  { [ "$PDF" = "failed" ] || [ "$FT" = "ready" ] || [ "$FT" = "failed" ]; } && break; sleep 2
done
[ "$PDF" = "ready" ] && ok "PDF 다운로드 완료" || bad "PDF 상태: $PDF" "서버에서 arxiv.org:443 접근 확인 (논문 화면의 '재시도' 버튼도 가능)"
req "$BASE/api/papers/$ID/pdf" | head -c 4 | grep -q '%PDF' && ok "PDF 파일 서빙 (매직 바이트 %PDF)" || bad "PDF 서빙 실패"
[ "$FT" = "ready" ] && ok "본문 추출 완료 ($(echo "$M" | jget "d.get('pages')") pages)" || bad "본문 추출 상태: $FT"

echo "[4] 리뷰 저장 / 검색"
MARK="smoke-$(date +%s)"
req -o /dev/null -X PUT "$BASE/api/papers/$ID/notes" -H 'Content-Type: application/json' -d "{\"content\":\"$MARK\"}"
[ "$(req "$BASE/api/papers/$ID/review" | jget "d['notes']")" = "$MARK" ] && ok "메모 저장/조회" || bad "메모 저장 실패"
[ "$(req "$BASE/api/local-search?q=$MARK" | jget "len(d)")" = "1" ] && ok "로컬 전문 검색 (메모)" || bad "로컬 검색 실패"
[ "$FT" = "ready" ] && { [ "$(req "$BASE/api/local-search?q=attention+transformer" | jget "len(d)")" -ge 1 ] && ok "로컬 전문 검색 (본문)" || bad "본문 검색 실패"; }
req -o /dev/null -X PUT "$BASE/api/papers/$ID/notes" -H 'Content-Type: application/json' -d '{"content":""}'

echo "[5] 공개 코드 탐색 (GitHub API / Hugging Face — 실제 외부 접속)"
if [ "$FT" = "ready" ] || [ "$PDF" = "ready" ]; then
  R=$(T=120 req -X POST "$BASE/api/papers/$ID/code/search")
  NC=$(echo "$R" | jget "len(d['candidates'])"); NE=$(echo "$R" | jget "len(d['errors'])")
  if [ -z "$NC" ]; then bad "코드 탐색 API 실패" "$(echo "$R" | head -c 300)"
  elif [ "$NE" -ge 3 ] 2>/dev/null && [ "$NC" = "0" ]; then bad "모든 소스 실패 (GitHub 검색×2, HF)" "$(echo "$R" | jget "'; '.join(d['errors'])")"
  else
    ok "탐색 완료: 후보 ${NC}건, 소스 오류 ${NE}건"
    echo "     상위 후보: $(echo "$R" | jget "', '.join(c['full_name']+'('+c['classification']+')' for c in d['candidates'][:3])")"
    [ "${NE:-0}" -gt 0 ] && echo "     ⚠ 일부 소스 오류: $(echo "$R" | jget "'; '.join(d['errors'])")  (GitHub 무인증 rate limit이면 PAPERLAB_GITHUB_TOKEN 설정)"
  fi
else echo "  (건너뜀 — PDF 없음)"; fi

echo "[6] LLM (선택)"
if [ "${SMOKE_LLM:-0}" = "1" ]; then
  R=$(T=120 req -X POST "$BASE/api/settings/llm/test")
  [ "$(echo "$R" | jget "d.get('ok')")" = "True" ] && ok "LLM 연결 성공 (프로필: $(echo "$R" | jget "d['profile']"))" || bad "LLM 연결 실패" "$(echo "$R" | head -c 400)"
else echo "  (건너뜀 — Settings에서 프로필 등록 후 SMOKE_LLM=1 로 실행)"; fi

[ "$EXISTED" = "0" ] && req -o /dev/null -X DELETE "$BASE/api/papers/$ID" && echo "  (테스트 논문 삭제함)"
echo; echo "결과: PASS=$PASS FAIL=$FAIL"; [ "$FAIL" = "0" ]
