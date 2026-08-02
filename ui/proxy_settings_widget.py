"""Persistent HTTP proxy settings UI."""

from __future__ import annotations

from urllib.parse import urlsplit

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from storage.settings_repository import SettingsRepository
from ui.widgets import card_layout, label


class ProxySettingsWidget(QWidget):
    def __init__(self, repository: SettingsRepository) -> None:
        super().__init__()
        self.repository = repository
        self._build_ui()
        self.load_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(14)

        card, content = card_layout()
        header = QHBoxLayout()
        header.addWidget(label("HTTP 网络代理", "SectionTitle"))
        header.addStretch()
        self.enabled = QCheckBox("启用代理")
        self.enabled.toggled.connect(self._update_enabled_state)
        header.addWidget(self.enabled)
        content.addLayout(header)
        description = QLabel("代理仅作用于本程序发出的 HTTP/HTTPS 请求，不会修改 Windows 系统代理，也不会影响 ADB 与手机网络。")
        description.setObjectName("Muted")
        description.setWordWrap(True)
        content.addWidget(description)

        form = QFormLayout()
        form.setSpacing(12)
        self.proxy_url = QLineEdit()
        self.proxy_url.setPlaceholderText("例如 http://127.0.0.1:7890")
        self.username = QLineEdit()
        self.username.setPlaceholderText("无认证时留空")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("留空表示不修改已保存密码")
        form.addRow("代理地址", self.proxy_url)
        form.addRow("用户名", self.username)
        form.addRow("密码", self.password)
        content.addLayout(form)

        content.addWidget(label("应用范围", "Muted"))
        self.use_for_model = QCheckBox("模型 API 请求使用代理")
        self.use_for_internal = QCheckBox("其他内部 HTTP 请求使用代理")
        content.addWidget(self.use_for_model)
        content.addWidget(self.use_for_internal)
        self.verify_ssl = QCheckBox("验证目标服务器 SSL 证书（推荐开启）")
        content.addWidget(self.verify_ssl)
        layout.addWidget(card)

        bypass_card, bypass_layout = card_layout()
        bypass_layout.addWidget(label("不使用代理的地址", "SectionTitle"))
        bypass_layout.addWidget(label("多个主机名使用逗号分隔；支持域名后缀和 <local>。", "Muted"))
        self.bypass_hosts = QPlainTextEdit()
        self.bypass_hosts.setMaximumHeight(92)
        self.bypass_hosts.setPlaceholderText("localhost,127.0.0.1,::1,.local")
        bypass_layout.addWidget(self.bypass_hosts)
        layout.addWidget(bypass_card)

        actions = QHBoxLayout()
        self.state_label = label("配置尚未修改", "Small")
        actions.addWidget(self.state_label)
        actions.addStretch()
        check = QPushButton("检查格式")
        check.clicked.connect(self.check_format)
        actions.addWidget(check)
        save = QPushButton("保存代理设置")
        save.setObjectName("Primary")
        save.clicked.connect(self.save_settings)
        actions.addWidget(save)
        layout.addLayout(actions)
        layout.addStretch()

    def load_settings(self) -> None:
        values = self.repository.get_proxy_settings(reveal_password=False)
        self.enabled.setChecked(values["enabled"])
        self.proxy_url.setText(values["proxy_url"])
        self.username.setText(values["username"])
        self.password.clear()
        self.password.setPlaceholderText("已保存加密密码；留空表示不修改" if values["has_password"] else "无认证时留空")
        self.use_for_model.setChecked(values["use_for_model"])
        self.use_for_internal.setChecked(values["use_for_internal"])
        self.bypass_hosts.setPlainText(values["bypass_hosts"])
        self.verify_ssl.setChecked(values["verify_ssl"])
        self._update_enabled_state()
        self.state_label.setText("配置已从数据库加载")

    def _update_enabled_state(self) -> None:
        active = self.enabled.isChecked()
        for widget in (self.proxy_url, self.username, self.password, self.use_for_model, self.use_for_internal, self.bypass_hosts, self.verify_ssl):
            widget.setEnabled(active)

    def _normalized_url(self) -> str:
        value = self.proxy_url.text().strip()
        if value and "://" not in value:
            value = "http://" + value
        return value

    def _parsed_proxy(self):
        try:
            parsed = urlsplit(self._normalized_url())
            port = parsed.port
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not port:
            return None
        return parsed

    def check_format(self) -> bool:
        if not self.enabled.isChecked():
            QMessageBox.information(self, "代理配置", "代理当前未启用，内部请求将直接连接。")
            return True
        parsed = self._parsed_proxy()
        if parsed is None:
            QMessageBox.warning(self, "格式不正确", "代理地址应包含主机和端口，例如：http://127.0.0.1:7890")
            return False
        if not self.use_for_model.isChecked() and not self.use_for_internal.isChecked():
            QMessageBox.warning(self, "应用范围为空", "请至少选择一个需要使用代理的请求类型。")
            return False
        QMessageBox.information(self, "格式正确", f"代理地址：{parsed.scheme}://{parsed.hostname}:{parsed.port}")
        return True

    def save_settings(self) -> None:
        if self.enabled.isChecked():
            if self._parsed_proxy() is None:
                QMessageBox.warning(self, "保存失败", "代理地址格式不正确，应类似：http://127.0.0.1:7890")
                return
            if not self.use_for_model.isChecked() and not self.use_for_internal.isChecked():
                QMessageBox.warning(self, "保存失败", "请至少选择一个代理应用范围。")
                return
        try:
            self.repository.save_proxy_settings({
                "enabled": self.enabled.isChecked(),
                "proxy_url": self._normalized_url(),
                "username": self.username.text(),
                "password": self.password.text(),
                "use_for_model": self.use_for_model.isChecked(),
                "use_for_internal": self.use_for_internal.isChecked(),
                "bypass_hosts": self.bypass_hosts.toPlainText(),
                "verify_ssl": self.verify_ssl.isChecked(),
            })
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "保存失败", str(error))
            return
        self.load_settings()
        self.state_label.setText("代理设置已保存")
        QMessageBox.information(self, "保存成功", "代理设置已保存，新的 HTTP 请求将立即使用该配置。")
