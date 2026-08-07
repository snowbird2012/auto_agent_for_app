"""TikTok inbox unread-message listener validated against a real device."""

from __future__ import annotations

from time import monotonic, sleep
import re
import unicodedata

import cv2
import numpy as np
import uiautomator2 as u2

from automation.tiktok_message_workflow import TikTokMessageWorkflow
from automation.tiktok_search_workflow import (
    ProgressCallback,
    TikTokSearchWorkflow,
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
            self._capture_screen_size()
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

            self._emit(progress, "OPEN_UNREAD_CHAT", "正在识别并进入带红色未读标志的会话", 100)
            if not self._open_unread_chat_with_retry(progress):
                self._emit(
                    progress,"INBOX_NO_UNREAD_ROW",
                    "收件箱会话列表中没有检测到红色未读标志，返回首页继续监听",100,
                )
                self._return_to_tiktok_home()
                return None

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
                raise WorkflowError("进入未读会话后没有读取到对方消息")
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

    def _open_unread_chat_with_retry(self, progress: ProgressCallback) -> bool:
        """Open the row carrying an unread marker; never assume a fixed row index."""
        missing_checks=0
        for attempt in range(1, 13):
            self._check_cancelled()
            nodes = self._nodes()
            if self._has_chat_input(nodes):
                return True
            screen_width,screen_height=self._screen_size()
            try:image=self.adb.screenshot(self.serial)
            except Exception:image=None
            unread=self._unread_conversation_node(
                nodes,screen_width,screen_height,image
            )
            if not unread:
                missing_checks+=1
                if missing_checks>=2:return False
                sleep(0.8)
                continue
            missing_checks=0
            rows=self._conversation_nodes(nodes,screen_width,screen_height)
            self._emit(
                progress,"INBOX_UNREAD_ROW",
                f"已定位未读会话：当前识别到 {len(rows)} 条会话，目标区域={unread.bounds}",100,
            )
            self.device.click(*unread.center)
            deadline = monotonic() + 2
            while monotonic() < deadline:
                self._check_cancelled()
                if self._has_chat_input(self._nodes()):
                    return True
                sleep(0.25)
            self._emit(
                progress,
                "WAIT_INBOX_POPUP",
                f"未读会话可能被顶部弹窗遮挡，准备再次识别并点击（{attempt}/12）",
                100,
            )
            sleep(0.8)
        raise WorkflowError("循环识别并点击未读会话12次后仍未进入聊天页面")

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
    def _conversation_nodes(
        nodes: list[UINode],
        screen_width: int = TikTokSearchWorkflow.REFERENCE_WIDTH,
        screen_height: int = TikTokSearchWorkflow.REFERENCE_HEIGHT,
    ) -> list[UINode]:
        top=round(screen_height*0.08); bottom=round(screen_height*0.88)
        minimum_width=round(screen_width*0.55)
        rows=[
            item for item in nodes
            if item.clickable
            and item.resource_id.endswith("/v15")
            and top <= item.bounds[1] < bottom
            and item.bounds[2]-item.bounds[0]>=minimum_width
        ]
        return sorted(rows,key=lambda item:item.bounds[1])

    @classmethod
    def _unread_conversation_node(
        cls,nodes: list[UINode],screen_width: int,screen_height: int,
        image: np.ndarray | None = None,
    ) -> UINode | None:
        rows=cls._conversation_nodes(nodes,screen_width,screen_height)
        for row in rows:
            if cls._row_has_accessible_unread_marker(nodes,row):return row
            if image is not None and cls._row_has_red_marker(image,row.bounds):return row
        return None

    @staticmethod
    def _row_has_accessible_unread_marker(nodes: list[UINode],row: UINode) -> bool:
        left,top,right,bottom=row.bounds
        row_width=max(1,right-left); row_height=max(1,bottom-top); row_area=max(1,row.area)
        keywords=(
            "未读","新消息","条新信息","unread","new message","new messages",
            "notification","badge","tin nhắn mới",
        )
        resource_keywords=("unread","badge","notice","notification","new_message")

        # Some TikTok builds expose the whole row as one accessibility node,
        # with the unread state included in its content-desc.
        row_semantics=f"{row.text} {row.description}".strip().casefold()
        if any(keyword in row_semantics for keyword in keywords):return True

        # Badges often overlap the row edge. Expand the association rectangle
        # proportionally instead of requiring the marker to be fully inside.
        horizontal_padding=int(row_width*0.06)
        vertical_padding=int(row_height*0.20)
        for item in nodes:
            if item is row:continue
            x,y=item.center
            if not (left-horizontal_padding<=x<=right+horizontal_padding
                    and top-vertical_padding<=y<=bottom+vertical_padding):continue
            semantics=f"{item.text} {item.description}".strip().casefold()
            resource=item.resource_id.casefold()
            if any(keyword in semantics for keyword in keywords):return True
            if any(keyword in resource for keyword in resource_keywords):return True
            badge_value=(item.text.strip() or item.description.strip()).strip()
            numeric_badge=bool(re.fullmatch(r"\d{1,3}\+?",badge_value))
            dot_badge=badge_value in {"•","●","·"}
            if ((numeric_badge or dot_badge)
                    and x>=left+int(row_width*0.48)
                    and item.area<=row_area*0.24):return True
        return False

    @staticmethod
    def _row_has_red_marker(
        image: np.ndarray,bounds: tuple[int,int,int,int]
    ) -> bool:
        if image.ndim!=3 or image.shape[2]<3:return False
        height,width=image.shape[:2]
        left,top,right,bottom=bounds
        left=max(0,min(width,left)); right=max(0,min(width,right))
        top=max(0,min(height,top)); bottom=max(0,min(height,bottom))
        if right<=left or bottom<=top:return False
        row_width=right-left; row_height=bottom-top
        # Badge placement differs across resolutions and TikTok builds. Scan
        # most of the row while excluding only the avatar area on the left.
        crop=image[top:bottom,left+int(row_width*0.28):right]
        if crop.size==0:return False
        hsv=cv2.cvtColor(crop[:,:,:3],cv2.COLOR_BGR2HSV)
        low=cv2.inRange(hsv,np.array([0,90,80]),np.array([18,255,255]))
        high=cv2.inRange(hsv,np.array([155,90,80]),np.array([179,255,255]))
        mask=cv2.bitwise_or(low,high)
        # Also accept red/pink pixels by channel dominance. This covers OEM
        # color profiles that shift TikTok red outside the expected HSV range.
        blue=crop[:,:,0].astype(np.int16)
        green=crop[:,:,1].astype(np.int16)
        red=crop[:,:,2].astype(np.int16)
        dominant=((red>=90)&((red-green)>=28)&((red-blue)>=18)).astype(np.uint8)*255
        mask=cv2.bitwise_or(mask,dominant)
        count,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
        minimum=max(2,int(row_width*row_height*0.00002))
        maximum=max(minimum,int(row_width*row_height*0.12))
        for index in range(1,count):
            component_width=int(stats[index,cv2.CC_STAT_WIDTH])
            component_height=int(stats[index,cv2.CC_STAT_HEIGHT])
            area=int(stats[index,cv2.CC_STAT_AREA])
            if (minimum<=area<=maximum
                    and 2<=component_width<=max(6,int(row_height*0.60))
                    and 2<=component_height<=max(6,int(row_height*0.60))):
                return True
        return False

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
                top + max(self._sy(120), int((bottom - top) * 0.28)),
                x,
                bottom - max(self._sy(80), int((bottom - top) * 0.08)),
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
    def _chat_fingerprint(
        nodes: list[UINode],bounds: tuple[int,int,int,int]
    ) -> tuple[tuple[str,str,str,tuple[int,int,int,int]],...]:
        """Fingerprint the visible chat list to detect whether paging moved."""
        left,top,right,bottom=bounds
        return tuple(
            (item.text,item.description,item.resource_id,item.bounds)
            for item in nodes
            if item.bounds[0]>=left and item.bounds[2]<=right
            and item.bounds[1]>=top and item.bounds[3]<=bottom
            and (item.text or item.description)
        )

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
