"""Tests for DB-backed AI settings: persistence, encryption, permissions."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{TMP}/settings.db"
os.environ["STORAGE_LOCAL_DIR"] = f"{TMP}/storage"
os.environ["SECRET_KEY"] = "unit-test-secret-key"
os.environ["AI_PROVIDER"] = "none"
os.environ["AI_API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import SystemSetting  # noqa: E402
from app.services.ai_settings import decrypt_secret, encrypt_secret  # noqa: E402

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  ok  {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


def main() -> int:
    print("== crypto ==")
    secret = "sk-test-1234567890abcdef"
    blob = encrypt_secret(secret)
    check(blob != secret, "ciphertext differs from plaintext")
    check(secret not in blob, "plaintext not embedded in ciphertext")
    check(decrypt_secret(blob) == secret, "roundtrip decrypts correctly")
    check(decrypt_secret("not-a-valid-token") == "", "invalid ciphertext degrades to empty")
    check(encrypt_secret("") == "", "empty value stays empty")

    with TestClient(app) as client:
        # first user is admin
        r = client.post(
            "/api/v1/auth/register",
            json={"email": "admin@example.com", "password": "secret123"},
        )
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.post(
            "/api/v1/auth/register",
            json={"email": "member@example.com", "password": "secret123"},
        )
        member = {"Authorization": f"Bearer {r.json()['access_token']}"}

        print("== defaults ==")
        r = client.get("/api/v1/settings/ai", headers=admin)
        data = r.json()
        check(r.status_code == 200, "admin can read config")
        check(data["provider"] == "none", "default provider is none")
        check(data["has_api_key"] is False, "no key by default")
        check(data["configured"] is False, "not configured by default")
        check(len(data["providers"]) >= 6, "provider catalogue returned")
        check("api_key" not in data or data.get("api_key") is None, "raw key never returned")

        print("== member read-only ==")
        r = client.get("/api/v1/settings/ai", headers=member)
        check(r.status_code == 200, "member can read config")
        r = client.put(
            "/api/v1/settings/ai",
            headers=member,
            json={"provider": "openai", "api_key": "sk-x"},
        )
        check(r.status_code == 403, "member cannot write settings")

        print("== invalid input ==")
        r = client.put(
            "/api/v1/settings/ai", headers=admin, json={"provider": "not-a-provider"}
        )
        check(r.status_code == 400, "unknown provider rejected")
        r = client.put(
            "/api/v1/settings/ai",
            headers=admin,
            json={"provider": "deepseek", "base_url": "ftp://bad"},
        )
        check(r.status_code == 400, "non-http base_url rejected")
        r = client.put(
            "/api/v1/settings/ai", headers=admin, json={"provider": "ollama", "base_url": ""}
        )
        check(r.status_code == 400, "ollama without base_url rejected")

        print("== save + encrypt ==")
        r = client.put(
            "/api/v1/settings/ai",
            headers=admin,
            json={
                "provider": "deepseek",
                "api_key": secret,
                "model": "deepseek-chat",
                "timeout_seconds": 45,
            },
        )
        check(r.status_code == 200, "admin can save config")
        saved = r.json()
        check(saved["provider"] == "deepseek", "provider persisted")
        check(saved["model"] == "deepseek-chat", "model persisted")
        check(saved["timeout_seconds"] == 45, "timeout persisted")
        check(saved["has_api_key"] is True, "key marked present")
        check(saved["api_key_source"] == "db", "key source is db")
        check(secret not in r.text, "raw key not present in response body")
        check(saved["api_key_masked"].endswith(secret[-4:]), "masked key shows last 4 chars")

        # verify ciphertext at rest
        with SessionLocal() as db:
            row = db.scalar(select(SystemSetting).where(SystemSetting.key == "ai_api_key"))
            check(row is not None, "key row exists")
            stored = row.value if row else ""
            check(stored != secret, "stored value is not plaintext")
            check(secret not in stored, "plaintext absent from database column")
            check(decrypt_secret(stored) == secret, "stored value decrypts to original")

        print("== masking round trip ==")
        masked = saved["api_key_masked"]
        r = client.put(
            "/api/v1/settings/ai",
            headers=admin,
            json={"provider": "deepseek", "api_key": masked},
        )
        check(r.status_code == 200, "saving masked placeholder accepted")
        after = r.json()
        check(after["api_key_masked"] == masked, "masked key unchanged (placeholder ignored)")

        print("== status reflects config ==")
        r = client.get("/api/v1/status")
        st = r.json()
        check(st["ai_provider"] == "deepseek", "status shows active provider")
        check(st["ai_available"] is True, "status reports availability")

        print("== clear key ==")
        r = client.put(
            "/api/v1/settings/ai",
            headers=admin,
            json={"provider": "deepseek", "api_key": ""},
        )
        cleared = r.json()
        check(cleared["has_api_key"] is False, "empty string clears the key")
        with SessionLocal() as db:
            row = db.scalar(select(SystemSetting).where(SystemSetting.key == "ai_api_key"))
            check((row.value if row else "") == "", "database key cleared")

        print("== test endpoint ==")
        r = client.post(
            "/api/v1/settings/ai/test",
            headers=member,
            json={"provider": "deepseek"},
        )
        check(r.status_code == 403, "member cannot run test")
        r = client.post(
            "/api/v1/settings/ai/test", headers=admin, json={"provider": "none"}
        )
        check(r.status_code == 400, "test rejects provider=none")
        r = client.post(
            "/api/v1/settings/ai/test",
            headers=admin,
            json={"provider": "deepseek", "api_key": ""},
        )
        result = r.json()
        check(result["ok"] is False, "test without key fails cleanly")
        check("API Key" in result["message"] or "配置不完整" in result["message"], "explicit missing-key message")

        print("== env fallback visibility ==")
        r = client.get("/api/v1/settings/ai", headers=admin)
        check("env_fallback" in r.json(), "env fallback info exposed")

    print()
    if failures:
        print("FAILURES:")
        for item in failures:
            print(" -", item)
        return 1
    print("ALL AI SETTINGS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
