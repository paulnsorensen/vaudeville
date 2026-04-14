"""Request handlers for the Vaudeville daemon."""

from __future__ import annotations

import json
import logging
import time

from ..core.protocol import ClassifyResult, compute_confidence, parse_verdict
from .condense import condense_text
from .event_log import ClassificationEvent, EventLogger
from .inference import (
    CachedBackend,
    CachedLogprobBackend,
    InferenceBackend,
    LogprobBackend,
)

logger = logging.getLogger(__name__)


def _run_inference(
    backend: InferenceBackend,
    prompt: str,
    prefix_len: int = 0,
) -> ClassifyResult:
    if prefix_len > 0 and isinstance(backend, CachedLogprobBackend):
        return backend.classify_cached_with_logprobs(prompt, prefix_len)
    elif prefix_len > 0 and isinstance(backend, CachedBackend):
        text = backend.classify_cached(prompt, prefix_len)
        return ClassifyResult(text=text)
    elif prefix_len > 0:
        logger.debug(
            "prefix_len=%d but backend lacks cached methods — uncached", prefix_len
        )
    if isinstance(backend, LogprobBackend):
        return backend.classify_with_logprobs(prompt, max_tokens=50)
    text = backend.classify(prompt, max_tokens=50)
    return ClassifyResult(text=text)


def _handle_condense(
    request: dict[str, object],
    backend: InferenceBackend,
) -> bytes:
    text = str(request.get("text", ""))
    t0 = time.monotonic()
    condensed = condense_text(text, backend)
    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "CONDENSE latency_ms=%.0f in_chars=%d out_chars=%d",
        elapsed_ms,
        len(text),
        len(condensed),
    )
    return json.dumps({"text": condensed}).encode() + b"\n"


def _handle_classify(
    request: dict[str, object],
    backend: InferenceBackend,
    event_logger: EventLogger | None = None,
) -> bytes:
    prompt = str(request.get("prompt", ""))
    rule = str(request.get("rule", ""))
    tier = str(request.get("tier", "enforce"))
    raw_prefix = request.get("prefix_len", 0)
    prefix_len = int(float(str(raw_prefix))) if raw_prefix else 0

    logger.debug("prompt=%d chars prefix_len=%d", len(prompt), prefix_len)
    t0 = time.monotonic()
    result = _run_inference(backend, prompt, prefix_len)
    elapsed_ms = (time.monotonic() - t0) * 1000
    response = parse_verdict(result.text)
    confidence = compute_confidence(result.logprobs, response.verdict)
    safe_reason = response.reason.replace("\n", " ").replace("\r", " ")[:100]
    logger.info(
        "CLASSIFY verdict=%s confidence=%.3f "
        " latency_ms=%.0f prompt_chars=%d reason=%s",
        response.verdict,
        confidence,
        elapsed_ms,
        len(prompt),
        safe_reason,
    )

    evt = ClassificationEvent(
        rule=rule,
        verdict=response.verdict,
        confidence=confidence,
        latency_ms=elapsed_ms,
        prompt_chars=len(prompt),
        reason=response.reason,
        input_snippet=prompt[:500],
        tier=tier,
    )
    if event_logger is not None:
        event_logger.log_event(evt)

    return _response(response.verdict, response.reason, confidence)


def handle_request(
    data: bytes,
    backend: InferenceBackend,
    event_logger: EventLogger | None = None,
) -> bytes:
    """Route a request by op field: 'classify' (default) or 'condense'."""
    try:
        request = json.loads(data.decode().strip())
        op = str(request.get("op", "classify"))
        if op == "condense":
            return _handle_condense(request, backend)
        return _handle_classify(request, backend, event_logger)
    except Exception as exc:
        logger.error("Request error: %s", exc)
        return _response("clean", "Inference error — fail open")


def _response(verdict: str, reason: str, confidence: float = 1.0) -> bytes:
    return (
        json.dumps(
            {
                "verdict": verdict,
                "reason": reason,
                "confidence": confidence,
            }
        ).encode()
        + b"\n"
    )
