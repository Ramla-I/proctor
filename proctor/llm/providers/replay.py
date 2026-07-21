"""Cassette record/replay providers — CI runs with no API keys.

A cassette is one JSON file per request, named by a hash of the
provider-agnostic request content. ``RecordingProvider`` wraps any live
provider and writes cassettes; ``ReplayProvider`` serves them back.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from proctor.llm.providers.base import Provider, register_provider
from proctor.llm.types import FinishReason, ProviderError, Request, Response, Usage


def request_hash(request: Request, model: str) -> str:
    canonical = json.dumps(
        {
            "model": request.model or model,
            "system": request.system,
            "messages": [[m.role, m.content] for m in request.messages],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _response_to_dict(response: Response) -> dict[str, Any]:
    return {
        "text": response.text,
        "finish_reason": response.finish_reason,
        "provider": response.provider,
        "model": response.model,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "cached_input_tokens": response.usage.cached_input_tokens,
            "output_tokens": response.usage.output_tokens,
            "reasoning_tokens": response.usage.reasoning_tokens,
        },
    }


def _response_from_dict(data: dict[str, Any]) -> Response:
    usage = data.get("usage", {})
    return Response(
        text=str(data.get("text", "")),
        finish_reason=cast(FinishReason, data.get("finish_reason", "stop")),
        provider=str(data.get("provider", "replay")),
        model=str(data.get("model", "")),
        latency_s=0.0,
        usage=Usage(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            reasoning_tokens=usage.get("reasoning_tokens"),
        ),
        raw={"cassette": True},
    )


@register_provider("replay")
class ReplayProvider:
    name = "replay"

    def __init__(self, settings: dict[str, Any]) -> None:
        self._dir = Path(str(settings.get("cassette_dir", "tests/cassettes")))
        self._model = str(settings.get("model", ""))

    def complete(self, request: Request) -> Response:
        key = request_hash(request, self._model)
        file = self._dir / f"{key}.json"
        if not file.is_file():
            raise ProviderError(
                f"no cassette {file} for this request; record it first "
                f"(RecordingProvider) or add it by hand"
            )
        return _response_from_dict(json.loads(file.read_text(encoding="utf-8")))


class RecordingProvider:
    """Wraps a live provider and writes a cassette per response."""

    name = "recording"

    def __init__(self, inner: Provider, cassette_dir: Path, model: str = "") -> None:
        self._inner = inner
        self._dir = cassette_dir
        self._model = model

    def complete(self, request: Request) -> Response:
        response = self._inner.complete(request)
        self._dir.mkdir(parents=True, exist_ok=True)
        key = request_hash(request, self._model)
        (self._dir / f"{key}.json").write_text(
            json.dumps(_response_to_dict(response), indent=2) + "\n",
            encoding="utf-8",
        )
        return response
