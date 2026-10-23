import asyncio
import codecs
import json
import ipaddress
import math
import os
import queue
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextvars import ContextVar
from urllib.parse import urlparse

import httpx

from config_contract import LLM_TIMEOUT_DEFAULTS, llm_temperature

_usage_records = ContextVar("llm_usage_records", default=None)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_io_loop = None
_io_loop_lock = threading.Lock()


def _request_loop():
    """Reuse a bounded resolver pool instead of waiting for DNS cleanup per call."""
    global _io_loop
    with _io_loop_lock:
        if _io_loop is None:
            loop = asyncio.new_event_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-resolver"))
            def serve():
                asyncio.set_event_loop(loop)
                loop.run_forever()
            threading.Thread(target=serve, name="llm-io", daemon=True).start()
            _io_loop = loop
        return _io_loop


def _await_request(coroutine, deadline, drain_events):
    future = asyncio.run_coroutine_threadsafe(coroutine, _request_loop())
    try:
        while True:
            drain_events()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                raise TimeoutError()
            try:
                return future.result(timeout=min(0.1, remaining))
            except FutureTimeoutError:
                if future.done():
                    return future.result()
    finally:
        if not future.done():
            future.cancel()
        drain_events()


def reset_llm_usage():
    _usage_records.set([])


def get_last_llm_usage():
    records = list(_usage_records.get() or [])
    result = {"source": "provider", "requests": records, "requestCount": len(records),
              "available": any(item.get("available") for item in records),
              "complete": bool(records) and all(item.get("available") for item in records)}
    for key in ("inputTokens", "outputTokens", "totalTokens"):
        values = [item[key] for item in records if item.get(key) is not None]
        result[key] = sum(values) if values else None
    return result


def _record_usage(usage=None, diagnostics=None):
    usage = usage if isinstance(usage, dict) else {}
    record = {}
    for target, names in (("inputTokens", ("prompt_tokens", "input_tokens")),
                          ("outputTokens", ("completion_tokens", "output_tokens")),
                          ("totalTokens", ("total_tokens",))):
        value = next((usage[name] for name in names if name in usage), None)
        record[target] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    record["available"] = any(record[key] is not None for key in ("inputTokens", "outputTokens", "totalTokens"))
    if diagnostics:
        record["diagnostics"] = diagnostics
    _usage_records.set([*(_usage_records.get() or []), record])


def _local_endpoint(base):
    try:
        hostname = urlparse(str(base or "")).hostname
        return hostname == "localhost" or ipaddress.ip_address(hostname or "").is_loopback
    except ValueError:
        return False


class LLMRequestError(RuntimeError):
    def __init__(self, message, retryable=False, code="network_error"):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


def validate_api_base(base):
    """Allow cleartext only for literal loopback addresses or exact localhost."""
    try:
        parsed = urlparse(str(base or ""))
        # Accessing port also rejects malformed and out-of-range ports.
        parsed.port
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError()
    except ValueError:
        raise LLMRequestError("模型 API Base 必须是明确的 HTTP(S) 地址。", code="configuration_error") from None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LLMRequestError("模型 API Base 不得含账号密码、查询参数或片段。", code="configuration_error")
    if parsed.scheme == "http" and not _local_endpoint(base):
        raise LLMRequestError("远程模型 API Base 必须使用 HTTPS；仅本机回环地址允许 HTTP。", code="configuration_error")


def llm_request_options(config=None):
    config = config or {}
    return {name: config.get(name, default) for name, default in LLM_TIMEOUT_DEFAULTS.items()}


def _timeout_options(timeout, connect_timeout_s, read_timeout_s, total_timeout_s):
    supplied = dict(connect_timeout_s=connect_timeout_s, read_timeout_s=read_timeout_s,
                    total_timeout_s=total_timeout_s)
    result = {}
    for name, value in supplied.items():
        value = value if value is not None else (timeout if timeout is not None else LLM_TIMEOUT_DEFAULTS[name])
        if isinstance(value, bool):
            raise LLMRequestError("模型超时参数必须为正数秒。", code="configuration_error")
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise LLMRequestError("模型超时参数必须为正数秒。", code="configuration_error") from exc
        if not math.isfinite(value) or value <= 0 or value > 86400:
            raise LLMRequestError("模型超时参数必须大于 0 且不超过 86400 秒。", code="configuration_error")
        result[name] = value
    return result


def _notify(callback, **event):
    if callback:
        try:
            callback(event)
        except Exception:
            # Progress observers cannot invalidate a successfully received response.
            pass


def _network_error(exc):
    """Classify without copying URLs, credentials, or provider response text."""
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return "dns_error", "模型接口 DNS 解析失败。"
        if isinstance(current, ssl.SSLError):
            return "tls_error", "模型接口 TLS 连接或证书校验失败。"
        current = current.__cause__ or current.__context__
    return "network_error", "模型接口网络连接失败。"


class _SSEReader:
    """Incrementally frame UTF-8 SSE; reasoning is counted, never retained."""
    def __init__(self, stats):
        self.stats = stats
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.lines = []
        self.event = ""
        self.content = []
        self.done = False
        self.finished = False

    def feed(self, chunk, final=False):
        self.buffer += self.decoder.decode(chunk, final=final)
        while self.buffer:
            match = re.search(r"\r\n|\r|\n", self.buffer)
            if not match:
                break
            if match.group() == "\r" and match.end() == len(self.buffer) and not final:
                break
            line, self.buffer = self.buffer[:match.start()], self.buffer[match.end():]
            self._line(line)
        if final:
            if self.buffer:
                self._line(self.buffer)
                self.buffer = ""
            self._dispatch()

    def _line(self, line):
        if not line:
            self._dispatch()
        elif line.startswith("data:"):
            value = line[5:]
            self.lines.append(value[1:] if value.startswith(" ") else value)
        elif line.startswith("event:"):
            self.event = line[6:].strip()
        # SSE comments, id, and retry fields do not contain model output.

    def _dispatch(self):
        lines, event = self.lines, self.event
        self.lines, self.event = [], ""
        if not lines:
            if event == "error":
                raise LLMRequestError("模型服务返回流错误事件。", code="provider_error")
            return
        raw = "\n".join(lines).strip()
        if not raw:
            return
        if raw == "[DONE]":
            self.done = True
            return
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("SSE object required")
        if event == "error" or data.get("error"):
            raise LLMRequestError("模型服务返回流错误事件。", code="provider_error")
        if isinstance(data.get("usage"), dict):
            self.stats["usage"] = data["usage"]
        choices = data.get("choices") or []
        if not isinstance(choices, list):
            raise ValueError("Invalid choices")
        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError("Invalid choice")
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            content, reasoning = delta.get("content") or "", delta.get("reasoning_content") or ""
            if not isinstance(content, str) or not isinstance(reasoning, str):
                raise ValueError("Text delta required")
            if self.finished and (content or reasoning):
                raise ValueError("Content after finish")
            self.content.append(content)
            self.stats["receivedCharacters"] += len(content)
            self.stats["reasoningCharacters"] += len(reasoning)
            finish = choice.get("finish_reason")
            if finish:
                if finish != "stop":
                    raise LLMRequestError("模型输出未完整结束（输出上限或服务限制），未采用部分结果。",
                                          code="output_truncated")
                self.finished = True

    def result(self):
        if not (self.done or self.finished):
            raise LLMRequestError("模型流在完成标记之前中断，未采用部分结果。",
                                  retryable=True, code="incomplete_stream")
        content = "".join(self.content)
        if not content.strip():
            raise LLMRequestError("模型没有返回可用的最终正文。", code="invalid_response")
        return content


def resolve_llm_config(api_key=None, api_base=None, model=None, temperature=None):
    key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base = api_base or os.environ.get("LLM_API_BASE") or ""
    mdl = model or os.environ.get("LLM_MODEL") or ""
    try:
        temp = llm_temperature(0.6 if temperature is None else temperature)
    except ValueError as exc:
        raise LLMRequestError(str(exc), code="configuration_error") from None
    return key, base, mdl, temp


def has_llm_key(api_key=None, api_base=None):
    key, base, _, _ = resolve_llm_config(api_key=api_key, api_base=api_base)
    return bool(key) or _local_endpoint(base)


async def _receive_response(url, payload, headers, limits, stats, progress):
    timeout = httpx.Timeout(connect=limits["connect_timeout_s"], read=limits["read_timeout_s"],
                            write=limits["connect_timeout_s"], pool=limits["connect_timeout_s"])
    # Cancellation from wait_for closes the stream and client, including stalled reads.
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()
            stats["headersReceived"] = True
            stats["httpStatus"] = response.status_code
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            is_sse = content_type == "text/event-stream"
            parser = _SSEReader(stats) if is_sse else None
            body = bytearray()
            size, last_progress = 0, -1.0
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise LLMRequestError("模型响应超过 8 MiB，已停止读取。", code="response_too_large")
                if parser:
                    parser.feed(chunk)
                else:
                    body.extend(chunk)
                elapsed = time.monotonic() - stats["started"]
                if last_progress < 0 or elapsed - last_progress >= 1:
                    progress("streaming")
                    last_progress = elapsed
                if parser and parser.done:
                    break
            if parser:
                parser.feed(b"", final=True)
                return parser.result()
            # Some compatible services ignore stream=true and send one JSON response.
            data = json.loads(body.decode("utf-8"))
            if data.get("error"):
                raise LLMRequestError("模型服务返回错误响应。", code="provider_error")
            stats["usage"] = data.get("usage")
            choice = data["choices"][0]
            if choice.get("finish_reason") not in (None, "stop"):
                raise LLMRequestError("模型输出未完整结束，未采用部分结果。", code="output_truncated")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Final content required")
            stats["receivedCharacters"] = len(content)
            stats["reasoningCharacters"] = len(choice["message"].get("reasoning_content") or "")
            return content


def call_llm(messages, api_key=None, api_base=None, model=None, temperature=None, timeout=None,
             *, connect_timeout_s=None, read_timeout_s=None, total_timeout_s=None, event_callback=None):
    """Synchronous facade over cancellable streaming HTTP; no model text in diagnostics."""
    limits = _timeout_options(timeout, connect_timeout_s, read_timeout_s, total_timeout_s)
    key, base, mdl, temp = resolve_llm_config(api_key, api_base, model, temperature)
    if not base or not mdl:
        raise LLMRequestError("请明确配置模型 API Base 和模型名称；系统没有默认外部模型服务。", code="configuration_error")
    validate_api_base(base)
    if not key and not _local_endpoint(base):
        raise LLMRequestError("未配置 LLM API Key。请设置 LLM_API_KEY/OPENAI_API_KEY 或在 config.local.json 中配置。",
                              code="configuration_error")

    payload = {
        "model": mdl,
        "messages": messages,
        "temperature": temp,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    stats = {"started": time.monotonic(), "receivedCharacters": 0, "reasoningCharacters": 0,
             "headersReceived": False}
    events = queue.SimpleQueue()

    def progress(phase, **extra):
        events.put(dict(phase=phase, elapsedSeconds=round(time.monotonic() - stats["started"], 3),
                        receivedCharacters=stats["receivedCharacters"],
                        reasoningCharacters=stats["reasoningCharacters"], **extra))

    def drain_events():
        # Database/UI observers run on the caller's thread, not the shared I/O loop.
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            _notify(event_callback, **event)

    async def request():
        return await asyncio.wait_for(
            _receive_response(base.rstrip("/") + "/chat/completions", payload, headers, limits, stats, progress),
            timeout=limits["total_timeout_s"])

    progress("connecting")
    try:
        result = _await_request(request(), stats["started"] + limits["total_timeout_s"], drain_events)
        progress("complete")
        return result
    except Exception as exc:
        if isinstance(exc, LLMRequestError):
            error = exc
        elif isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            error = LLMRequestError("模型生成达到总时限，已取消请求；本次调用不再自动重试。", code="total_timeout")
        elif isinstance(exc, httpx.ConnectTimeout):
            error = LLMRequestError("模型接口建立连接超时。", True, "connect_timeout")
        elif isinstance(exc, httpx.ReadTimeout):
            code = "stream_idle_timeout" if stats["headersReceived"] else "first_response_timeout"
            message = "模型响应流长时间未收到数据，已停止等待。" if stats["headersReceived"] else "等待模型首个响应超时。"
            error = LLMRequestError(message, True, code)
        elif isinstance(exc, (httpx.WriteTimeout, httpx.PoolTimeout)):
            error = LLMRequestError("模型接口发送请求或获取连接超时。", True, "connect_timeout")
        elif isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            error = LLMRequestError(f"模型接口 HTTP {status}", status in {408, 429, 500, 502, 503, 504}, "http_error")
        elif isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError)) and stats["headersReceived"]:
            error = LLMRequestError("模型响应流中断，未采用部分结果。", True, "incomplete_stream")
        elif isinstance(exc, (httpx.RequestError, OSError)):
            code, message = _network_error(exc)
            error = LLMRequestError(message, code != "tls_error", code)
        else:
            error = LLMRequestError("模型接口返回格式不符合预期。", code="invalid_response")
        stats["errorCode"] = error.code
        progress("error", code=error.code, error_type=type(exc).__name__)
        if error is exc:
            raise
        # Inner library exceptions may contain request headers or URLs.
        raise error from None
    finally:
        drain_events()
        _record_usage(stats.get("usage"), {
            "elapsedSeconds": round(time.monotonic() - stats["started"], 3),
            "receivedCharacters": stats["receivedCharacters"], "reasoningCharacters": stats["reasoningCharacters"],
            "errorCode": stats.get("errorCode", ""), "httpStatus": stats.get("httpStatus"),
        })


def extract_json_text(text):
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return cleaned
    start_candidates = [idx for idx in [cleaned.find("{"), cleaned.find("[")] if idx >= 0]
    if not start_candidates:
        raise ValueError("LLM 输出中没有 JSON 对象或数组。")
    start = min(start_candidates)
    end_obj = cleaned.rfind("}")
    end_arr = cleaned.rfind("]")
    end = max(end_obj, end_arr)
    if end <= start:
        raise ValueError("LLM 输出中的 JSON 片段不完整。")
    return cleaned[start:end + 1]


def call_llm_json(
    messages,
    api_key=None,
    api_base=None,
    model=None,
    temperature=None,
    timeout=None,
    retries=2,
    required_keys=None,
    validator=None,
    *,
    connect_timeout_s=None,
    read_timeout_s=None,
    total_timeout_s=None,
    event_callback=None,
):
    limits = _timeout_options(timeout, connect_timeout_s, read_timeout_s, total_timeout_s)
    started = time.monotonic()
    deadline = started + limits["total_timeout_s"]
    required_keys = required_keys or []
    reset_llm_usage()
    last_error = None
    json_messages = [
        *messages,
        {
            "role": "system",
            "content": (
                "你必须只输出合法 JSON，不要输出 Markdown 代码块、解释文字或额外前后缀。"
                "如果字段未知，使用空数组、空字符串或 null。"
            ),
        },
    ]
    attempts = min(3, max(1, retries + 1))
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = LLMRequestError("模型分析达到总时限，已停止重试。", code="total_timeout")
            break

        def progress(event):
            _notify(event_callback, **{**event, "attempt": attempt + 1,
                                      "elapsedSeconds": round(time.monotonic() - started, 3)})

        try:
            raw = call_llm(
                json_messages,
                api_key=api_key,
                api_base=api_base,
                model=model,
                temperature=temperature,
                connect_timeout_s=limits["connect_timeout_s"],
                read_timeout_s=limits["read_timeout_s"],
                total_timeout_s=remaining,
                event_callback=progress,
            )
            data = json.loads(extract_json_text(raw))
            if not isinstance(data, dict):
                raise ValueError("LLM 输出必须是 JSON 对象。")
            missing = [key for key in required_keys if key not in data]
            if missing:
                raise ValueError("LLM JSON 缺少必要字段：" + "、".join(missing))
            if validator:
                validator(data)
            if time.monotonic() >= deadline:
                raise LLMRequestError("模型分析达到总时限，已停止处理。", code="total_timeout")
            return data, raw
        except LLMRequestError as exc:
            last_error = exc
            if not exc.retryable or attempt + 1 >= attempts:
                break
            delay = 2 ** attempt
            if deadline - time.monotonic() <= delay:
                last_error = LLMRequestError("剩余总时限不足以进行下一次重试。", code="total_timeout")
                break
            progress({"phase": "retry", "code": exc.code, "error_type": type(exc).__name__})
            time.sleep(delay)
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            progress({"phase": "retry", "code": "validation_error", "error_type": type(exc).__name__})
            json_messages.append(
                {
                    "role": "user",
                    "content": "上一轮 JSON 结构或字段校验失败，请按完整字段要求重新输出一个合法 JSON 对象。",
                }
            )
    code = last_error.code if isinstance(last_error, LLMRequestError) else "validation_error"
    # Do not echo validators, raw output, request URLs, or provider error text.
    message = str(last_error) if isinstance(last_error, LLMRequestError) else "模型 JSON 结构或字段校验失败。"
    _notify(event_callback, phase="error", attempt=attempt + 1, code=code,
            elapsedSeconds=round(time.monotonic() - started, 3), error_type="LLMRequestError")
    raise LLMRequestError(f"结构化 LLM 调用失败：{message}", code=code) from None
