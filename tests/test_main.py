import importlib
import sys
from types import SimpleNamespace

import builtins
import os
import pytest


class MockResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        # Mimic requests raising for 4xx/5xx
        if 400 <= self.status_code:
            raise Exception(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def env_token_and_host(monkeypatch):
    monkeypatch.setenv("TFE_HOST", "https://app.terraform.io")
    monkeypatch.setenv("TFE_TOKEN", "test-token")
    yield


@pytest.fixture
def main_mod(monkeypatch):
    # Ensure fresh import of main after env set
    if "main" in sys.modules:
        del sys.modules["main"]
    # Ensure repo root is on sys.path for importing main.py
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import main as main_module
    return main_module


def test_find_user_and_team_success(monkeypatch, main_mod):
    org = "acme"
    email = "user@example.com"

    # Mock GET for organization-memberships and teams
    def fake_get(url, headers):
        if "/organization-memberships" in url:
            assert "q=user%40example.com" in url  # email should be URL-encoded
            return MockResponse(200, {
                "data": [
                    {
                        "id": "ou-1",
                        "relationships": {
                            "user": {"data": {"id": "user-1"}},
                            "teams": {"data": [{"id": "team-123", "type": "teams"}]}
                        }
                    }
                ]
            })
        if "/teams" in url:
            return MockResponse(200, {
                "data": [
                    {"id": "team-123", "attributes": {"name": "owners", "users-count": 4, "visibility": "secret"}}
                ]
            })
        raise AssertionError(f"Unexpected GET url: {url}\n")

    monkeypatch.setattr(main_mod, "requests", SimpleNamespace(get=fake_get))

    org_membership_id, user_id, team_ids = main_mod.find_user_and_team(org, email)
    assert org_membership_id == "ou-1"
    assert user_id == "user-1"
    assert team_ids == ["team-123"]


def test_main_user_in_team_and_bulk_remove_success(monkeypatch, capsys, main_mod):
    # Prepare mocks
    def fake_get(url, headers):
        if "/organization-memberships" in url:
            return MockResponse(200, {
                "data": [
                    {
                        "id": "ou-1",
                        "relationships": {
                            "user": {"data": {"id": "user-1"}},
                            "teams": {"data": [{"id": "team-123", "type": "teams"}]}
                        }
                    }
                ]
            })
        if "/teams" in url:
            return MockResponse(200, {
                "data": [
                    {"id": "team-123", "attributes": {"name": "owners", "users-count": 4, "visibility": "secret"}}
                ]
            })
        raise AssertionError(f"Unexpected GET url: {url}")

    # Capture the DELETE call and assert payload
    delete_calls = {}

    def fake_delete(url, headers, json):
        delete_calls["url"] = url
        delete_calls["json"] = json
        return MockResponse(204)

    # Avoid waiting 3 seconds
    def fake_sleep(_):
        return None

    # Patch
    monkeypatch.setattr(main_mod, "requests", SimpleNamespace(get=fake_get, delete=fake_delete))
    import time as _time
    monkeypatch.setattr(main_mod, "time", SimpleNamespace(sleep=fake_sleep))

    # Run main with arguments
    argv = [
        "main.py",
        "--org", "acme",
        "--team", "owners",
        "--email", "user@example.com",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 0

    # Validate delete endpoint and payload
    assert delete_calls["url"].endswith("/teams/team-123/relationships/organization-memberships")
    assert delete_calls["json"] == {
        "data": [
            {"type": "organization-memberships", "id": "ou-1"}
        ]
    }


def test_main_team_not_found(monkeypatch, main_mod):
    def fake_get(url, headers):
        if "/organization-memberships" in url:
            return MockResponse(200, {"data": [{"id": "ou-1", "relationships": {"user": {"data": {"id": "user-1"}}, "teams": {"data": []}}}]})
        if "/teams" in url:
            return MockResponse(200, {"data": [{"id": "team-999", "attributes": {"name": "not-owners"}}]})
        raise AssertionError(f"Unexpected GET url: {url}")

    monkeypatch.setattr(main_mod, "requests", SimpleNamespace(get=fake_get))

    argv = ["main.py", "--org", "acme", "--team", "owners", "--email", "user@example.com"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 2


def test_main_user_not_found(monkeypatch, main_mod):
    def fake_get(url, headers):
        if "/organization-memberships" in url:
            return MockResponse(200, {"data": []})
        if "/teams" in url:
            return MockResponse(200, {"data": [{"id": "team-123", "attributes": {"name": "owners"}}]})
        raise AssertionError(f"Unexpected GET url: {url}")

    monkeypatch.setattr(main_mod, "requests", SimpleNamespace(get=fake_get))

    argv = ["main.py", "--org", "acme", "--team", "owners", "--email", "user@example.com"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 1


def test_main_user_not_in_team(monkeypatch, main_mod):
    def fake_get(url, headers):
        if "/organization-memberships" in url:
            return MockResponse(200, {
                "data": [
                    {"id": "ou-1", "relationships": {"user": {"data": {"id": "user-1"}}, "teams": {"data": [{"id": "team-999"}]}}}
                ]
            })
        if "/teams" in url:
            return MockResponse(200, {"data": [{"id": "team-123", "attributes": {"name": "owners"}}]})
        raise AssertionError(f"Unexpected GET url: {url}")

    # Track if delete was called (shouldn't be)
    called = {"delete": False}

    def fake_delete(url, headers, json):
        called["delete"] = True
        return MockResponse(204)

    monkeypatch.setattr(main_mod, "requests", SimpleNamespace(get=fake_get, delete=fake_delete))

    argv = ["main.py", "--org", "acme", "--team", "owners", "--email", "user@example.com"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 3
    assert called["delete"] is False


def test_emails_file_parsing(tmp_path, monkeypatch, main_mod):
    # Prepare file with comments, commas, and spaces
    content = """
    # comment line
    user1@example.com, user2@example.com
    user3@example.com
    """.strip()
    p = tmp_path / "emails.txt"
    p.write_text(content, encoding="utf-8")

    def fake_get(url, headers):
        if "/organization-memberships" in url:
            # Return a membership for all users with a consistent team id
            return MockResponse(200, {
                "data": [
                    {"id": "ou-X", "relationships": {"user": {"data": {"id": "user-X"}}, "teams": {"data": [{"id": "team-123"}]}}}
                ]
            })
        if "/teams" in url:
            return MockResponse(200, {"data": [{"id": "team-123", "attributes": {"name": "owners"}}]})
        raise AssertionError(f"Unexpected GET url: {url}")

    delete_calls = {"payloads": []}

    def fake_delete(url, headers, json):
        delete_calls["payloads"].append(json)
        return MockResponse(204)

    # Avoid waiting 3 seconds
    def fake_sleep(_):
        return None

    monkeypatch.setattr(main_mod, "requests", SimpleNamespace(get=fake_get, delete=fake_delete))
    monkeypatch.setattr(main_mod, "time", SimpleNamespace(sleep=fake_sleep))

    argv = [
        "main.py", "--org", "acme", "--team", "owners", "--emails-file", str(p)
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 0
    # Ensure a bulk payload is sent
    assert delete_calls["payloads"], "Expected one bulk delete payload"
    payload = delete_calls["payloads"][0]
    assert payload["data"], "Payload should include data array"
    # Should have as many entries as emails (3)
    assert len(payload["data"]) == 3


def test_emails_file_extension_enforced(tmp_path, monkeypatch, main_mod):
    p = tmp_path / "emails.md"
    p.write_text("user@example.com\n", encoding="utf-8")

    argv = ["main.py", "--org", "acme", "--team", "owners", "--emails-file", str(p)]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 1
