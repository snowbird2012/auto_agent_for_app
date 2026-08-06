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

    USER_RESULT_NAME_SUFFIXES = (
        "/tv_username",
        "/user_name",
        "/title",
        "/tv_name",
        "/nickname",
    )

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
            self._resolve_package()
            self._emit(progress, "START_APP", "正在启动 TikTok", 8)
            self.adb.force_stop_app(self.serial, self.package)
            self.adb.start_app(self.serial, self.package)
            self.device = u2.connect(self.serial)
            self._capture_screen_size()
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
            self._open_message_page()
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

    @classmethod
    def _handle_key(cls, value: str) -> str:
        """Return the comparable username used by result lists (without @)."""
        return cls._clean_handle(value).removeprefix("@").casefold()

    def _open_users_category(self) -> None:
        deadline = monotonic() + 12
        while monotonic() < deadline:
            self._check_cancelled()
            node = self._find_first(
                lambda item: item.bounds[1] < self._sy(450)
                and (item.description == "用户" or item.text == "用户")
            )
            if node:
                if not node.selected:
                    self.device.click(*node.center)
                    sleep(1)
                selected = self._find_first(
                    lambda item: item.bounds[1] < self._sy(450)
                    and (item.description == "用户" or item.text == "用户")
                    and item.selected
                )
                if selected:
                    return
            sleep(0.5)
        raise WorkflowError("搜索结果中没有找到“用户”分类")

    def _open_exact_user(self, handle: str) -> None:
        target = self._handle_key(handle)
        deadline = monotonic() + 15
        while monotonic() < deadline:
            self._check_cancelled()
            nodes = self._nodes()
            match = self._exact_result_node(nodes, target)
            if match:
                width, _ = self.device.window_size()
                self.device.click(width // 2, match.center[1])
                self._wait_node(
                    lambda item: self._clean_handle(item.text).startswith("@")
                    and self._handle_key(item.text) == target
                    and not item.resource_id.endswith("/hgt"),
                    12,
                )
                return
            sleep(0.6)
        raise WorkflowError(f"没有找到完全匹配的用户：{handle}")

    @classmethod
    def _exact_result_node(cls, nodes: list[UINode], target: str) -> UINode | None:
        """Match either name shown in one user result record."""
        target_key = cls._handle_key(target)
        anchors = [
            item for item in nodes
            if item.resource_id.endswith("/tv_username") and item.text.strip()
        ]
        matched_rows: list[UINode] = []
        candidates = [
            item for item in nodes
            if item.text.strip()
            and not item.resource_id.endswith("/hgt")
            and cls._handle_key(item.text) == target_key
        ]
        for candidate in candidates:
            if not anchors:break
            anchor=min(anchors,key=lambda item:abs(item.center[1]-candidate.center[1]))
            same_container=(bool(anchor.container_key) and anchor.container_key==candidate.container_key)
            anchor_height=max(1,anchor.bounds[3]-anchor.bounds[1])
            candidate_height=max(1,candidate.bounds[3]-candidate.bounds[1])
            same_row=abs(anchor.center[1]-candidate.center[1]) <= max(80,anchor_height*3,candidate_height*3)
            if same_container or same_row:matched_rows.append(anchor)
        if matched_rows:return min(matched_rows,key=lambda item:item.bounds[1])

        # Fallback for UI variants that do not expose the username anchor.
        matches=[item for item in nodes
                 if item.resource_id.endswith(cls.USER_RESULT_NAME_SUFFIXES)
                 and cls._handle_key(item.text)==target_key]
        return min(matches,key=lambda item:item.bounds[1]) if matches else None

    def _verify_profile(self, handle: str) -> None:
        target = self._handle_key(handle)
        profile_handle = self._find_first(
            lambda item: self._clean_handle(item.text).startswith("@")
            and self._handle_key(item.text) == target
            and not item.resource_id.endswith("/hgt")
        )
        if not profile_handle:
            raise WorkflowError(f"用户主页校验失败，未进入 {handle}")

    def _open_message_page(self) -> None:
        message_node = self._wait_message_action(10)
        self.device.click(*message_node.center)
        self._wait_node(
            lambda item: item.class_name == "android.widget.EditText"
            and not item.resource_id.endswith("/hgt"),
            12,
        )

    def _wait_message_action(self, timeout: float) -> UINode:
        deadline=monotonic()+timeout
        while monotonic()<deadline:
            self._check_cancelled()
            node=self._message_action_node(
                self._nodes(),self._sy(300),self._sy(1900)
            )
            if node:return node
            sleep(0.4)
        raise WorkflowError("用户主页没有找到“消息”按钮")

    @staticmethod
    def _message_action_node(
        nodes: list[UINode], minimum_top: int = 0, maximum_bottom: int = 10_000
    ) -> UINode | None:
        labels=[item for item in nodes
                if (item.text.strip()=="消息" or item.description.strip()=="消息")
                and item.class_name!="android.widget.EditText"
                and item.bounds[1]>=minimum_top and item.bounds[3]<=maximum_bottom]
        actions: list[tuple[UINode,UINode]]=[]
        for label_node in labels:
            if label_node.clickable:
                actions.append((label_node,label_node)); continue
            x,y=label_node.center
            containers=[item for item in nodes
                        if item.clickable and item.area>=label_node.area
                        and item.bounds[0]<=x<=item.bounds[2]
                        and item.bounds[1]<=y<=item.bounds[3]]
            action=min(containers,key=lambda item:item.area) if containers else label_node
            actions.append((label_node,action))
        if not actions:return None
        actions.sort(key=lambda pair:(pair[0].bounds[1],pair[0].bounds[0]))
        return actions[0][1]

    def _send_chat_message(self, message: str) -> None:
        before_nodes=self._nodes()
        screen_width=max(1,self._screen_width)
        before_outgoing={
            self._message_node_fingerprint(item)
            for item in self._matching_outgoing_nodes(before_nodes,message,screen_width)
        }
        before_bottom=max(
            (item.bounds[3] for item in self._matching_outgoing_nodes(
                before_nodes,message,screen_width
            )),default=None,
        )
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
        cleared_checks=0
        while monotonic() < deadline:
            self._check_cancelled()
            nodes = self._nodes()
            input_nodes=[item for item in nodes
                         if item.class_name=="android.widget.EditText"
                         and not item.resource_id.endswith("/hgt")]
            node_contains=any(
                self._message_text_key(item.text)==self._message_text_key(message)
                for item in input_nodes
            )
            try:live_text=str(field.get_text() or "")
            except Exception:live_text=""
            live_contains=(
                self._message_text_key(live_text)==self._message_text_key(message)
            )
            if not node_contains and not live_contains:
                cleared_checks+=1
                if cleared_checks>=2:return
            else:
                cleared_checks=0
            outgoing=self._matching_outgoing_nodes(nodes,message,screen_width)
            for item in outgoing:
                is_new=self._message_node_fingerprint(item) not in before_outgoing
                remains_latest=(
                    before_bottom is None
                    or item.bounds[3]>=before_bottom-max(4,self._sy(8))
                )
                if is_new and remains_latest:return
            sleep(0.5)
        raise WorkflowError("点击发送后未确认到新消息")

    @classmethod
    def _message_text_key(cls,value: str) -> str:
        cleaned=cls._clean_handle(value).replace("\r\n","\n").replace("\r","\n")
        return " ".join(cleaned.split())

    @classmethod
    def _matching_outgoing_nodes(
        cls,nodes: list[UINode],message: str,screen_width: int
    ) -> list[UINode]:
        target=cls._message_text_key(message)
        matches=[
            item for item in nodes
            if target
            and item.class_name!="android.widget.EditText"
            and item.center[0]>=screen_width//2
            and target in {
                cls._message_text_key(item.text),
                cls._message_text_key(item.description),
            }
        ]
        return sorted(matches,key=lambda item:item.bounds[3])

    @classmethod
    def _message_node_fingerprint(cls,item: UINode) -> tuple:
        return (
            item.resource_id,item.bounds,
            cls._message_text_key(item.text),
            cls._message_text_key(item.description),
        )

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
