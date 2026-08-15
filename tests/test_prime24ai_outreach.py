"""Unit tests for the isolated Prime24AI Agnes adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import prime24ai_batch
from scripts.prime24ai_outreach import (
    AgnesRunError,
    ProspectValidationError,
    build_manuscript,
    build_task_payload,
    load_prospects,
    run_prospect,
    validate_prospect,
)


def _raw_prospect(**overrides):
    data = {
        "id": "example-search",
        "company": "Example Search",
        "website": "https://example.com",
        "contact_name": "Alex",
        "observed_workflow": "candidate intake is reviewed, routed, and followed up manually",
        "automation_proposal": "candidate intake to structured summary, recruiter approval, and follow-up",
        "cta": "Would a short walkthrough be useful?",
        "source_note": "Public website review",
    }
    data.update(overrides)
    return data


def _template():
    return {
        "opening": "Built for {company}. Public site: {website}.",
        "problem": "Observed: {observed_workflow}.",
        "solution": "Concept: {automation_proposal}.",
        "human_control": "Recruiters keep human approval at decision points.",
        "cta": "Prime24AI Automation Sprint. {cta}",
    }


class TestProspectValidation:
    def test_valid_prospect(self):
        prospect = validate_prospect(_raw_prospect())
        assert prospect.id == "example-search"
        assert prospect.company == "Example Search"
        assert prospect.website == "https://example.com"

    @pytest.mark.parametrize(
        "field",
        ["id", "company", "website", "observed_workflow", "automation_proposal", "cta"],
    )
    def test_missing_required_field(self, field):
        raw = _raw_prospect()
        raw.pop(field)
        with pytest.raises(ProspectValidationError, match="missing required fields"):
            validate_prospect(raw)

    @pytest.mark.parametrize(
        "website",
        ["example.com", "ftp://example.com", "not a url", ""],
    )
    def test_malformed_url(self, website):
        with pytest.raises(ProspectValidationError):
            validate_prospect(_raw_prospect(website=website))

    @pytest.mark.parametrize("bad_id", ["Example Search", "../example", "example_search", "-bad"])
    def test_rejects_unsafe_ids(self, bad_id):
        with pytest.raises(ProspectValidationError):
            validate_prospect(_raw_prospect(id=bad_id))

    def test_rejects_unresolved_placeholders(self):
        with pytest.raises(ProspectValidationError, match="placeholder"):
            validate_prospect(_raw_prospect(cta="Talk to {{FirstName}} today"))

    def test_duplicate_ids_rejected(self, tmp_path):
        path = tmp_path / "prospects.json"
        path.write_text(json.dumps([_raw_prospect(), _raw_prospect()]), encoding="utf-8")
        with pytest.raises(ProspectValidationError, match="duplicate"):
            load_prospects(path)


class TestScriptAndPayload:
    def test_script_contains_specific_company_and_human_control(self):
        prospect = validate_prospect(_raw_prospect())
        manuscript = build_manuscript(prospect, _template())
        assert "Example Search" in manuscript
        assert "candidate intake" in manuscript
        assert "human approval" in manuscript
        assert "walkthrough" in manuscript

    def test_payload_uses_manuscript_endpoint_contract(self):
        prospect = validate_prospect(_raw_prospect())
        manuscript = build_manuscript(prospect, _template())
        payload = build_task_payload(prospect, manuscript)
        assert payload["manuscript_text"] == manuscript
        assert payload["creative_name"] == "prime24ai_example-search"
        assert payload["audio_enabled"] is True
        assert payload["subtitle_enabled"] is True
        assert payload["audio_lang"] == "en"
        assert payload["video_width"] == 1152
        assert payload["video_height"] == 648
        assert payload["video_width"] / payload["video_height"] == pytest.approx(16 / 9)

    def test_invalid_video_duration_rejected(self):
        prospect = validate_prospect(_raw_prospect())
        with pytest.raises(ProspectValidationError, match="video_duration"):
            build_task_payload(prospect, "hello", video_duration=31)

    def test_dry_run_never_calls_network(self, tmp_path):
        template_path = tmp_path / "template.json"
        template_path.write_text(json.dumps(_template()), encoding="utf-8")
        prospect = validate_prospect(_raw_prospect())

        class NoNetworkSession:
            def post(self, *args, **kwargs):
                raise AssertionError("network must not be called in dry-run")

            def get(self, *args, **kwargs):
                raise AssertionError("network must not be called in dry-run")

        result = run_prospect(
            prospect,
            template_path=template_path,
            dry_run=True,
            session=NoNetworkSession(),
        )
        assert result["status"] == "validated"
        assert result["dry_run"] is True
        assert "manuscript" in result


class TestBatchIsolation:
    def test_batch_continues_after_one_failure(self, tmp_path, monkeypatch, capsys):
        prospects_path = tmp_path / "prospects.json"
        prospects_path.write_text(
            json.dumps(
                [
                    _raw_prospect(id="first", company="First Search"),
                    _raw_prospect(id="second", company="Second Search"),
                ]
            ),
            encoding="utf-8",
        )
        template_path = tmp_path / "template.json"
        template_path.write_text(json.dumps(_template()), encoding="utf-8")

        calls = []

        def fake_run(prospect, **kwargs):
            calls.append(prospect.id)
            if prospect.id == "first":
                raise AgnesRunError("simulated failure")
            return {
                "prospect_id": prospect.id,
                "company": prospect.company,
                "status": "completed",
            }

        monkeypatch.setattr(prime24ai_batch, "run_prospect", fake_run)
        code = prime24ai_batch.main(
            [
                "--prospects",
                str(prospects_path),
                "--template",
                str(template_path),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )

        assert calls == ["first", "second"]
        assert code == 2
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "completed_with_failures"
        assert output["failed"] == 1
        assert output["completed"] == 1

    def test_unknown_batch_id_fails_before_render(self, tmp_path, capsys):
        prospects_path = tmp_path / "prospects.json"
        prospects_path.write_text(json.dumps([_raw_prospect()]), encoding="utf-8")
        code = prime24ai_batch.main(
            ["--prospects", str(prospects_path), "--ids", "does-not-exist", "--dry-run"]
        )
        assert code == 1
        output = json.loads(capsys.readouterr().out)
        assert "unknown prospect" in output["error"]
