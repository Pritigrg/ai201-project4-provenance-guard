from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from audit_log import append_audit_log, get_audit_log
from detector import (
    classify,
    combine_signals,
    confidence_for,
    lexical_repetition_score,
    llm_ai_score,
    sentence_rhythm_score,
    transparency_label,
)

load_dotenv()

app = Flask(__name__)

# Rate limiter. In-memory storage is fine for local/dev; swap storage_uri for
# Redis in production so limits are shared across workers. Limits are applied
# per-route (see /submit) rather than globally.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.errorhandler(429)
def rate_limit_exceeded(error: Any) -> Any:
    """Return planning.md's structured error when a client exceeds the limit."""
    return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429

DATA_DIR = Path(os.environ.get("PROVENANCE_DATA_DIR", Path(__file__).parent / "data"))
SUBMISSIONS_PATH = DATA_DIR / "submissions.json"
AUDIT_LOG_PATH = DATA_DIR / "audit_log.jsonl"

DATA_DIR.mkdir(exist_ok=True)


def now_utc_iso() -> str:
    """Return an ISO-8601 UTC timestamp for audit-log entries."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json_list(path: Path) -> list[dict[str, Any]]:
    """Read a JSON list from disk. Return an empty list if the file does not exist yet."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def write_json_list(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a JSON list to disk with indentation so it is easy to inspect."""
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def save_submission(record: dict[str, Any]) -> None:
    """Append a submission record to the local submissions store."""
    rows = read_json_list(SUBMISSIONS_PATH)
    rows.append(record)
    write_json_list(SUBMISSIONS_PATH, rows)


def find_submission(content_id: str) -> dict[str, Any] | None:
    """Return the stored submission record for a content_id, or None."""
    for row in read_json_list(SUBMISSIONS_PATH):
        if row.get("content_id") == content_id:
            return row
    return None


def update_submission(content_id: str, updates: dict[str, Any]) -> None:
    """Apply field updates to the stored submission record for a content_id."""
    rows = read_json_list(SUBMISSIONS_PATH)
    for row in rows:
        if row.get("content_id") == content_id:
            row.update(updates)
            break
    write_json_list(SUBMISSIONS_PATH, rows)


@app.route("/health", methods=["GET"])
def health() -> Any:
    return jsonify({"status": "ok"})


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit_content() -> Any:
    """
    Accept text content for attribution analysis.

    JSON fields:
    - text: submitted creative text (required)
    - creator_id: ID of the creator (optional; defaults to "anonymous")
    - title, content_type: optional metadata

    Response includes the content_id, combined attribution + confidence over both
    signals, each individual signal score, and the reader-facing transparency
    label (which varies by classification). Rate-limited to 10/min; 100/day per IP.
    """
    payload = request.get_json(silent=True) or {}

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Missing or empty required field: text"}), 400

    creator_id = payload.get("creator_id")
    if not isinstance(creator_id, str) or not creator_id.strip():
        creator_id = "anonymous"
    title = payload.get("title", "")
    content_type = payload.get("content_type", "text")

    content_id = f"content_{uuid.uuid4().hex[:8]}"
    timestamp = now_utc_iso()

    # Signal 1: Groq LLM, with graceful fallback to the lexical heuristic.
    llm = llm_ai_score(text)
    if llm["available"]:
        signal1_value = llm["score"]
        llm_score = llm["score"]
        signal_source = "groq_llm"
        low_reliability = False
    else:
        signal1_value = lexical_repetition_score(text)
        llm_score = None
        signal_source = "heuristic_fallback"
        low_reliability = True

    # Signal 2: sentence rhythm / structure variation.
    rhythm_score = sentence_rhythm_score(text)

    # Combine both signals into the calibrated M4 decision.
    ai_likelihood_score = combine_signals(signal1_value, rhythm_score)
    attribution = classify(ai_likelihood_score)
    confidence = confidence_for(ai_likelihood_score)

    label = transparency_label(attribution)

    submission_record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "title": title,
        "content_type": content_type,
        "text": text,
        "timestamp": timestamp,
        "status": "classified",
        "attribution": attribution,
        "confidence": confidence,
        "ai_likelihood_score": ai_likelihood_score,
        "llm_score": llm_score,
        "llm_reason": llm["reason"],
        "sentence_rhythm_score": rhythm_score,
        "signal_source": signal_source,
        "low_reliability": low_reliability,
        "transparency_label": label,
        "appealed": False,
    }
    save_submission(submission_record)

    audit_entry = {
        "event_type": "submission",
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "attribution": attribution,
        "confidence": confidence,
        "ai_likelihood_score": ai_likelihood_score,
        "llm_score": llm_score,
        "sentence_rhythm_score": rhythm_score,
        "signal_source": signal_source,
        "low_reliability": low_reliability,
        "transparency_label": label,
        "appealed": False,
        "status": "classified",
    }
    append_audit_log(AUDIT_LOG_PATH, audit_entry)

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": confidence,
        "ai_likelihood_score": ai_likelihood_score,
        "llm_score": llm_score,
        "sentence_rhythm_score": rhythm_score,
        "signal_source": signal_source,
        "low_reliability": low_reliability,
        "label": label,
        "status": "classified",
    }), 201


@app.route("/appeal", methods=["POST"])
@app.route("/appeal/<content_id>", methods=["POST"])
def appeal_content(content_id: str | None = None) -> Any:
    """
    Accept a creator's appeal of a classification decision.

    The content_id may be supplied in the URL path (``/appeal/<content_id>``) or
    in the JSON body. The creator's explanation is read from ``creator_reasoning``
    (also accepts ``reason`` / ``appeal_reason`` as aliases).

    On success the endpoint:
    1. Validates the content exists and the reasoning is non-empty.
    2. (If a creator_id is provided) checks it matches the original submitter.
    3. Updates the content status to ``under_review``.
    4. Appends an appeal event to the audit log alongside the original decision.
    5. Returns a confirmation. Automated re-classification is out of scope.
    """
    payload = request.get_json(silent=True) or {}

    content_id = content_id or payload.get("content_id")
    if not isinstance(content_id, str) or not content_id.strip():
        return jsonify({"error": "Missing required field: content_id"}), 400

    reasoning = (
        payload.get("creator_reasoning")
        or payload.get("reason")
        or payload.get("appeal_reason")
    )
    if not isinstance(reasoning, str) or not reasoning.strip():
        return jsonify({"error": "Missing or empty required field: creator_reasoning"}), 400
    reasoning = reasoning.strip()

    submission = find_submission(content_id)
    if submission is None:
        return jsonify({"error": f"Unknown content_id: {content_id}"}), 404

    # Simulated authentication: if a creator_id is supplied it must match the
    # original submitter (planning.md). Omitting it is allowed for convenience.
    creator_id = payload.get("creator_id")
    if isinstance(creator_id, str) and creator_id.strip():
        if creator_id.strip() != submission.get("creator_id"):
            return jsonify({"error": "creator_id does not match the original submitter"}), 403
    else:
        creator_id = submission.get("creator_id")

    timestamp = now_utc_iso()
    update_submission(
        content_id,
        {
            "status": "under_review",
            "appealed": True,
            "appeal_reasoning": reasoning,
            "appealed_at": timestamp,
        },
    )

    appeal_entry = {
        "event_type": "appeal",
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "original_attribution": submission.get("attribution"),
        "original_confidence": submission.get("confidence"),
        "llm_score": submission.get("llm_score"),
        "sentence_rhythm_score": submission.get("sentence_rhythm_score"),
        "appeal_reasoning": reasoning,
        "appealed": True,
        "new_status": "under_review",
        "status": "under_review",
    }
    append_audit_log(AUDIT_LOG_PATH, appeal_entry)

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "status": "under_review",
        "appeal_reasoning": reasoning,
        "message": "Your appeal has been recorded and the content is now under review.",
    }), 201


@app.route("/log", methods=["GET"])
def read_log() -> Any:
    """Return recent audit-log entries as JSON for grading/documentation visibility."""
    try:
        limit = int(request.args.get("limit", 20))
    except ValueError:
        limit = 20

    entries = get_audit_log(AUDIT_LOG_PATH, limit=limit)
    return jsonify({"entries": entries})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
