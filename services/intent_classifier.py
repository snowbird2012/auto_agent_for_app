"""Prompt construction and strict result parsing for bulk intent screening."""

from __future__ import annotations

import json
import re
from typing import Any


SYSTEM_PROMPT = """你是用户意向判断器。请依据用户标签、用户名和全部留言，严格执行用户给出的判断规则。
每个输入用户必须且只能返回一条结果。只输出 JSON，不要输出 Markdown 或解释。
输出格式：{"results":[{"id":1,"intent":true,"reason":"简短原因"}]}。
intent 只能是 true 或 false。"""


def build_intent_prompt(rule_prompt: str, users: list[dict[str, Any]]) -> str:
    if not rule_prompt.strip():
        raise ValueError("意向判断提示词不能为空")
    payload = [
        {
            "id": int(user["id"]),
            "username": user.get("username", ""),
            "handle": user.get("handle", ""),
            "tags": user.get("tags", []),
            "comments": [item.get("comment", "") for item in user.get("comments", [])],
        }
        for user in users
    ]
    return (
        f"判断规则：\n{rule_prompt.strip()}\n\n"
        "待判断用户（必须逐一返回）：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def parse_intent_result(text: str, expected_ids: set[int]) -> tuple[list[int], list[int]]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise ValueError(f"模型返回的 JSON 无法解析：{error}") from error
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise ValueError("模型返回缺少 results 数组")
    decisions: dict[int, bool] = {}
    for item in results:
        if not isinstance(item, dict) or "id" not in item or "intent" not in item:
            raise ValueError("模型结果项必须包含 id 和 intent")
        try:
            user_id = int(item["id"])
        except (TypeError, ValueError) as error:
            raise ValueError("模型结果中的 id 无效") from error
        intent = item["intent"]
        if type(intent) is not bool:
            raise ValueError(f"用户 {user_id} 的 intent 必须是布尔值")
        if user_id in decisions:
            raise ValueError(f"模型重复返回用户 {user_id}")
        decisions[user_id] = intent
    returned_ids = set(decisions)
    if returned_ids != expected_ids:
        missing = sorted(expected_ids - returned_ids)
        extra = sorted(returned_ids - expected_ids)
        raise ValueError(f"模型结果不完整：缺失 {missing}，多余 {extra}")
    intent_ids = [user_id for user_id in sorted(expected_ids) if decisions[user_id]]
    non_intent_ids = [user_id for user_id in sorted(expected_ids) if not decisions[user_id]]
    return intent_ids, non_intent_ids

