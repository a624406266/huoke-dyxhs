"""Real HTTP/SSE transport tests against an isolated loopback-only server.

No provider account, external model, browser session, or recorded user data is
used. Synthetic credentials/content are only inspected inside this fixture.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from llm_client import LLMRequestError, call_llm, call_llm_json, get_last_llm_usage, reset_llm_usage


def event(data, *, newline="\n", kind=None):
    body = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    prefix = f"event: {kind}{newline}" if kind else ""
    return (prefix + "".join(f"data: {line}{newline}" for line in body.split("\n")) + newline).encode("utf-8")


def delta(content=None, *, reasoning=None, finish=None):
    value = {}
    if content is not None:
        value["content"] = content
    if reasoning is not None:
        value["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "delta": value, "finish_reason": finish}]}


def start_stream(handler):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.flush()


def chunk(handler, data):
    if data:
        handler.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
        handler.wfile.flush()


def end_stream(handler):
    handler.wfile.write(b"0\r\n\r\n")
    handler.wfile.flush()
    handler.close_connection = True


def stream_response(frames, *, delay=0, fragments=None, terminate=True):
    def respond(handler):
        start_stream(handler)
        for frame in frames:
            if delay and handler.server.stopping.wait(delay):
                return
            parts = [frame] if not fragments else [frame[index:index + fragments] for index in range(0, len(frame), fragments)]
            for part in parts:
                chunk(handler, part)
        if terminate:
            end_stream(handler)
        else:
            handler.close_connection = True
    return respond


def complete_response(text, *, usage=None, done=True, finish="stop"):
    frames = [event(delta(text)), event(delta(finish=finish))]
    if usage is not None:
        frames.append(event({"choices": [], "usage": usage}))
    if done:
        frames.append(event("[DONE]"))
    return stream_response(frames)


def error_response(code=503, body=None):
    def respond(handler):
        data = json.dumps(body or {"error": {"message": "synthetic temporary failure"}}).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(data)
        handler.wfile.flush()
        handler.close_connection = True
    return respond


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, responders):
        self.responders = responders
        self.requests = []
        self.request_lock = threading.Lock()
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", 0), FixtureHandler)

    def handle_error(self, request, client_address):
        # Timeouts deliberately disconnect while a fixture is still writing.
        pass


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        record = {"path": self.path, "headers": dict(self.headers), "json": json.loads(raw)}
        with self.server.request_lock:
            index = len(self.server.requests)
            self.server.requests.append(record)
        responder = self.server.responders[min(index, len(self.server.responders) - 1)]
        try:
            responder(self)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True


@contextmanager
def local_server(*responders):
    server = FixtureServer(list(responders))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.stopping.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class LLMStreamingTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        reset_llm_usage()

    def options(self, base, **overrides):
        return {"api_base": base, "model": "fixture-model", "connect_timeout_s": 0.5,
                "read_timeout_s": 0.5, "total_timeout_s": 3, **overrides}

    def assert_error_code(self, events, expected):
        self.assertIn(expected, [item.get("code") for item in events if isinstance(item, dict)])

    def test_real_chunked_utf8_crlf_multiline_data_and_usage(self):
        frames = [
            b": keep-alive\r\n\r\n",
            event(delta(reasoning="仅供内部推理的测试文本"), newline="\r\n"),
            event(delta("你好"), newline="\r\n"),
            event('{"choices": [\n{"index":0,"delta":{"content":"，世界"},"finish_reason":null}]}', newline="\r\n"),
            event(delta(finish="stop"), newline="\r\n"),
            event({"choices": [], "usage": {"prompt_tokens": 17, "completion_tokens": 6, "total_tokens": 23}}, newline="\r\n"),
            event("[DONE]", newline="\r\n"),
        ]
        events = []
        with local_server(stream_response(frames, fragments=2)) as (server, base):
            result = call_llm([{"role": "user", "content": "合成输入"}], **self.options(base, event_callback=events.append))
            self.assertEqual(result, "你好，世界")
            self.assertEqual(len(server.requests), 1)
            request = server.requests[0]
            self.assertEqual(request["path"], "/v1/chat/completions")
            self.assertIs(request["json"].get("stream"), True)
            self.assertIs(request["json"].get("stream_options", {}).get("include_usage"), True)
            self.assertNotIn("Authorization", request["headers"])
        usage = get_last_llm_usage()
        self.assertEqual(usage["requestCount"], 1)
        self.assertEqual(usage["inputTokens"], 17)
        self.assertEqual(usage["outputTokens"], 6)
        self.assertEqual(usage["totalTokens"], 23)
        self.assertTrue(usage["complete"])
        self.assertIn("complete", [item.get("phase") for item in events])

    def test_finish_stop_is_complete_without_done_marker(self):
        with local_server(complete_response("完成", done=False)) as (_, base):
            self.assertEqual(call_llm([], **self.options(base)), "完成")

    def test_done_marker_is_complete_without_finish_stop(self):
        with local_server(stream_response([event(delta("完成")), event("[DONE]")])) as (_, base):
            self.assertEqual(call_llm([], **self.options(base)), "完成")

    def test_continuous_reasoning_exceeds_idle_interval_and_then_succeeds(self):
        frames = [event(delta(reasoning="持续推理")) for _ in range(8)]
        frames += [event(delta("最终回答")), event(delta(finish="stop")), event("[DONE]")]
        with local_server(stream_response(frames, delay=0.04)) as (_, base):
            started = time.monotonic()
            result = call_llm([], **self.options(base, read_timeout_s=0.12, total_timeout_s=1.8))
            elapsed = time.monotonic() - started
        self.assertEqual(result, "最终回答")
        self.assertGreater(elapsed, 0.3)
        self.assertLess(elapsed, 1.8)

    def test_missing_usage_is_unavailable_not_invented_zero(self):
        with local_server(complete_response("done")) as (_, base):
            call_llm([], **self.options(base))
        usage = get_last_llm_usage()
        self.assertEqual(usage["requestCount"], 1)
        self.assertFalse(usage["available"])
        self.assertFalse(usage["complete"])
        self.assertIsNone(usage["totalTokens"])

    def test_header_wait_has_first_response_timeout(self):
        def delayed_headers(handler):
            if not handler.server.stopping.wait(0.4):
                complete_response("late")(handler)
        events = []
        with local_server(delayed_headers) as (_, base):
            started = time.monotonic()
            with self.assertRaises(LLMRequestError):
                call_llm([], **self.options(base, read_timeout_s=0.08, total_timeout_s=1, event_callback=events.append))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.7)
        self.assert_error_code(events, "first_response_timeout")

    def test_stream_idle_timeout_does_not_return_partial_content(self):
        def idle_stream(handler):
            start_stream(handler)
            chunk(handler, event(delta("partial")))
            if not handler.server.stopping.wait(0.4):
                chunk(handler, event("[DONE]"))
                end_stream(handler)
        events = []
        with local_server(idle_stream) as (_, base):
            started = time.monotonic()
            with self.assertRaises(LLMRequestError):
                call_llm([], **self.options(base, read_timeout_s=0.08, total_timeout_s=1, event_callback=events.append))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.7)
        self.assert_error_code(events, "stream_idle_timeout")

    def test_active_stream_is_stopped_by_hard_total_deadline(self):
        events = []
        responder = stream_response([event(delta(reasoning="active")) for _ in range(100)], delay=0.02)
        with local_server(responder) as (server, base):
            started = time.monotonic()
            with self.assertRaises(LLMRequestError):
                call_llm([], **self.options(base, read_timeout_s=0.3, total_timeout_s=0.28, event_callback=events.append))
            elapsed = time.monotonic() - started
            self.assertEqual(len(server.requests), 1)
        self.assertGreater(elapsed, 0.15)
        self.assertLess(elapsed, 0.85)
        self.assert_error_code(events, "total_timeout")

    def test_total_deadline_also_covers_waiting_for_headers(self):
        def stalled_headers(handler):
            handler.server.stopping.wait(2)
        events = []
        with local_server(stalled_headers) as (_, base):
            started = time.monotonic()
            with self.assertRaises(LLMRequestError):
                call_llm([], **self.options(base, read_timeout_s=2, total_timeout_s=0.2, event_callback=events.append))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.75)
        self.assert_error_code(events, "total_timeout")

    def test_blocked_dns_obeys_deadline_and_next_request_still_succeeds(self):
        original_getaddrinfo = socket.getaddrinfo
        dns_started, dns_finished = threading.Event(), threading.Event()
        dns_lock = threading.Lock()
        events = []

        def delayed_first_local_dns(host, *args, **kwargs):
            # The real resolver is called only for the local fixture hostname.
            if host not in ("localhost", b"localhost"):
                raise AssertionError("Fixture must never resolve an external hostname")
            with dns_lock:
                delay = not dns_started.is_set()
                if delay:
                    dns_started.set()
            if not delay:
                return original_getaddrinfo(host, *args, **kwargs)
            try:
                time.sleep(0.7)
                return original_getaddrinfo(host, *args, **kwargs)
            finally:
                dns_finished.set()

        with local_server(complete_response("后续请求成功")) as (server, base):
            base = base.replace("127.0.0.1", "localhost")
            with patch("socket.getaddrinfo", side_effect=delayed_first_local_dns):
                try:
                    started = time.monotonic()
                    with self.assertRaises(LLMRequestError) as caught:
                        call_llm([], **self.options(base, connect_timeout_s=1,
                                 total_timeout_s=0.15, event_callback=events.append))
                    elapsed = time.monotonic() - started
                    self.assertTrue(dns_started.is_set())
                    self.assertEqual(caught.exception.code, "total_timeout")
                    self.assertLess(elapsed, 0.5)
                    self.assertFalse(dns_finished.is_set())
                    self.assertEqual(len(server.requests), 0)
                    self.assertEqual(call_llm([], **self.options(base, total_timeout_s=0.5)),
                                     "后续请求成功")
                    self.assertEqual(len(server.requests), 1)
                    self.assert_error_code(events, "total_timeout")
                finally:
                    # Keep the patch installed until the cancelled DNS worker
                    # exits so no delayed resolver escapes into later tests.
                    if dns_started.is_set():
                        self.assertTrue(dns_finished.wait(2))

    def test_clean_http_end_without_sse_terminator_is_incomplete(self):
        events = []
        with local_server(stream_response([event(delta('{"ok":true}'))])) as (_, base):
            with self.assertRaises(LLMRequestError):
                call_llm([], **self.options(base, event_callback=events.append))
        self.assert_error_code(events, "incomplete_stream")

    def test_length_finish_without_done_is_not_complete(self):
        events = []
        with local_server(complete_response("truncated", done=False, finish="length")) as (_, base):
            with self.assertRaises(LLMRequestError):
                call_llm([], **self.options(base, event_callback=events.append))
        self.assert_error_code(events, "output_truncated")

    def test_error_event_is_not_a_successful_completion(self):
        events = []
        frames = [event(delta("partial")), event({"error": {"message": "fixture-provider-private-message"}}, kind="error"), event("[DONE]")]
        with local_server(stream_response(frames)) as (_, base):
            with self.assertRaises(LLMRequestError) as caught:
                call_llm([], **self.options(base, event_callback=events.append))
        self.assert_error_code(events, "provider_error")
        self.assertNotIn("fixture-provider-private-message", str(caught.exception) + json.dumps(events))

    def test_malformed_json_and_invalid_utf8_are_rejected(self):
        for frame in (b"data: not-json\n\n", b"data: \xff\n\n"):
            with self.subTest(frame_kind="utf8" if b"\xff" in frame else "json"):
                events = []
                with local_server(stream_response([frame, event("[DONE]")])) as (_, base):
                    with self.assertRaises(LLMRequestError):
                        call_llm([], **self.options(base, event_callback=events.append))
                self.assert_error_code(events, "invalid_response")

    def test_json_contract_retries_object_required_keys_and_validator(self):
        seen = []
        def validator(value):
            seen.append(value)
            if value["ok"] is not True:
                raise ValueError("fixture rejected nonboolean ok")
        with local_server(complete_response("[]"), complete_response('{"ok":1}'), complete_response('{"ok":true}')) as (server, base):
            data, raw = call_llm_json([], required_keys=["ok"], validator=validator, retries=20, **self.options(base, total_timeout_s=8))
            self.assertEqual(data, {"ok": True})
            self.assertEqual(json.loads(raw), {"ok": True})
            self.assertEqual(len(server.requests), 3)
        self.assertEqual(seen, [{"ok": 1}, {"ok": True}])

    def test_json_required_fields_are_not_optional(self):
        with local_server(complete_response('{"other":1}')) as (server, base):
            with self.assertRaises(RuntimeError):
                call_llm_json([], required_keys=["ok"], retries=0, **self.options(base))
            self.assertEqual(len(server.requests), 1)

    def test_validator_exception_traceback_does_not_leak_private_values(self):
        private = "fixture-private-validator-payload"
        events = []

        def reject(value):
            raise ValueError(private)

        with local_server(complete_response('{"ok":true}')) as (server, base):
            with self.assertRaises(LLMRequestError) as caught:
                call_llm_json([], validator=reject, retries=0,
                              **self.options(base, event_callback=events.append))
            self.assertEqual(len(server.requests), 1)
        diagnostic = "".join(traceback.format_exception(caught.exception))
        diagnostic += str(caught.exception) + json.dumps(events) + json.dumps(get_last_llm_usage())
        self.assertNotIn(private, diagnostic)
        self.assert_error_code(events, "validation_error")

    def test_retry_limit_is_three_total_attempts_even_if_higher_requested(self):
        with local_server(stream_response([event(delta('{"ok":true}'))])) as (server, base):
            with self.assertRaises(RuntimeError):
                call_llm_json([], retries=99, **self.options(base, total_timeout_s=8))
            self.assertEqual(len(server.requests), 3)

    def test_network_disconnect_can_retry_then_complete(self):
        def disconnect(handler):
            handler.close_connection = True
            handler.connection.shutdown(socket.SHUT_RDWR)
            handler.connection.close()
        with local_server(disconnect, complete_response('{"ok":true}')) as (server, base):
            result, _ = call_llm_json([], required_keys=["ok"], **self.options(base, total_timeout_s=5))
            self.assertEqual(result, {"ok": True})
            self.assertEqual(len(server.requests), 2)

    def test_retry_usage_accumulates_actual_provider_counts(self):
        first = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
        second = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
        with local_server(complete_response("[]", usage=first), complete_response('{"ok":true}', usage=second)) as (_, base):
            call_llm_json([], required_keys=["ok"], **self.options(base, total_timeout_s=5))
        usage = get_last_llm_usage()
        self.assertEqual(usage["requestCount"], 2)
        self.assertEqual(usage["inputTokens"], 18)
        self.assertEqual(usage["outputTokens"], 5)
        self.assertEqual(usage["totalTokens"], 23)
        self.assertTrue(usage["complete"])

    def test_all_retries_and_backoff_share_one_total_deadline(self):
        def slow_temporary_error(handler):
            if not handler.server.stopping.wait(0.16):
                error_response(503)(handler)
        events = []
        with local_server(slow_temporary_error) as (server, base):
            started = time.monotonic()
            with self.assertRaises(RuntimeError):
                call_llm_json([], retries=99, **self.options(base, total_timeout_s=0.35, event_callback=events.append))
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(len(server.requests), 1)
            self.assertLessEqual(len(server.requests), 3)
        self.assertLess(elapsed, 0.9)
        self.assert_error_code(events, "total_timeout")

    def test_401_is_not_retried_and_diagnostics_do_not_leak(self):
        key, prompt, provider = "fixture-private-api-key", "fixture-private-request-body", "fixture-private-provider-body"
        events, stdout, stderr = [], StringIO(), StringIO()
        with local_server(error_response(401, {"error": {"message": f"{key} {prompt} {provider}"}})) as (server, base):
            with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(RuntimeError) as caught:
                call_llm_json([{"role": "user", "content": prompt}], api_key=key,
                              **self.options(base, event_callback=events.append))
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(server.requests[0]["headers"].get("Authorization"), f"Bearer {key}")
        diagnostics = str(caught.exception) + json.dumps(events) + stdout.getvalue() + stderr.getvalue() + json.dumps(get_last_llm_usage())
        for private in (key, prompt, provider):
            self.assertNotIn(private, diagnostics)

    def test_success_events_are_metadata_only(self):
        output, reasoning = "fixture-private-output-body", "fixture-private-reasoning-body"
        events = []
        frames = [event(delta(reasoning=reasoning)), event(delta(output)), event(delta(finish="stop")), event("[DONE]")]
        with local_server(stream_response(frames)) as (_, base):
            self.assertEqual(call_llm([], **self.options(base, event_callback=events.append)), output)
        self.assertTrue(events)
        allowed = {"phase", "attempt", "elapsedSeconds", "receivedCharacters", "reasoningCharacters", "code", "error_type"}
        for item in events:
            self.assertFalse(set(item) - allowed)
            self.assertIn(item["phase"], {"connecting", "streaming", "complete", "retry", "error"})
        diagnostic = json.dumps(events)
        self.assertNotIn(output, diagnostic)
        self.assertNotIn(reasoning, diagnostic)

    def test_failing_event_callback_cannot_break_or_retry_request(self):
        def broken_callback(value):
            raise RuntimeError("fixture-private-callback-error")
        with local_server(complete_response("ok")) as (server, base):
            self.assertEqual(call_llm([], **self.options(base, event_callback=broken_callback)), "ok")
            self.assertEqual(len(server.requests), 1)


if __name__ == "__main__":
    unittest.main()
