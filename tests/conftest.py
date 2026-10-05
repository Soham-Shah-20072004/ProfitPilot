import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="pp-test-")
# tests wipe the demo data: they use a throwaway SQLite file unless PP_TEST_DATABASE_URL points elsewhere
os.environ["PP_DATABASE_URL"] = os.environ.get("PP_TEST_DATABASE_URL") or f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["PP_JOBS_INTERVAL_SEC"] = "0"
os.environ["PP_API_KEY"] = ""


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.services.seed import reset_demo

    with TestClient(app) as c:
        with SessionLocal() as db:
            reset_demo(db)
        yield c
