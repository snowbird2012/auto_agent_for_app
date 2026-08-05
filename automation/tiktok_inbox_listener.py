"""TikTok inbox unread-message listener validated against a real device."""

from __future__ import annotations

from time import monotonic, sleep
import unicodedata

import uiautomator2 as u2

from automation.tiktok_message_workflow import TikTokMessageWorkflow
from automation.tiktok_search_workflow import (
    ProgressCallback,
    UINode,
    WorkflowCancelled,
    WorkflowError,
)


class TikTokInboxListener(TikTokMessageWorkflow):
    def listen_for_test(self, progress: ProgressCallback, timeout: float = 60):
        """Bounded listener used only by development/device verification."""
        return self.listen_once(progress, timeout=timeout)
    def listen_once(
        self,
        progress: ProgressCallback,
        timeout: float | None = 60,
    ) -> dict[str, str] | None:
        try:
            self._resolve_package()
            if self.device is None:
                self.device = u2.connect(self.serial)
            self._return_to_tiktok_home()
            self._emit(progress, "LISTEN_INBOX", "正在首页监听收件箱红色角标", 100)
            deadline = (
                monotonic() + max(1, float(timeout))
                if timeout is not None else None
            )
            inbox: UINode | None = None
            while deadline is None or monotonic() < deadline:
                self._check_cancelled()
                nodes = self._nodes()
                inbox = self._inbox_node(nodes)
                if inbox and self._has_unread_badge(nodes, inbox):
                    break
                sleep(2)
            else:
                self._emit(
                    progress,
                    "LISTEN_IDLE",
                    f"监听 {int(timeout or 0)} 秒，收件箱没有出现未读角标",
                    100,
                )
                return None

            self._emit(progress, "INBOX_UNREAD", "检测到收件箱未读角标", 100)
            assert inbox is not None
            self.device.click(*inbox.center)
            self._wait_node(
                lambda item: item.text == "收件箱"
                and item.resource_id.endswith("/title"),
                12,
            )
            sleep(0.8)
            # Double-clicking the active inbox tab refreshes/repositions the list,
            # placing the conversation requiring a response at the first row.
            current_inbox = self._inbox_node(self._nodes()) or inbox
            self.device.double_click(*current_inbox.center, 0.15)
            sleep(1.5)

            self._emit(progress, "OPEN_UNREAD_CHAT", "正在进入第一条未读会话", 100)
            self._open_first_chat_with_retry(progress)

            deadline = monotonic() + 10
            record: dict | None = None
            while monotonic() < deadline:
                nodes = self._nodes()
                width, _ = self.device.window_size()
                messages = self._collect_incoming_messages(nodes, width)
                if messages:
                    sender = self._chat_sender(nodes)
                    record = {
                        "sender": sender,
                        "messages": messages,
                        # Keep the legacy field for callers that have not yet
                        # migrated to batch handling.
                        "message": "\n".join(item["content"] for item in messages),
                    }
                    break
                sleep(0.5)
            if not record:
                raise WorkflowError("进入第一条会话后没有读取到对方消息")
            self._emit(
                progress,
                "INBOX_MESSAGE",
                f"收到消息 | 用户：{record['sender']} | 内容：{record['message']}",
                100,
            )
            return record
        except WorkflowCancelled:
            raise
        except Exception as error:
            evidence = self._save_evidence("inbox_failure")
            suffix = f"；失败截图：{evidence}" if evidence else ""
            if isinstance(error, WorkflowError):
                raise WorkflowError(str(error) + suffix) from error
            raise WorkflowError(f"消息监听失败：{error}{suffix}") from error

    def _open_first_chat_with_retry(self, progress: ProgressCallback) -> None:
        """Retry the first inbox row while a transient message banner covers it."""
        for attempt in range(1, 13):
            self._check_cancelled()
            nodes = self._nodes()
            if self._has_chat_input(nodes):
                return
            first = self._first_conversation_node(nodes)
            if not first:
                if attempt > 1:
                    self._emit(
                        progress,
                        "WAIT_INBOX_POPUP",
                        f"顶部弹窗尚未消失，等待后重试（{attempt}/12）",
                        100,
                    )
                sleep(1)
                continue
            self.device.click(*first.center)
            deadline = monotonic() + 2
            while monotonic() < deadline:
                self._check_cancelled()
                if self._has_chat_input(self._nodes()):
                    return
                sleep(0.25)
            self._emit(
                progress,
                "WAIT_INBOX_POPUP",
                f"第一条会话被弹窗遮挡，准备再次点击（{attempt}/12）",
                100,
            )
            sleep(0.8)
        raise WorkflowError("循环点击第一条会话12次后仍未进入聊天页面")

    @staticmethod
    def _inbox_node(nodes: list[UINode]) -> UINode | None:
        candidates = [
            item for item in nodes
            if item.description == "收件箱" and item.clickable
        ]
        return max(candidates, key=lambda item: item.bounds[1]) if candidates else None

    @staticmethod
    def _has_unread_badge(nodes: list[UINode], inbox: UINode) -> bool:
        left, top, right, bottom = inbox.bounds
        return any(
            item is not inbox
            and item.text.strip().isdigit()
            and item.bounds[0] >= left
            and item.bounds[2] <= right
            and item.bounds[1] >= top
            and item.bounds[3] <= bottom
            for item in nodes
        )

    @staticmethod
    def _first_conversation_node(nodes: list[UINode]) -> UINode | None:
        candidates = [
            item for item in nodes
            if item.clickable
            and item.resource_id.endswith("/v15")
            and 220 <= item.bounds[1] < 1000
        ]
        return min(candidates, key=lambda item: item.bounds[1]) if candidates else None

    @staticmethod
    def _has_chat_input(nodes: list[UINode]) -> bool:
        return any(
            item.class_name == "android.widget.EditText"
            and not item.resource_id.endswith("/hgt")
            for item in nodes
        )

    @staticmethod
    def _latest_incoming(nodes: list[UINode], screen_width: int) -> UINode | None:
        candidates = [
            item for item in nodes
            if item.resource_id.endswith("/kci")
            and item.text.strip()
            and item.center[0] < screen_width // 2
        ]
        return max(candidates, key=lambda item: item.bounds[3]) if candidates else None

    @classmethod
    def _incoming_run(
        cls, nodes: list[UINode], screen_width: int
    ) -> list[dict[str, str]]:
        """Return every consecutive incoming bubble at the bottom of a chat."""
        bubbles = cls._message_bubbles(nodes, screen_width)
        incoming: list[dict[str, str]] = []
        for _, direction, message in reversed(bubbles):
            if direction == "outbound":
                break
            incoming.append(message)
        incoming.reverse()
        return cls._deduplicate_accessibility_children(incoming)

    @classmethod
    def _message_bubbles(
        cls, nodes: list[UINode], screen_width: int
    ) -> list[tuple[int, str, dict[str, str]]]:
        bubbles: list[tuple[int, str, dict[str, str]]] = []
        for item in nodes:
            content = item.text.strip()
            if item.resource_id.endswith("/kci") and content:
                direction = "inbound" if item.center[0] < screen_width // 2 else "outbound"
                bubbles.append((item.bounds[3], direction, {
                    "type": cls._text_message_type(content),
                    "content": content,
                }))
                continue
            media = cls._media_message(item, screen_width)
            if media:
                bubbles.append((item.bounds[3], "inbound", media))

        bubbles.sort(key=lambda value: value[0])
        return bubbles

    @staticmethod
    def _deduplicate_accessibility_children(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        # Do not collapse adjacent equal values: users may legitimately send
        # the same text or emoji multiple times.
        return list(messages)

    def _collect_incoming_messages(
        self, nodes: list[UINode], screen_width: int, max_pages: int = 6
    ) -> list[dict[str, str]]:
        """Scan older chat pages until our latest outbound bubble is reached."""
        collected: list[dict[str, str]] = []
        current_nodes = nodes
        for _ in range(max_pages):
            self._check_cancelled()
            bubbles = self._message_bubbles(current_nodes, screen_width)
            outbound_indexes = [
                index for index, (_, direction, _) in enumerate(bubbles)
                if direction == "outbound"
            ]
            start = (outbound_indexes[-1] + 1) if outbound_indexes else 0
            page = self._deduplicate_accessibility_children([
                message for _, direction, message in bubbles[start:]
                if direction == "inbound"
            ])
            collected = self._merge_message_pages(page, collected)
            if outbound_indexes:
                break

            chat_list = next((
                item for item in current_nodes
                if item.resource_id.endswith("/t70")
                and "RecyclerView" in item.class_name
            ), None)
            if not chat_list:
                break
            before = self._chat_fingerprint(current_nodes, chat_list.bounds)
            left, top, right, bottom = chat_list.bounds
            x = (left + right) // 2
            self.device.swipe(
                x,
                top + max(120, int((bottom - top) * 0.28)),
                x,
                bottom - max(80, int((bottom - top) * 0.08)),
                duration=0.35,
            )
            sleep(0.55)
            next_nodes = self._nodes()
            after = self._chat_fingerprint(next_nodes, chat_list.bounds)
            if after == before:
                break
            current_nodes = next_nodes
        return collected

    @staticmethod
    def _merge_message_pages(
        older_page: list[dict[str, str]], newer_page: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        if not newer_page:
            return older_page
        overlap = 0
        for size in range(min(len(older_page), len(newer_page)), 0, -1):
            if older_page[-size:] == newer_page[:size]:
                overlap = size
                break
        return older_page + newer_page[overlap:]

    @staticmethod
    def _text_message_type(content: str) -> str:
        meaningful = [
            character for character in content
            if not character.isspace()
            and unicodedata.category(character) not in {"Mn", "Cf"}
        ]
        if meaningful and all(
            unicodedata.category(character).startswith(("S", "P"))
            for character in meaningful
        ):
            return "emoji"
        return "text"

    @staticmethod
    def _media_message(item: UINode, screen_width: int) -> dict[str, str] | None:
        """Classify accessible non-text chat bubbles and retain a placeholder."""
        description = item.description.strip()
        if item.center[0] >= screen_width // 2:
            return None
        if not item.container_key or "Image" not in item.class_name:
            return None
        if not description:
            left, top, right, bottom = item.bounds
            width, height = right - left, bottom - top
            # Large image nodes inside the left half of the chat recycler are
            # media bubbles. Small nodes are normally avatars/reaction icons.
            if width >= 120 and height >= 120 and left >= int(screen_width * 0.06):
                return {"type": "unknown_media", "content": "[媒体消息]"}
            return None
        lowered = description.casefold()
        definitions = (
            ("sticker", ("贴纸", "表情", "sticker", "emoji"), "[表情]"),
            ("gif", ("gif", "动图"), "[GIF]"),
            ("image", ("图片", "照片", "photo", "image"), "[图片]"),
            ("voice", ("语音", "voice", "audio"), "[语音]"),
            ("shared_card", ("分享", "卡片", "shared", "card"), "[分享卡片]"),
        )
        for message_type, keywords, fallback in definitions:
            if any(keyword in lowered for keyword in keywords):
                return {
                    "type": message_type,
                    "content": f"{fallback} {description}" if description != fallback else fallback,
                }
        return {"type": "unknown_media", "content": f"[媒体消息] {description}"}

    @staticmethod
    def _chat_sender(nodes: list[UINode]) -> str:
        candidates = [
            item.text.strip()
            for item in nodes
            if item.resource_id.endswith("/i44") and item.text.strip()
        ]
        return candidates[0] if candidates else "未知用户"
