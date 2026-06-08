"""Thin async client for the Parallel Web Systems REST API.

Raw httpx on purpose — keeps the request/response shapes explicit (no SDK
version guesswork) and makes the wire format obvious. Two capabilities:

  - Task API  (Stage 1 deep dive): POST /v1/tasks/runs  ->  GET .../result
  - Search API (Stage 2 comparison): POST /v1/search
"""
from __future__ import annotations

import httpx

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
        self._base = settings.parallel_base_url.rstrip("/")

    def _headers(self, *, beta: str | None = None) -> dict[str, str]:
        headers = {
            "x-api-key": self._settings.parallel_api_key,
            "Content-Type": "application/json",
        }
        if beta:
            headers["parallel-beta"] = beta
        return headers

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
        payload = {
            "input": task_input,
            "processor": self._settings.parallel_task_processor,
            "task_spec": {"output_schema": PROFILE_SCHEMA},
        }

        # Longer timeouts: the Task API runs a real research job.
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0)) as client:
            run = await self._post(client, "/v1/tasks/runs", payload, beta=None)
            run_id = run.get("run_id") or run.get("id")
            if not run_id:
                raise ParallelError(f"Task run did not return a run_id: {run}")

            # /result blocks until the run finishes — no polling loop needed.
            result = await self._get(client, f"/v1/tasks/runs/{run_id}/result")

        output = result.get("output", result)
        content = output.get("content", output)
        # content may be a JSON string or already-parsed object depending on processor.
        if isinstance(content, str):
            import json

            try:
                content = json.loads(content)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ParallelError(f"Could not parse Task output as JSON: {exc}") from exc

        return {"profile": content, "basis": output.get("basis")}

    # ---------------------------------------------------------------- Search API
    async def search(self, objective: str, search_queries: list[str]) -> list[dict]:
        """Run one Parallel search and return its results[] list."""
        payload = {
            "objective": objective,
            "search_queries": search_queries,
            "max_results": self._settings.results_per_query,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            data = await self._post(
                client, "/v1/search", payload, beta=self._settings.parallel_search_beta
            )
        return data.get("results", []) or []

    # --------------------------------------------------------------- HTTP helpers
    async def _post(
        self, client: httpx.AsyncClient, path: str, json: dict, *, beta: str | None
    ) -> dict:
        resp = await client.post(self._base + path, json=json, headers=self._headers(beta=beta))
        return self._handle(resp)

    async def _get(self, client: httpx.AsyncClient, path: str) -> dict:
        resp = await client.get(self._base + path, headers=self._headers())
        return self._handle(resp)

    @staticmethod
    def _handle(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            raise ParallelError(
                f"Parallel API {resp.request.method} {resp.request.url.path} "
                f"failed [{resp.status_code}]: {resp.text[:500]}"
            )
        return resp.json()
