import io
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch

from relay import agent_activity, harness, models, provider


def request(model="gpt-6.2-sol", effort="auto", files=(), **options):
    message = provider.user_message({"text": "Compare the alternatives.", "files": list(files)})
    settings = {"model": model, "effort": effort, "reasoning_summary": True,
                "context": 0, "max_output": 0, "instructions": ""}
    return provider.request_body([message], settings, **options)


def run_stream(body, events):
    encoded = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
    with patch("urllib.request.urlopen", return_value=io.BytesIO(encoded)) as transport:
        result = list(provider.events("fixture", body, threading.Event()))
    return transport.call_args.args[0], result


class ProviderModes(unittest.TestCase):
    def test_http_error_preserves_request_id_status_and_reported_usage(self):
        err = {"error": {"message": "Project rate limit exceeded.", "code": "rate_limit_exceeded",
                         "param": "model", "type": "rate_limit_error"}}
        for usage in (None, {"input_tokens": 19, "output_tokens": 3}):
            with self.subTest(usage=usage):
                payload = json.dumps(err | {"usage": usage}).encode()
                error = HTTPError(provider.ENDPOINT, 429, "Too many requests", {"x-request-id": "req_test"}, io.BytesIO(payload))
                with patch("urllib.request.urlopen", side_effect=error):
                    (kind, result), = provider.events("fixture", request(), threading.Event())
                self.assertEqual((kind, result["status"], result["usage"]), ("end", "error", usage))
                self.assertEqual(bool(result.get("rejected")), usage is None)
                self.assertIn("rate_limit", result)
                self.assertIsNone(result["service_tier"])
                note = result["note"]
                for detail in ("HTTP 429", "rate_limit_exceeded", "param=model", "req_test", "Project rate limit exceeded."):
                    self.assertIn(detail, note)

    def test_sse_flat_error_and_nested_response_failure(self):
        self.assertEqual(provider.explain({"type": "error", "message": "error", "code": "error"}),
                         "OpenAI could not complete the request.")
        _, events = run_stream(request(), [{"type": "response.created", "response": {"id": "resp_A"}},
            {"type": "error", "message": "Invalid sk-proj-fixture", "code": "bad_argument", "param": "input"}])
        kind, value = events[-1]
        self.assertEqual((kind, value["status"]), ("end", "error"))
        self.assertIn("resp_A", value["note"])
        self.assertIn("Invalid [redacted]", value["note"])
        self.assertNotIn("sk-proj-fixture", value["note"])
        response = {"id": "resp_B", "model": "gpt-6.2-sol", "service_tier": "fast",
                    "usage": {"input_tokens": 16, "output_tokens": 4}, "status": "failed",
                    "error": {"code": "server_error", "message": "Generation failed."}}
        _, events = run_stream(request(), [{"type": "response.failed", "response": response}])
        kind, value = events[-1]
        self.assertEqual((kind, value["status"], value["usage"]), ("end", "error", response["usage"]))
        self.assertEqual((value["billed_model"], value["service_tier"]), ("gpt-6.2-sol", "fast"))
        self.assertIn("Generation failed.", value["note"])

    def test_ultra_uses_real_delegation_and_filters_child_text(self):
        body = request(effort="ultra")
        self.assertEqual(body["reasoning"], {"effort": "xhigh"})
        self.assertEqual(body["multi_agent"], {"enabled": True, "max_concurrent_subagents": 3})
        self.assertIs(body["store"], False)
        self.assertEqual(request("gpt-5.6-terra", "ultra")["reasoning"]["effort"], "max")
        self.assertNotIn("ultra", models.capabilities("gpt-6-luna")["efforts"])
        self.assertNotIn("multi_agent", request(effort="xhigh"))

        def output(text, agent, phase="final_answer"):
            return {"type": "message", "agent": {"agent_name": agent}, "phase": phase,
                    "content": [{"type": "output_text", "text": text}]}

        items = [output("Progress", "/root", "commentary"), output("Child notes", "/root/reviewer"),
                 output("Combined answer", "/root")]
        events = []
        for index, item in enumerate(items):
            events.extend([{"type": "response.output_item.added", "output_index": index, "item": item},
                           {"type": "response.output_text.delta", "output_index": index,
                            "delta": item["content"][0]["text"]}])
        events.append({"type": "response.completed", "response": {"output": items}})
        req, stream = run_stream(body, events)
        self.assertEqual(req.get_header("Openai-beta"), "responses_multi_agent=v1")
        self.assertEqual([event for event in stream if event[0] not in ("end", "agent_activity")],
                         [("subagent", "/root/reviewer"), ("delta", "Combined answer")])
        self.assertEqual(stream[-1][1]["text"], "Combined answer")
        self.assertEqual(stream[-1][1]["subagents"], ["/root/reviewer"])
        self.assertNotIn("output_items", stream[-1][1])
        log = {}
        for kind, value in stream:
            if kind == "agent_activity":
                agent_activity.record(log, value)
        self.assertEqual(list(log["/root/reviewer"].values()),
                         [{"kind": "final_answer", "text": "Child notes", "done": True}])

    def test_agent_log_routes_interleaved_events_and_final_snapshots(self):
        a, b = "/root/notes", "/root/check"
        decoder = provider.Decoder(request(effort="ultra"))
        def at(name):
            return {"agent_name": name}
        def msg(item_id, phase, text):
            return {"id": item_id, "type": "message", "phase": phase,
                    "content": [{"type": "output_text", "text": text}]}
        def done(index, item):
            return {"type": "response.output_item.done", "output_index": index, "item": item}
        events = [
            done(0, {"id": "spawn", "type": "multi_agent_call_output", "action": "spawn_agent",
                     "output": [{"type": "output_text", "text": json.dumps({"task_name": a})}]}),
            {"type": "response.output_item.added", "output_index": 1, "item": msg("progress", "commentary", ""), "agent": at(a)},
            {"type": "response.output_item.added", "output_index": 2, "item": msg("answer", "final_answer", ""), "agent": at(b)},
            {"type": "response.output_text.delta", "item_id": "progress", "delta": "Checking."},
            {"type": "response.output_text.delta", "output_index": 2, "delta": "OK"},
            done(3, {"id": "mail", "type": "agent_message", "author": b, "recipient": a, "agent": at(a),
                     "content": [{"type": "encrypted_content", "encrypted_content": "opaque-test"}]}),
            done(4, {"id": "tool", "type": "function_call", "name": "read_file", "agent": at(a),
                     "arguments": json.dumps({"path": "README.md", "start": 12, "count": 3})}),
            {"type": "response.output_item.added", "output_index": 5, "agent": at(b),
             "item": {"id": "search", "type": "web_search_call", "status": "in_progress"}},
            {"type": "response.web_search_call.searching", "output_index": 5, "item_id": "search"},
            done(5, {"id": "search", "type": "web_search_call", "status": "completed",
                     "action": {"type": "search", "query": "model release"}}),
            {"type": "response.completed", "response": {"output": [
                msg("progress", "commentary", "Checking."), msg("answer", "final_answer", "OK!"),
                msg("root", "final_answer", "Combined") | {"agent": at("/root")}]}},
        ]
        stream = [result for event in events for result in decoder.feed(event)]
        log = {}
        for kind, value in stream:
            if kind == "agent_activity":
                agent_activity.record(log, value)
        self.assertEqual([value for kind, value in stream if kind == "subagent"], [a, b])
        self.assertEqual(log[a]["progress"], {"kind": "commentary", "text": "Checking.", "done": True})
        self.assertEqual(log[b]["answer"]["text"], "OK!")
        self.assertEqual(log[b]["mail"], {"kind": "delivery", "target": a})
        self.assertEqual(log[a]["mail"], {"kind": "received", "target": b})
        self.assertEqual(log[a]["tool"], {"kind": "tool", "tool": "read_file", "target": "README.md:12", "stage": "requested"})
        self.assertEqual(log[b]["search"], {"kind": "tool", "tool": "web_search", "target": "model release", "stage": "completed"})
        self.assertFalse(any(kind == "search" for kind, _ in stream))
        self.assertEqual(log[a]["spawn"]["kind"], "spawn")
        self.assertNotIn("opaque-test", str(log))
        self.assertNotIn("delta", [kind for kind, _ in stream])
        self.assertEqual(stream[-1][1]["text"], "Combined")

    def test_stop_preserves_partial_agent_message(self):
        body = request(effort="ultra")
        stop = threading.Event()
        turn = harness.Turn(stop)
        def upstream(key, body, stopped):
            decoder = provider.Decoder(body)
            for event in [{"type": "response.output_item.added", "output_index": 0,
                           "item": {"type": "message", "id": "m", "phase": "commentary", "agent": {"agent_name": "/root/check"}}},
                          {"type": "response.output_text.delta", "output_index": 0, "delta": "Still reading"}]:
                yield from decoder.feed(event)
        def emit(event, **fields):
            if event == "agent_activity" and fields.get("delta"):
                stop.set()
        turn.send(upstream, "fixture", body, emit, None, lambda result: None)
        result = turn.result()
        self.assertEqual((result["status"], result["text"]), ("stopped", ""))
        self.assertEqual(result["agent_activity"]["/root/check"]["m"], {"kind": "commentary", "text": "Still reading"})

    def test_catalog_effort_and_stateless_function_continuation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            catalog = {"models": [{"slug": "gpt-6.2-sol", "multi_agent_reasoning_effort": "high",
                                   "supported_reasoning_levels": [{"effort": "high"}, {"effort": "max"},
                                                                  {"effort": "ultra"}]}]}
            (root / "models_cache.json").write_text(json.dumps(catalog))
            _, presets = models.codex_model_defaults(root)
        body = request(effort="ultra", ultra_effort=presets["gpt-6.2-sol"])
        self.assertEqual(body["reasoning"]["effort"], "high")
        body["tools"] = [{"type": "function", "name": "read_file"}]
        items = [{"type": "reasoning", "encrypted_content": "opaque", "summary": []},
                 {"type": "function_call", "call_id": "a", "name": "read_file", "arguments": "{}"}]
        events = [{"type": "response.output_item.done", "item": item} for item in items]
        events.append({"type": "response.completed", "response": {}})
        _, stream = run_stream(body, events)
        self.assertEqual(stream[-1][1]["output_items"], items)

    def test_summary_and_pdf_use_native_response_shapes(self):
        pdf = {"name": "Notes.pdf", "kind": "pdf", "data": "data:application/pdf;base64,JVBERi0="}
        body = request(files=[pdf])
        self.assertEqual(body["input"][-1]["content"][-1], {"type": "input_file", "filename": "Notes.pdf",
                                                            "file_data": pdf["data"]})
        self.assertEqual(body["reasoning"], {"summary": "auto"})
        events = [{"type": "response.reasoning_summary_text.delta", "delta": "Compared options."},
                  {"type": "response.completed", "response": {"output": [
                      {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Compared options."}]}
                  ]}}]
        req, stream = run_stream(body, events)
        self.assertIsNone(req.get_header("Openai-beta"))
        self.assertIn(("summary", "Compared options."), stream)
        self.assertEqual(stream[-1][1]["reasoning_summary"], "Compared options.")
        self.assertNotIn("reasoning", request("gpt-4o", files=[pdf]))
        with self.assertRaisesRegex(ValueError, "PDF"):
            request("o3-mini", files=[pdf])


if __name__ == "__main__":
    unittest.main()
