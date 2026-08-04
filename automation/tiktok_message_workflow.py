"""Real TikTok user search and direct-message workflow."""

from __future__ import annotations

from time import monotonic, sleep
import unicodedata

import uiautomator2 as u2

from automation.tiktok_search_workflow import (
    ProgressCallback,
    TikTokSearchWorkflow,
    UINode,
    WorkflowCancelled,
    WorkflowError,
)


class TikTokMessageWorkflow(TikTokSearchWorkflow):
    """Search an exact TikTok handle, open its chat, and send one message."""

    def run_message(
        self,
        handle: str,
        message: str,
        progress: ProgressCallback,
    ) -> str:
        normalized = self.normalize_handle(handle)
        message = str(message).strip()
        if not message:
            raise WorkflowError("发送消息不能为空")
        try:
            self._emit(progress, "START_APP", "正在启动 TikTok", 8)
            self.adb.force_stop_app(self.serial, self.package)
            self.adb.start_app(self.serial, self.package)
            self.device = u2.connect(self.serial)
            self._wait_package(12)

            self._emit(progress, "OPEN_SEARCH", "正在打开 TikTok 搜索", 18)
            self._open_search()
            self._emit(progress, "SEARCH_USER", f"正在搜索用户：{normalized}", 30)
            self._enter_keyword(normalized)
            self._wait_search_results(15)

            self._emit(progress, "SELECT_USERS", "正在切换到用户搜索结果", 43)
            self._open_users_category()
            self._emit(progress, "OPEN_USER", f"正在进入用户主页：{normalized}", 58)
            self._open_exact_user(normalized)
            self._verify_profile(normalized)

            self._emit(progress, "OPEN_MESSAGE", "正在打开用户私信页面", 72)
            self._open_message_page(normalized)
            self._emit(progress, "SEND_MESSAGE", f"正在发送消息：{message}", 84)
            self._send_chat_message(message)
            self._emit(progress, "MESSAGE_SENT", f"已向 {normalized} 发送消息：{message}", 92)
            self._emit(progress, "RETURN_HOME", "消息发送完成，正在返回 TikTok 首页", 96)
            self._return_to_tiktok_home()
            self._emit(progress, "HOME_READY", "已返回 TikTok 首页", 100)
            return normalized
        except WorkflowCancelled:
            raise
        except Exception as error:
            evidence = self._save_evidence("message_failure")
            suffix = f"；失败截图：{evidence}" if evidence else ""
            if isinstance(error, WorkflowError):
                raise WorkflowError(str(error) + suffix) from error
            raise WorkflowError(f"发送消息自动化失败：{error}{suffix}") from error

    @staticmethod
    def normalize_handle(handle: str) -> str:
        value = TikTokMessageWorkflow._clean_handle(handle)
        if value and not value.startswith("@"):
            value = "@" + value
        if len(value) <= 1:
            raise WorkflowError("请输入要发送消息的 @用户名")
        if any(character.isspace() for character in value):
            raise WorkflowError("@用户名不能包含空格")
        return value

    @staticmethod
    def _clean_handle(value: str) -> str:
        return "".join(
            character for character in str(value).strip()
            if unicodedata.category(character) != "Cf"
        )

    def _open_users_category(self) -> None:
        deadline = monotonic() + 12
        while monotonic() < deadline:
            self._check_cancelled()
            node = self._find_first(
                lambda item: item.bounds[1] < 450
                and (item.description == "用户" or item.text == "用户")
            )
            if node:
                if not node.selected:
                    self.device.click(*node.center)
                    sleep(1)
                selected = self._find_first(
                    lambda item: item.bounds[1] < 450
                    and (item.description == "用户" or item.text == "用户")
                    and item.selected
                )
                if selected:
                    return
            sleep(0.5)
        raise WorkflowError("搜索结果中没有找到“用户”分类")

    def _open_exact_user(self, handle: str) -> None:
        target = handle.lstrip("@").casefold()
        deadline = monotonic() + 15
        while monotonic() < deadline:
            self._check_cancelled()
            nodes = self._nodes()
            match = self._exact_result_node(nodes, target)
            if match:
                width, _ = self.device.window_size()
                self.device.click(width // 2, match.center[1])
                self._wait_node(
                    lambda item: item.text.startswith("@")
                    and self._clean_handle(item.text).lstrip("@").casefold() == target
                    and not item.resource_id.endswith("/hgt"),
                    12,
                )
                return
            sleep(0.6)
        raise WorkflowError(f"没有找到完全匹配的用户：{handle}")

    @classmethod
    def _exact_result_node(cls, nodes: list[UINode], target: str) -> UINode | None:
        matches = [
            item for item in nodes
            if item.resource_id.endswith("/tv_username")
            and cls._clean_handle(item.text).lstrip("@").casefold() == target
        ]
        return min(matches, key=lambda item: item.bounds[1]) if matches else None

    def _verify_profile(self, handle: str) -> None:
        target = handle.lstrip("@").casefold()
        profile_handle = self._find_first(
            lambda item: item.text.startswith("@")
            and self._clean_handle(item.text).lstrip("@").casefold() == target
            and not item.resource_id.endswith("/hgt")
        )
        if not profile_handle:
            raise WorkflowError(f"用户主页校验失败，未进入 {handle}")

    def _open_message_page(self, handle: str) -> None:
        message_node = self._wait_node(
            lambda item: item.text == "消息" and item.bounds[1] > 400,
            10,
        )
        self.device.click(*message_node.center)
        self._wait_node(
            lambda item: item.class_name == "android.widget.EditText"
            and not item.resource_id.endswith("/hgt"),
            12,
        )
        self._scroll_chat_to_top(handle)

    def _scroll_chat_to_top(self, handle: str) -> None:
        """Find the full handle, scrolling toward the chat top at most five times."""
        target = handle.lstrip("@").casefold()
        for _ in range(5):
            self._check_cancelled()
            nodes = self._nodes()
            if self._has_exact_handle(nodes, target):
                return
            chat_list = next(
                (
                    item for item in nodes
                    if item.resource_id.endswith("/t70")
                    and "RecyclerView" in item.class_name
                ),
                None,
            )
            if not chat_list:
                raise WorkflowError("没有找到私信消息列表")
            before = self._chat_fingerprint(nodes, chat_list.bounds)
            left, top, right, bottom = chat_list.bounds
            x = (left + right) // 2
            start_y = top + max(120, int((bottom - top) * 0.28))
            end_y = bottom - max(80, int((bottom - top) * 0.08))
            self.device.swipe(x, start_y, x, end_y, duration=0.35)
            sleep(0.65)
            after_nodes = self._nodes()
            if self._has_exact_handle(after_nodes, target):
                return
            after = self._chat_fingerprint(after_nodes, chat_list.bounds)
            if after == before:
                raise WorkflowError(
                    f"私信页面已到顶部，但没有找到完整用户名：{handle}"
                )
        raise WorkflowError(f"私信页面滚动5次后仍未找到完整用户名：{handle}")

    @classmethod
    def _has_exact_handle(cls, nodes: list[UINode], target: str) -> bool:
        return any(
            cls._clean_handle(item.text).startswith("@")
            and cls._clean_handle(item.text).lstrip("@").casefold() == target
            and not item.resource_id.endswith("/hgt")
            for item in nodes
        )

    @staticmethod
    def _chat_fingerprint(
        nodes: list[UINode], bounds: tuple[int, int, int, int]
    ) -> tuple[tuple[str, str, str, tuple[int, int, int, int]], ...]:
        left, top, right, bottom = bounds
        return tuple(
            (item.text, item.description, item.resource_id, item.bounds)
            for item in nodes
            if item.bounds[0] >= left
            and item.bounds[2] <= right
            and item.bounds[1] >= top
            and item.bounds[3] <= bottom
            and (item.text or item.description)
        )

    def _send_chat_message(self, message: str) -> None:
        before_count = sum(1 for item in self._nodes() if item.text == message)
        field = self.device(className="android.widget.EditText")
        if not field.wait(timeout=8):
            raise WorkflowError("没有找到私信输入框")
        field.click()
        field.set_text(message)
        if str(field.get_text() or "").strip() != message:
            raise WorkflowError("私信输入内容校验失败")
        send = self._wait_node(
            lambda item: item.description == "发送" and item.clickable,
            8,
        )
        self.device.click(*send.center)
        deadline = monotonic() + 12
        while monotonic() < deadline:
            self._check_cancelled()
            nodes = self._nodes()
            count = sum(1 for item in nodes if item.text == message)
            input_node = next(
                (
                    item for item in nodes
                    if item.class_name == "android.widget.EditText"
                    and not item.resource_id.endswith("/hgt")
                ),
                None,
            )
            input_cleared = input_node is not None and input_node.text.strip() != message
            if count > before_count and input_cleared:
                return
            sleep(0.5)
        raise WorkflowError("点击发送后未确认到新消息")

    def send_current_chat_message(self, message: str, progress: ProgressCallback) -> None:
        message = str(message).strip()
        if not message:
            raise WorkflowError("回复消息不能为空")
        self._emit(progress, "SEND_MODEL_REPLY", f"正在发送模型回复：{message}", 100)
        self._send_chat_message(message)
        self._emit(progress, "MODEL_REPLY_SENT", f"模型回复发送成功：{message}", 100)
        self._return_to_tiktok_home()


    def _return_to_tiktok_home(self) -> None:
        _, height = self.device.window_size()
        for _ in range(8):
            self._check_cancelled()
            home = self._home_node(self._nodes(), height)
            if home:
                if not home.selected:
                    self.device.click(*home.center)
                    sleep(1)
                    home = self._home_node(self._nodes(), height)
                if home and home.selected:
                    return
            # The first back can close the keyboard; subsequent backs leave
            # chat, profile, and search until the bottom navigation is visible.
            self.device.press("back")
            sleep(0.8)
        raise WorkflowError("消息已发送，但无法返回 TikTok 首页")

    @staticmethod
    def _home_node(nodes: list[UINode], screen_height: int) -> UINode | None:
        candidates = [
            item for item in nodes
            if item.bounds[1] >= int(screen_height * 0.75)
            and (item.description == "首页" or item.text == "首页")
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                not item.clickable,
                item.description != "首页",
                not item.selected,
            )
        )
        return candidates[0]
