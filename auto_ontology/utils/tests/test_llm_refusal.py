# SPDX-License-Identifier: Apache-2.0
"""A model endpoint that answers 401/402/403 is reported as such, not as "the LLM failed".

Uses the real openai exception types, built from real httpx responses, and a real
local HTTP endpoint answering 402 for the end-to-end case."""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import openai
import pytest

from auto_ontology.utils import llm_invoke
from auto_ontology.utils.llm_invoke import ModelEndpointRefused, last_refusal_since, refusal_from


def _status_error(status: int, body: dict) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://endpoint.invalid/v1/chat/completions")
    response = httpx.Response(status, json=body, request=request)
    return openai.APIStatusError("boom", response=response, body=body)


@pytest.mark.parametrize("status", [401, 402, 403])
def test_refusal_statuses_are_recognised(status: int) -> None:
    exc = _status_error(status, {"error": {"code": "in_flight_budget_exhausted", "message": "out of credits"}})
    refusal = refusal_from(exc)
    assert isinstance(refusal, ModelEndpointRefused)
    assert str(refusal) == f"the model endpoint refused: {status} in_flight_budget_exhausted out of credits"


def test_other_statuses_and_wrapped_causes() -> None:
    assert refusal_from(_status_error(500, {"error": {"message": "x"}})) is None
    assert refusal_from(ValueError("nope")) is None
    try:
        try:
            raise _status_error(402, {"error": {"message": "credits"}})
        except Exception as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert refusal_from(outer).status == 402


def test_a_real_endpoint_answering_402_is_noted() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = b'{"error": {"code": "in_flight_budget_exhausted", "message": "Insufficient credits"}}'
            self.send_response(402)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: D401
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="m", api_key="k", base_url=f"http://127.0.0.1:{server.server_port}/v1", max_retries=0)
        started = time.monotonic()
        with pytest.raises(Exception) as caught:
            llm_invoke._safe_invoke_text(llm, "hello")
        assert last_refusal_since(started) is not None
        assert str(last_refusal_since(started)) == (
            "the model endpoint refused: 402 in_flight_budget_exhausted Insufficient credits"
        )
        assert refusal_from(caught.value) is not None
    finally:
        server.shutdown()
