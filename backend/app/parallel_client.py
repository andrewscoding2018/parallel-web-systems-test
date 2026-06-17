"""Thin async adapter around the Parallel Python SDK.

The FastAPI app stays async while the official SDK is sync, so network calls run
in worker threads. The public methods normalize SDK response models into the
plain dict/list shapes used by the scoring layer.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from parallel import APIStatusError, Parallel

from .config import Settings


class ParallelError(RuntimeError):
    """Raised when the Parallel API returns a non-2xx response or is misconfigured."""


# JSON schema for the Stage 1 deep dive. Drives query + competitor generation.
PROFILE_SCHEMA: dict = {
    "type": "json",
    "json_schema": {
        "type": "object",
        "properties": {
            "company_name": {
                "type": "string",
                "description": (
                    "The company's brand name as third parties write it, e.g. "
                    "'Torrey Hills Technologies' — not the bare domain. Used to detect "
                    "mentions on third-party pages."
                ),
            },
            "one_liner": {
                "type": "string",
                "description": "What the company makes, plainly.",
            },
            "product_lines": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Distinct product categories, e.g. 'tungsten-copper heat sinks', "
                    "'solar cell firing furnaces'."
                ),
            },
            "buyer_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "6-10 natural-language queries a buyer or AI agent would ask when "
                    "shopping this category, e.g. 'best belt furnace for solar cell firing', "
                    "'tungsten copper heat sink supplier'."
                ),
            },
            "competitors": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Named competing manufacturers in these categories, with domains if "
                    "known, e.g. 'Acme Furnaces (acme.com)'."
                ),
            },
        },
        "required": ["product_lines", "buyer_queries", "competitors"],
    },
}


class ParallelClient:
    def __init__(self, settings: Settings):
        if not settings.parallel_api_key:
            raise ParallelError(
                "PARALLEL_API_KEY is not set. Add it to backend/.env or the environment."
            )
        self._settings = settings
        self._client = Parallel(
            api_key=settings.parallel_api_key,
            base_url=settings.parallel_base_url,
        )

    # ------------------------------------------------------------------ Task API
    async def deep_dive(self, domain: str) -> dict:
        """Run the Stage 1 Task and block on its result.

        Returns the parsed output content dict plus optional basis, shaped as
        {"profile": {...}, "basis": [...]}.
        """
        task_input = (
            f"Analyze the company at the domain '{domain}'. Identify what it makes, its "
            "distinct product lines, the natural-language queries a buyer or AI shopping "
            "agent would ask when looking for these products, and its named competing "
            "manufacturers (include domains where known)."
        )
        try:
            task_run = await asyncio.to_thread(
                self._client.task_run.create,
                input=task_input,
                processor=self._settings.parallel_task_processor,
                task_spec={"output_schema": PROFILE_SCHEMA},
                timeout=600.0,
            )
            result = await asyncio.to_thread(
                self._client.task_run.result,
                task_run.run_id,
                api_timeout=600,
                timeout=600.0,
            )
        except APIStatusError as exc:
            raise ParallelError(self._format_api_error(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            raise ParallelError(f"Parallel Task API failed: {exc}") from exc

        output = result.output
        content = getattr(output, "content", output)
        # content may be a JSON string or already-parsed object depending on processor.
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ParallelError(f"Could not parse Task output as JSON: {exc}") from exc

        return {
            "profile": content,
            "basis": self._dump_model(getattr(output, "basis", None)),
        }

    # ---------------------------------------------------------------- Search API
    async def search(self, objective: str, search_queries: list[str]) -> list[dict]:
        """Run one Parallel search and return its results[] list."""
        try:
            data = await asyncio.to_thread(
                self._client.search,
                objective=objective,
                search_queries=search_queries,
                advanced_settings={"max_results": self._settings.results_per_query},
                timeout=60.0,
            )
        except APIStatusError as exc:
            raise ParallelError(self._format_api_error(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            raise ParallelError(f"Parallel Search API failed: {exc}") from exc

        return [self._dump_model(result) for result in data.results]

    @staticmethod
    def _dump_model(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [ParallelClient._dump_model(item) for item in value]
        if isinstance(value, dict):
            return {key: ParallelClient._dump_model(item) for key, item in value.items()}
        return value

    @staticmethod
    def _format_api_error(exc: APIStatusError) -> str:
        response_text = getattr(exc.response, "text", "")
        return (
            f"Parallel API failed [{exc.status_code}]: "
            f"{response_text[:500] or str(exc)}"
        )
