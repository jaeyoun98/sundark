import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.backends import chatgpt
from app.errors import AIError, ModelUnavailableError, RateLimitError


def completed(text="가"):
    return {
        "type": "response.completed",
        "response": {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        },
    }


class ChatGPTBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.events = [completed()]
        self.code = 200
        self.body = ""
        real_client = httpx.AsyncClient

        def handler(request):
            self.requests.append(request)
            body = self.body or "".join(
                "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n"
                for e in self.events
            )
            return httpx.Response(self.code, text=body)

        client = patch(
            "app.backends.chatgpt.httpx.AsyncClient",
            side_effect=lambda **kw: real_client(
                **kw, transport=httpx.MockTransport(handler)
            ),
        )
        auth = patch(
            "app.backends.chatgpt.chatgpt_auth.access_token",
            new=AsyncMock(return_value="mock"),
        )
        client.start()
        auth.start()
        self.addCleanup(client.stop)
        self.addCleanup(auth.stop)

    async def test_payload_disables_tools_and_sends_images_inline(self):
        self.assertEqual(
            await chatgpt.run("paper", model="m", system="s", images=[b"PNG"]),
            "가",
        )
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
        payload = json.loads(request.content)
        self.assertEqual(payload["tools"], [])
        self.assertEqual(payload["tool_choice"], "none")
        self.assertIs(payload["store"], False)
        self.assertIs(payload["stream"], True)
        self.assertEqual(payload["instructions"], "s")
        self.assertEqual(
            payload["input"][0]["content"],
            [
                {"type": "input_text", "text": "paper"},
                {"type": "input_image", "image_url": "data:image/png;base64,UE5H"},
            ],
        )

    async def test_stream_deltas_and_completion(self):
        self.events = [
            {"type": "response.output_text.delta", "delta": "가"},
            {"type": "response.output_text.delta", "delta": "나"},
            completed("가나"),
        ]
        self.assertEqual(
            [part async for part in chatgpt.stream("p", model="m")], ["가", "나"]
        )

    async def test_done_sentinel_requires_valid_completed_response(self):
        delta = {"type": "response.output_text.delta", "delta": "가"}
        for terminator in ("data: [DONE]\n\n", "data: [DONE]"):
            self.body = (
                "data: " + json.dumps(delta) + "\n\n"
                "data: " + json.dumps(completed()) + "\n\n" + terminator
            )
            self.assertEqual(
                [chunk async for chunk in chatgpt.stream("p", model="m")], ["가"]
            )
            for events in ([delta], [delta, {"type": "response.failed"}]):
                self.body = (
                    "".join("data: " + json.dumps(event) + "\n\n" for event in events)
                    + terminator
                )
                chunks = []
                with self.assertRaises(AIError):
                    async for chunk in chatgpt.stream("p", model="m"):
                        chunks.append(chunk)
                self.assertEqual(chunks, [])

    async def test_rejects_unfinished_failed_and_mismatched_streams(self):
        for events in (
            [{"type": "response.output_text.delta", "delta": "partial"}],
            [{"type": "response.failed"}],
            [{"type": "response.incomplete"}],
            [{"type": "error"}],
            [{"type": "response.output_text.delta", "delta": "x"}, completed("y")],
            [completed("")],
            [{"type": "response.output_item.added", "item": {"type": "function_call"}}],
        ):
            self.events = events
            with self.subTest(events=events), self.assertRaises(AIError):
                await chatgpt.run("p", model="m")

    async def test_provider_errors_are_classified_without_disclosing_body(self):
        for code, body, error in (
            (429, "sensitive provider quota", RateLimitError),
            (400, "sensitive model_not_found", ModelUnavailableError),
            (401, "sensitive credential", AIError),
        ):
            self.code, self.body = code, body
            with self.assertRaises(error) as raised:
                await chatgpt.run("p", model="m")
            self.assertNotIn("sensitive", str(raised.exception))

    async def test_malformed_stream_and_tool_final_are_rejected(self):
        for body in ("data: invalid\n\n", "data: []\n\n"):
            self.body = body
            with self.assertRaises(AIError):
                await chatgpt.run("p", model="m")
        self.body = ""
        event = completed()
        event["response"]["output"] = [{"type": "function_call"}]
        self.events = [event]
        with self.assertRaises(AIError):
            await chatgpt.run("p", model="m")

    async def test_midstream_subscription_limit_stops_with_rate_limit_error(self):
        for kind in ("error", "response.failed"):
            error = {
                "code": "subscription_sharing_usage_limit_exceeded",
                "message": "sensitive provider detail",
            }
            failure = {"type": kind, "error": error}
            if kind == "response.failed":
                failure = {"type": kind, "response": {"error": error}}
            self.events = [
                {"type": "response.output_text.delta", "delta": "partial"},
                failure,
            ]
            chunks = []
            with self.assertRaises(RateLimitError) as raised:
                async for chunk in chatgpt.stream("p", model="m"):
                    chunks.append(chunk)
            self.assertEqual(chunks, [])
            self.assertNotIn("sensitive", str(raised.exception))

    async def test_invalid_translation_batch_never_releases_numbered_lines(self):
        text = "0: First translation\n1: Second translation\n"
        tool = completed(text)
        tool["response"]["output"] = [{"type": "function_call"}]
        for tail in (
            [],
            [{"type": "response.failed"}],
            [{"type": "response.incomplete"}],
            [completed(text + "extra")],
            [tool],
        ):
            self.events = [{"type": "response.output_text.delta", "delta": text}] + tail
            chunks = []
            with self.subTest(tail=tail), self.assertRaises(AIError):
                async for chunk in chatgpt.stream("p", model="m"):
                    chunks.append(chunk)
            self.assertEqual(chunks, [])

    async def test_http_structured_subscription_limit_is_classified(self):
        self.code = 403
        self.body = json.dumps(
            {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}
        )
        with self.assertRaises(RateLimitError):
            await chatgpt.run("p", model="m")

    async def test_failed_output_and_sequence_are_not_error_diagnostics(self):
        event = completed("quota rate limit")
        event["type"] = "response.failed"
        event["sequence_number"] = 429
        event["response"]["status"] = "failed"
        self.events = [event]
        with self.assertRaises(AIError) as raised:
            await chatgpt.run("p", model="m")
        self.assertIs(type(raised.exception), AIError)

    async def test_top_level_error_code_is_classified(self):
        self.events = [
            {
                "type": "error",
                "code": "subscription_sharing_usage_limit_exceeded",
                "message": "sensitive provider detail",
                "sequence_number": 1,
            }
        ]
        with self.assertRaises(RateLimitError) as raised:
            await chatgpt.run("p", model="m")
        self.assertNotIn("sensitive", str(raised.exception))

    async def test_empty_final_envelope_uses_completed_item_snapshot(self):
        item = completed("가나")["response"]["output"][0]
        item["status"] = "completed"
        self.events = [
            {"type": "response.output_text.delta", "delta": "가나"},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": []},
            },
        ]
        self.assertEqual(await chatgpt.run("p", model="m"), "가나")
        for index, text, status in (
            (1, "가나", "completed"),
            (0, "different", "completed"),
            (0, "가나", "incomplete"),
        ):
            item["content"][0]["text"] = text
            item["status"] = status
            self.events[1]["output_index"] = index
            with self.subTest(index=index, status=status), self.assertRaises(AIError):
                await chatgpt.run("p", model="m")
        self.events = [self.events[0], self.events[-1]]
        with self.assertRaises(AIError):
            await chatgpt.run("p", model="m")
