"""Drift agent - first node of the pipeline.

Rewritten for the platform port. The original reads CV4CDD-4D outputs from
disk (`prediction_results.csv` + `window_info.json` + `<LogName>.xes`); here
we consume the cv4cdd module's `detections` cache and the platform's
normalised event log instead.

Outputs the same three keys as the original:
  * `drift_info` (DriftInfo) - process_name, changepoints, drift_type,
    confidence, start/end timestamps (ISO-8601, tz-naive UTC).
  * `drift_keywords` - Porter-stemmed activity-label keywords for retrieval.
  * `drift_phrase` - LLM-synthesised one-sentence summary of the change.
"""

from __future__ import annotations

import logging
import re

from langchain_core.language_models import BaseChatModel
from nltk.stem import PorterStemmer

from ..state.schema import DriftInfo, GraphState


# Built-in mapping from the cv4cdd panel's drift_type strings to the
# canonical SUDDEN_DRIFT / GRADUAL_DRIFT / etc. labels the explanation prompts
# branch on. cv4cdd emits these in lowercase, our explanation prompts match
# substring 'sudden' | 'gradual' | 'incremental' | 'recurring'.
def _canonical_drift_type(cv4cdd_type: str) -> str:
    t = (cv4cdd_type or "").lower()
    if "sudden" in t:
        return "SUDDEN_DRIFT"
    if "gradual" in t:
        return "GRADUAL_DRIFT"
    if "incremental" in t:
        return "INCREMENTAL_DRIFT"
    if "recurring" in t:
        return "RECURRING_DRIFT"
    return cv4cdd_type.upper() + "_DRIFT"


_STEMMER = PorterStemmer()


def _stem_keywords(words: list[str]) -> list[str]:
    out: set[str] = set()
    for word in words:
        if not word or not word.isalpha() or len(word) <= 3:
            continue
        out.add(_STEMMER.stem(word.lower()))
    return sorted(out)


def _activity_keywords(activities: list[str]) -> list[str]:
    words: list[str] = []
    for activity in activities:
        if not activity:
            continue
        for tok in re.split(r"[\s_\-/,()]+", str(activity)):
            words.append(tok)
    return _stem_keywords(words)


def _format_trace(df, activity: str, max_events: int = 8) -> str:
    """Pick a representative case containing `activity` and format its events.

    Used to give the LLM enough local context to synthesise the drift phrase.
    """
    matches = df[df["activity"].astype(str) == str(activity)]
    if matches.empty:
        return f"- (no trace found containing '{activity}')"
    case_id = matches.iloc[0]["case_id"]
    case = df[df["case_id"] == case_id].head(max_events)
    return "\n".join(f"- [{row.timestamp}] {row.activity}" for row in case.itertuples())


def make_drift_agent(*, llm: BaseChatModel):
    """Return a stateful drift_agent callable bound to a chat model.

    LLM responses are cached on the returned closure so the same drift inside
    one pipeline invocation doesn't pay the API cost twice.
    """
    phrase_cache: dict[str, str] = {}

    def run_drift_agent(state: GraphState) -> dict:
        logging.info("--- Running Drift Agent ---")
        selection = state.get("selected_drift") or {}
        drift_record = selection.get("drift_record")
        events_preview = selection.get("events_preview")
        process_name = selection.get("process_name", "process")

        if not drift_record:
            return {"error_message": "No drift selected."}

        cv4cdd_type = drift_record.get("type", "")
        drift_type = _canonical_drift_type(cv4cdd_type)
        confidence = float(drift_record.get("confidence", 0.0))
        start_ts = drift_record.get("start_timestamp")
        end_ts = drift_record.get("end_timestamp")
        start_activity = drift_record.get("start_activity") or ""
        end_activity = drift_record.get("end_activity") or ""

        changepoint_pair = (start_activity, end_activity)
        keywords = _activity_keywords([start_activity, end_activity])

        # Synthesise the drift phrase using the LLM.
        cache_key = f"{process_name}|{start_activity}|{end_activity}|{drift_type}"
        summary = phrase_cache.get(cache_key)
        if summary is None:
            start_trace = (
                _format_trace(events_preview, start_activity)
                if events_preview is not None and start_activity
                else f"- (start activity: {start_activity})"
            )
            end_trace = (
                _format_trace(events_preview, end_activity)
                if events_preview is not None and end_activity
                else f"- (end activity: {end_activity})"
            )

            prompt = (
                "You are a concise business process analyst. Summarize the "
                "change between two process snapshots from a single drift "
                "window in one descriptive sentence.\n\n"
                f"Process: {process_name}\n"
                f"Drift type: {drift_type}\n\n"
                f"Snapshot near drift start:\n{start_trace}\n\n"
                f"Snapshot near drift end:\n{end_trace}\n\n"
                "One-sentence summary of the change:"
            )
            try:
                resp = llm.invoke(prompt)
                summary = (resp.content or "").strip()
            except Exception as e:
                logging.warning("LLM drift-phrase synthesis failed: %s", e)
                summary = (
                    " ".join(re.findall(r"[A-Za-z]+", " ".join(changepoint_pair))).lower()
                    or process_name
                )
            phrase_cache[cache_key] = summary

        drift_phrase = f"{process_name}: {summary}"

        drift_info: DriftInfo = {
            "process_name": process_name,
            "changepoints": changepoint_pair,
            "drift_type": drift_type,
            "confidence": confidence,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
        }
        if "gold_doc" in selection:
            drift_info["gold_doc"] = selection["gold_doc"]

        logging.info(
            "Drift agent: %s [%s -> %s] (%.2f)",
            drift_type,
            start_activity,
            end_activity,
            confidence,
        )

        return {
            "drift_info": drift_info,
            "drift_keywords": keywords,
            "drift_phrase": drift_phrase,
        }

    return run_drift_agent


def build_drift_record(
    *,
    cv4cdd_drift: dict,
    df,
    n_windows: int,
) -> dict:
    """Adapt one cv4cdd drift dict into the structure drift_agent expects.

    The cv4cdd module emits drifts shaped like:
        {
          "type": "sudden" | "gradual" | "incremental" | "recurring",
          "start_window": int, "end_window": int,
          "start_timestamp": iso8601, "end_timestamp": iso8601,
          "confidence": float, "bbox": [x1, y1, x2, y2],
        }

    We map the window indexes to the most common activity in that window of
    the platform's `events.parquet`, so downstream agents can reason about
    "activity X was replaced by activity Y" rather than "window 47 became
    window 92".
    """
    start_window = int(cv4cdd_drift.get("start_window", 0))
    end_window = int(cv4cdd_drift.get("end_window", n_windows - 1))

    record = dict(cv4cdd_drift)
    record["start_activity"] = _dominant_activity_in_window(df, n_windows, start_window)
    record["end_activity"] = _dominant_activity_in_window(df, n_windows, end_window)
    return record


def _dominant_activity_in_window(df, n_windows: int, window_idx: int) -> str:
    if df is None or df.empty or n_windows <= 0:
        return ""
    chunk_size = max(1, len(df) // n_windows)
    start = max(0, window_idx * chunk_size)
    end = min(len(df), start + chunk_size)
    chunk = df.iloc[start:end]
    if chunk.empty:
        return ""
    return str(chunk["activity"].value_counts().idxmax())
