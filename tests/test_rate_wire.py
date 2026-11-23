import json
import unittest
from unittest.mock import patch

from relay import live, provider
from test_live_context import SocketFixture
from test_provider_modes import request, run_stream


def limited(delay):
    return {"code": "rate_limit_exceeded", "message": (
        "Rate limit reached for gpt-6.2-astra in organization org-fixture on tokens per min (TPM): "
        f"Limit 1000000, Used 948533, Requested 54282. Please try again in {delay}.")}


class RateLimitWire(unittest.TestCase):
    def test_midstream_sse_and_websocket_errors_keep_partial_text_and_wait_time(self):
        body = request("gpt-6.2-astra", effort="ultra")
        for transport, hint, seconds in (("sse", "169ms", .169), ("socket", "523ms", .523),
                                         ("socket", "778ms", .778), ("socket", "1.75s", 1.75)):
            with self.subTest(transport=transport, hint=hint):
                error = limited(hint)
                events = [{"type": "response.created", "response": {"id": "resp_fixture"}},
                          {"type": "response.output_text.delta", "delta": "Partial."}]
                if transport == "sse":
                    events.append({"type": "response.failed", "response": {"id": "resp_fixture", "error": error}})
                    _, decoded = run_stream(body, events)
                else:
                    events.append({"type": "error", "status": 429, "error": error})
                    control = live.RunControl(body["model"])
                    with patch.object(control, "_connect", return_value=SocketFixture(lambda event: events)):
                        decoded = list(control.events("fixture", body, control))
                    self.assertIsNone(control.socket)
                kind, result = decoded[-1]
                self.assertEqual((kind, result["status"], result["text"]), ("end", "error", "Partial."))
                self.assertEqual(result["rate_limit"], {"retry_after": seconds, "metric": "TPM"})
                self.assertNotIn("rejected", result)
                self.assertNotIn("org-fixture", result["note"])
                self.assertIn("resp_fixture", result["note"])

    def test_http_delay_priority_and_quota_codes(self):
        def failure(error, headers):
            return provider.http_failure(json.dumps({"error": error}), 429, headers, False)

        kind, result = failure(limited("169ms"), {"Retry-After": "2", "x-ratelimit-reset-tokens": "1m"})
        self.assertEqual((kind, result["rejected"], result["rate_limit"]["retry_after"]), ("end", True, 2))
        error = {"code": "slow_down", "message": "Slow down."}
        _, result = failure(error, {"x-ratelimit-remaining-project-tokens": "0", "x-ratelimit-reset-project-tokens": "1m2.5s",
                                    "x-ratelimit-remaining-requests": "6", "x-ratelimit-reset-requests": "1s"})
        self.assertEqual(result["rate_limit"]["retry_after"], 62.5)
        for code in ("insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
                     "project_spend_limit_exceeded", "organization_usage_limit_exceeded"):
            self.assertEqual(failure({"code": code}, {"Retry-After": "1"})[0], "rejected")
        self.assertEqual(failure({"code": "rate_limit_exceeded", "message": "Request too large for the token limit."}, {})[0],
                         "rejected")

    def test_websocket_handshake_429_waits_before_any_https_fallback(self):
        class HandshakeError(Exception):
            status_code = 429
            resp_headers = {"retry-after": "1"}
            resp_body = json.dumps({"error": limited("169ms")}).encode()
        body = request()
        control = live.RunControl(body["model"])
        with patch.object(control, "_connect", side_effect=HandshakeError), patch.object(provider, "events") as https:
            (kind, result), = control.events("fixture", body, control)
        https.assert_not_called()
        self.assertEqual((kind, result["rate_limit"]["retry_after"], result["rejected"]), ("end", 1, True))
