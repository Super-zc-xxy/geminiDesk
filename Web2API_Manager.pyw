import sys
import os
import json
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
    QStackedWidget, QFormLayout, QSystemTrayIcon, QMenu, QMessageBox,
    QFileDialog, QFrame, QStyle
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QFont, QColor

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "services_config.json")

DEFAULT_CONFIG = {
    "services": [
        {
            "id": "gemini",
            "name": "Google Gemini (4 Argon)",
            "exec_path": r"E:\GeminiDeskTop\gemini-web2api.exe",
            "port": 8081,
            "work_dir": r"E:\GeminiDeskTop",
            "auto_start": True
        }
    ]
}

class ServiceItemWidget(QWidget):
    def __init__(self, service_data, parent_manager):
        super().__init__()
        self.service_data = service_data
        self.manager = parent_manager
        self.process = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        # 状态小圆点
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #ef4444; font-size: 16px;")
        layout.addWidget(self.status_dot)

        # 服务名称与端口
        info_layout = QVBoxLayout()
        self.name_label = QLabel(service_data.get("name", "未命名服务"))
        self.name_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.url_label = QLabel(f"http://127.0.0.1:{service_data.get('port', 8081)}/v1")
        self.url_label.setStyleSheet("color: #64748b; font-size: 11px;")
        info_layout.addWidget(self.name_label)
        info_layout.addWidget(self.url_label)
        layout.addLayout(info_layout)

        layout.addStretch()

        # 复制地址按钮
        self.btn_copy = QPushButton("复制URL")
        self.btn_copy.setStyleSheet("padding: 4px 8px; border-radius: 4px; background: #f1f5f9;")
        self.btn_copy.clicked.connect(self.copy_url)
        layout.addWidget(self.btn_copy)

        # 启动/停止 开关按钮
        self.btn_toggle = QPushButton("启动")
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                padding: 6px 14px;
                background-color: #3b82f6;
                color: white;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #2563eb; }
        """)
        self.btn_toggle.clicked.connect(self.toggle_service)
        layout.addWidget(self.btn_toggle)

    def copy_url(self):
        url = self.url_label.text()
        QApplication.clipboard().setText(url)
        self.btn_copy.setText("已复制✓")
        QTimer.singleShot(1500, lambda: self.btn_copy.setText("复制URL"))

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def toggle_service(self):
        if self.is_running():
            self.stop_service()
        else:
            self.start_service()

    def start_service(self):
        exec_path = self.service_data.get("exec_path", "").strip()
        work_dir = self.service_data.get("work_dir", "").strip() or os.path.dirname(exec_path)

        if not exec_path or not os.path.exists(exec_path):
            QMessageBox.warning(self, "错误", f"找不到程序文件：\n{exec_path}")
            return

        try:
            # 静默启动子进程（无黑窗口）
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW

            self.process = subprocess.Popen(
                [exec_path],
                cwd=work_dir,
                creationflags=creationflags
            )
            self.status_dot.setStyleSheet("color: #22c55e; font-size: 16px;")
            self.btn_toggle.setText("停止")
            self.btn_toggle.setStyleSheet("""
                QPushButton {
                    padding: 6px 14px;
                    background-color: #ef4444;
                    color: white;
                    border-radius: 6px;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #dc2626; }
            """)
        except Exception as e:
            QMessageBox.critical(self, "启动失败", str(e))

    def stop_service(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

        self.status_dot.setStyleSheet("color: #ef4444; font-size: 16px;")
        self.btn_toggle.setText("启动")
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                padding: 6px 14px;
                background-color: #3b82f6;
                color: white;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #2563eb; }
        """)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web2API 管理中心")
        self.resize(550, 420)
        self.service_widgets = []
        self.load_config()
        self.init_ui()
        self.init_tray()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception:
                self.config = DEFAULT_CONFIG
        else:
            self.config = DEFAULT_CONFIG
            self.save_config()

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print("保存配置失败:", e)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 18, 18, 18)

        # 头部标题与添加按钮
        header_layout = QHBoxLayout()
        title_label = QLabel("网页反代服务中心")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #0f172a;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        btn_add = QPushButton("+ 添加新反代")
        btn_add.setStyleSheet("""
            QPushButton {
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: #e2e8f0; }
        """)
        btn_add.clicked.connect(self.show_add_dialog)
        header_layout.addWidget(btn_add)
        main_layout.addLayout(header_layout)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #e2e8f0; margin: 8px 0;")
        main_layout.addWidget(line)

        # 服务列表
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                background: #ffffff;
            }
            QListWidget::item {
                border-bottom: 1px solid #f1f5f9;
            }
        """)
        main_layout.addWidget(self.list_widget)

        self.refresh_services_list()

        # 底部提示
        tip_label = QLabel("💡 提示：点击关闭窗口会自动最小化到系统右下角托盘，并在后台常驻。")
        tip_label.setStyleSheet("color: #94a3b8; font-size: 12px; margin-top: 6px;")
        main_layout.addWidget(tip_label)

    def refresh_services_list(self):
        self.list_widget.clear()
        self.service_widgets.clear()

        for s_data in self.config.get("services", []):
            item = QListWidgetItem(self.list_widget)
            item_widget = ServiceItemWidget(s_data, self)
            item.setSizeHint(item_widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, item_widget)
            self.service_widgets.append(item_widget)

            # 是否随软件自动启动
            if s_data.get("auto_start", False):
                QTimer.singleShot(500, item_widget.start_service)

    def show_add_dialog(self):
        # 简单添加弹窗
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择反代可执行文件 (.exe 或 .py)", "E:\\", "Executable (*.exe *.bat *.py *.pyw);;All Files (*)"
        )
        if file_path:
            name = os.path.splitext(os.path.basename(file_path))[0]
            port = 8082
            # 计算可用端口
            existing_ports = [s.get("port", 8080) for s in self.config.get("services", [])]
            while port in existing_ports:
                port += 1

            new_service = {
                "id": f"service_{len(self.config['services']) + 1}",
                "name": name,
                "exec_path": file_path,
                "port": port,
                "work_dir": os.path.dirname(file_path),
                "auto_start": True
            }
            self.config["services"].append(new_service)
            self.save_config()
            self.refresh_services_list()

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        # 使用标准的系统设置图标
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)
        self.tray_icon.setIcon(icon)

        tray_menu = QMenu()
        show_action = tray_menu.addAction("打开主面板")
        show_action.triggered.connect(self.showNormal)

        quit_action = tray_menu.addAction("完全退出")
        quit_action.triggered.connect(self.quit_app)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()

    def closeEvent(self, event):
        # 拦截关闭事件，转为隐藏到托盘
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "Web2API 已最小化",
            "服务仍在后台运行，双击托盘图标即可唤出主界面。",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )

    def quit_app(self):
        # 停止所有子进程后完全退出
        for w in self.service_widgets:
            if w.is_running():
                w.stop_service()
        QApplication.quit()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
