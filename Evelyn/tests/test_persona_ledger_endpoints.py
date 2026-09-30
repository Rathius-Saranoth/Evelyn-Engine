# test_persona_ledger_endpoints.py
# date created: 2026-09-29 17:15:00
# date modified: 2026-09-29 19:15:09
# tags: #testing, #persona, #ledger, #endpoints, #api

"""
Hermetic integration tests for persona ledger curation API endpoints:
- GET /api/persona/ledgers
- GET /api/persona/ledger/{filename}
- POST /api/persona/ledger/{filename}/item/update
- POST /api/persona/ledger/{filename}/item/delete
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import evelyn_config as cfg
import evelyn_server
from evelyn_server import app


@pytest.fixture
def hermetic_persona_dir(monkeypatch):
    """Create a temporary sandbox for persona files and monkeypatch server paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Create sample User_Profile_facts.md
        user_facts = """---
title: User_Profile_facts.md
tags: [persona, profile, user]
date created: 2026-09-29 12:00:00
date modified: 2026-09-29 12:00:00
---

## Communication Style
* [Tier 1] **Direct Answers**: Prefers concise, unembellished answers.
* [Tier 2] **Casual Tone**: Enjoys dry humor.

## Technical Preferences
* [Tier 1] **Python**: Loves modern Python with type hints.
"""
        (tmp_path / "User_Profile_facts.md").write_text(user_facts, encoding="utf-8")

        # 2. Create sample User_Profile.md
        user_profile = """---
title: User_Profile.md
tags: [persona, profile, user]
date created: 2026-09-29 12:00:00
date modified: 2026-09-29 12:00:00
---

## Communication Style
* **Direct Answers**: Prefers concise, unembellished answers.
* **Casual Tone**: Enjoys dry humor.

## Technical Preferences
* **Python**: Loves modern Python with type hints.
"""
        (tmp_path / "User_Profile.md").write_text(user_profile, encoding="utf-8")

        # 3. Create sample Assistant_Profile_facts.md
        asst_facts = """---
title: Assistant_Profile_facts.md
tags: [persona, profile, assistant]
---

## Core Demeanor
* [Tier 1] **Steady Partner**: Calm and reliable partner.
"""
        (tmp_path / "Assistant_Profile_facts.md").write_text(asst_facts, encoding="utf-8")

        # 4. Create sample System_Directives_facts.md
        dir_facts = """---
title: System_Directives_facts.md
tags: [persona, directives]
---

## Tool Directives
* [Tier 1] **Single Writer**: Never run multi-process writers to Chroma.
"""
        (tmp_path / "System_Directives_facts.md").write_text(dir_facts, encoding="utf-8")

        # Monkeypatch server PERSONA_DIR and update_frontmatter subprocess
        monkeypatch.setattr(evelyn_server, "PERSONA_DIR", tmp_path)

        yield tmp_path


def _get_auth_headers():
    return {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}


def test_list_persona_ledgers(hermetic_persona_dir):
    client = TestClient(app)
    res = client.get("/api/persona/ledgers", headers=_get_auth_headers())
    assert res.status_code == 200

    data = res.json()
    assert "ledgers" in data
    assert len(data["ledgers"]) == 3

    filenames = [l["filename"] for l in data["ledgers"]]
    assert "User_Profile_facts.md" in filenames
    assert "Assistant_Profile_facts.md" in filenames
    assert "System_Directives_facts.md" in filenames

    user_led = next(l for l in data["ledgers"] if l["filename"] == "User_Profile_facts.md")
    assert user_led["total_items"] == 3
    assert user_led["total_words"] > 0


def test_get_persona_ledger(hermetic_persona_dir):
    client = TestClient(app)

    # Valid request
    res = client.get("/api/persona/ledger/User_Profile_facts.md", headers=_get_auth_headers())
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "User_Profile_facts.md"
    assert data["profile_filename"] == "User_Profile.md"
    assert data["total_items"] == 3
    assert "## Communication Style" in data["sections"]
    items = data["sections"]["## Communication Style"]
    assert len(items) == 2
    assert items[0]["label"] == "Direct Answers"
    assert items[0]["tier"] == 1

    # Invalid filename validation
    bad_res = client.get("/api/persona/ledger/Malicious_File.md", headers=_get_auth_headers())
    assert bad_res.status_code == 400


def test_update_and_add_ledger_item(hermetic_persona_dir):
    client = TestClient(app)

    # Prevent subprocess run of update_frontmatter.py in unit test
    with patch("subprocess.run"):
        # 1. Update existing item
        update_payload = {
            "section": "## Communication Style",
            "orig_label": "Direct Answers",
            "label": "Direct Answers",
            "fact": "Prefers extremely concise, direct answers.",
            "tier": 1,
        }
        res = client.post(
            "/api/persona/ledger/User_Profile_facts.md/item/update",
            json=update_payload,
            headers=_get_auth_headers(),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"

        # Verify ledger file was updated
        ledger_text = (hermetic_persona_dir / "User_Profile_facts.md").read_text(encoding="utf-8")
        assert "* [Tier 1] **Direct Answers**: Prefers extremely concise, direct answers." in ledger_text

        # Verify presentation file was recompiled
        pres_text = (hermetic_persona_dir / "User_Profile.md").read_text(encoding="utf-8")
        assert "* **Direct Answers**: Prefers extremely concise, direct answers." in pres_text

        # 2. Add new item
        add_payload = {
            "section": "## Workflow Rules",
            "orig_label": "",
            "label": "Code Hygiene",
            "fact": "Always run hygiene check before git commits.",
            "tier": 2,
        }
        res_add = client.post(
            "/api/persona/ledger/User_Profile_facts.md/item/update",
            json=add_payload,
            headers=_get_auth_headers(),
        )
        assert res_add.status_code == 200
        assert res_add.json()["total_items"] == 4

        ledger_text = (hermetic_persona_dir / "User_Profile_facts.md").read_text(encoding="utf-8")
        assert "## Workflow Rules" in ledger_text
        assert "* [Tier 2] **Code Hygiene**: Always run hygiene check before git commits." in ledger_text


def test_delete_ledger_item(hermetic_persona_dir):
    client = TestClient(app)

    with patch("subprocess.run"):
        # Delete "Casual Tone"
        del_payload = {
            "section": "## Communication Style",
            "label": "Casual Tone",
            "fact": "",
        }
        res = client.post(
            "/api/persona/ledger/User_Profile_facts.md/item/delete",
            json=del_payload,
            headers=_get_auth_headers(),
        )
        assert res.status_code == 200
        assert res.json()["total_items"] == 2

        # Verify removed from facts ledger
        ledger_text = (hermetic_persona_dir / "User_Profile_facts.md").read_text(encoding="utf-8")
        assert "Casual Tone" not in ledger_text
        assert "Direct Answers" in ledger_text

        # Verify removed from presentation document
        pres_text = (hermetic_persona_dir / "User_Profile.md").read_text(encoding="utf-8")
        assert "Casual Tone" not in pres_text
        assert "Direct Answers" in pres_text
