import json
import os
import re
import urllib.request


def resolve_llm_config(api_key=None, api_base=None, model=None, temperature=None):
    key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base = api_base or os.environ.get("LLM_API_BASE") or "https://poolrouter.com/v1"
    mdl = model or os.environ.get("LLM_MODEL") or "gemini-3.5-flash"
    temp = 0.6 if temperature is None else float(temperature)
    return key, base, mdl, temp


def has_llm_key(api_key=None):
    key, _, _, _ = resolve_llm_config(api_key=api_key)
    return bool(key)


def call_llm(messages, api_key=None, api_base=None, model=None, temperature=None, timeout=45):
    """
    通用 OpenAI 兼容接口调用逻辑。
    密钥只允许来自参数或环境变量，不能在代码中硬编码。
    """
    key, base, mdl, temp = resolve_llm_config(api_key, api_base, model, temperature)
    if not key:
        raise RuntimeError("未配置 LLM API Key。请设置 LLM_API_KEY/OPENAI_API_KEY 或在 config.local.json 中配置。")

    url = f"{base.rstrip('/')}/chat/completions"

    payload = {
        "model": mdl,
        "messages": messages,
        "temperature": temp
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"调用 AI 专家接口失败: {str(e)}")


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
    timeout=45,
    retries=2,
    required_keys=None,
):
    required_keys = required_keys or []
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
    for attempt in range(max(1, retries + 1)):
        try:
            raw = call_llm(
                json_messages,
                api_key=api_key,
                api_base=api_base,
                model=model,
                temperature=temperature,
                timeout=timeout,
            )
            data = json.loads(extract_json_text(raw))
            missing = [key for key in required_keys if isinstance(data, dict) and key not in data]
            if missing:
                raise ValueError("LLM JSON 缺少必要字段：" + "、".join(missing))
            return data, raw
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            json_messages.append(
                {
                    "role": "user",
                    "content": f"上一轮输出解析失败：{exc}。请重新只输出符合要求的 JSON。",
                }
            )
    raise RuntimeError(f"结构化 LLM 调用失败：{last_error}")
