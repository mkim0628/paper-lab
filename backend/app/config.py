import os
from pathlib import Path

# 모든 논문 데이터는 이 디렉터리 아래에 파일로 저장된다 (source of truth).
DATA_DIR = Path(os.environ.get("PAPERLAB_DATA", Path(__file__).resolve().parents[2] / "data")).resolve()
PAPERS_DIR = DATA_DIR / "papers"
FRONTEND_DIST = Path(os.environ.get("PAPERLAB_FRONTEND", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
ARXIV_API = os.environ.get("PAPERLAB_ARXIV_API", "https://export.arxiv.org/api/query")
ARXIV_PDF = os.environ.get("PAPERLAB_ARXIV_PDF", "https://arxiv.org/pdf")
# arXiv API 이용 규칙: 3초에 1요청
ARXIV_MIN_INTERVAL = float(os.environ.get("PAPERLAB_ARXIV_INTERVAL", "3.0"))
GITHUB_API = os.environ.get("PAPERLAB_GITHUB_API", "https://api.github.com")
HF_API = os.environ.get("PAPERLAB_HF_API", "https://huggingface.co/api")
# 선택: 인증하면 GitHub rate limit이 시간당 60 → 5000 (검색은 분당 10 → 30)
GITHUB_TOKEN = os.environ.get("PAPERLAB_GITHUB_TOKEN", "")
S2_API = os.environ.get("PAPERLAB_S2_API", "https://api.semanticscholar.org")
OPENALEX_API = os.environ.get("PAPERLAB_OPENALEX_API", "https://api.openalex.org")
# 선택: 무료 API 키 (공유 rate limit/예산 회피). 키는 서버 환경변수로만 전달한다.
S2_API_KEY = os.environ.get("PAPERLAB_S2_API_KEY", "")
OPENALEX_API_KEY = os.environ.get("PAPERLAB_OPENALEX_API_KEY", "")
# 선택: OpenAlex polite pool 용 연락처. 개인정보이므로 사용자가 직접 설정한 경우에만 전송한다.
OPENALEX_MAILTO = os.environ.get("PAPERLAB_OPENALEX_MAILTO", "")
RELATED_BACKOFF = float(os.environ.get("PAPERLAB_RELATED_BACKOFF", "3.0"))
ARXIV_RETRY_BACKOFF = float(os.environ.get("PAPERLAB_ARXIV_RETRY_BACKOFF", "3.0"))
