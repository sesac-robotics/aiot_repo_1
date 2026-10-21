"""Validate PC2's published buffalo_l 512-D face JSON (stdlib only)."""
from __future__ import annotations
from datetime import datetime
import json
import math
from pathlib import Path


def read_object(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def require_aware_time(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("timestamp must be a timezone-aware ISO 8601 string")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp must include a timezone (e.g. +00:00)")
    return dt


def normalize_face_embedding(payload: dict) -> list[float]:
    if payload.get("model") != "buffalo_l":
        raise ValueError("v0.1 accepts buffalo_l model only")
    if payload.get("embedding_dim") != 512:
        raise ValueError("embedding_dim must equal 512")
    values = payload.get("embedding")
    if not isinstance(values, list) or len(values) != 512:
        raise ValueError("embedding must be a list of exactly 512 floats")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise ValueError("embedding contains an invalid number")
    vals = [float(x) for x in values]
    length = math.sqrt(sum(x * x for x in vals))
    if not math.isfinite(length) or length <= 0:
        raise ValueError("embedding magnitude must be positive and finite")
    return [x / length for x in vals]


def validate_registered(payload: dict) -> tuple[str, str, list[float], int, datetime | None]:
    pid = payload.get("patient_id")
    if not isinstance(pid, str) or not pid.strip() or len(pid) > 40:
        raise ValueError("patient_id must be a non-empty string <= 40 characters")
    vec = normalize_face_embedding(payload)
    n = payload.get("images_used", 1)
    if isinstance(n, bool) or not isinstance(n, int) or not (1 <= n <= 32767):
        raise ValueError("images_used must be an integer between 1 and 32767")
    created = require_aware_time(payload["created_at"]) if payload.get("created_at") else None
    return (pid, payload["model"], vec, n, created)


def validate_captured(payload: dict) -> tuple[str, str, list[float], datetime]:
    eid = payload.get("event_id")
    if not isinstance(eid, str) or not eid.strip() or len(eid) > 64:
        raise ValueError("capture event_id must be 1-64 characters")
    if payload.get("status") != "OK":
        raise ValueError(f"capture status must be OK, got: {payload.get('status')}")
    vec = normalize_face_embedding(payload)
    stamp = require_aware_time(payload.get("timestamp"))
    return (eid, payload["model"], vec, stamp)
