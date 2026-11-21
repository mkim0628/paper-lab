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
