"""Provider-aware HTTP client used by the model test console."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Callable, Iterator

import requests

from services.proxy import build_proxy_map, request_verify_ssl


class ModelTestError(RuntimeError):
    pass


class ModelTestClient:
    def stream_test(
        self,
        provider: dict,
        model: dict,
        prompt: str,
        system_prompt: str = "",
        stream: bool = True,
        cancelled: Callable[[], bool] | None = None,
        proxy_settings: dict | None = None,
    ) -> Iterator[str]:
        cancelled = cancelled or (lambda: False)
        if not provider.get("api_key"):
            raise ModelTestError("该厂家尚未配置 API Key")
        if not provider.get("enabled"):
            raise ModelTestError("该 API 厂家当前处于停用状态")
        if not model.get("enabled"):
            raise ModelTestError("该模型当前处于停用状态")
        if not prompt.strip():
            raise ModelTestError("测试内容不能为空")

        model_type = model["model_type"]
        if model_type in {"llm", "vision"}:
            protocol = provider["api_protocol"]
            if protocol in {"openai", "openai_compatible"}:
                yield from self._openai_chat(provider, model, prompt, system_prompt, stream, cancelled, proxy_settings)
            elif protocol == "anthropic":
                yield from self._anthropic_chat(provider, model, prompt, system_prompt, stream, cancelled, proxy_settings)
            elif protocol == "gemini":
                yield from self._gemini_chat(provider, model, prompt, system_prompt, stream, cancelled, proxy_settings)
            else:
                raise ModelTestError("自定义 HTTP 协议尚未定义请求格式")
        elif model_type == "embedding":
            yield self._embedding(provider, model, prompt, proxy_settings)
        elif model_type == "rerank":
            yield self._rerank(provider, model, prompt, proxy_settings)
        else:
            raise ModelTestError(f"不支持的模型类型：{model_type}")

    @staticmethod
    def _headers(provider: dict) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        if provider.get("organization"):
            headers["OpenAI-Organization"] = provider["organization"]
        return headers

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        if response.ok:
            return
        try:
            detail = response.json()
            text = json.dumps(detail, ensure_ascii=False)
        except (ValueError, requests.RequestException):
            text = response.text
        raise ModelTestError(f"HTTP {response.status_code}: {text[:800]}")

    def _openai_chat(self, provider: dict, model: dict, prompt: str, system_prompt: str, stream: bool, cancelled: Callable[[], bool], proxy_settings: dict | None) -> Iterator[str]:
        url = provider["base_url"].rstrip("/") + "/chat/completions"
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": prompt.strip()})
        payload = dict(model.get("extra_json") or {})
        payload.update({"model": model["model_id"], "messages": messages, "stream": stream})
        if model.get("temperature") is not None:
            payload.setdefault("temperature", model["temperature"])
        with self._post(url, proxy_settings, headers=self._headers(provider), json=payload, stream=stream, timeout=(10, provider["timeout_seconds"])) as response:
            self._raise_for_status(response)
            if not stream:
                data = response.json()
                yield self._openai_content(data)
                return
            for data in self._sse_json(response, cancelled):
                choices = data.get("choices") or []
                if not choices:
                    continue
                content = choices[0].get("delta", {}).get("content")
                if isinstance(content, str):
                    yield content
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("text"):
                            yield str(part["text"])

    @staticmethod
    def _openai_content(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise ModelTestError("响应中没有 choices 内容")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content)

    def _anthropic_chat(self, provider: dict, model: dict, prompt: str, system_prompt: str, stream: bool, cancelled: Callable[[], bool], proxy_settings: dict | None) -> Iterator[str]:
        base = provider["base_url"].rstrip("/")
        url = base + "/messages" if base.endswith("/v1") else base + "/v1/messages"
        headers = {
            "x-api-key": provider["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "text/event-stream, application/json",
        }
        payload = dict(model.get("extra_json") or {})
        payload.update({"model": model["model_id"], "max_tokens": payload.get("max_tokens", 1024), "messages": [{"role": "user", "content": prompt.strip()}], "stream": stream})
        if system_prompt.strip():
            payload["system"] = system_prompt.strip()
        if model.get("temperature") is not None:
            payload.setdefault("temperature", model["temperature"])
        with self._post(url, proxy_settings, headers=headers, json=payload, stream=stream, timeout=(10, provider["timeout_seconds"])) as response:
            self._raise_for_status(response)
            if not stream:
                data = response.json()
                yield "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
                return
            for data in self._sse_json(response, cancelled):
                if data.get("type") == "content_block_delta":
                    text = data.get("delta", {}).get("text")
                    if text:
                        yield text

    def _gemini_chat(self, provider: dict, model: dict, prompt: str, system_prompt: str, stream: bool, cancelled: Callable[[], bool], proxy_settings: dict | None) -> Iterator[str]:
        base = provider["base_url"].rstrip("/") or "https://generativelanguage.googleapis.com/v1beta"
        action = "streamGenerateContent?alt=sse" if stream else "generateContent"
        url = f"{base}/models/{model['model_id']}:{action}"
        headers = {"x-goog-api-key": provider["api_key"], "content-type": "application/json", "accept": "text/event-stream, application/json"}
        payload = dict(model.get("extra_json") or {})
        payload["contents"] = [{"role": "user", "parts": [{"text": prompt.strip()}]}]
        if system_prompt.strip():
            payload["system_instruction"] = {"parts": [{"text": system_prompt.strip()}]}
        with self._post(url, proxy_settings, headers=headers, json=payload, stream=stream, timeout=(10, provider["timeout_seconds"])) as response:
            self._raise_for_status(response)
            if not stream:
                yield self._gemini_content(response.json())
                return
            for data in self._sse_json(response, cancelled):
                text = self._gemini_content(data, required=False)
                if text:
                    yield text

    @staticmethod
    def _gemini_content(data: dict, required: bool = True) -> str:
        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(str(part.get("text", "")) for part in parts)
        if required and not text:
            raise ModelTestError("响应中没有可显示的文本")
        return text

    def _embedding(self, provider: dict, model: dict, prompt: str, proxy_settings: dict | None) -> str:
        if provider["api_protocol"] not in {"openai", "openai_compatible"}:
            raise ModelTestError("当前仅支持通过 OpenAI 兼容接口测试向量模型")
        url = provider["base_url"].rstrip("/") + "/embeddings"
        payload = dict(model.get("extra_json") or {})
        payload.update({"model": model["model_id"], "input": prompt.strip()})
        with self._post(url, proxy_settings, headers=self._headers(provider), json=payload, timeout=(10, provider["timeout_seconds"])) as response:
            self._raise_for_status(response)
            data = response.json()
        vectors = data.get("data") or []
        if not vectors:
            raise ModelTestError("响应中没有向量数据")
        vector = vectors[0].get("embedding") or []
        preview = vector[:24]
        return f"向量维度：{len(vector)}\n前 {len(preview)} 维：\n" + json.dumps(preview, ensure_ascii=False)

    def _rerank(self, provider: dict, model: dict, prompt: str, proxy_settings: dict | None) -> str:
        if provider["api_protocol"] not in {"openai", "openai_compatible"}:
            raise ModelTestError("当前仅支持通过兼容接口测试排序模型")
        lines = [line.strip() for line in prompt.splitlines() if line.strip()]
        if len(lines) < 3:
            raise ModelTestError("排序测试需要至少三行：第一行为查询，后续每行是一篇候选文档")
        url = provider["base_url"].rstrip("/") + "/rerank"
        payload = dict(model.get("extra_json") or {})
        payload.update({"model": model["model_id"], "query": lines[0], "documents": lines[1:]})
        with self._post(url, proxy_settings, headers=self._headers(provider), json=payload, timeout=(10, provider["timeout_seconds"])) as response:
            self._raise_for_status(response)
            return json.dumps(response.json(), ensure_ascii=False, indent=2)

    @staticmethod
    def _network_options(proxy_settings: dict | None, url: str) -> dict:
        return {
            "proxies": build_proxy_map(proxy_settings, url, "model"),
            "verify": request_verify_ssl(proxy_settings, "model"),
        }

    @contextmanager
    def _post(self, url: str, proxy_settings: dict | None, **kwargs):
        # Do not silently inherit environment/system proxy variables. Every
        # request follows the explicit AutoAgent proxy settings instead.
        with requests.Session() as session:
            session.trust_env = False
            with session.post(url, **kwargs, **self._network_options(proxy_settings, url)) as response:
                yield response

    @staticmethod
    def _sse_json(response: requests.Response, cancelled: Callable[[], bool]) -> Iterator[dict]:
        for raw_line in response.iter_lines(decode_unicode=True):
            if cancelled():
                return
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue
