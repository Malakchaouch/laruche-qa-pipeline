"""
API channel — talk to the chatbot's HTTP endpoint instead of its web UI.

The endpoint streams: it answers `POST /api/chat` with `text/event-stream`,
emitting one Server-Sent Event per token —

    data: {"token": "Hello! ", "conversation_id": "e1c038cf-..."}
    data: {"token": "I ",      "conversation_id": "e1c038cf-..."}

so a plain "send request, read body" client hangs until it times out. We read
the stream line by line and concatenate the tokens back into one reply.

Uses urllib from the standard library on purpose: no new dependency to install,
nothing to go wrong in a venv.

Same result contract as the browser executor (verdict / total_ms / steps /
evidence_dir), so the Judge, the Reporter and the regression comparator work on
API results without any change.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "/api/chat"
DEFAULT_TIMEOUT_S = 60.0


class ApiError(RuntimeError):
    """Raised when the endpoint cannot be reached or answers unusably."""


def _parse_sse_line(raw: bytes) -> dict[str, Any] | None:
    """Turn one `data: {...}` line into a dict, or None if it isn't one.

    Ignores comments, blank keep-alive lines and the `[DONE]` sentinel some
    SSE servers send. A malformed payload is skipped rather than fatal: losing
    one token is better than losing the whole reply.
    """
    line = raw.decode("utf-8", errors="replace").strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def send_message(
    base_url: str,
    message: str,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    conversation_id: str = "",
    mode: str = "instant",
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """POST one message and reassemble the streamed reply.

    Returns {reply, conversation_id, status, elapsed_ms, events}.
    Raises ApiError when the endpoint is unreachable or returns an error status.
    """
    url = base_url.rstrip("/") + endpoint
    body = json.dumps({
        "message": message,
        "display_message": "",
        "conversation_id": conversation_id,
        "mode": mode,
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
                        "Content-Type": "application/json; charset=utf-8",
            # Ask for the stream explicitly; the server may fall back to plain
            # JSON for clients that do not, and we handle both below.
            "Accept": "text/event-stream, application/json",
        },
    )

    tokens: list[str] = []
    convo = conversation_id
    events = 0
    started = time.perf_counter()

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")

            if "text/event-stream" in content_type:
                for raw in response:                    # line-by-line, as it arrives
                    obj = _parse_sse_line(raw)
                    if obj is None:
                        continue
                    events += 1
                    token = obj.get("token")
                    if isinstance(token, str):
                        tokens.append(token)
                    convo = obj.get("conversation_id") or convo
            else:
                # Non-streaming fallback: one JSON object with the whole reply.
                data = json.loads(response.read().decode("utf-8", errors="replace"))
                for key in ("response", "reply", "message", "answer", "text"):
                    if isinstance(data.get(key), str):
                        tokens.append(data[key])
                        break
                convo = data.get("conversation_id") or convo
                events = 1

    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code} from {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"cannot reach {url}: {e.reason}") from e
    except TimeoutError as e:
        raise ApiError(f"timeout after {timeout_s:.0f}s waiting on {url}") from e

    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "reply": "".join(tokens).strip(),
        "conversation_id": convo,
        "status": status,
        "elapsed_ms": round(elapsed_ms, 1),
        "events": events,
    }


def extract_message(scenario: dict[str, Any]) -> str:
    """Pull the user's question out of a scenario.

    The Generator turns a corpus intent into a DSL scenario for the browser, and
    in doing so the flat `input` field disappears — the question survives only as
    the text of the `type` step:

        {"do": "type", "target": "chat_input", "text": "What is my AUM?"}

    So the API channel reads that step. `input` is still checked first for raw
    corpus entries, which is what unit tests and any non-DSL caller pass.

    Returns "" only when the scenario genuinely has no question: the empty-input
    limit cases deliberately omit the `type` step, and sending an empty message
    is exactly what they are testing.
    """
    direct = scenario.get("input")
    if isinstance(direct, str) and direct.strip():
        return direct

    for step in scenario.get("steps", []) or []:
        if isinstance(step, dict) and step.get("do") == "type":
            text = step.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return ""


def run_api_scenario(
    scenario: dict[str, Any],
    base_url: str,
    evidence_dir: Path,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run one scenario over HTTP and return a browser-executor-shaped result.

    Mirrors what the interpreter produces for the web channel, so downstream
    nodes stay channel-agnostic. Evidence is the request and the reassembled
    reply, saved as JSON — the API equivalent of a screenshot.
    """
    sid = scenario.get("id", "?")
    message = extract_message(scenario)
    started = time.perf_counter()

    evidence_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []
    reply = ""
    verdict = "PASS"
    failure_kind = None
    reason = ""

    try:
        outcome = send_message(
            base_url, message, endpoint=endpoint, timeout_s=timeout_s
        )
        reply = outcome["reply"]
        steps.append({
            "verb": "send_message",
            "ok": True,
            "ms": outcome["elapsed_ms"],
            "detail": f"{outcome['events']} event(s), HTTP {outcome['status']}",
        })

        if not reply:
            # A 200 with nothing in it is a failure of the chatbot, not of the
            # transport — the same judgement the browser executor makes when the
            # answer element stays empty.
            verdict, failure_kind = "FAIL", "empty_reply"
            reason = "endpoint answered but the reply was empty"

    except ApiError as e:
        verdict, failure_kind = "FAIL", "transport"
        reason = str(e)
        steps.append({"verb": "send_message", "ok": False, "ms": 0.0, "detail": reason})

    total_ms = round((time.perf_counter() - started) * 1000, 1)

    (evidence_dir / "exchange.json").write_text(
        json.dumps(
            {"scenario_id": sid, "channel": "api", "endpoint": endpoint,
             "input": message, "reply": reply, "verdict": verdict,
             "failure_kind": failure_kind, "reason": reason,
             "total_ms": total_ms},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "scenario_id": sid,
        "channel": "api",
        "verdict": verdict,
        "failure_kind": failure_kind,
        "reason": reason,
        "answer": reply,
        "reply_text": reply,
        "total_ms": total_ms,
        "steps": steps,
        "evidence_dir": str(evidence_dir),
    }