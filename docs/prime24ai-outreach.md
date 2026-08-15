# Prime24AI Personalized Outreach Videos

This repository includes an isolated Prime24AI adapter that turns a verified recruiting/staffing prospect record into a narrated Agnes manuscript video without changing Agnes core pipelines.

## Purpose

Use personalized video as a follow-up layer for high-value Prime24AI outreach. The adapter is intentionally conservative:

- it uses only prospect facts supplied in the JSON record;
- it does not invent metrics, testimonials, client names, or guarantees;
- it keeps recruiter judgment and approval explicitly human-controlled;
- it runs one prospect at a time by default;
- `--dry-run` validates and prints the generated manuscript without contacting Agnes.

## Files

- `scripts/prime24ai_outreach.py` — validate, build manuscript, submit one task, poll, download MP4.
- `scripts/prime24ai_batch.py` — sequential batch runner with per-prospect failure isolation.
- `prime24ai/prospects.example.json` — starter prospect records.
- `prime24ai/templates/recruiting.json` — controlled five-beat recruiting sales template.
- `tests/test_prime24ai_outreach.py` — validation, dry-run, payload, and batch-isolation coverage.

## Prerequisites

1. Start Agnes normally:

```bash
./start.sh
```

2. Configure an Agnes API key using the existing Agnes configuration flow. Do not commit a real key.

3. Verify the service:

```bash
curl -s http://localhost:8765/api/config | python3 -m json.tool
curl -s http://localhost:8765/api/voices | python3 -m json.tool
```

## Validate before generating

Always run a dry-run first:

```bash
python scripts/prime24ai_outreach.py \
  --prospects prime24ai/prospects.example.json \
  --id acumen-executive-search \
  --dry-run
```

The command prints the generated manuscript and Agnes payload but makes no network call to the video service.

## Generate one video

```bash
python scripts/prime24ai_outreach.py \
  --prospects prime24ai/prospects.example.json \
  --id acumen-executive-search \
  --server-url http://localhost:8765
```

Successful output is written to:

```text
output/prime24ai/acumen-executive-search.mp4
```

## Generate the initial five-prospect batch

Dry-run all records:

```bash
python scripts/prime24ai_batch.py \
  --prospects prime24ai/prospects.example.json \
  --dry-run
```

Generate all records sequentially:

```bash
python scripts/prime24ai_batch.py \
  --prospects prime24ai/prospects.example.json \
  --server-url http://localhost:8765
```

Generate a subset:

```bash
python scripts/prime24ai_batch.py \
  --prospects prime24ai/prospects.example.json \
  --ids acumen-executive-search it-motives rainier-recruiting
```

A failed prospect does not stop the remaining batch. The process exits `2` when at least one prospect failed and prints a structured JSON summary.

## Prospect contract

Each object requires:

```json
{
  "id": "company-slug",
  "company": "Company Name",
  "website": "https://company.example",
  "contact_name": "Optional name",
  "observed_workflow": "Verified public-facing workflow observation",
  "automation_proposal": "Bounded automation Prime24AI is proposing",
  "cta": "Low-friction next step",
  "source_note": "Optional provenance note"
}
```

`id` must be a lowercase filesystem-safe slug. `website` must be an absolute HTTP or HTTPS URL. Unresolved placeholders are rejected.

## Story structure

The recruiting template uses five beats:

1. Company-specific opening.
2. Observed repetitive workflow.
3. Proposed automation.
4. Explicit human approval and recruiter control.
5. Prime24AI fixed-scope Automation Sprint CTA.

The default task uses Agnes `manuscript` mode, English Edge TTS, subtitles, and 1152×648 output (16:9). The manuscript endpoint remains the source of truth for actual scene splitting and final runtime duration.

## Testing

Run the adapter tests only:

```bash
python -m pytest tests/test_prime24ai_outreach.py -q
```

Run the repository suite:

```bash
python -m pytest tests/ -q
```

The tests do not require an Agnes API key and do not perform model-network calls.

## Operational rule

Do not automatically send a generated video to a prospect. Render, review the result, and only then attach or link it in a targeted follow-up. This keeps the outreach low-volume and prevents a bad render or inaccurate visual from reaching a prospect.
