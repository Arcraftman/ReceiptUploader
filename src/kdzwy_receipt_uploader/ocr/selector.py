"""OpenAI-compatible template selection client."""
from __future__ import annotations

import copy
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from .models import OcrPipelineError


class OpenAICompatibleTemplateSelector:
    """Call a configured OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        api_key: str | None,
        endpoint: str,
        model: str = "qwen3.7-flash",
        timeout: int = 60,
        *,
        api_key_env: str = "DASHSCOPE_API_KEY",
        provider_name: str = "Qwen",
        enable_thinking: bool = False,
    ) -> None:
        self.api_key = api_key or ""
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.api_key_env = api_key_env
        self.provider_name = provider_name
        self.enable_thinking = enable_thinking

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> "OpenAICompatibleTemplateSelector":
        raw = settings.get("llm", {})
        if not isinstance(raw, Mapping):
            raise OcrPipelineError("pipeline.llm 必须是 JSON 对象")
        provider_name = str(raw.get("provider_name", "Qwen")).strip() or "Qwen"
        model = str(raw.get("model", "qwen3.7-flash")).strip()
        endpoint = str(
            raw.get(
                "endpoint",
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            )
        ).strip()
        api_key_env = str(raw.get("api_key_env", "DASHSCOPE_API_KEY")).strip()
        timeout = raw.get("timeout_seconds", 60)
        enable_thinking = raw.get("enable_thinking", False)
        if not model:
            raise OcrPipelineError("pipeline.llm.model 不能为空")
        if not endpoint.startswith("https://"):
            raise OcrPipelineError("pipeline.llm.endpoint 必须使用 https://")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", api_key_env):
            raise OcrPipelineError("pipeline.llm.api_key_env 必须是大写环境变量名")
        if not isinstance(enable_thinking, bool):
            raise OcrPipelineError("pipeline.llm.enable_thinking 必须是 JSON 布尔值")
        try:
            timeout_value = int(timeout)
        except (TypeError, ValueError) as exc:
            raise OcrPipelineError("pipeline.llm.timeout_seconds 必须是正整数") from exc
        if timeout_value <= 0:
            raise OcrPipelineError("pipeline.llm.timeout_seconds 必须是正整数")
        return cls(
            os.environ.get(api_key_env),
            endpoint,
            model,
            timeout_value,
            api_key_env=api_key_env,
            provider_name=provider_name,
            enable_thinking=enable_thinking,
        )

    def choose(self, ocr_text: str, templates: list[Mapping[str, Any]], invoice_code: str = "", final_template_context: Mapping[str, Any] | None = None, business_rules: str = "", verified_memory: list[Mapping[str, Any]] | None = None, prompt_path: Path | None = None) -> dict[str, Any]:
        if not self.api_key:
            return {"status": f"待提供{self.provider_name} API", "invoiceCode": invoice_code, "templatePath": "", "confidence": 0, "reason": f"未配置 {self.api_key_env}", "raw": None, "textLength": len(ocr_text), "selectionMode": "llm_not_called", "llmAttempted": False, "llmProvider": self.provider_name, "llmModel": self.model}
        catalog = [{key: value for key, value in item.items() if key in {"id", "name", "path", "decisionCode", "decisionName", "documentBlock", "documentType", "settlementMethod", "businessType", "currency", "keywords", "summary", "entries", "matchRules", "amountSource", "bankAccountNumber"}} for item in templates]
        # Dynamic account/item catalogs are used by local validation and entry
        # rendering, not by the classifier.  Sending them to Qwen made the
        # request unnecessarily large and caused opaque HTTP 400 responses.
        sample = {
            key: copy.deepcopy(value)
            for key, value in dict(final_template_context or {}).items()
            if key
            not in {
                "dynamicAccountCatalog",
                "dynamicItemClassCatalog",
                "runtimeAccountMeta",
            }
        }
        if prompt_path is None or not prompt_path.is_file():
            return {
                "status": "error",
                "invoiceCode": invoice_code,
                "templatePath": "",
                "confidence": 0,
                "reason": "缺少当前公司的模板分类提示词",
                "raw": None,
                "textLength": len(ocr_text),
            }
        prompt = prompt_path.read_text(encoding="utf-8")
        replacements = {
            "<<BUSINESS_RULES>>": business_rules,
            "<<VERIFIED_MEMORY>>": json.dumps(list(verified_memory or [])[-25:], ensure_ascii=False),
            "<<INVOICE_CODE>>": invoice_code,
            "<<OCR_TEXT>>": ocr_text[:30000],
            "<<TEMPLATE_CATALOG>>": json.dumps(catalog, ensure_ascii=False),
            "<<FINAL_CONTEXT>>": json.dumps(sample, ensure_ascii=False),
        }
        for token, value in replacements.items():
            prompt = prompt.replace(token, value)
        body = {
            "model": self.model,
            "temperature": 0,
            "enable_thinking": self.enable_thinking,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你只负责严格分类和字段提取，只返回 JSON 对象，不生成会计分录。"},
                {"role": "user", "content": prompt},
            ],
        }
        request_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        retryable_http_codes = {408, 409, 429, 500, 502, 503, 504}
        for attempt in range(3):
            request = urllib.request.Request(
                self.endpoint,
                data=request_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                content = payload["choices"][0]["message"]["content"]
                parsed = _parse_json_object(content)
                parsed["status"] = "success"
                parsed["invoiceCode"] = invoice_code
                parsed["raw"] = payload
                parsed["textLength"] = len(ocr_text)
                parsed["selectionMode"] = "llm_api"
                parsed["llmAttempted"] = True
                parsed["llmProvider"] = self.provider_name
                parsed["llmModel"] = self.model
                parsed["llmRequestId"] = str(payload.get("id") or payload.get("request_id") or "")
                return parsed
            except urllib.error.HTTPError as exc:
                try:
                    error_body = exc.read().decode("utf-8", errors="replace").strip()
                except OSError:
                    error_body = ""
                if exc.code in retryable_http_codes and attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                detail = error_body or str(exc.reason or exc)
                return {
                    "status": "error",
                    "invoiceCode": invoice_code,
                    "templatePath": "",
                    "confidence": 0,
                    "reason": f"HTTP {exc.code}: {detail}",
                    "raw": {"httpStatus": exc.code, "errorBody": error_body},
                    "textLength": len(ocr_text),
                    "selectionMode": "llm_api_error",
                    "llmAttempted": True,
                    "llmProvider": self.provider_name,
                    "llmModel": self.model,
                }
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                return {"status": "error", "invoiceCode": invoice_code, "templatePath": "", "confidence": 0, "reason": str(exc), "raw": None, "textLength": len(ocr_text), "selectionMode": "llm_api_error", "llmAttempted": True, "llmProvider": self.provider_name, "llmModel": self.model}
            except (KeyError, IndexError, json.JSONDecodeError, TypeError, ValueError) as exc:
                return {"status": "error", "invoiceCode": invoice_code, "templatePath": "", "confidence": 0, "reason": str(exc), "raw": None, "textLength": len(ocr_text), "selectionMode": "llm_api_error", "llmAttempted": True, "llmProvider": self.provider_name, "llmModel": self.model}
        raise AssertionError("Qwen retry loop exited unexpectedly")


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("模型返回不是JSON对象")
    return value
