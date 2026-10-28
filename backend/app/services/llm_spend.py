"""Read bounded private provider evidence; never trigger an LLM or billing refresh."""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from typing import Any, TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.storage.r2 import R2Storage

ZONE = ZoneInfo("Europe/London")
PREFIX = "reports/openrouter-daily"
TOKEN_METRICS = ("tokens_prompt", "tokens_completion", "tokens_reasoning", "tokens_cached", "tokens_total")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid provider metric")
    return value


def _date(value: str) -> dt.date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Expected YYYY-MM-DD")
    return dt.date.fromisoformat(value)


def _timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Missing timezone")
    return parsed


def summarise_report(raw: dict[str, Any], now: dt.datetime, expected_date: str | None = None) -> dict[str, Any]:
    if (raw.get("ok") is not True or raw.get("reconciled") is not True
            or raw.get("provider") != "OpenRouter" or raw.get("currency") != "USD"):
        raise ValueError("Unverified provider evidence")
    period = raw["period"]
    day = _date(period["date"])
    today = now.astimezone(ZONE).date()
    if day >= today or (expected_date and day.isoformat() != expected_date) or period.get("timezone") != "Europe/London":
        raise ValueError("Wrong reporting period")
    start = dt.datetime.combine(day, dt.time(), ZONE).astimezone(dt.timezone.utc)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), ZONE).astimezone(dt.timezone.utc)
    if _timestamp(period["start"]) != start or _timestamp(period["end"]) != end:
        raise ValueError("Incomplete reporting window")
    collected = _timestamp(raw["collectedAt"])
    if collected < end or collected > now + dt.timedelta(minutes=5):
        raise ValueError("Invalid collection time")
    usd = _number(raw["recordedSpendUsd"])
    totals = raw["totals"]
    requests = _number(totals["request_count"])
    if not math.isclose(usd, _number(totals["total_usage"]), rel_tol=1e-8, abs_tol=1e-8):
        raise ValueError("Unreconciled charge total")
    rows = raw["breakdown"]
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Invalid model coverage")
    models: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("model")
        name = name[:200] if isinstance(name, str) and name else "Unattributed model"
        model = models.setdefault(name, {"model": name, "usd": 0, "requests": 0})
        model["usd"] += _number(row["total_usage"])
        model["requests"] += _number(row["request_count"])
    if (not math.isclose(sum(row["usd"] for row in models.values()), usd, rel_tol=1e-8, abs_tol=1e-8)
            or sum(row["requests"] for row in models.values()) != requests):
        raise ValueError("Unreconciled model breakdown")
    fx = raw.get("gbp")
    gbp = None
    if isinstance(fx, dict):
        estimate, rate = _number(fx["estimate"]), _number(fx["rate"])
        _date(fx["rateDate"])
        if rate <= 0 or not isinstance(fx.get("source"), str) or not fx["source"].strip() or not math.isclose(estimate, usd * rate, rel_tol=1e-8, abs_tol=1e-8):
            raise ValueError("Unverified GBP conversion")
        gbp = {"estimate": estimate, "rate": rate, "rateDate": fx["rateDate"], "source": fx["source"][:300]}
    return {"date": day.isoformat(), "timezone": "Europe/London", "collected_at": raw["collectedAt"],
            "usd": usd, "gbp": gbp, "requests": requests,
            "tokens": {metric: _number(totals[metric]) for metric in TOKEN_METRICS if metric in totals},
            "models": sorted(models.values(), key=lambda row: row["usd"], reverse=True),
            "provisional": True, "fresh": day == today - dt.timedelta(days=1) and collected.astimezone(ZONE).date() == today,
            "source": "OpenRouter Analytics API", "scope": "Whole OpenRouter account", "reconciled": True}


def read_spend_report(storage: R2Storage, bucket: str, selected_date: str | None = None,
                      now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    expected = (now.astimezone(ZONE).date() - dt.timedelta(days=1)).isoformat()
    if selected_date and _date(selected_date) >= now.astimezone(ZONE).date():
        raise ValueError("Choose a complete London calendar day")

    def read(key_date: str | None) -> dict[str, Any]:
        if not bucket:
            raise RuntimeError("Private reporting storage is not configured")
        obj = storage.read_object(f"{PREFIX}/{key_date or 'latest'}.json", max_bytes=2_000_000,
                                  bucket=bucket, read_only=True)
        return summarise_report(json.loads(obj.content), now, key_date)

    try:
        report = read(selected_date)
    except (FileNotFoundError, RuntimeError, ValueError, TypeError, KeyError, AttributeError):
        return {"ok": True, "available": False, "expected_date": expected,
                "reason": "Verified OpenRouter spend evidence is unavailable. Check collection and private storage access.",
                "report": None, "comparisons": []}
    comparisons = []
    for label, offset in (("Previous day", 1), ("Same weekday last week", 7)):
        date = (_date(report["date"]) - dt.timedelta(days=offset)).isoformat()
        try:
            previous = read(date)
            change = report["usd"] - previous["usd"]
            comparisons.append({"label": label, "date": date, "available": True, "usd": previous["usd"],
                                "change_usd": change, "change_percent": change / previous["usd"] * 100 if previous["usd"] else None})
        except (FileNotFoundError, RuntimeError, ValueError, TypeError, KeyError, AttributeError):
            comparisons.append({"label": label, "date": date, "available": False})
    return {"ok": True, "available": True, "expected_date": expected, "report": report,
            "comparisons": comparisons, "task_attribution_available": False, "quality_evidence_available": False}
