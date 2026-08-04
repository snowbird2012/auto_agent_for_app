"""Run a message strategy and enforce its structured reply contract."""
from __future__ import annotations
import json, re
from typing import Callable
from services.model_test_client import ModelTestClient

OUTPUT_RULE = """
必须只输出一个 JSON 对象，不要输出 Markdown 或解释。格式严格为：
{"need_reply": true, "content": "需要发送的回复"}
或：
{"need_reply": false, "content": ""}
""".strip()


class MessageStrategyService:
    def evaluate(self, strategy: dict, model: dict, provider: dict, user_message: str,
                 proxy_settings: dict, cancelled: Callable[[], bool] | None = None) -> dict:
        system_prompt = str(strategy["system_prompt"]).strip() + "\n\n" + OUTPUT_RULE
        prompt = f"请分析下面收到的用户消息，并决定是否回复：\n\n{user_message.strip()}"
        raw = "".join(ModelTestClient().stream_test(
            provider, model, prompt, system_prompt, stream=False,
            cancelled=cancelled, proxy_settings=proxy_settings,
        ))
        return self.parse(raw)

    @staticmethod
    def parse(raw: str) -> dict:
        text=str(raw).strip()
        fenced=re.fullmatch(r"```(?:json)?\s*(.*?)\s*```",text,re.I|re.S)
        if fenced:text=fenced.group(1).strip()
        try:data=json.loads(text)
        except json.JSONDecodeError as error: raise ValueError(f"模型未返回有效 JSON：{error}") from error
        if not isinstance(data,dict) or type(data.get("need_reply")) is not bool:
            raise ValueError("结构化输出必须包含布尔字段 need_reply")
        if "content" not in data:
            raise ValueError("结构化输出必须包含字段 content")
        content=data["content"]
        if not isinstance(content,str): raise ValueError("结构化输出字段 content 必须是字符串")
        content=content.strip()
        if data["need_reply"] and not content: raise ValueError("need_reply=true 时 content 不能为空")
        return {"need_reply":data["need_reply"],"content":content}
