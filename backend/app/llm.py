"""大模型调用层。

只做两件事：
1. 从环境变量解析模型配置（OpenAI 兼容接口 / 本地 Ollama）；
2. 发一次对话请求并把纯文本结果返回给上层。

业务规则、JSON 结构校验都由 clip_writer 负责，这里保持薄。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

import httpx


class LlmError(RuntimeError):
    """模型不可用、请求失败或返回为空时抛出，message 使用中文。"""


@dataclass(frozen=True)
class LlmConfig:
    """一次调用所需的模型参数。"""

    provider: str  # openai / ollama
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 180.0
    temperature: float = 0.2


# 同一个进程只探测一次 Ollama 模型列表，避免每次请求都多打一次接口
_ollama_model_cache: dict[str, str] = {}

# 项目根目录的 .env，本地放密钥用；不引入额外依赖
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_dotenv(path: Path = _ENV_FILE) -> None:
    """读取简单的 KEY=VALUE 配置，已存在的环境变量优先。"""

    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _discover_ollama_model(base_url: str) -> str:
    """从本地 Ollama 的模型列表里挑一个可用的对话模型。"""

    cached = _ollama_model_cache.get(base_url)
    if cached:
        return cached

    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        response = httpx.get(url, timeout=3.0)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LlmError(
            "无法连接本地 Ollama（{url}）。请先执行 ollama serve，"
            "或设置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 改用其他模型服务。".format(
                url=base_url
            )
        ) from exc

    models = [
        str(item.get("name", ""))
        for item in data.get("models", [])
        if item.get("name")
    ]
    # 向量模型不能做对话，直接排除
    chat_models = [name for name in models if "embed" not in name.lower()]
    if not chat_models:
        raise LlmError(
            "本地 Ollama 没有可用的对话模型。请先执行 "
            "`ollama pull qwen2.5:7b`，或设置 LLM_MODEL 指定已安装的模型。"
        )

    model = chat_models[0]
    _ollama_model_cache[base_url] = model
    return model


def load_config() -> LlmConfig:
    """读取环境变量，返回可用的模型配置。

    支持的环境变量：
    - LLM_PROVIDER：openai 或 ollama，默认按是否配置 key 自动判断
    - LLM_BASE_URL：OpenAI 兼容地址（一般以 /v1 结尾）或 Ollama 地址
    - LLM_API_KEY：OpenAI 兼容服务的密钥
    - LLM_MODEL：模型名
    - LLM_TIMEOUT：单次请求超时秒数，默认 180
    - LLM_TEMPERATURE：采样温度，默认 0.2
    """

    api_key = _env("LLM_API_KEY")
    base_url = _env("LLM_BASE_URL")
    model = _env("LLM_MODEL")
    provider = _env("LLM_PROVIDER").lower()

    if not provider:
        provider = "openai" if (api_key or base_url) else "ollama"
    if provider not in {"openai", "ollama"}:
        raise LlmError("LLM_PROVIDER 只能是 openai 或 ollama")

    try:
        timeout = float(_env("LLM_TIMEOUT", "180"))
    except ValueError as exc:
        raise LlmError("LLM_TIMEOUT 必须是数字（单位：秒）") from exc
    try:
        temperature = float(_env("LLM_TEMPERATURE", "0.2"))
    except ValueError as exc:
        raise LlmError("LLM_TEMPERATURE 必须是数字") from exc

    if provider == "ollama":
        base_url = base_url or "http://127.0.0.1:11434"
        model = model or _discover_ollama_model(base_url)
        return LlmConfig(
            provider="ollama",
            base_url=base_url,
            model=model,
            timeout=timeout,
            temperature=temperature,
        )

    if not model:
        raise LlmError(
            "使用 OpenAI 兼容接口时必须设置 LLM_MODEL（例如 gpt-4o-mini、"
            "deepseek-chat、qwen-plus）。"
        )
    if not base_url:
        base_url = "https://api.openai.com/v1"
    return LlmConfig(
        provider="openai",
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=timeout,
        temperature=temperature,
    )


def describe() -> dict:
    """给前端/健康检查用的配置摘要，不暴露密钥，也不发请求。"""

    try:
        config = load_config()
    except LlmError as exc:
        return {
            "configured": False,
            "provider": _env("LLM_PROVIDER") or "auto",
            "model": _env("LLM_MODEL") or None,
            "base_url": _env("LLM_BASE_URL") or None,
            "error": str(exc),
        }
    return {
        "configured": True,
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "error": None,
    }


# Cloudflare 网关超时的状态码：上游长时间不吐数据就会被掐断
GATEWAY_TIMEOUT_CODES = (520, 521, 522, 523, 524)


def _is_html(text: str) -> bool:
    """判断响应体是不是网关返回的 HTML 错误页（对老板没有任何可读信息）。"""

    head = (text or "")[:300].lstrip().lower()
    return head.startswith("<!doctype") or head.startswith("<html") or "<html" in head


def _error_detail(body_text: str) -> str:
    """从错误响应体里提取一句可读的原因，HTML 错误页直接丢弃。"""

    text = (body_text or "").strip()
    if not text or _is_html(text):
        return ""
    try:
        data = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")[:300]
        if error:
            return str(error)[:300]
        return str(data.get("message") or "")[:300]
    return ""


def _http_error_message(status_code: int, body_text: str = "") -> str:
    """把常见 HTTP 错误翻译成老板能看懂的中文。"""

    detail = _error_detail(body_text)

    if status_code in (401, 403):
        hint = "鉴权失败，请检查 LLM_API_KEY 是否正确。"
    elif status_code == 404:
        hint = "接口地址不存在，请检查 LLM_BASE_URL（OpenAI 兼容地址通常以 /v1 结尾）。"
    elif status_code == 429:
        hint = "模型服务限流，请稍后重试。"
    elif status_code in GATEWAY_TIMEOUT_CODES:
        hint = (
            f"模型网关超时（HTTP {status_code}）：上游连续约 100 秒没返回任何数据，"
            "请求被网关掐断，不是模型本身报错。程序会优先用流式请求避免超时；"
            "如果仍然出现，请减少字幕条数（分段上传）或换响应更快的模型。"
        )
    elif status_code == 504:
        hint = "模型网关超时（HTTP 504）：上游没有及时响应，请稍后重试或换更快的模型。"
    elif status_code >= 500:
        hint = f"模型服务内部错误（HTTP {status_code}），请稍后重试。"
    else:
        hint = f"模型服务返回 HTTP {status_code}。"
    if detail:
        hint = f"{hint}（{detail}）"
    return hint


def _sse_content(lines) -> str:
    """从流式响应的 SSE 行里拼出完整正文。"""

    parts: list[str] = []
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        choices = data.get("choices") or []
        if not choices:
            continue
        delta = (choices[0].get("delta") or {}).get("content")
        if delta:
            parts.append(str(delta))
    return "".join(parts)


def _openai_request(
    url: str,
    headers: dict,
    payload: dict,
    config: LlmConfig,
    stream: bool,
) -> tuple[int, str, str]:
    """发一次 OpenAI 兼容请求，返回 (状态码, 正文, 原始错误文本)。

    stream=True 时边收边拼，避免网关“长时间没数据”判超时（Cloudflare 524）。
    """

    # connect 单独限时；read 是“两个数据块之间的最长等待”，流式下不会误伤长输出
    timeout = httpx.Timeout(config.timeout, connect=15.0)

    if not stream:
        response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code >= 400:
            return response.status_code, "", response.text
        try:
            data = response.json()
            return 200, str(data["choices"][0]["message"]["content"]), ""
        except (ValueError, KeyError, IndexError, TypeError):
            return 200, "", "模型返回结构异常，没有拿到 message.content"

    with httpx.stream("POST", url, headers=headers, json=payload, timeout=timeout) as response:
        if response.status_code >= 400:
            return response.status_code, "", response.read().decode("utf-8", "replace")
        return 200, _sse_content(response.iter_lines()), ""


def _post_openai(messages: list[dict], config: LlmConfig) -> str:
    base = config.base_url.rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
    }
    # 大多数兼容服务支持 JSON 模式；个别服务不支持，失败后去掉再试一次
    json_mode = _env("LLM_JSON_MODE", "on").lower() not in {"0", "off", "false"}
    # 默认走流式：上游网关约 100 秒没数据就会返回 524，流式能持续吐数据躲开它
    use_stream = _env("LLM_STREAM", "on").lower() not in {"0", "off", "false"}

    # 参数组合按“越省事越先试”的顺序：先带 response_format、再退回普通请求体；
    # 每个组合都先试流式，再退回非流式（兼容不支持 stream 的服务）。
    payloads = [body]
    if json_mode:
        payloads.insert(0, {**body, "response_format": {"type": "json_object"}})
    attempts: list[tuple[dict, bool]] = []
    if use_stream:
        # 流式优先，避免网关“长时间无数据”判超时；再退回非流式兼容不支持 stream 的服务
        attempts += [(payload, True) for payload in payloads]
    attempts += [(payload, False) for payload in payloads]

    last_status = 0
    last_error = ""
    # 网络抖动（对端中途断开、读到一半 EOF）时允许整体重试一次
    for round_index in range(2):
        try:
            for payload, stream in attempts:
                if stream:
                    payload = {**payload, "stream": True}
                status, content, error = _openai_request(
                    url, headers, payload, config, stream
                )

                if status < 400:
                    if content.strip():
                        return content
                    # 200 但没拿到正文（例如网关返回了非 SSE 的空体）：记下来，继续尝试下一种组合
                    last_status, last_error = status, error or "模型返回了空内容"
                    continue

                # 参数不被支持时换下一种组合；其余错误（鉴权/限流/网关超时）没必要重试
                if status in (400, 404, 422):
                    last_status, last_error = status, error
                    continue
                raise LlmError(_http_error_message(status, error))
        except httpx.TimeoutException as exc:
            raise LlmError(
                f"模型 {config.model} 在 {config.timeout:.0f} 秒内没有返回，"
                "可以调大 LLM_TIMEOUT 或换更小的模型。"
            ) from exc
        except httpx.ConnectError as exc:
            raise LlmError(
                f"无法连接模型服务 {config.base_url}：{exc}。"
                "请检查网络和 LLM_BASE_URL 是否正确。"
            ) from exc
        except httpx.HTTPError as exc:
            # 已经连上、但响应中途断了（例如 incomplete chunked read）：换一轮重试
            if round_index == 0:
                continue
            raise LlmError(
                "模型服务响应中途断开了（网络不稳或上游主动断开），"
                "已自动重试仍失败。请稍后重试，或换响应更稳的模型。"
            ) from exc

    raise LlmError(_http_error_message(last_status, last_error))


def _post_ollama(messages: list[dict], config: LlmConfig) -> str:
    base = config.base_url.rstrip("/")
    url = f"{base}/api/chat"
    body = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": config.temperature},
    }
    try:
        response = httpx.post(url, json=body, timeout=config.timeout)
    except httpx.TimeoutException as exc:
        raise LlmError(
            f"本地模型 {config.model} 在 {config.timeout:.0f} 秒内没有返回，"
            "可以调大 LLM_TIMEOUT 或换更小的模型。"
        ) from exc
    except httpx.HTTPError as exc:
        raise LlmError(
            f"无法连接本地 Ollama（{config.base_url}）。请确认 ollama serve 正在运行。"
        ) from exc

    if response.status_code >= 400:
        raise LlmError(_http_error_message(response.status_code, response.text))

    try:
        data = response.json()
        return str(data["message"]["content"])
    except (ValueError, KeyError, TypeError) as exc:
        raise LlmError("Ollama 返回结构异常，没有拿到 message.content") from exc


def chat(messages: list[dict], config: LlmConfig | None = None) -> str:
    """发一次对话请求，返回模型输出的原始文本。"""

    resolved = config or load_config()
    content = (
        _post_ollama(messages, resolved)
        if resolved.provider == "ollama"
        else _post_openai(messages, resolved)
    )
    if not content.strip():
        raise LlmError("模型返回了空内容，请重试或更换模型。")
    return content
