import sys
import os
import json
import subprocess
import threading
import time
import socket
import requests
import winreg

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QStackedWidget,
    QSystemTrayIcon, QMenu, QMessageBox, QFrame, QStyle,
    QTabWidget, QGroupBox, QScrollArea, QComboBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QFont, QColor, QAction

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INTERNAL_GEMINI_PORT = 8085
INTERNAL_DEEPSEEK_PORT = 8087
PUBLIC_PORT = 8090

GEMINI_EXE = os.path.join(BASE_DIR, "gemini-web2api.exe")
GEMINI_COOKIE_FILE = os.path.join(BASE_DIR, "cookie.txt")
DEEPSEEK_SERVER_PY = os.path.join(BASE_DIR, "deepseek_server.py")
DEEPSEEK_TOKEN_FILE = os.path.join(BASE_DIR, "deepseek_token.txt")
DEEPSEEK_VENV_PYTHON = os.path.join(BASE_DIR, "deepseek_venv", "Scripts", "python.exe")
if not os.path.exists(DEEPSEEK_VENV_PYTHON):
    DEEPSEEK_VENV_PYTHON = sys.executable

GATEWAY_SERVER_PY = os.path.join(BASE_DIR, "gateway_server.py")
CONFIG_FILE = os.path.join(BASE_DIR, "settings_config.json")
APP_ICON = os.path.join(BASE_DIR, "app.ico")

DEFAULT_CONFIG = {
    "port": 8090,
    "api_key": "sk-gemini-argon",
    "selected_model": "gemini-4-hydrogen",
    "auto_start": True
}

MODEL_OPTIONS = [
    ("deepseek-v4.1-flash (DeepSeek 4.1 Flash - 极速日常 - 推荐)", "deepseek-v4.1-flash"),
    ("deepseek-v4.1-pro (DeepSeek 4.1 Pro - 专家重构)", "deepseek-v4.1-pro"),
    ("deepseek-v4.1-thinking (DeepSeek 4.1 深度思考)", "deepseek-v4.1-thinking"),
    ("gemini-4-hydrogen (Google 4 Hydrogen - 最新日常极速 - 推荐)", "gemini-4-hydrogen"),
    ("gemini-3.8-flash (Google 3.8 Flash - 高速代码开发)", "gemini-3.8-flash"),
    ("gemini-flash-lite (Google 3.5 Flash-Lite - 秒级快速解答)", "gemini-flash-lite"),
    ("gemini-3.5-flash (Google 经典高速版)", "gemini-3.5-flash"),
    ("gemini-3.5-flash-thinking (Google 深度推理版)", "gemini-3.5-flash-thinking"),
    ("gemini-3.1-pro (Google 3.1 Pro - 深度研究版)", "gemini-3.1-pro"),
    ("gemini-4-argon (Google 4 Argon - 代码与分析)", "gemini-4-argon"),
    ("gemini-4-carbon (Google 4 Carbon - 高难任务)", "gemini-4-carbon"),
    ("auto (自动探测最新模型)", "auto"),
]

def is_autostart_enabled():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, "Web2API-Hub")
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def set_autostart(enable: bool):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if enable:
            exe_path = os.path.join(BASE_DIR, "Web2API-Hub.exe")
            winreg.SetValueEx(key, "Web2API-Hub", 0, winreg.REG_SZ, f'"{exe_path}"')
        else:
            try:
                winreg.DeleteValue(key, "Web2API-Hub")
            except Exception:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print("设置自启动失败:", e)

def is_port_open(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex(('127.0.0.1', port)) == 0
    except Exception:
        return False

def kill_port_owner(port):
    if sys.platform != "win32":
        return
    try:
        cmd = f'powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue; if ($c) {{ Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }}"'
        subprocess.run(cmd, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass

class LogEmitter(QObject):
    new_log = pyqtSignal(str)

class ModernCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            ModernCard {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                padding: 18px;
            }
        """)

class UnifiedServiceCard(ModernCard):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win
        self.proc_gemini = None
        self.proc_deepseek = None
        self.proc_gateway = None

        self.init_card_ui()

    def init_card_ui(self):
        card_layout = QVBoxLayout(self)
        card_layout.setSpacing(14)

        # 头部：状态与开关
        top_row = QHBoxLayout()
        self.status_icon = QLabel("●")
        self.status_icon.setStyleSheet("color: #ef4444; font-size: 22px;")
        top_row.addWidget(self.status_icon)

        title_box = QVBoxLayout()
        svc_title = QLabel("Web2API Gemini 桌面反代管理中心")
        svc_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #0f172a;")
        self.status_text = QLabel(f"状态：已停止 (统一对外端口: {PUBLIC_PORT})")
        self.status_text.setStyleSheet("color: #64748b; font-size: 13px;")
        title_box.addWidget(svc_title)
        title_box.addWidget(self.status_text)
        top_row.addLayout(title_box)

        top_row.addStretch()

        self.btn_toggle = QPushButton("启动服务")
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 24px;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #1d4ed8; }
        """)
        self.btn_toggle.clicked.connect(self.toggle_service)
        top_row.addWidget(self.btn_toggle)
        card_layout.addLayout(top_row)

        card_layout.addWidget(self.create_separator())

        # 模型选择器行
        model_row = QHBoxLayout()
        lbl_model = QLabel("目标反代模型:")
        lbl_model.setStyleSheet("font-weight: bold; color: #334155; font-size: 13px; min-width: 90px;")
        model_row.addWidget(lbl_model)

        self.combo_model = QComboBox()
        self.combo_model.setStyleSheet("""
            QComboBox {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 12px;
                background: #f8fafc;
                font-size: 13px;
                font-weight: 500;
                color: #0f172a;
            }
            QComboBox:hover {
                border-color: #2563eb;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
        """)
        for label, val in MODEL_OPTIONS:
            self.combo_model.addItem(label, val)

        # 设置当前选中模型
        current_model = self.main_win.config.get("selected_model", "gemini-4-argon")
        for idx in range(self.combo_model.count()):
            if self.combo_model.itemData(idx) == current_model:
                self.combo_model.setCurrentIndex(idx)
                break

        self.combo_model.currentIndexChanged.connect(self.on_model_changed)
        model_row.addWidget(self.combo_model, 1)

        btn_sync = QPushButton("🔄 自动更新模型")
        btn_sync.setToolTip("从运行中的 Gemini 核心引擎同步最新发现的模型")
        btn_sync.setStyleSheet("padding: 5px 12px; background: #eff6ff; border: 1px solid #bfdbfe; color: #1d4ed8; border-radius: 6px; font-size: 12px; font-weight: 500;")
        btn_sync.clicked.connect(lambda: self.sync_models_from_backend(True))
        model_row.addWidget(btn_sync)

        self.lbl_model_hint = QLabel("即时生效无需重启")
        self.lbl_model_hint.setStyleSheet("color: #10b981; font-size: 12px; font-weight: 500;")
        model_row.addWidget(self.lbl_model_hint)
        card_layout.addLayout(model_row)

        card_layout.addWidget(self.create_separator())

        # Base URL 行
        url_row = QHBoxLayout()
        lbl_url = QLabel("Base URL:")
        lbl_url.setStyleSheet("font-weight: bold; color: #334155; font-size: 13px; min-width: 90px;")
        url_row.addWidget(lbl_url)

        self.url_edit = QLineEdit(f"http://127.0.0.1:{PUBLIC_PORT}/v1")
        self.url_edit.setReadOnly(True)
        self.url_edit.setStyleSheet("padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px; background: #f8fafc; font-family: Consolas; font-size: 13px;")
        url_row.addWidget(self.url_edit)

        btn_copy_url = QPushButton("复制 URL")
        btn_copy_url.setStyleSheet("padding: 6px 14px; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; font-weight: 500;")
        btn_copy_url.clicked.connect(lambda: self.copy_text(self.url_edit.text(), btn_copy_url, "复制 URL"))
        url_row.addWidget(btn_copy_url)

        btn_test = QPushButton("测试连接")
        btn_test.setStyleSheet("padding: 6px 14px; background: #f0fdf4; border: 1px solid #86efac; color: #166534; border-radius: 6px; font-weight: 500;")
        btn_test.clicked.connect(self.test_connection)
        url_row.addWidget(btn_test)

        card_layout.addLayout(url_row)

        # API Key 行
        key_row = QHBoxLayout()
        lbl_key = QLabel("API Key:")
        lbl_key.setStyleSheet("font-weight: bold; color: #334155; font-size: 13px; min-width: 90px;")
        key_row.addWidget(lbl_key)

        api_key_val = self.main_win.config.get("api_key", "sk-gemini-argon")
        self.key_edit = QLineEdit(api_key_val)
        self.key_edit.setReadOnly(True)
        self.key_edit.setStyleSheet("padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px; background: #f8fafc; font-family: Consolas; font-size: 13px;")
        key_row.addWidget(self.key_edit)

        btn_copy_key = QPushButton("复制 Key")
        btn_copy_key.setStyleSheet("padding: 6px 14px; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; font-weight: 500;")
        btn_copy_key.clicked.connect(lambda: self.copy_text(self.key_edit.text(), btn_copy_key, "复制 Key"))
        key_row.addWidget(btn_copy_key)

        spacer_btn = QPushButton("")
        spacer_btn.setStyleSheet("visibility: hidden; padding: 6px 14px; border: none;")
        key_row.addWidget(spacer_btn)

        card_layout.addLayout(key_row)

    def create_separator(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setStyleSheet("background-color: #f1f5f9; height: 1px; border: none;")
        return line

    def on_model_changed(self):
        new_model = self.combo_model.currentData()
        self.main_win.config["selected_model"] = new_model
        self.main_win.save_config()
        self.lbl_model_hint.setText("✓ 已切换并即时生效")
        self.main_win.append_log(f"[配置切换] 目标反代模型已切换为: {new_model} (所有客户端即刻生效)")
        QTimer.singleShot(2500, lambda: self.lbl_model_hint.setText("即时生效无需重启"))

    def sync_models_from_backend(self, show_msg=True):
        try:
            resp = requests.get(f"http://127.0.0.1:{PUBLIC_PORT}/v1/models", timeout=3, proxies={"http": None, "https": None})
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                backend_models = [item.get("id") for item in data if item.get("id")]
                
                current_selected = self.combo_model.currentData() or self.main_win.config.get("selected_model", "auto")
                existing_values = [self.combo_model.itemData(i) for i in range(self.combo_model.count())]
                new_added = 0
                for mid in backend_models:
                    if mid not in existing_values:
                        display_name = f"{mid} (动态探测)"
                        self.combo_model.addItem(display_name, mid)
                        existing_values.append(mid)
                        new_added += 1

                for i in range(self.combo_model.count()):
                    if self.combo_model.itemData(i) == current_selected:
                        self.combo_model.setCurrentIndex(i)
                        break

                self.main_win.append_log(f"[模型同步] 成功同步最新模型列表 (Gemini 可用模型 {len(existing_values)} 个，新增 {new_added} 个)")
                if show_msg:
                    QMessageBox.information(
                        self, "模型同步成功 ✓",
                        f"已成功探测并同步 Google Gemini 最新可用模型！\n\n"
                        f"• 当前可用模型总数: {len(existing_values)} 个\n"
                        f"• 新增探测同步的模型: {new_added} 个\n\n"
                        f"你可以在下拉框直接选择，或在客户端中直接输入对应模型 ID！"
                    )
            else:
                if show_msg:
                    QMessageBox.warning(self, "同步失败", f"网关返回异常状态码: {resp.status_code}")
        except Exception as e:
            if show_msg:
                QMessageBox.warning(self, "同步提示", f"未能获取最新模型列表，请确保服务已启动！\n\n详情: {e}")

    def copy_text(self, text, btn, origin_text):
        QApplication.clipboard().setText(text)
        btn.setText("已复制✓")
        QTimer.singleShot(1500, lambda: btn.setText(origin_text))

    def is_running(self):
        if is_port_open(PUBLIC_PORT):
            return True
        if self.proc_gateway and self.proc_gateway.poll() is None:
            return True
        if self.proc_deepseek and self.proc_deepseek.poll() is None:
            return True
        return False

    def sync_ui_status(self, running: bool):
        if running:
            self.status_icon.setStyleSheet("color: #22c55e; font-size: 22px;")
            self.status_text.setText(f"状态：🟢 正在运行中 (公共网关: {PUBLIC_PORT} | Gemini: {INTERNAL_GEMINI_PORT} | DeepSeek: {INTERNAL_DEEPSEEK_PORT})")
            self.status_text.setStyleSheet("color: #16a34a; font-weight: bold; font-size: 13px;")
            self.btn_toggle.setText("停止服务")
            self.btn_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #ef4444;
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 8px 24px;
                    border-radius: 8px;
                }
                QPushButton:hover { background-color: #dc2626; }
            """)
        else:
            self.status_icon.setStyleSheet("color: #ef4444; font-size: 22px;")
            self.status_text.setText(f"状态：已停止 (统一对外端口: {PUBLIC_PORT})")
            self.status_text.setStyleSheet("color: #64748b; font-size: 13px;")
            self.btn_toggle.setText("启动服务")
            self.btn_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #2563eb;
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 8px 24px;
                    border-radius: 8px;
                }
                QPushButton:hover { background-color: #1d4ed8; }
            """)

    def toggle_service(self):
        if self.is_running():
            self.stop_service()
        else:
            self.start_service()

    def start_service(self):
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        if not os.path.exists(GEMINI_EXE):
            QMessageBox.warning(self, "错误", f"找不到 Gemini 可执行程序：\n{GEMINI_EXE}")
            return

        python_exe = os.path.join(BASE_DIR, "deepseek_venv", "Scripts", "python.exe")
        if not os.path.exists(python_exe):
            python_exe = r"E:\Hermes\hermes-agent\venv\Scripts\python.exe"
        if not os.path.exists(python_exe):
            python_exe = sys.executable

        kill_port_owner(INTERNAL_GEMINI_PORT)
        kill_port_owner(INTERNAL_DEEPSEEK_PORT)
        kill_port_owner(PUBLIC_PORT)
        time.sleep(0.3)

        try:
            # 1. 启动内部 Gemini 核心 (8085)
            cmd_gemini = [GEMINI_EXE, "-port", str(INTERNAL_GEMINI_PORT)]
            if os.path.exists(GEMINI_COOKIE_FILE) and os.path.getsize(GEMINI_COOKIE_FILE) > 0:
                cmd_gemini.extend(["-cookie-file", "cookie.txt"])

            gemini_env = os.environ.copy()
            if is_port_open(7890):
                gemini_env["http_proxy"] = "http://127.0.0.1:7890"
                gemini_env["https_proxy"] = "http://127.0.0.1:7890"
                gemini_env["HTTP_PROXY"] = "http://127.0.0.1:7890"
                gemini_env["HTTPS_PROXY"] = "http://127.0.0.1:7890"
                cmd_gemini.extend(["-proxy", "http://127.0.0.1:7890"])

            self.proc_gemini = subprocess.Popen(
                cmd_gemini,
                cwd=BASE_DIR,
                env=gemini_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
                encoding="utf-8",
                errors="replace"
            )
            threading.Thread(target=lambda: self.stream_logs(self.proc_gemini, "Gemini核心"), daemon=True).start()

            # 2. 启动内部 DeepSeek 核心 (8087)
            if os.path.exists(DEEPSEEK_SERVER_PY):
                ds_python = DEEPSEEK_VENV_PYTHON if os.path.exists(DEEPSEEK_VENV_PYTHON) else sys.executable
                self.proc_deepseek = subprocess.Popen(
                    [ds_python, DEEPSEEK_SERVER_PY, str(INTERNAL_DEEPSEEK_PORT)],
                    cwd=BASE_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=creationflags,
                    encoding="utf-8",
                    errors="replace"
                )
                threading.Thread(target=lambda: self.stream_logs(self.proc_deepseek, "DeepSeek核心"), daemon=True).start()

            # 3. 启动对外统一网关 (8090)
            self.proc_gateway = subprocess.Popen(
                [python_exe, GATEWAY_SERVER_PY, str(PUBLIC_PORT)],
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
                encoding="utf-8",
                errors="replace"
            )
            threading.Thread(target=lambda: self.stream_logs(self.proc_gateway, "Gemini智能网关"), daemon=True).start()

            self.sync_ui_status(True)
            self.main_win.append_log(f"[服务管理] Web2API 双引擎反代服务已启动！统一监听端口: {PUBLIC_PORT} (Gemini: {INTERNAL_GEMINI_PORT} | DeepSeek: {INTERNAL_DEEPSEEK_PORT})")
            QTimer.singleShot(2500, lambda: self.sync_models_from_backend(False))
        except Exception as e:
            self.sync_ui_status(False)
            QMessageBox.critical(self, "启动失败", f"启动失败：{e}")

    def stream_logs(self, proc, tag):
        while proc and proc.poll() is None:
            line = proc.stdout.readline()
            if line:
                self.main_win.append_log(f"[{tag}] {line.strip()}")
            else:
                time.sleep(0.1)

    def stop_service(self):
        if self.proc_gateway:
            self.proc_gateway.terminate()
            try:
                self.proc_gateway.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.proc_gateway.kill()
            self.proc_gateway = None

        if self.proc_deepseek:
            self.proc_deepseek.terminate()
            try:
                self.proc_deepseek.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.proc_deepseek.kill()
            self.proc_deepseek = None

        if self.proc_gemini:
            self.proc_gemini.terminate()
            try:
                self.proc_gemini.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.proc_gemini.kill()
            self.proc_gemini = None

        kill_port_owner(INTERNAL_GEMINI_PORT)
        kill_port_owner(INTERNAL_DEEPSEEK_PORT)
        kill_port_owner(PUBLIC_PORT)

        self.sync_ui_status(False)
        self.main_win.append_log(f"[服务管理] 反代服务已停止。")

    def test_connection(self):
        try:
            resp = requests.get(f"http://127.0.0.1:{PUBLIC_PORT}/v1/models", timeout=3, proxies={"http": None, "https": None})
            if resp.status_code == 200:
                self.sync_ui_status(True)
                data = resp.json()
                models = [m.get("id") for m in data.get("data", [])]
                current_choice = self.combo_model.currentData()
                QMessageBox.information(
                    self, "测试成功 ✓",
                    f"【Gemini 全能反代服务】运行正常！(HTTP 200)\n\n"
                    f"当前生效目标模型: {current_choice}\n"
                    f"服务端口: {PUBLIC_PORT}\n"
                    f"兼容协议: OpenAI Chat & Codex Responses API"
                )
            else:
                QMessageBox.warning(self, "连接异常", f"服务返回异常状态码: {resp.status_code}")
        except Exception as e:
            self.sync_ui_status(False)
            QMessageBox.warning(
                self, "连接未就绪",
                f"无法连接到本地端口 {PUBLIC_PORT}。\n"
                f"服务可能尚未启动或正在初始化，请点击【启动服务】后重试。\n\n"
                f"详细错误: {e}"
            )

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web2API Hub - 网页端大模型反代管理中心")
        self.resize(980, 620)
        self.setMinimumSize(900, 560)

        self.log_emitter = LogEmitter()
        self.log_emitter.new_log.connect(self.add_log_text)

        self.load_config()
        self.init_ui()
        self.init_tray()

        # 启动后自动启动服务
        if self.config.get("auto_start", True):
            QTimer.singleShot(600, self.card_service.start_service)

        # 定时器：每 1.5 秒自动同步真实运行健康状态
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self.check_service_health)
        self.health_timer.start(1500)

    def check_service_health(self):
        running = self.card_service.is_running()
        self.card_service.sync_ui_status(running)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception:
                self.config = DEFAULT_CONFIG.copy()
        else:
            self.config = DEFAULT_CONFIG.copy()
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

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 侧边栏
        sidebar = QWidget()
        sidebar.setFixedWidth(190)
        sidebar.setStyleSheet("background-color: #0f172a;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 24, 12, 24)
        sidebar_layout.setSpacing(10)

        # Logo / 标题
        logo_label = QLabel("⚡ Web2API Hub")
        logo_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #f8fafc; padding-bottom: 20px;")
        sidebar_layout.addWidget(logo_label)

        # 导航按钮
        self.nav_btns = []
        nav_items = [("🚀 服务管理", 0), ("⚙️ 配置参数", 1), ("📜 运行日志", 2)]
        for text, idx in nav_items:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setChecked(idx == 0)
            btn.setStyleSheet("""
                QPushButton {
                    text-align: left;
                    padding: 10px 14px;
                    border-radius: 8px;
                    color: #94a3b8;
                    font-size: 14px;
                    font-weight: 500;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #1e293b;
                    color: #f1f5f9;
                }
                QPushButton:checked {
                    background-color: #2563eb;
                    color: #ffffff;
                    font-weight: bold;
                }
            """)
            btn.clicked.connect(lambda checked, i=idx: self.switch_page(i))
            sidebar_layout.addWidget(btn)
            self.nav_btns.append(btn)

        sidebar_layout.addStretch()

        # 版本标识
        lbl_version = QLabel("v2.0 极简通用版")
        lbl_version.setStyleSheet("color: #475569; font-size: 11px; padding-left: 6px;")
        sidebar_layout.addWidget(lbl_version)

        main_layout.addWidget(sidebar)

        # 主内容区
        content_area = QWidget()
        content_area.setStyleSheet("background-color: #f8fafc;")
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.create_services_page())
        self.stack.addWidget(self.create_settings_page())
        self.stack.addWidget(self.create_logs_page())

        content_layout.addWidget(self.stack)
        main_layout.addWidget(content_area)

    def switch_page(self, index):
        for i, btn in enumerate(self.nav_btns):
            btn.setChecked(i == index)
        self.stack.setCurrentIndex(index)

    # ================= 页面 1: 服务管理 =================
    def create_services_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        header = QLabel("反代服务控制台")
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #0f172a;")
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 16, 0)
        scroll_layout.setSpacing(16)

        # 核心服务卡片
        self.card_service = UnifiedServiceCard(self)
        scroll_layout.addWidget(self.card_service)

        # 提示卡片
        tip_card = ModernCard()
        tip_layout = QVBoxLayout(tip_card)
        tip_title = QLabel("📌 全客户端无缝接入指南")
        tip_title.setStyleSheet("font-weight: bold; color: #334155; font-size: 14px;")
        tip_layout.addWidget(tip_title)

        tip_desc = QLabel(
            f"• <b>统一端口</b>：所有客户端一律使用 <code>http://127.0.0.1:{PUBLIC_PORT}/v1</code>，无需区分端口！<br>"
            "• <b>全协议自适应</b>：<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;1. <b>Codex</b>：发送 <code>/v1/responses</code> 请求，网关自动转化为流式回答，彻底杜绝 404 与 403。<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;2. <b>Cursor / Cherry Studio / NextChat</b>：发送标准 <code>/v1/chat/completions</code> 即可秒级连通。<br>"
            "• <b>动态模型切换</b>：在上方下拉菜单选择目标模型后<b>即时生效</b>，Codex 的所有输入会自动由你选中的模型作答！"
        )
        tip_desc.setStyleSheet("color: #475569; line-height: 1.8; font-size: 12px;")
        tip_layout.addWidget(tip_desc)
        scroll_layout.addWidget(tip_card)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        return page

    # ================= 页面 2: 配置参数 =================
    def create_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        header = QLabel("系统凭据与高级配置")
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #0f172a;")
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #e2e8f0; border-radius: 8px; background: white; padding: 16px; }
            QTabBar::tab { background: #f1f5f9; color: #475569; padding: 8px 16px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 4px; font-weight: 500; }
            QTabBar::tab:selected { background: white; color: #2563eb; font-weight: bold; border: 1px solid #e2e8f0; border-bottom: none; }
        """)

        # Tab: Gemini 凭据
        tab_gemini = QWidget()
        g_layout = QVBoxLayout(tab_gemini)
        g_tip = QLabel("Google Gemini Cookie 凭据 (cookie.txt 内容)：")
        g_tip.setStyleSheet("font-weight: bold; color: #1e293b;")
        g_layout.addWidget(g_tip)

        self.edit_gemini_cookie = QTextEdit()
        self.edit_gemini_cookie.setStyleSheet("font-family: Consolas; font-size: 11px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px;")
        if os.path.exists(GEMINI_COOKIE_FILE):
            try:
                with open(GEMINI_COOKIE_FILE, "r", encoding="utf-8") as f:
                    self.edit_gemini_cookie.setPlainText(f.read())
            except Exception:
                pass
        g_layout.addWidget(self.edit_gemini_cookie)

        btn_save_g = QPushButton("保存 Gemini 凭据")
        btn_save_g.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 6px 16px; border-radius: 6px;")
        btn_save_g.clicked.connect(self.save_gemini_cookie)
        g_btn_row = QHBoxLayout()
        g_btn_row.addStretch()
        g_btn_row.addWidget(btn_save_g)
        g_layout.addLayout(g_btn_row)

        tabs.addTab(tab_gemini, "Gemini Cookie 凭据")

        # Tab: DeepSeek Token 凭据
        tab_deepseek = QWidget()
        d_layout = QVBoxLayout(tab_deepseek)
        d_tip = QLabel("DeepSeek 网页端 Token 凭据 (deepseek_token.txt 内容)：")
        d_tip.setStyleSheet("font-weight: bold; color: #1e293b;")
        d_layout.addWidget(d_tip)

        self.edit_deepseek_token = QTextEdit()
        self.edit_deepseek_token.setStyleSheet("font-family: Consolas; font-size: 11px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px;")
        if os.path.exists(DEEPSEEK_TOKEN_FILE):
            try:
                with open(DEEPSEEK_TOKEN_FILE, "r", encoding="utf-8") as f:
                    self.edit_deepseek_token.setPlainText(f.read())
            except Exception:
                pass
        d_layout.addWidget(self.edit_deepseek_token)

        btn_save_d = QPushButton("保存 DeepSeek 凭据")
        btn_save_d.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 6px 16px; border-radius: 6px;")
        btn_save_d.clicked.connect(self.save_deepseek_token)
        d_btn_row = QHBoxLayout()
        d_btn_row.addStretch()
        d_btn_row.addWidget(btn_save_d)
        d_layout.addLayout(d_btn_row)

        tabs.addTab(tab_deepseek, "DeepSeek Token 凭据")

        layout.addWidget(tabs)

        # 开机自启动设置
        opt_card = ModernCard()
        opt_layout = QHBoxLayout(opt_card)
        opt_lbl = QLabel("开机自动启动 Web2API Hub")
        opt_lbl.setStyleSheet("font-weight: 500; color: #1e293b; font-size: 13px;")
        opt_layout.addWidget(opt_lbl)
        opt_layout.addStretch()

        self.btn_autostart_toggle = QPushButton("启用" if not is_autostart_enabled() else "已启用 (点击关闭)")
        self.btn_autostart_toggle.setStyleSheet("""
            QPushButton {
                padding: 6px 14px;
                border-radius: 6px;
                background-color: #f1f5f9;
                border: 1px solid #cbd5e1;
                font-weight: bold;
            }
        """)
        self.btn_autostart_toggle.clicked.connect(self.toggle_autostart)
        opt_layout.addWidget(self.btn_autostart_toggle)

        layout.addWidget(opt_card)

        # 核心引擎版本与在线检测升级
        core_card = ModernCard()
        core_layout = QVBoxLayout(core_card)
        core_top = QHBoxLayout()
        core_lbl = QLabel("核心引擎版本：gemini-web2api v1.2.0 (支持模型动态发现)")
        core_lbl.setStyleSheet("font-weight: 500; color: #1e293b; font-size: 13px;")
        core_top.addWidget(core_lbl)
        core_top.addStretch()

        btn_check_update = QPushButton("🔍 检查最新版本与模型")
        btn_check_update.setStyleSheet("""
            QPushButton {
                padding: 6px 14px;
                border-radius: 6px;
                background-color: #f0fdf4;
                border: 1px solid #86efac;
                color: #166534;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #dcfce7; }
        """)
        btn_check_update.clicked.connect(self.check_core_updates)
        core_top.addWidget(btn_check_update)
        core_layout.addLayout(core_top)

        core_desc = QLabel("核心引擎启动时会自动向 Google 动态探测账号开放的新模型。若开源社区发布了支持更多新架构特性的新版本，点击此按钮可一键联网检测。")
        core_desc.setStyleSheet("color: #64748b; font-size: 12px; line-height: 1.6;")
        core_layout.addWidget(core_desc)

        layout.addWidget(core_card)
        layout.addStretch()
        return page

    def check_core_updates(self):
        import webbrowser
        self.append_log("[版本检测] 正在连接 GitHub 检测最新核心版本与模型支持...")
        try:
            resp = requests.get("https://api.github.com/repos/ikhsan3adi/gemini-web2api/releases/latest", timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "v1.2.0")
                body = data.get("body", "")
                html_url = data.get("html_url", "https://github.com/ikhsan3adi/gemini-web2api/releases")

                if hasattr(self, 'card_service'):
                    self.card_service.sync_models_from_backend(show_msg=False)

                msg = (
                    f"【检测完成】\n\n"
                    f"• 当前本地内核: v1.2.0\n"
                    f"• GitHub 社区最新版本: {tag}\n\n"
                    f"更新日志/新模型特性:\n{body[:260]}...\n\n"
                    f"是否打开 GitHub Release 下载发布页？"
                )
                reply = QMessageBox.question(self, "内核更新检测", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    webbrowser.open(html_url)
            else:
                QMessageBox.warning(self, "检测提示", f"GitHub 响应状态码: {resp.status_code}，请稍后重试。")
        except Exception as e:
            QMessageBox.warning(self, "检测失败", f"无法连接到 GitHub Release 检测服务：\n{e}\n\n(提示：核心会自动向 Google 探测账号模型，通常无需频繁更换内核)")

    def save_gemini_cookie(self):
        content = self.edit_gemini_cookie.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "Cookie 内容不能为空！")
            return
        try:
            with open(GEMINI_COOKIE_FILE, "w", encoding="utf-8") as f:
                f.write(content)
            QMessageBox.information(self, "保存成功", "Gemini Cookie 已更新保存！")
            self.append_log("[配置保存] Gemini Cookie 凭据已成功写入 cookie.txt。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入失败：{e}")

    def save_deepseek_token(self):
        content = self.edit_deepseek_token.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "DeepSeek Token 内容不能为空！")
            return
        try:
            with open(DEEPSEEK_TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(content)
            QMessageBox.information(self, "保存成功", "DeepSeek Token 已更新保存！")
            self.append_log("[配置保存] DeepSeek Token 凭据已成功写入 deepseek_token.txt。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入失败：{e}")

    def toggle_autostart(self):
        curr = is_autostart_enabled()
        set_autostart(not curr)
        new_state = not curr
        self.btn_autostart_toggle.setText("已启用 (点击关闭)" if new_state else "启用")
        QMessageBox.information(self, "设置完成", f"开机自动启动已{'开启' if new_state else '关闭'}！")

    # ================= 页面 3: 运行日志 =================
    def create_logs_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header = QLabel("实时运行日志")
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #0f172a;")
        header_row.addWidget(header)
        header_row.addStretch()

        btn_clear = QPushButton("清空日志")
        btn_clear.setStyleSheet("background: #f1f5f9; border: 1px solid #cbd5e1; padding: 5px 12px; border-radius: 6px;")
        btn_clear.clicked.connect(lambda: self.log_box.clear())
        header_row.addWidget(btn_clear)
        layout.addLayout(header_row)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("""
            QTextEdit {
                background-color: #0f172a;
                color: #38bdf8;
                font-family: Consolas, monospace;
                font-size: 12px;
                border-radius: 8px;
                padding: 12px;
                border: 1px solid #334155;
            }
        """)
        layout.addWidget(self.log_box)
        return page

    def append_log(self, text):
        now_str = time.strftime("%H:%M:%S")
        self.log_emitter.new_log.emit(f"[{now_str}] {text}")

    def add_log_text(self, text):
        self.log_box.append(text)

    # ================= 系统托盘与现代化右键菜单 =================
    def init_tray(self):
        self.tray = QSystemTrayIcon(self)
        if os.path.exists(APP_ICON):
            self.tray.setIcon(QIcon(APP_ICON))
        else:
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)
            self.tray.setIcon(icon)

        # 现代暗色 Fluent 风格
        self.tray_menu = QMenu()
        self.tray_menu.setStyleSheet("""
            QMenu {
                background-color: #0f172a;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 6px 4px;
            }
            QMenu::item {
                color: #f1f5f9;
                padding: 7px 24px 7px 12px;
                border-radius: 5px;
                font-size: 13px;
                font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            }
            QMenu::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QMenu::item:disabled {
                color: #64748b;
                background-color: transparent;
            }
            QMenu::separator {
                height: 1px;
                background: #1e293b;
                margin: 5px 8px;
            }
        """)

        self.tray_menu.aboutToShow.connect(self.update_tray_menu)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda r: self.showNormal() if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def update_tray_menu(self):
        self.tray_menu.clear()

        # 1. 顶部打开控制台
        act_show = self.tray_menu.addAction("⚡ 打开控制台主界面")
        font = act_show.font()
        font.setBold(True)
        act_show.setFont(font)
        act_show.triggered.connect(self.showNormal)

        self.tray_menu.addSeparator()

        # 2. 服务状态
        running = self.card_service.is_running()
        status_text = "🟢 双引擎反代: 运行中 (点击暂停)" if running else "🔴 双引擎反代: 已停止 (点击启动)"
        act_toggle = self.tray_menu.addAction(status_text)
        act_toggle.triggered.connect(self.card_service.toggle_service)

        # 3. 当前模型显示
        curr_m = self.config.get("selected_model", "gemini-4-argon")
        act_m = self.tray_menu.addAction(f"🎯 目标模型: {curr_m}")
        act_m.setEnabled(False)

        self.tray_menu.addSeparator()

        # 4. 一键复制 URL 与 Key
        act_cp_url = self.tray_menu.addAction(f"📋 复制 Base URL ({PUBLIC_PORT})")
        act_cp_url.triggered.connect(lambda: self.copy_from_tray(f"http://127.0.0.1:{PUBLIC_PORT}/v1", "Base URL"))

        act_cp_key = self.tray_menu.addAction("📋 复制 API Key")
        act_cp_key.triggered.connect(lambda: self.copy_from_tray(self.config.get("api_key", "sk-gemini-argon"), "API Key"))

        self.tray_menu.addSeparator()

        # 5. 开机自启动
        act_autostart = self.tray_menu.addAction("开机自动启动")
        act_autostart.setCheckable(True)
        act_autostart.setChecked(is_autostart_enabled())
        act_autostart.triggered.connect(lambda checked: set_autostart(checked))

        self.tray_menu.addSeparator()

        # 6. 退出
        act_quit = self.tray_menu.addAction("❌ 彻底退出服务")
        act_quit.triggered.connect(self.quit_app)

    def copy_from_tray(self, text, name):
        QApplication.clipboard().setText(text)
        self.tray.showMessage(
            "已复制到剪贴板",
            f"{name} 已复制：{text}",
            QSystemTrayIcon.MessageIcon.Information,
            1500
        )

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Web2API Hub 已常驻托盘",
            "右键托盘图标可快速开关服务、复制 URL 或打开控制台。",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )

    def quit_app(self):
        if self.card_service.is_running():
            self.card_service.stop_service()
        QApplication.quit()

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        import traceback
        with open(os.path.join(BASE_DIR, "crash.log"), "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
