"""TikTok search-to-chat state machine, validated against a real device UI."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from threading import Event
from time import monotonic, sleep
from typing import Callable
import unicodedata
import xml.etree.ElementTree as ET

import cv2
import uiautomator2 as u2

from devices import ADBClient


# 冷启动到搜索入口可用的最长等待。实测低端机（OPPO A16 / Android 11）需要 21~28 秒，
# 机型和网络越差越久，这里留出足够余量。
SEARCH_READY_TIMEOUT = 60.0
# 这段时间内只轮询、不按返回键：闪屏阶段按返回会把 App 逐层退出。
SEARCH_BACK_GRACE = 25.0


class WorkflowError(RuntimeError):
    pass


class WorkflowCancelled(RuntimeError):
    pass


@dataclass(slots=True)
class UINode:
    text: str
    description: str
    resource_id: str
    class_name: str
    clickable: bool
    selected: bool
    bounds: tuple[int, int, int, int]
    container_key: str = ""
    container_bounds: tuple[int, int, int, int] | None = None
    container_parent_class: str = ""
    container_parent_resource: str = ""

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)

    @property
    def area(self) -> int:
        left, top, right, bottom = self.bounds
        return max(0, right - left) * max(0, bottom - top)


@dataclass(slots=True)
class CommentEntry:
    username: str
    comment: str
    title_node: UINode
    bounds: tuple[int, int, int, int] | None = None
    is_reply: bool = False

    @property
    def key(self) -> tuple[str, str, bool]:
        return self.username, self.comment, self.is_reply


@dataclass(slots=True)
class ProfileInfo:
    username: str = ""
    handle: str = ""
    following: str = "未知"
    followers: str = "未知"
    likes: str = "未知"


ProgressCallback = Callable[[str, str, int], None]
UserCallback = Callable[[dict[str, str]], bool | None]
RoomSeenCallback = Callable[[str, str], bool]
RoomRecordedCallback = Callable[[str, str, str, str], None]


class TikTokSearchWorkflow:
    REFERENCE_WIDTH = 1080
    REFERENCE_HEIGHT = 2400
    COMMENT_VISIBLE_TOP_RATIO = 850 / REFERENCE_HEIGHT
    COMMENT_VISIBLE_BOTTOM_RATIO = 2200 / REFERENCE_HEIGHT
    RESULT_CONTENT_TOP_RATIO = 360 / REFERENCE_HEIGHT
    RESULT_MIN_AREA_RATIO = 120_000 / (REFERENCE_WIDTH * REFERENCE_HEIGHT)

    def __init__(self, adb: ADBClient, serial: str, package: str = "com.zhiliaoapp.musically", evidence_root: str | Path | None = None) -> None:
        self.adb = adb
        self.serial = serial
        self.package = package
        self._package_resolved = False
        self.device = None
        self.cancel_event = Event()
        self._last_result_hint = ""
        self._last_result_slot = 0
        self._current_keyword = ""
        self._last_room_label = ""
        self._screen_width = self.REFERENCE_WIDTH
        self._screen_height = self.REFERENCE_HEIGHT
        root = Path(__file__).resolve().parents[1]
        self.evidence_root = Path(evidence_root) if evidence_root else root / "data" / "evidence" / "tasks"

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(
        self,
        keyword: str,
        content_type: str,
        progress: ProgressCallback,
        max_comments: int = 20,
        collection_minutes: float = 2,
        user_callback: UserCallback | None = None,
        room_seen_callback: RoomSeenCallback | None = None,
        room_recorded_callback: RoomRecordedCallback | None = None,
    ) -> str:
        keyword = keyword.strip()
        if not keyword:
            raise WorkflowError("搜索关键词不能为空")
        if content_type not in {"video", "live", "either"}:
            raise WorkflowError("内容类型必须是 video、live 或 either")
        self._current_keyword = keyword
        try:
            self._resolve_package()
            self._emit(progress, "START_APP", "正在启动 TikTok", 8)
            self.adb.force_stop_app(self.serial, self.package)
            self.adb.start_app(self.serial, self.package)
            self.device = u2.connect(self.serial)
            self._capture_screen_size()
            self._wait_package(12)

            self._emit(progress, "OPEN_SEARCH", "正在定位搜索入口", 18)
            self._open_search()

            self._emit(progress, "ENTER_KEYWORD", f"正在输入关键词：{keyword}", 32)
            self._enter_keyword(keyword)

            self._emit(progress, "WAIT_RESULTS", "等待搜索结果加载", 48)
            self._wait_search_results(15)

            requested_label = {
                "video": "视频", "live": "直播", "either": "视频或直播"
            }[content_type]
            self._emit(
                progress,
                "SELECT_CONTENT",
                f"搜索结果已加载，正在选择{requested_label}分类",
                52,
            )
            chosen_type = self._choose_category(content_type)
            if chosen_type == "video":
                self._collect_video_rooms(
                    progress,
                    max(1, min(200, int(max_comments))),
                    max(0.1, min(1440.0, float(collection_minutes))),
                    keyword,
                    user_callback,
                    room_seen_callback,
                    room_recorded_callback,
                )
            else:
                self._collect_live_rooms(
                    progress,
                    max(1, min(200, int(max_comments))),
                    max(0.1, min(1440.0, float(collection_minutes))),
                    keyword,
                    user_callback,
                    room_seen_callback,
                    room_recorded_callback,
                )
            return chosen_type
        except WorkflowCancelled:
            raise
        except Exception as error:
            evidence = self._save_evidence("failure")
            suffix = f"；失败截图：{evidence}" if evidence else ""
            if isinstance(error, WorkflowError):
                raise WorkflowError(str(error) + suffix) from error
            raise WorkflowError(f"自动化执行失败：{error}{suffix}") from error

    def _capture_screen_size(self) -> tuple[int, int]:
        try:
            width, height = self.device.window_size()
            width, height = int(width), int(height)
            if width > 0 and height > 0:
                self._screen_width, self._screen_height = width, height
        except Exception:
            pass
        return (
            int(getattr(self, "_screen_width", self.REFERENCE_WIDTH)),
            int(getattr(self, "_screen_height", self.REFERENCE_HEIGHT)),
        )

    def _screen_size(self) -> tuple[int, int]:
        """Return the cached task screen size without repeated device RPCs."""
        if hasattr(self, "_screen_width") and hasattr(self, "_screen_height"):
            return int(self._screen_width), int(self._screen_height)
        return self._capture_screen_size()

    def _sx(self, reference_x: int | float) -> int:
        width, _ = self._screen_size()
        return round(width * float(reference_x) / self.REFERENCE_WIDTH)

    def _sy(self, reference_y: int | float) -> int:
        _, height = self._screen_size()
        return round(height * float(reference_y) / self.REFERENCE_HEIGHT)

    def _resolve_package(self) -> None:
        if self._package_resolved:
            return
        self.package = self.adb.resolve_tiktok_package(self.serial, self.package)
        self._package_resolved = True

    def _emit(self, callback: ProgressCallback, step: str, message: str, percent: int) -> None:
        self._check_cancelled()
        callback(step, message, percent)

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise WorkflowCancelled("任务已由用户停止")

    def _wait_package(self, timeout: float) -> None:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self._check_cancelled()
            current = self.device.app_current()
            if current.get("package") == self.package:
                return
            sleep(0.4)
        raise WorkflowError("TikTok 启动超时")

    def _open_search(self, timeout: float = SEARCH_READY_TIMEOUT) -> None:
        # TikTok can reopen on the Tako chat page. Only the real search field
        # (`hgt`) is accepted; generic EditTexts would submit the keyword to
        # Tako instead of executing a TikTok content search.
        #
        # 冷启动期间首页尚未渲染，搜索按钮要等 20 秒以上才出现；这段时间按返回键
        # 会把 App 逐层退出（连按几次就回到桌面），因此先纯轮询，
        # 超过 SEARCH_BACK_GRACE 仍找不到首页时才认为停在了子页面，再按返回。
        deadline = monotonic() + timeout
        back_allowed_at = monotonic() + SEARCH_BACK_GRACE
        while monotonic() < deadline:
            self._check_cancelled()
            if self._find_node(
                class_name="android.widget.EditText", resource_suffix="/hgt"
            ):
                return
            node = self._find_first(
                lambda item: item.description == "搜索"
                and item.clickable and item.bounds[1] < self._sy(500)
            )
            if node:
                self.device.click(*node.center)
                try:
                    self._wait_node(
                        lambda item: item.class_name == "android.widget.EditText"
                        and item.resource_id.endswith("/hgt"),
                        8,
                    )
                    return
                except WorkflowError:
                    pass
            elif monotonic() >= back_allowed_at:
                self.device.press("back")
            sleep(1.0)
        raise WorkflowError(
            f"无法打开 TikTok 搜索页面（已等待 {timeout:.0f} 秒）"
        )

    def _enter_keyword(self, keyword: str) -> None:
        field = self.device(resourceId=f"{self.package}:id/hgt")
        if not field.wait(timeout=8):
            raise WorkflowError("未找到搜索输入框")
        field.click()
        field.set_text(keyword)
        actual = field.get_text() or ""
        if actual.strip() != keyword:
            raise WorkflowError(f"关键词输入校验失败，期望“{keyword}”，实际“{actual}”")
        submit = self.device(className="android.widget.Button", text="搜索")
        if submit.exists:
            submit.click()
        else:
            self.device.press("enter")

    def _wait_search_results(self, timeout: float) -> None:
        # timeout is only a failure ceiling. There is no minimum wait: the
        # first hierarchy containing a result-category marker returns at once.
        category_bottom = self._sy(450)
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self._check_cancelled()
            nodes = self._nodes()
            if any(
                item.bounds[1] < category_bottom
                and (
                    item.description in {"综合", "视频", "直播"}
                    or item.text in {"综合", "视频", "直播"}
                )
                for item in nodes
            ):
                return
            sleep(1.0)
        raise WorkflowError(f"等待搜索结果加载超时（{timeout:g} 秒）")

    def _choose_category(self, requested: str) -> str:
        options = [requested] if requested != "either" else ["video", "live"]
        labels = {"video": "视频", "live": "直播"}
        for option in options:
            label = labels[option]
            deadline = monotonic() + 12
            swipe_index = 0
            while monotonic() < deadline:
                self._check_cancelled()
                node = self._category_node(label)
                if node and self._activate_category(label):
                    return option
                # TikTok loads and repositions the horizontal category strip
                # asynchronously. Search in both directions, starting the
                # gesture outside the fixed Tako tab so it reaches the strip.
                if swipe_index % 2 == 0:
                    self.device.swipe(
                        self._sx(880), self._sy(320),
                        self._sx(300), self._sy(320), duration=0.35,
                    )
                else:
                    self.device.swipe(
                        self._sx(760), self._sy(320),
                        self._sx(1030), self._sy(320), duration=0.35,
                    )
                swipe_index += 1
                sleep(0.8)
        raise WorkflowError("搜索结果中没有找到视频或直播分类")

    def _category_node(self, label: str) -> UINode | None:
        # Restrict matching to the category strip. Result cards may also contain
        # text such as “直播” and must never be mistaken for a tab.
        nodes = [
            item for item in self._nodes()
            if item.bounds[1] < self._sy(450)
            and (item.description == label or item.text == label)
        ]
        if not nodes:
            return None
        # The outer FrameLayout with content-desc is more reliable than its
        # child TextView, while a selected tab can legitimately be non-clickable.
        nodes.sort(key=lambda item: (item.description != label, not item.clickable))
        return nodes[0]

    def _activate_category(self, label: str) -> bool:
        deadline = monotonic() + 8
        next_click_at = 0.0
        while monotonic() < deadline:
            self._check_cancelled()
            node = self._category_node(label)
            if not node:
                return False
            if node.selected:
                return True
            now = monotonic()
            if now >= next_click_at:
                self.device.click(*node.center)
                next_click_at = now + 1.0
            # Poll the accessibility selected flag; it usually changes on the
            # next hierarchy frame, so do not impose a fixed post-click delay.
            sleep(0.15)
        return False

    def _open_first_result(self, content_type: str) -> None:
        if not self._open_result_at_slot(content_type, 0):
            raise WorkflowError(f"没有找到可进入的{self._type_label(content_type)}结果")

    def _open_result_at_slot(
        self, content_type: str, slot: int, timeout: float = 18
    ) -> bool:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self._check_cancelled()
            nodes = self._nodes()
            width, height = self._screen_size()
            candidates = self._result_candidates(
                nodes, content_type, width, height
            )
            if len(candidates) > slot:
                candidates.sort(key=lambda item: (item.bounds[1], item.bounds[0]))
                candidate = candidates[slot]
                self._last_result_slot = slot
                self._last_result_hint = self._result_card_hint(nodes, candidate)
                self.device.click(*candidate.center)
                self._wait_content_open(content_type, 15)
                return True
            sleep(0.7)
        return False

    @classmethod
    def _result_card_hint(cls, nodes: list[UINode], candidate: UINode) -> str:
        """Read a stable creator/title hint from an otherwise empty cover node."""
        left, top, right, bottom = candidate.bounds
        nearby = [
            item for item in nodes
            if left <= item.center[0] <= right
            and top - 80 <= item.center[1] <= bottom + 260
        ]
        preferred_suffixes = (
            "/user_name", "/tv_username", "/title", "/tv_desc", "/desc"
        )
        values: list[str] = []
        for preferred_only in (True, False):
            for item in sorted(nearby, key=lambda value: (value.bounds[1], value.bounds[0])):
                if preferred_only and not item.resource_id.endswith(preferred_suffixes):
                    continue
                value = cls._single_line(item.text or item.description)
                if (
                    value
                    and value not in values
                    and value not in {"直播", "LIVE", "视频"}
                    and not value.replace(",", "").replace(".", "").isdigit()
                ):
                    values.append(value)
            if values:
                break
        if not values:
            value = cls._single_line(candidate.description or candidate.text)
            if value:
                values.append(value)
        return "|".join(values[:5])[:240]

    @staticmethod
    def _result_candidates(
        nodes: list[UINode],
        content_type: str,
        screen_width: int = REFERENCE_WIDTH,
        screen_height: int = REFERENCE_HEIGHT,
    ) -> list[UINode]:
        """Return result cards, including TikTok's non-clickable live covers."""
        top = round(
            screen_height * TikTokSearchWorkflow.RESULT_CONTENT_TOP_RATIO
        )
        min_card_width = round(screen_width * 400 / TikTokSearchWorkflow.REFERENCE_WIDTH)
        max_card_width = round(screen_width * 650 / TikTokSearchWorkflow.REFERENCE_WIDTH)
        min_card_height = round(screen_height * 600 / TikTokSearchWorkflow.REFERENCE_HEIGHT)
        min_card_area = round(
            screen_width * screen_height
            * TikTokSearchWorkflow.RESULT_MIN_AREA_RATIO
        )
        if content_type == "live":
            # On current TikTok builds live cover nodes are deliberately marked
            # clickable=false. Android still dispatches a tap on their bounds to
            # the parent GridView, so do not apply the clickable filter here.
            covers = [
                item for item in nodes
                if item.resource_id.endswith("/mm_") and item.bounds[1] >= top
                and item.area >= min_card_area
            ]
            if covers:
                return covers
            # Resource IDs can change between releases. Live cards are large,
            # column-sized FrameLayouts below the category bar; tapping their
            # centre works even when accessibility exposes no click action.
            return [
                item for item in nodes
                if item.class_name == "android.widget.FrameLayout"
                and item.bounds[1] >= top
                and min_card_width <= item.bounds[2] - item.bounds[0] <= max_card_width
                and item.bounds[3] - item.bounds[1] >= min_card_height
            ]

        covers = [
            item for item in nodes
            if item.clickable and item.resource_id.endswith("/umr")
            and item.bounds[1] >= top
        ]
        if covers:
            return covers
        return [
            item for item in nodes
            if item.clickable and item.bounds[1] >= top and item.area >= min_card_area
            and item.class_name in {"android.widget.FrameLayout", "android.view.ViewGroup"}
        ]

    def _wait_content_open(self, content_type: str, timeout: float) -> None:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self._check_cancelled()
            current = self.device.app_current().get("activity", "").lower()
            nodes = self._nodes()
            if content_type == "video" and (
                "detail" in current or any("评论" in item.description and item.clickable for item in nodes)
            ):
                return
            if content_type == "live" and (
                "live" in current or any(self._looks_like_chat_input(item) for item in nodes)
            ):
                return
            sleep(0.6)
        raise WorkflowError(f"进入{self._type_label(content_type)}超时")

    def _open_chat(self, content_type: str) -> None:
        if content_type == "video":
            node = self._wait_node(
                lambda item: item.clickable and "评论" in item.description
                and ("阅读" in item.description or "添加" in item.description),
                10,
            )
            self.device.click(*node.center)
            self._wait_node(
                lambda item: item.class_name.endswith("RecyclerView") or self._looks_like_chat_input(item),
                10,
            )
            return
        # A live room normally exposes its chat input immediately. Some builds
        # require one click on a “聊天/评论” affordance first.
        if any(self._looks_like_chat_input(item) for item in self._nodes()):
            return
        entry = self._find_first(lambda item: item.clickable and any(word in (item.description + item.text) for word in ("聊天", "评论", "发言")))
        if entry:
            self.device.click(*entry.center)
        self._wait_node(self._looks_like_chat_input, 10)

    def _collect_video_rooms(
        self,
        progress: ProgressCallback,
        max_comments: int,
        collection_minutes: float,
        keyword: str,
        user_callback: UserCallback | None,
        room_seen_callback: RoomSeenCallback | None,
        room_recorded_callback: RoomRecordedCallback | None,
    ) -> int:
        started_at = monotonic()
        deadline = started_at + collection_minutes * 60
        room_number = 1
        rooms_entered = 0
        result_slot = 0
        total_count = 0
        empty_result_pages = 0
        self._emit(
            progress,
            "COLLECT_COMMENTS",
            f"开始定时采集：{collection_minutes:g} 分钟，每个房间最多采集 {max_comments} 位用户",
            60,
        )
        while monotonic() < deadline:
            self._check_cancelled()
            remaining = max(0, int(deadline - monotonic()))
            self._emit(
                progress,
                "SELECT_CONTENT",
                f"正在选择第 {room_number} 个视频房间，剩余约 {remaining} 秒",
                self._timed_progress(started_at, deadline),
            )
            opened = self._open_result_at_slot("video", result_slot, timeout=5)
            if not opened:
                empty_result_pages += 1
                if empty_result_pages >= 3:
                    break
                self.device.swipe(
                    self._sx(540), self._sy(2050),
                    self._sx(540), self._sy(650), duration=0.6,
                )
                sleep(1.0)
                result_slot = 0
                continue
            empty_result_pages = 0
            result_slot += 1
            room_key = self._room_identity("video")
            if room_seen_callback and room_seen_callback("video", room_key):
                self._emit(
                    progress,
                    "ROOM_SKIPPED",
                    "该视频在最近 24 小时内已采集，跳过并继续下一个视频",
                    self._timed_progress(started_at, deadline),
                )
                self._return_to_search_results("video")
                continue
            if room_recorded_callback:
                room_recorded_callback(
                    "video", room_key, keyword, self._last_room_label
                )
            rooms_entered += 1
            self._emit(
                progress,
                "ROOM_STARTED",
                f"已进入第 {room_number} 个视频房间，正在打开评论区",
                self._timed_progress(started_at, deadline),
            )
            self._open_chat("video")
            count = self._collect_video_comments(
                progress,
                max_comments,
                deadline=deadline,
                room_number=room_number,
                started_at=started_at,
                keyword=keyword,
                user_callback=user_callback,
            )
            total_count += count
            self._emit(
                progress,
                "ROOM_COMPLETED",
                f"第 {room_number} 个房间采集完成：{count} 条；累计 {total_count} 条",
                self._timed_progress(started_at, deadline),
            )
            if monotonic() >= deadline:
                break
            self._emit(
                progress,
                "NEXT_ROOM",
                f"第 {room_number} 个房间已结束，返回搜索结果继续下一个房间",
                self._timed_progress(started_at, deadline),
            )
            self._return_to_search_results("video")
            room_number += 1
        self._emit(
            progress,
            "COLLECTION_FINISHED",
            f"定时采集结束：共进入 {rooms_entered} 个房间，采集 {total_count} 位用户",
            100,
        )
        return total_count

    @staticmethod
    def _timed_progress(started_at: float, deadline: float) -> int:
        duration = max(1.0, deadline - started_at)
        ratio = min(1.0, max(0.0, (monotonic() - started_at) / duration))
        return min(98, 60 + int(ratio * 38))

    def _return_to_search_results(self, content_type: str = "video") -> None:
        category = self._type_label(content_type)
        for _ in range(5):
            self._check_cancelled()
            nodes = self._nodes()
            has_search = any(item.resource_id.endswith("/hgt") for item in nodes)
            has_category_tab = any(
                item.bounds[1] < self._sy(450)
                and (item.text == category or item.description == category)
                for item in nodes
            )
            if has_search and has_category_tab:
                if not self._activate_category(category):
                    raise WorkflowError(f"返回搜索结果后无法重新选择{category}分类")
                return
            self.device.press("back")
            sleep(0.8)
        raise WorkflowError("采集当前房间后无法返回搜索结果")

    def _collect_video_comments(
        self,
        progress: ProgressCallback,
        max_comments: int,
        *,
        deadline: float | None = None,
        room_number: int = 1,
        started_at: float | None = None,
        keyword: str = "",
        user_callback: UserCallback | None = None,
    ) -> int:
        collected: set[tuple[str, str]] = set()
        profile_failures: dict[tuple[str, str], int] = {}
        stagnant_scrolls = 0
        last_signature: tuple[tuple[str, str, bool], ...] = ()
        self._emit(
            progress,
            "COLLECT_COMMENTS",
            f"房间 {room_number}：开始采集评论用户，最多 {max_comments} 位",
            self._timed_progress(started_at, deadline)
            if started_at is not None and deadline is not None else 88,
        )
        attempts = 0
        while (
            len(collected) < max_comments
            and stagnant_scrolls < 5
            and (deadline is None or monotonic() < deadline)
        ):
            self._check_cancelled()
            attempts += 1
            if attempts > max_comments * 6 + 60:
                break
            self._ensure_comment_page()
            screen_width, screen_height = self._screen_size()
            nodes = self._nodes()
            page_rows = self._visible_comment_entries(
                nodes,
                screen_height=screen_height,
                screen_width=screen_width,
                include_replies=True,
            )
            entries = [entry for entry in page_rows if not entry.is_reply]
            pending = next(
                (entry for entry in entries if (entry.username, entry.comment) not in collected),
                None,
            )
            if pending:
                key = (pending.username, pending.comment)
                profile = self._read_profile_info(pending.title_node)
                if profile is None:
                    profile_failures[key] = profile_failures.get(key, 0) + 1
                    if profile_failures[key] < 2:
                        sleep(0.7)
                        continue
                    profile = ProfileInfo()
                collected.add(key)
                safe_username = self._single_line(pending.username)
                safe_handle = self._single_line(profile.handle or "@未知")
                safe_comment = self._single_line(pending.comment)
                percent = (
                    self._timed_progress(started_at, deadline)
                    if started_at is not None and deadline is not None
                    else min(98, 88 + int(len(collected) / max_comments * 10))
                )
                self._emit(
                    progress,
                    "COMMENT_COLLECTED",
                    f"房间：{room_number} | 用户名：{safe_username} | @名字：{safe_handle} | "
                    f"关注：{profile.following} | 粉丝：{profile.followers} | "
                    f"赞：{profile.likes} | 留言：{safe_comment}",
                    percent,
                )
                if user_callback is not None and profile.handle:
                    user_callback({
                        "username": pending.username,
                        "handle": profile.handle,
                        "following": profile.following,
                        "followers": profile.followers,
                        "likes": profile.likes,
                        "comment": pending.comment,
                        "keyword": keyword,
                        "room_number": str(room_number),
                        "mark": "视频",
                    })
                # Returning from a profile can slightly reposition the list.
                # Re-dump before touching the next username; never reuse nodes.
                sleep(0.5)
                continue

            signature = tuple(item.key for item in page_rows)
            stagnant_scrolls = (
                stagnant_scrolls + 1 if signature == last_signature else 0
            )
            last_signature = signature
            if not self._scroll_comment_page(nodes, page_rows):
                stagnant_scrolls += 1
        return len(collected)

    def _scroll_comment_page(
        self, nodes: list[UINode], rows: list[CommentEntry]
    ) -> bool:
        """Scroll with a retained row anchor so variable-height rows are not skipped."""
        if self._send_to_marker(nodes):
            self._dismiss_send_to_sheet(nodes)
            nodes = self._nodes()
        width, height = self._screen_size()
        if not rows:
            rows = self._visible_comment_entries(
                nodes,
                screen_height=height,
                screen_width=width,
                include_replies=True,
            )
        comment_list = next((
            item for item in nodes
            if item.resource_id.endswith("/t73")
            and "RecyclerView" in item.class_name
        ), None)
        if comment_list:
            left, top, right, bottom = comment_list.bounds
        else:
            left, right = 0, width
            top = round(height * self.COMMENT_VISIBLE_TOP_RATIO)
            bottom = round(height * self.COMMENT_VISIBLE_BOTTOM_RATIO)
        viewport_height = max(1, bottom - top)
        x = left + (right - left) // 2
        start_y = bottom - round(viewport_height * 0.10)

        if not rows:
            distance = max(1, round(viewport_height * 0.20))
            self._fast_comment_swipe(x, start_y, x, start_y - distance)
            sleep(0.6)
            return True

        anchor = max(rows, key=lambda item: (item.bounds or item.title_node.bounds)[3])
        anchor_bounds = anchor.bounds or anchor.title_node.bounds
        target_y = top + round(viewport_height * 0.15)
        requested = anchor_bounds[1] - target_y
        minimum = max(1, round(viewport_height * 0.18))
        maximum = max(minimum, round(viewport_height * 0.45))
        distance = max(minimum, min(maximum, requested))
        # Keep touch-down brief: a long gesture on TikTok comments opens the
        # “发送给” share sheet instead of scrolling.
        self._fast_comment_swipe(x, start_y, x, start_y - distance)
        sleep(0.6)

        after_nodes = self._nodes()
        if self._send_to_marker(after_nodes):
            self._dismiss_send_to_sheet(after_nodes)
            after_nodes = self._nodes()
        after_rows = self._visible_comment_entries(
            after_nodes,
            screen_height=height,
            screen_width=width,
            include_replies=True,
        )
        matches = [item for item in after_rows if item.key == anchor.key]
        if matches:
            new_top = min(
                (item.bounds or item.title_node.bounds)[1] for item in matches
            )
            return abs(anchor_bounds[1] - new_top) >= max(
                3, round(viewport_height * 0.01)
            )

        # Momentum occasionally moves farther than the gesture distance. Move
        # back a little and require the overlap anchor to reappear; otherwise
        # stopping is safer than silently skipping users.
        reverse = max(1, round(viewport_height * 0.20))
        reverse_start = top + round(viewport_height * 0.25)
        self._fast_comment_swipe(
            x, reverse_start, x, min(bottom, reverse_start + reverse)
        )
        sleep(0.6)
        recovered_nodes = self._nodes()
        if self._send_to_marker(recovered_nodes):
            self._dismiss_send_to_sheet(recovered_nodes)
            recovered_nodes = self._nodes()
        recovered_rows = self._visible_comment_entries(
            recovered_nodes,
            screen_height=height,
            screen_width=width,
            include_replies=True,
        )
        if not any(item.key == anchor.key for item in recovered_rows):
            raise WorkflowError(
                "评论翻页后重叠锚点丢失，为避免遗漏用户已停止当前房间采集"
            )
        return True

    def _fast_comment_swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int
    ) -> None:
        """Move immediately, hold at the target for 0.5s, then release."""
        device = getattr(self, "device", None)
        touch = getattr(device, "touch", None)
        if touch is not None:
            touched = False
            try:
                touch.down(int(start_x), int(start_y))
                touched = True
                # Crossing the touch-slop immediately cancels TikTok's
                # long-press detector. Holding only after reaching the target
                # suppresses fling/inertia without opening “发送给”.
                for step in range(1, 7):
                    ratio = step / 6
                    touch.move(
                        round(start_x + (end_x - start_x) * ratio),
                        round(start_y + (end_y - start_y) * ratio),
                    )
                sleep(0.5)
            finally:
                if touched:
                    touch.up(int(end_x), int(end_y))
            return

        # Older/fallback devices without low-level touch injection still use
        # a short native swipe rather than uiautomator2's slow interpolation.
        adb = getattr(self, "adb", None)
        serial = getattr(self, "serial", "")
        if adb is not None and serial:
            adb.shell(
                serial,
                [
                    "input", "touchscreen", "swipe",
                    str(int(start_x)), str(int(start_y)),
                    str(int(end_x)), str(int(end_y)), "100",
                ],
                timeout=3,
            )
            return
        # Unit-test/compatibility fallback when no ADB client is attached.
        self.device.swipe(
            start_x, start_y, end_x, end_y, duration=0.10
        )

    def _collect_live_rooms(
        self,
        progress: ProgressCallback,
        max_users: int,
        collection_minutes: float,
        keyword: str,
        user_callback: UserCallback | None,
        room_seen_callback: RoomSeenCallback | None,
        room_recorded_callback: RoomRecordedCallback | None,
    ) -> int:
        started_at = monotonic()
        deadline = started_at + collection_minutes * 60
        result_slot = 0
        room_number = 1
        rooms_entered = 0
        total_count = 0
        empty_result_pages = 0
        self._emit(
            progress,
            "COLLECT_LIVE_USERS",
            f"开始定时采集直播观众：{collection_minutes:g} 分钟，每个房间最多 {max_users} 位",
            60,
        )
        while monotonic() < deadline:
            self._check_cancelled()
            self._emit(
                progress,
                "SELECT_CONTENT",
                f"正在选择第 {room_number} 个直播房间",
                self._timed_progress(started_at, deadline),
            )
            if not self._open_result_at_slot("live", result_slot, timeout=5):
                empty_result_pages += 1
                if empty_result_pages >= 3:
                    break
                self.device.swipe(
                    self._sx(540), self._sy(2050),
                    self._sx(540), self._sy(650), duration=0.6,
                )
                sleep(1.0)
                result_slot = 0
                continue
            empty_result_pages = 0
            result_slot += 1
            room_key = self._room_identity("live")
            if room_seen_callback and room_seen_callback("live", room_key):
                self._emit(
                    progress,
                    "ROOM_SKIPPED",
                    "该直播间在最近 24 小时内已采集，跳过并继续下一个直播间",
                    self._timed_progress(started_at, deadline),
                )
                self._return_to_search_results("live")
                continue
            if room_recorded_callback:
                room_recorded_callback(
                    "live", room_key, keyword, self._last_room_label
                )
            rooms_entered += 1
            self._emit(
                progress,
                "ROOM_STARTED",
                f"已进入第 {room_number} 个直播间，正在打开观众排名",
                self._timed_progress(started_at, deadline),
            )
            count = self._collect_live_ranked_users(
                progress,
                max_users,
                deadline=deadline,
                room_number=room_number,
                started_at=started_at,
                keyword=keyword,
                user_callback=user_callback,
            )
            total_count += count
            self._emit(
                progress,
                "ROOM_COMPLETED",
                f"第 {room_number} 个直播间采集完成：新增 {count} 位；累计 {total_count} 位",
                self._timed_progress(started_at, deadline),
            )
            if monotonic() >= deadline:
                break
            self._return_to_search_results("live")
            room_number += 1
        self._emit(
            progress,
            "COLLECTION_FINISHED",
            f"直播观众采集结束：共进入 {rooms_entered} 个房间，新增 {total_count} 位用户",
            100,
        )
        return total_count

    def _collect_live_ranked_users(
        self,
        progress: ProgressCallback,
        max_users: int,
        *,
        deadline: float,
        room_number: int,
        started_at: float,
        keyword: str,
        user_callback: UserCallback | None,
    ) -> int:
        ranking_entry = None
        entry_deadline = monotonic() + 10
        while monotonic() < entry_deadline and monotonic() < deadline:
            ranking_entry = self._find_first(
                lambda item: item.clickable and (
                    item.resource_id.endswith("/psx")
                    or item.resource_id.endswith("/tv_online_audience_num")
                    or "次观看" in item.description
                )
            )
            if ranking_entry:
                break
            # Live controls auto-hide. A neutral tap restores the overlay
            # without liking, following, or sending a chat message.
            width, height = self.device.window_size()
            self.device.click(width // 2, height // 2)
            sleep(0.8)
        if not ranking_entry:
            self._emit(
                progress,
                "ROOM_NO_RANKING",
                f"房间 {room_number} 当前没有可用的观众排名，继续下一个直播间",
                self._timed_progress(started_at, deadline),
            )
            return 0
        self.device.click(*ranking_entry.center)
        panel = self._wait_node(
            lambda item: item.resource_id.endswith("/rpr"), 10
        )
        width, height = self.device.window_size()
        panel_top = panel.bounds[1]
        # The list is a custom-rendered view. Select “头号观众”, then open
        # each visible row to read its accessible profile card.
        self.device.click(int(width * 0.15), min(height - 1, panel_top + int(height * 0.095)))
        sleep(0.8)
        seen_handles: set[str] = set()
        new_count = 0
        stagnant_pages = 0
        previous_page: tuple[str, ...] = ()
        while new_count < max_users and stagnant_pages < 3 and monotonic() < deadline:
            page_handles: list[str] = []
            row_y = panel_top + int(height * 0.16)
            row_step = max(1, int(height * 0.071))
            # Process every visible user before scrolling again. The ranking
            # keeps its position after returning from a full profile; scrolling
            # before each row would collect only the first user of every page.
            for index in range(4):
                if new_count >= max_users or monotonic() >= deadline:
                    break
                y = row_y + index * row_step
                if y >= height - max(1, round(height * 90 / self.REFERENCE_HEIGHT)):
                    break
                profile = self._read_live_rank_profile(int(width * 0.16), y)
                if not profile or not profile.handle:
                    continue
                handle_key = profile.handle.casefold()
                page_handles.append(handle_key)
                if handle_key in seen_handles:
                    continue
                seen_handles.add(handle_key)
                record = {
                    "username": self._single_line(profile.username),
                    "handle": profile.handle,
                    "following": profile.following,
                    "followers": profile.followers,
                    "likes": profile.likes,
                    "comment": "",
                    "keyword": keyword,
                    "room_number": str(room_number),
                    "mark": "直播",
                }
                created = user_callback(record) if user_callback is not None else True
                if created is False:
                    continue
                new_count += 1
                self._emit(
                    progress,
                    "LIVE_USER_COLLECTED",
                    f"房间：{room_number} | 用户名：{self._single_line(record['username'])} | "
                    f"@名字：{profile.handle} | 关注：{profile.following} | "
                    f"粉丝：{profile.followers} | 赞：{profile.likes}",
                    self._timed_progress(started_at, deadline),
                )
            signature = tuple(page_handles)
            stagnant_pages = stagnant_pages + 1 if not signature or signature == previous_page else 0
            previous_page = signature
            if new_count >= max_users:
                break
            self._advance_live_ranking_page(panel_top, width, height)
        return new_count

    def _advance_live_ranking_page(
        self, panel_top: int, width: int, height: int
    ) -> None:
        """Scroll exactly once after all four visible ranking users are read."""
        # Move about three row-heights and intentionally keep one overlapping
        # user visible. Deduplication removes the overlap and prevents users
        # between two pages from being skipped.
        bottom_margin = max(1, round(height * 170 / self.REFERENCE_HEIGHT))
        start_y = min(height - bottom_margin, panel_top + int(height * 0.48))
        end_y = panel_top + int(height * 0.265)
        x = int(width * 0.78)
        self.device.swipe(x, start_y, x, end_y, duration=0.7)
        sleep(0.8)

    def _read_live_rank_profile(self, x: int, y: int) -> ProfileInfo | None:
        self.device.click(x, y)
        opened_profile_layer = False
        try:
            card_deadline = monotonic() + 6
            while monotonic() < card_deadline:
                self._check_cancelled()
                nodes = self._nodes()
                card_handle = next((
                    item for item in nodes
                    if item.resource_id.endswith("/user_name") and item.text.strip()
                ), None)
                card_name = next((
                    item for item in nodes
                    if item.resource_id.endswith("/pdl") and item.text.strip()
                ), None)
                avatar = next((
                    item for item in nodes
                    if item.resource_id.endswith("/bit") and item.clickable
                ), None)
                if card_handle and card_name and avatar:
                    opened_profile_layer = True
                    username = card_name.text.strip()
                    # The first tap only opens a compact LIVE profile card.
                    # Its counters are incomplete, so tap the avatar again and
                    # read the real profile page as requested.
                    self.device.click(*avatar.center)
                    full_deadline = monotonic() + 10
                    while monotonic() < full_deadline:
                        self._check_cancelled()
                        full_nodes = self._nodes()
                        full_handle = next((
                            item.text.strip() for item in full_nodes
                            if item.resource_id.endswith("/scn")
                            and self._valid_handle(item.text.strip())
                        ), "")
                        if full_handle and any(
                            item.resource_id.endswith("/sb6") for item in full_nodes
                        ):
                            # The profile shell appears before its counters have
                            # necessarily finished refreshing on slower phones.
                            sleep(1)
                            self._check_cancelled()
                            refreshed_nodes = self._nodes()
                            refreshed_handle = next((
                                item.text.strip() for item in refreshed_nodes
                                if item.resource_id.endswith("/scn")
                                and self._valid_handle(item.text.strip())
                            ), "")
                            return ProfileInfo(
                                username=username,
                                handle=refreshed_handle or full_handle,
                                following=self._profile_stat(refreshed_nodes, "关注"),
                                followers=self._profile_stat(refreshed_nodes, "粉丝"),
                                likes=self._profile_stat(refreshed_nodes, "赞", "获赞"),
                            )
                        sleep(0.4)
                    return None
                if any(item.resource_id.endswith("/rpr") for item in nodes):
                    sleep(0.35)
                    continue
                opened_profile_layer = True
                sleep(0.35)
            return None
        finally:
            if opened_profile_layer:
                self.device.press("back")
                try:
                    self._wait_node(lambda item: item.resource_id.endswith("/rpr"), 6)
                except WorkflowError:
                    pass
                sleep(0.35)

    @staticmethod
    def _live_stat(value: str, label: str) -> str:
        match = re.search(rf"([\d.,万亿KkMm]+)\s*{re.escape(label)}", value)
        return match.group(1) if match else "未知"

    def _room_identity(self, content_type: str) -> str:
        # The LIVE activity is reported before its creator/title nodes finish
        # rendering. Wait briefly; otherwise every room receives the same
        # empty `slot:` identity and unrelated rooms are skipped for 24 hours.
        deadline = monotonic() + 5
        room_values: list[str] = []
        while monotonic() < deadline:
            self._check_cancelled()
            nodes = self._nodes()
            room_values = self._room_identity_values(nodes, content_type)
            if room_values:
                break
            sleep(0.4)

        values: list[str] = []
        for value in [self._last_result_hint, *room_values]:
            if value and value not in values:
                values.append(value)
        stable_text = "|".join(values[:8])
        if not stable_text:
            stable_text = (
                f"search:{self._current_keyword}|slot:{self._last_result_slot}"
            )
        self._last_room_label = stable_text[:240]
        return hashlib.sha256(
            f"{content_type}|{stable_text}".encode("utf-8")
        ).hexdigest()

    @classmethod
    def _room_identity_values(
        cls, nodes: list[UINode], content_type: str
    ) -> list[str]:
        primary_suffixes = (
            ("/user_name", "/scn", "/title")
            if content_type == "live"
            else ("/title", "/desc", "/tv_desc", "/user_name", "/scn")
        )
        values: list[str] = []
        for item in nodes:
            if item.resource_id.endswith(primary_suffixes):
                value = cls._single_line(item.text or item.description)
                if value and value not in values:
                    values.append(value)
        if values:
            return values[:6]

        # Some LIVE versions expose only a combined accessibility label such
        # as "creator,968". Remove the volatile viewer-count suffix.
        for item in nodes:
            if item.resource_id.endswith("/b1o"):
                value = cls._single_line(item.text or item.description)
                value = re.sub(r"[,，]\s*[\d.,万亿KkMm]+\s*$", "", value)
                if value and value not in values:
                    values.append(value)
        if values:
            return values[:3]
        for item in nodes:
            value = cls._single_line(item.text or item.description)
            if value.startswith("@") and value not in values:
                values.append(value)
        return values[:3]

    @classmethod
    def _visible_comment_entries(
        cls,
        nodes: list[UINode],
        screen_height: int = REFERENCE_HEIGHT,
        screen_width: int | None = None,
        include_replies: bool = False,
    ) -> list[CommentEntry]:
        screen_width = screen_width or max(
            (item.bounds[2] for item in nodes), default=cls.REFERENCE_WIDTH
        )
        comment_list = next((
            item for item in nodes
            if item.resource_id.endswith("/t73")
            and "RecyclerView" in item.class_name
        ), None)
        if comment_list:
            list_left, visible_top, list_right, visible_bottom = comment_list.bounds
        else:
            list_left, list_right = 0, screen_width
            visible_top = round(screen_height * cls.COMMENT_VISIBLE_TOP_RATIO)
            visible_bottom = round(screen_height * cls.COMMENT_VISIBLE_BOTTOM_RATIO)
        viewport_height = max(1, visible_bottom - visible_top)
        bottom_guard = round(viewport_height * 0.03)
        # A fully visible first row commonly starts exactly at RecyclerView.top.
        # Adding a top guard incorrectly discarded that row.
        usable_top = visible_top
        usable_bottom = visible_bottom - bottom_guard

        grouped: dict[str, list[UINode]] = {}
        for item in nodes:
            if item.container_key:
                grouped.setdefault(item.container_key, []).append(item)
        grouped_entries: list[CommentEntry] = []
        for items in grouped.values():
            title = next((
                item for item in items
                if item.resource_id.endswith("/title") and item.text.strip()
            ), None)
            comment = next((
                item for item in items
                if item.resource_id.endswith("/ewn") and item.text.strip()
            ), None)
            if not title or not comment:
                continue
            row_bounds = title.container_bounds or (
                min(title.bounds[0], comment.bounds[0]),
                min(title.bounds[1], comment.bounds[1]),
                max(title.bounds[2], comment.bounds[2]),
                max(title.bounds[3], comment.bounds[3]),
            )
            if row_bounds[1] < usable_top or row_bounds[3] > usable_bottom:
                continue
            avatar = next((
                item for item in items if item.resource_id.endswith("/bit")
            ), None)
            is_reply = cls._is_reply_comment_row(
                title, avatar, list_left, max(1, list_right - list_left)
            )
            if include_replies or not is_reply:
                grouped_entries.append(CommentEntry(
                    title.text.strip(), comment.text.strip(), title,
                    row_bounds, is_reply,
                ))
        if grouped_entries:
            grouped_entries.sort(key=lambda item: (item.bounds or item.title_node.bounds)[1])
            return grouped_entries

        # Compatibility fallback for captured/test hierarchies without parent
        # metadata. Runtime parsing normally uses the container path above.
        titles = sorted(
            (
                item for item in nodes
                if item.resource_id.endswith("/title")
                and item.text.strip()
                and item.bounds[1] >= visible_top
                and item.bounds[1] < visible_bottom
            ),
            key=lambda item: item.bounds[1],
        )
        comments = sorted(
            (
                item for item in nodes
                if item.resource_id.endswith("/ewn")
                and item.text.strip()
                and item.bounds[1] >= visible_top
                and item.bounds[1] < visible_bottom
            ),
            key=lambda item: item.bounds[1],
        )
        result: list[CommentEntry] = []
        for index, title in enumerate(titles):
            next_top = (
                titles[index + 1].bounds[1]
                if index + 1 < len(titles) else visible_bottom
            )
            comment = next((
                item for item in comments
                if item.bounds[1] >= title.bounds[3] and item.bounds[1] < next_top
            ), None)
            if comment:
                is_reply = cls._is_reply_comment_row(
                    title, None, list_left, max(1, list_right - list_left)
                )
                if include_replies or not is_reply:
                    result.append(CommentEntry(
                        title.text.strip(), comment.text.strip(), title,
                        (
                            min(title.bounds[0], comment.bounds[0]),
                            title.bounds[1],
                            max(title.bounds[2], comment.bounds[2]),
                            comment.bounds[3],
                        ),
                        is_reply,
                    ))
        return result

    @staticmethod
    def _is_reply_comment_row(
        title: UINode,
        avatar: UINode | None,
        list_left: int,
        list_width: int,
    ) -> bool:
        """Classify indented reply rows without relying on absolute pixels."""
        parent_class = title.container_parent_class.rsplit(".", 1)[-1]
        title_indent = (title.bounds[0] - list_left) / max(1, list_width)
        if parent_class == "LinearLayout" and title_indent < 0.20:
            return False
        if parent_class == "FrameLayout" and title_indent >= 0.20:
            return True
        if title_indent >= 0.205:
            return True
        if avatar is not None:
            avatar_indent = (avatar.bounds[0] - list_left) / max(1, list_width)
            avatar_width = (avatar.bounds[2] - avatar.bounds[0]) / max(1, list_width)
            if avatar_indent >= 0.10 and avatar_width <= 0.075:
                return True
        return False

    def _read_profile_info(self, title_node: UINode) -> ProfileInfo | None:
        opened_profile = False
        try:
            self.device.click(*title_node.center)
            deadline = monotonic() + 8
            while monotonic() < deadline:
                self._check_cancelled()
                nodes = self._nodes()
                if any(item.resource_id.endswith("/ed5") for item in nodes):
                    sleep(0.3)
                    continue
                opened_profile = True
                if any(
                    "发送给" in (item.text + item.description) for item in nodes
                ):
                    self._dismiss_send_to_sheet(nodes)
                    return None
                handle = next((
                    item.text.strip() for item in nodes
                    if item.resource_id.endswith("/scn")
                    and self._valid_handle(item.text.strip())
                ), "")
                if not handle:
                    handle = next((
                        item.text.strip() for item in nodes
                        if self._valid_handle(item.text.strip())
                        and item.bounds[1] < self._sy(900)
                    ), "")
                if handle:
                    # Wait for asynchronously loaded profile counters, then
                    # read the page again instead of using the first frame.
                    sleep(1)
                    self._check_cancelled()
                    refreshed_nodes = self._nodes()
                    refreshed_handle = next((
                        item.text.strip() for item in refreshed_nodes
                        if item.resource_id.endswith("/scn")
                        and self._valid_handle(item.text.strip())
                    ), "")
                    return ProfileInfo(
                        handle=refreshed_handle or handle,
                        following=self._profile_stat(refreshed_nodes, "关注"),
                        followers=self._profile_stat(refreshed_nodes, "粉丝"),
                        likes=self._profile_stat(refreshed_nodes, "赞", "获赞"),
                    )
                sleep(0.5)
            return None
        finally:
            if opened_profile:
                self.device.press("back")
                try:
                    self._wait_node(
                        lambda item: item.resource_id.endswith("/ed5")
                        or item.resource_id.endswith("/vjb"),
                        8,
                    )
                except WorkflowError:
                    pass
                sleep(0.5)

    @staticmethod
    def _valid_handle(value: str) -> bool:
        return bool(re.fullmatch(r"@[^@\s]{2,}", value.strip()))

    @staticmethod
    def _profile_stat(nodes: list[UINode], *labels: str) -> str:
        label_nodes = [
            item for item in nodes
            if item.resource_id.endswith("/sb6") and item.text.strip() in labels
        ]
        value_nodes = [
            item for item in nodes
            if item.resource_id.endswith("/sb7") and item.text.strip()
        ]
        if not label_nodes or not value_nodes:
            return "未知"
        target_x = label_nodes[0].center[0]
        value = min(value_nodes, key=lambda item: abs(item.center[0] - target_x))
        return value.text.strip() or "未知"

    def _ensure_comment_page(self) -> None:
        for _ in range(3):
            nodes = self._nodes()
            if self._send_to_marker(nodes):
                self._dismiss_send_to_sheet(nodes)
                sleep(0.4)
                continue
            if any(
                item.resource_id.endswith("/ed5") or item.resource_id.endswith("/vjb")
                for item in nodes
            ):
                return
            # Recover from accidental share/profile sheets without interacting
            # with any recipient or sending content.
            self.device.press("back")
            sleep(0.8)
        raise WorkflowError("无法返回视频评论区，已停止采集以避免误操作")

    @staticmethod
    def _send_to_marker(nodes: list[UINode]) -> UINode | None:
        return next((
            item for item in nodes
            if "发送给" in (item.text + item.description)
        ), None)

    def _dismiss_send_to_sheet(self, nodes: list[UINode] | None = None) -> bool:
        """Dismiss TikTok's accidental long-press share sheet via blank space."""
        current_nodes = nodes if nodes is not None else self._nodes()
        marker = self._send_to_marker(current_nodes)
        if marker is None:
            return False
        width, height = self._screen_size()
        # Tap above the sheet title, constrained to the upper blank/video area.
        blank_y = marker.bounds[1] - round(height * 0.08)
        blank_y = max(round(height * 0.10), min(round(height * 0.35), blank_y))
        self.device.click(width // 2, blank_y)
        deadline = monotonic() + 3
        while monotonic() < deadline:
            self._check_cancelled()
            if self._send_to_marker(self._nodes()) is None:
                return True
            sleep(0.25)
        raise WorkflowError("检测到“发送给”弹窗，但点击上方空白处后未能关闭")

    @staticmethod
    def _single_line(value: str) -> str:
        cleaned = "".join(
            character for character in value.replace("|", "｜")
            if unicodedata.category(character) not in {"Cc", "Cf"}
        )
        return " ".join(cleaned.split())

    @staticmethod
    def _looks_like_chat_input(item: UINode) -> bool:
        value = item.text + item.description
        input_hint = any(
            word in value
            for word in ("添加评论", "说点什么", "发言", "聊天", "输入")
        )
        return input_hint and item.class_name in {
            "android.widget.EditText",
            "android.widget.TextView",
        }

    def _wait_node(self, predicate: Callable[[UINode], bool], timeout: float) -> UINode:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self._check_cancelled()
            node = self._find_first(predicate)
            if node:
                return node
            sleep(0.5)
        raise WorkflowError("等待目标控件超时")

    def _find_node(self, *, text: str | None = None, description: str | None = None, class_name: str | None = None, resource_suffix: str | None = None) -> UINode | None:
        return self._find_first(lambda item: (
            (text is None or item.text == text)
            and (description is None or item.description == description)
            and (class_name is None or item.class_name == class_name)
            and (resource_suffix is None or item.resource_id.endswith(resource_suffix))
        ))

    def _find_first(self, predicate: Callable[[UINode], bool]) -> UINode | None:
        return next((item for item in self._nodes() if predicate(item)), None)

    def _nodes(self) -> list[UINode]:
        self._check_cancelled()
        try:
            root = ET.fromstring(self.device.dump_hierarchy(compressed=False))
        except Exception as error:
            raise WorkflowError(f"读取 TikTok 页面结构失败：{error}") from error
        result: list[UINode] = []
        parents = {child: parent for parent in root.iter() for child in parent}
        for element in root.iter("node"):
            bounds = self._parse_bounds(element.attrib.get("bounds", ""))
            if not bounds:
                continue
            container_key = ""
            container_bounds = None
            container_parent_class = ""
            container_parent_resource = ""
            parent = parents.get(element)
            while parent is not None:
                parent_resource = parent.attrib.get("resource-id", "")
                if parent_resource.endswith(("/etr", "/t70")):
                    container_key = parent.attrib.get("bounds", "")
                    container_bounds = self._parse_bounds(container_key)
                    container_parent = parents.get(parent)
                    if container_parent is not None:
                        container_parent_class = container_parent.attrib.get(
                            "class", ""
                        )
                        container_parent_resource = container_parent.attrib.get(
                            "resource-id", ""
                        )
                    break
                parent = parents.get(parent)
            result.append(UINode(
                text=element.attrib.get("text", ""),
                description=element.attrib.get("content-desc", ""),
                resource_id=element.attrib.get("resource-id", ""),
                class_name=element.attrib.get("class", ""),
                clickable=element.attrib.get("clickable") == "true",
                selected=element.attrib.get("selected") == "true",
                bounds=bounds,
                container_key=container_key,
                container_bounds=container_bounds,
                container_parent_class=container_parent_class,
                container_parent_resource=container_parent_resource,
            ))
        return result

    @staticmethod
    def _parse_bounds(value: str) -> tuple[int, int, int, int] | None:
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value)
        return tuple(map(int, match.groups())) if match else None

    def _save_evidence(self, prefix: str) -> str:
        try:
            self.evidence_root.mkdir(parents=True, exist_ok=True)
            filename = self.evidence_root / f"{prefix}_{self.serial}_{int(monotonic() * 1000)}.png"
            image = self.adb.screenshot(self.serial)
            success, encoded = cv2.imencode(".png", image)
            if success:
                encoded.tofile(filename)
                return str(filename)
        except Exception:
            return ""
        return ""

    @staticmethod
    def _type_label(content_type: str) -> str:
        return "直播" if content_type == "live" else "视频"
