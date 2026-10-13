"""Official SIWC Responses transport with inline images and no agent tools."""

from __future__ import annotations

import base64
import json

import httpx

from ..errors import AIError, from_text
from . import chatgpt_auth

NAME = "chatgpt"
LABEL = "ChatGPT"
MODEL_SUGGESTIONS = []
_ENDPOINT = "https://api.openai.com/v1/responses"
_FAILURE = "ChatGPT could not complete the request; check login and model"


def _error(text: str) -> AIError:
    # Provider codes use underscores; retain their original model-error spelling.
    return from_text(text + " " + text.replace("_", " "), _FAILURE)


def _event_error(event: dict) -> AIError:
    response = event.get("response")
    response = response if isinstance(response, dict) else {}
    diagnostics = [
        event.get("error"),
        response.get("error"),
        response.get("incomplete_details"),
    ]
    if event.get("type") == "error":
        diagnostics.append(
            {key: event.get(key) for key in ("code", "message", "param")}
        )
    return _error(json.dumps(diagnostics))


def installed() -> bool:
    return True


def describe() -> dict:
    return {"provider": "OpenAI", **chatgpt_auth.status()}


def _final(response):
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise AIError("ChatGPT returned no completed response")
    output = response.get("output")
    if not isinstance(output, list):
        raise AIError("ChatGPT returned an invalid response")
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise AIError("ChatGPT returned an invalid response")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise AIError("ChatGPT returned an unexpected tool response")
        content = item.get("content")
        if not isinstance(content, list):
            raise AIError("ChatGPT returned an invalid response")
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise AIError("ChatGPT returned an unsupported response")
            text = part.get("text")
            if not isinstance(text, str):
                raise AIError("ChatGPT returned an invalid response")
            texts.append(text)
    text = "".join(texts)
    if not text.strip():
        raise AIError("ChatGPT returned no final text")
    return text


def _decode(data):
    try:
        event = json.loads(data)
    except ValueError as exc:
        raise AIError("ChatGPT returned an invalid stream") from exc
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise AIError("ChatGPT returned an invalid stream")
    return event


async def _events(prompt, model, system, images):
    token = await chatgpt_auth.access_token()
    content = [{"type": "input_text", "text": prompt}]
    content.extend(
        {
            "type": "input_image",
            "image_url": "data:image/png;base64," + base64.b64encode(png).decode(),
        }
        for png in images
    )
    payload = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "tools": [],
        "tool_choice": "none",
        "store": False,
        "stream": True,
    }
    if system:
        payload["instructions"] = system
    try:
        async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
            async with client.stream(
                "POST",
                _ENDPOINT,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    raise _error(
                        str(response.status_code) + body.decode("utf-8", "replace"),
                    )
                data = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        message = "\n".join(data)
                        if message == "[DONE]":
                            return
                        yield _decode(message)
                        data = []
                if data:
                    message = "\n".join(data)
                    if message != "[DONE]":
                        yield _decode(message)
    except httpx.HTTPError as exc:
        raise AIError("Could not connect to ChatGPT; try again later") from exc


async def _text(prompt, model, system, images):
    final, emitted = None, []
    completed_items = {}
    events = _events(prompt, model, system, images)
    try:
        async for event in events:
            kind = event["type"]
            if final is not None:
                raise AIError("ChatGPT returned data after completion")
            if kind in ("error", "response.failed", "response.incomplete"):
                raise _event_error(event)
            if kind in ("response.output_item.added", "response.output_item.done"):
                item = event.get("item", {})
                if not isinstance(item, dict) or item.get("type") not in (
                    "message",
                    "reasoning",
                ):
                    raise AIError("ChatGPT returned an unexpected tool response")
            if kind == "response.output_item.done":
                index = event.get("output_index")
                if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                    raise AIError("ChatGPT returned an invalid output index")
                if index in completed_items or (
                    item.get("type") == "message" and item.get("status") != "completed"
                ):
                    raise AIError("ChatGPT returned an invalid completed item")
                completed_items[index] = item
            if kind == "response.output_text.delta":
                delta = event.get("delta")
                if not isinstance(delta, str):
                    raise AIError("ChatGPT returned an invalid text delta")
                emitted.append(delta)
            elif kind == "response.completed":
                response = event.get("response")
                # Plan-backed streams may omit output from the final envelope. Use
                # completed item snapshots, never unconfirmed deltas, in that case.
                if isinstance(response, dict) and response.get("output") == []:
                    indices = sorted(completed_items)
                    if indices != list(range(len(indices))):
                        raise AIError("ChatGPT returned an incomplete output sequence")
                    response = {
                        **response,
                        "output": [completed_items[i] for i in indices],
                    }
                final = _final(response)
    finally:
        await events.aclose()
    if final is None:
        raise AIError("ChatGPT stream ended without a completed response")
    if emitted and "".join(emitted) != final:
        raise AIError("ChatGPT stream did not match its final response")
    # Consumers paint and cache chunks immediately; release only validated output.
    if emitted:
        for delta in emitted:
            yield delta
    else:
        yield final


async def run(prompt, *, model, system=None, images=()):
    return "".join([part async for part in _text(prompt, model, system, images)])


async def stream(prompt, *, model, system=None):
    events = _text(prompt, model, system, ())
    try:
        async for part in events:
            yield part
    finally:
        await events.aclose()
