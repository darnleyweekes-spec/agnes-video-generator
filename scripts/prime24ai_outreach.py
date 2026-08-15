#!/usr/bin/env python3
"""Prime24AI personalized prospect video runner for Agnes Video Generator.

This adapter deliberately stays outside Agnes core. It converts a verified prospect
record into a controlled manuscript, submits the manuscript to the existing Agnes
API, polls for completion, and downloads the final MP4.

Examples:
    python scripts/prime24ai_outreach.py \
      --prospects prime24ai/prospects.example.json \
      --id acumen-executive-search --dry-run

    python scripts/prime24ai_outreach.py \
      --prospects prime24ai/prospects.example.json \
      --id acumen-executive-search \
      --server-url http://localhost:8765
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROSPECTS = PROJECT_ROOT / "prime24ai" / "prospects.example.json"
DEFAULT_TEMPLATE = PROJECT_ROOT / "prime24ai" / "templates" / "recruiting.json"
DEFAULT_SERVER_URL = "http://localhost:8765"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "prime24ai"

_REQUIRED_FIELDS = (
    "id",
    "company",
    "website",
    "observed_workflow",
    "automation_proposal",
    "cta",
)
_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}|<[^<>]+>|\[[A-Z][A-Z0-9 _-]{2,}\]")


class ProspectValidationError(ValueError):
    """Raised when prospect input cannot be used safely."""


class AgnesRunError(RuntimeError):
    """Raised when Agnes cannot create or complete a task."""


@dataclass(frozen=True)
class Prospect:
    id: str
    company: str
    website: str
    observed_workflow: str
    automation_proposal: str
    cta: str
    contact_name: str = ""
    source_note: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "company": self.company,
            "website": self.website,
            "observed_workflow": self.observed_workflow,
            "automation_proposal": self.automation_proposal,
            "cta": self.cta,
            "contact_name": self.contact_name,
            "source_note": self.source_note,
        }


def _clean_text(value: Any, field: str, max_len: int = 2000) -> str:
    if not isinstance(value, str):
        raise ProspectValidationError(f"{field} must be a string")
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ProspectValidationError(f"{field} cannot be empty")
    if len(cleaned) > max_len:
        raise ProspectValidationError(f"{field} exceeds {max_len} characters")
    if _PLACEHOLDER_RE.search(cleaned):
        raise ProspectValidationError(f"{field} contains an unresolved placeholder")
    return cleaned


def validate_prospect(raw: dict[str, Any]) -> Prospect:
    if not isinstance(raw, dict):
        raise ProspectValidationError("prospect must be a JSON object")

    missing = [field for field in _REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ProspectValidationError(f"missing required fields: {', '.join(missing)}")

    prospect_id = _clean_text(raw["id"], "id", max_len=64).lower()
    if not _ID_RE.fullmatch(prospect_id):
        raise ProspectValidationError(
            "id must be a lowercase slug containing only letters, numbers, and hyphens"
        )

    website = _clean_text(raw["website"], "website", max_len=500)
    parsed = urlparse(website)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProspectValidationError("website must be an absolute http(s) URL")

    contact_name = raw.get("contact_name", "")
    if contact_name:
        contact_name = _clean_text(contact_name, "contact_name", max_len=200)

    source_note = raw.get("source_note", "")
    if source_note:
        source_note = _clean_text(source_note, "source_note", max_len=1000)

    return Prospect(
        id=prospect_id,
        company=_clean_text(raw["company"], "company", max_len=200),
        website=website,
        observed_workflow=_clean_text(
            raw["observed_workflow"], "observed_workflow", max_len=1400
        ),
        automation_proposal=_clean_text(
            raw["automation_proposal"], "automation_proposal", max_len=1400
        ),
        cta=_clean_text(raw["cta"], "cta", max_len=500),
        contact_name=contact_name,
        source_note=source_note,
    )


def load_prospects(path: str | os.PathLike[str]) -> list[Prospect]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, list):
        raise ProspectValidationError("prospect file must contain a JSON array")

    prospects = [validate_prospect(item) for item in payload]
    ids = [p.id for p in prospects]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ProspectValidationError(f"duplicate prospect id(s): {', '.join(duplicates)}")
    return prospects


def load_template(path: str | os.PathLike[str] = DEFAULT_TEMPLATE) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        template = json.load(handle)
    required = {"opening", "problem", "solution", "human_control", "cta"}
    missing = sorted(required.difference(template))
    if missing:
        raise ProspectValidationError(
            f"template missing required keys: {', '.join(missing)}"
        )
    return template


def _format_line(text: str, prospect: Prospect) -> str:
    values = prospect.as_dict()
    try:
        rendered = text.format_map(values)
    except KeyError as exc:
        raise ProspectValidationError(f"template references unknown field: {exc.args[0]}")
    if _PLACEHOLDER_RE.search(rendered):
        raise ProspectValidationError("rendered script contains an unresolved placeholder")
    return rendered.strip()


def build_manuscript(
    prospect: Prospect,
    template: dict[str, Any],
) -> str:
    """Build a controlled 5-beat sales manuscript from prospect-supplied facts.

    The adapter never invents metrics, testimonials, customer names, or guarantees.
    Prospect-specific observations and automation ideas are supplied explicitly in
    the input record and merely framed into the fixed Prime24AI story structure.
    """

    sections = [
        _format_line(str(template["opening"]), prospect),
        _format_line(str(template["problem"]), prospect),
        _format_line(str(template["solution"]), prospect),
        _format_line(str(template["human_control"]), prospect),
        _format_line(str(template["cta"]), prospect),
    ]
    manuscript = "\n\n".join(sections)
    if len(manuscript) > 50000:
        raise ProspectValidationError("generated manuscript exceeds Agnes limit")
    return manuscript


def build_task_payload(
    prospect: Prospect,
    manuscript: str,
    *,
    voice: str = "en-US-GuyNeural",
    width: int = 1152,
    height: int = 648,
    video_duration: int = 10,
) -> dict[str, Any]:
    if width <= 0 or height <= 0:
        raise ProspectValidationError("video dimensions must be positive")
    if video_duration < 2 or video_duration > 30:
        raise ProspectValidationError("video_duration must be between 2 and 30 seconds")

    return {
        "manuscript_text": manuscript,
        "creative_name": f"prime24ai_{prospect.id}",
        "video_width": width,
        "video_height": height,
        "video_duration": video_duration,
        "audio_enabled": True,
        "audio_voice": voice,
        "audio_rate": "+0%",
        "audio_lang": "en",
        "subtitle_enabled": True,
        "subtitle_style_mode": "fixed",
        "subtitle_color": "white",
        "subtitle_fontsize": 42,
        "subtitle_position": "bottom",
        "subtitle_stroke_color": "black",
        "subtitle_stroke_width": 2,
        "subtitle_bg_color": "black@0.45",
    }


def _raise_for_json_error(response: requests.Response, action: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:1000]
        raise AgnesRunError(f"{action} failed: HTTP {response.status_code}: {detail}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise AgnesRunError(f"{action} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise AgnesRunError(f"{action} returned unexpected response shape")
    return data


def submit_manuscript(
    session: requests.Session,
    server_url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = session.post(
        f"{server_url.rstrip('/')}/api/tasks/manuscript",
        data=payload,
        timeout=30,
    )
    data = _raise_for_json_error(response, "task submission")
    if not data.get("ok") or not data.get("task_id"):
        raise AgnesRunError(f"task submission rejected: {data}")
    return data


def wait_for_task(
    session: requests.Session,
    server_url: str,
    task_id: str,
    *,
    poll_interval: float = 10.0,
    timeout: float = 3600.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        response = session.get(
            f"{server_url.rstrip('/')}/api/tasks/{task_id}",
            timeout=20,
        )
        state = _raise_for_json_error(response, "task status")
        last_state = state
        status = str(state.get("status", "")).lower()
        if status == "completed":
            return state
        if status == "failed":
            message = state.get("current_message") or "Agnes task failed"
            raise AgnesRunError(str(message))
        time.sleep(max(0.05, poll_interval))

    progress = ""
    if last_state:
        progress = f"; last status={last_state.get('status')} step={last_state.get('current_step')}"
    raise AgnesRunError(f"task {task_id} timed out after {timeout:.0f}s{progress}")


def download_video(
    session: requests.Session,
    server_url: str,
    task_id: str,
    destination: str | os.PathLike[str],
) -> Path:
    output_path = Path(destination)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = session.get(
        f"{server_url.rstrip('/')}/api/video/{task_id}",
        timeout=120,
        stream=True,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise AgnesRunError(
            f"video download failed: HTTP {response.status_code}: {response.text[:500]}"
        ) from exc
    with output_path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    return output_path


def run_prospect(
    prospect: Prospect,
    *,
    server_url: str = DEFAULT_SERVER_URL,
    output_dir: str | os.PathLike[str] = DEFAULT_OUTPUT_DIR,
    template_path: str | os.PathLike[str] = DEFAULT_TEMPLATE,
    voice: str = "en-US-GuyNeural",
    dry_run: bool = False,
    poll_interval: float = 10.0,
    timeout: float = 3600.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    template = load_template(template_path)
    manuscript = build_manuscript(prospect, template)
    payload = build_task_payload(prospect, manuscript, voice=voice)

    result: dict[str, Any] = {
        "prospect_id": prospect.id,
        "company": prospect.company,
        "website": prospect.website,
        "manuscript": manuscript,
        "payload": payload,
        "dry_run": dry_run,
    }
    if dry_run:
        result["status"] = "validated"
        return result

    owns_session = session is None
    client = session or requests.Session()
    try:
        submitted = submit_manuscript(client, server_url, payload)
        task_id = submitted["task_id"]
        state = wait_for_task(
            client,
            server_url,
            task_id,
            poll_interval=poll_interval,
            timeout=timeout,
        )
        output_path = Path(output_dir) / f"{prospect.id}.mp4"
        final_path = download_video(client, server_url, task_id, output_path)
        result.update(
            {
                "status": "completed",
                "task_id": task_id,
                "dir_name": submitted.get("dir_name", state.get("dir_name", "")),
                "output": str(final_path),
            }
        )
        return result
    finally:
        if owns_session:
            client.close()


def _select_prospect(prospects: Iterable[Prospect], prospect_id: str) -> Prospect:
    for prospect in prospects:
        if prospect.id == prospect_id:
            return prospect
    raise ProspectValidationError(f"unknown prospect id: {prospect_id}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prospects", default=str(DEFAULT_PROSPECTS))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--id", required=True, help="Prospect id from the JSON file")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--voice", default="en-US-GuyNeural")
    parser.add_argument("--poll", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        prospects = load_prospects(args.prospects)
        prospect = _select_prospect(prospects, args.id)
        result = run_prospect(
            prospect,
            server_url=args.server_url,
            output_dir=args.output_dir,
            template_path=args.template,
            voice=args.voice,
            dry_run=args.dry_run,
            poll_interval=args.poll,
            timeout=args.timeout,
        )
    except (ProspectValidationError, AgnesRunError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
