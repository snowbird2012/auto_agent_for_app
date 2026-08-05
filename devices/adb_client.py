"""Small, dependency-free ADB client for multi-device management."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
from typing import Iterable

import cv2
import numpy as np


DEFAULT_TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
# TikTok 按发行地区有两个互不相同的包名，不是前缀关系：
# 东南亚等地区的机器装的通常是 trill。各自还可能带 `.go` 之类的商店后缀。
TIKTOK_PACKAGES = (DEFAULT_TIKTOK_PACKAGE, "com.ss.android.ugc.trill")


class ADBError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AndroidDevice:
    serial: str
    state: str
    product: str = ""
    model: str = ""
    device_name: str = ""
    transport_id: str = ""
    manufacturer: str = ""
    android_version: str = ""
    sdk_version: str = ""
    resolution: str = ""
    battery_level: int | None = None
    battery_status: str = ""
    foreground_package: str = ""
    connection_type: str = "USB"
    uiautomator2_initialized: bool | None = None
    uiautomator2_error: str = ""
    error: str = ""

    @property
    def authorized(self) -> bool:
        return self.state == "device"

    @property
    def automation_ready(self) -> bool:
        return self.authorized and self.uiautomator2_initialized is True

    @property
    def display_name(self) -> str:
        name = " ".join(part for part in (self.manufacturer, self.model) if part).strip()
        return name or self.model or self.device_name or self.serial


class ADBClient:
    def __init__(self, adb_path: str | Path | None = None, timeout: float = 12.0) -> None:
        self.adb_path = self._resolve_adb(adb_path)
        self.timeout = timeout

    @staticmethod
    def _resolve_adb(explicit: str | Path | None) -> Path:
        candidates: list[str | Path | None] = [
            explicit,
            shutil.which("adb"),
            Path("C:/Program Files/platform-tools/adb.exe"),
            Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return Path(candidate).resolve()
        raise ADBError("未找到 adb，请安装 Android platform-tools 或将 adb 加入 PATH")

    def list_devices(self, include_details: bool = True) -> list[AndroidDevice]:
        output = self._run(["devices", "-l"], timeout=10).stdout
        devices = self.parse_devices_output(output)
        if not include_details:
            return devices
        authorized = [device for device in devices if device.authorized]
        if not authorized:
            return devices
        details: dict[str, AndroidDevice] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(authorized))) as pool:
            futures = {pool.submit(self.get_device_details, item): item.serial for item in authorized}
            for future in as_completed(futures):
                serial = futures[future]
                try:
                    details[serial] = future.result()
                except Exception as error:
                    base = next(item for item in authorized if item.serial == serial)
                    details[serial] = replace(base, error=str(error))
        return [details.get(device.serial, device) for device in devices]

    @staticmethod
    def parse_devices_output(output: str) -> list[AndroidDevice]:
        devices: list[AndroidDevice] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("List of devices") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            metadata: dict[str, str] = {}
            for token in parts[2:]:
                if ":" in token:
                    key, value = token.split(":", 1)
                    metadata[key] = value.replace("_", " ")
            devices.append(AndroidDevice(
                serial=serial,
                state=state,
                product=metadata.get("product", ""),
                model=metadata.get("model", ""),
                device_name=metadata.get("device", ""),
                transport_id=metadata.get("transport_id", ""),
                connection_type="Wi-Fi" if ":" in serial else "USB",
            ))
        return devices

    def get_device_details(self, device: AndroidDevice | str) -> AndroidDevice:
        base = device if isinstance(device, AndroidDevice) else AndroidDevice(serial=device, state="device")
        serial = base.serial
        properties = self.parse_getprop(self.shell(serial, ["getprop"], timeout=10))
        battery_text = self.shell(serial, ["dumpsys", "battery"], timeout=10)
        size_text = self.shell(serial, ["wm", "size"], timeout=10)
        activity_text = self.shell(serial, ["dumpsys", "activity", "activities"], timeout=12)
        uiautomator2_initialized, uiautomator2_error = self.check_uiautomator2(serial)
        battery_level, battery_status = self.parse_battery(battery_text)
        model = properties.get("ro.product.model", "") or base.model
        return replace(
            base,
            manufacturer=properties.get("ro.product.manufacturer", ""),
            model=model,
            product=properties.get("ro.product.name", "") or base.product,
            device_name=properties.get("ro.product.device", "") or base.device_name,
            android_version=properties.get("ro.build.version.release", ""),
            sdk_version=properties.get("ro.build.version.sdk", ""),
            resolution=self.parse_resolution(size_text),
            battery_level=battery_level,
            battery_status=battery_status,
            foreground_package=self.parse_foreground_package(activity_text),
            uiautomator2_initialized=uiautomator2_initialized,
            uiautomator2_error=uiautomator2_error,
        )

    def check_uiautomator2(self, serial: str) -> tuple[bool, str]:
        """Verify that uiautomator2 assets exist and its device service responds."""
        try:
            marker = self.shell(serial, ["ls", "/data/local/tmp/u2.jar"], timeout=6)
            if not marker.strip().endswith("/u2.jar"):
                return False, "未发现 uiautomator2 设备端文件"
            import uiautomator2 as u2

            device = u2.connect(serial)
            info = device.info
            if not isinstance(info, dict):
                return False, "uiautomator2 服务未返回设备信息"
            return True, ""
        except Exception as error:
            return False, str(error)

    def initialize_uiautomator2(self, serial: str) -> None:
        """Install uiautomator2 assets using the current Python environment."""
        command = [sys.executable, "-m", "uiautomator2", "-s", serial, "init"]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as error:
            raise ADBError("uiautomator2 初始化超时") from error
        except OSError as error:
            raise ADBError(f"无法执行 uiautomator2 初始化：{error}") from error
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise ADBError(message or "uiautomator2 初始化失败")
        initialized, error = self.check_uiautomator2(serial)
        if not initialized:
            raise ADBError(error or "初始化完成后 uiautomator2 服务仍不可用")

    @staticmethod
    def parse_getprop(output: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in output.splitlines():
            match = re.match(r"\[([^]]+)\]:\s*\[(.*)\]", line.strip())
            if match:
                result[match.group(1)] = match.group(2)
        return result

    @staticmethod
    def parse_battery(output: str) -> tuple[int | None, str]:
        level_match = re.search(r"^\s*level:\s*(\d+)", output, re.MULTILINE)
        status_match = re.search(r"^\s*status:\s*(\d+)", output, re.MULTILINE)
        statuses = {1: "未知", 2: "充电中", 3: "未充电", 4: "未充电", 5: "已充满"}
        level = int(level_match.group(1)) if level_match else None
        status = statuses.get(int(status_match.group(1)), "未知") if status_match else "未知"
        return level, status

    @staticmethod
    def parse_resolution(output: str) -> str:
        override = re.search(r"Override size:\s*(\d+x\d+)", output)
        physical = re.search(r"Physical size:\s*(\d+x\d+)", output)
        value = (override or physical)
        return value.group(1).replace("x", " × ") if value else ""

    @staticmethod
    def parse_foreground_package(output: str) -> str:
        patterns = (
            r"topResumedActivity=.*?\s([\w.]+)/[\w.$]+",
            r"mResumedActivity:.*?\s([\w.]+)/[\w.$]+",
            r"ResumedActivity:.*?\s([\w.]+)/[\w.$]+",
        )
        for pattern in patterns:
            match = re.search(pattern, output)
            if match:
                return match.group(1)
        return ""

    def screenshot(self, serial: str) -> np.ndarray:
        result = self._run(["-s", serial, "exec-out", "screencap", "-p"], timeout=20, text=False)
        data = np.frombuffer(result.stdout, dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            raise ADBError("设备截图解码失败")
        return image

    def list_installed_packages(self, serial: str, prefix: str = "") -> list[str]:
        arguments = ["pm", "list", "packages"]
        if prefix.strip():
            arguments.append(prefix.strip())
        output = self.shell(serial, arguments, timeout=12)
        packages = {
            line.split(":", 1)[1].strip()
            for line in output.splitlines()
            if line.strip().startswith("package:") and ":" in line
        }
        return sorted(item for item in packages if item)

    def resolve_tiktok_package(
        self, serial: str, preferred: str = DEFAULT_TIKTOK_PACKAGE
    ) -> str:
        """Resolve any known TikTok package or its store-specific suffix variant."""
        preferred = str(preferred or DEFAULT_TIKTOK_PACKAGE).strip()
        installed: list[str] = []
        for package in TIKTOK_PACKAGES:
            installed.extend(self.list_installed_packages(serial, package))
        candidates = [
            item for item in dict.fromkeys(installed)
            if any(item == p or item.startswith(p + ".") for p in TIKTOK_PACKAGES)
        ]
        if preferred in candidates:
            return preferred
        if not candidates:
            raise ADBError(
                "设备中未找到 TikTok，支持的包名前缀为："
                f"{'、'.join(TIKTOK_PACKAGES)}"
            )
        # Prefer the official package. If only variants are installed, choose
        # the shortest deterministic suffix (for example `.go`).
        return min(
            candidates,
            key=lambda item: (item not in TIKTOK_PACKAGES, len(item), item),
        )

    def shell(self, serial: str, arguments: Iterable[str], timeout: float | None = None) -> str:
        return self._run(["-s", serial, "shell", *arguments], timeout=timeout).stdout.strip()

    def press_home(self, serial: str) -> None:
        self.shell(serial, ["input", "keyevent", "KEYCODE_HOME"])

    def start_app(self, serial: str, package: str = DEFAULT_TIKTOK_PACKAGE) -> None:
        if package == DEFAULT_TIKTOK_PACKAGE:
            package = self.resolve_tiktok_package(serial, package)
        output = self.shell(serial, ["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], timeout=20)
        if "No activities found" in output or "monkey aborted" in output.lower():
            raise ADBError(f"设备中未找到应用：{package}")

    def force_stop_app(self, serial: str, package: str = DEFAULT_TIKTOK_PACKAGE) -> None:
        if package == DEFAULT_TIKTOK_PACKAGE:
            package = self.resolve_tiktok_package(serial, package)
        self.shell(serial, ["am", "force-stop", package])

    def _run(self, arguments: list[str], timeout: float | None = None, text: bool = True) -> subprocess.CompletedProcess:
        command = [str(self.adb_path), *arguments]
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                timeout=timeout or self.timeout,
                check=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except subprocess.TimeoutExpired as error:
            raise ADBError(f"ADB 命令超时：{' '.join(arguments[:3])}") from error
        except OSError as error:
            raise ADBError(f"无法执行 ADB：{error}") from error
        if result.returncode != 0:
            stderr = result.stderr.strip() if text else ""
            raise ADBError(stderr or f"ADB 命令失败，退出码 {result.returncode}")
        return result
