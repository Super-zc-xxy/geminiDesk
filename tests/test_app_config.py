import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 动态探测并兼容环境中的依赖路径
for extra_path in [
    os.path.join(BASE_DIR, "deepseek_venv", "Lib", "site-packages"),
    r"E:\Hermes\hermes-agent\venv\Lib\site-packages"
]:
    if os.path.exists(extra_path) and extra_path not in sys.path:
        sys.path.append(extra_path)

import app

class TestAppConfigAndDeepSeekIntegration(unittest.TestCase):
    def test_model_options_has_deepseek(self):
        """1. MODEL_OPTIONS 必须包含 DeepSeek V4.1 系列三款模型选项"""
        options_dict = dict(app.MODEL_OPTIONS)
        self.assertIn("deepseek-v4.1-flash", options_dict.values())
        self.assertIn("deepseek-v4.1-pro", options_dict.values())
        self.assertIn("deepseek-v4.1-thinking", options_dict.values())

        expected_labels = {
            "deepseek-v4.1-flash (DeepSeek 4.1 Flash - 极速日常 - 推荐)": "deepseek-v4.1-flash",
            "deepseek-v4.1-pro (DeepSeek 4.1 Pro - 专家重构)": "deepseek-v4.1-pro",
            "deepseek-v4.1-thinking (DeepSeek 4.1 深度思考)": "deepseek-v4.1-thinking",
        }
        for label, val in expected_labels.items():
            self.assertIn((label, val), app.MODEL_OPTIONS)

    def test_deepseek_constants(self):
        """2. 检查 DeepSeek 内部端口、脚本与解释器常量"""
        self.assertEqual(getattr(app, "INTERNAL_DEEPSEEK_PORT", None), 8087)
        self.assertTrue(hasattr(app, "DEEPSEEK_SERVER_PY"))
        self.assertTrue(app.DEEPSEEK_SERVER_PY.endswith("deepseek_server.py"))
        self.assertTrue(hasattr(app, "DEEPSEEK_TOKEN_FILE"))
        self.assertTrue(app.DEEPSEEK_TOKEN_FILE.endswith("deepseek_token.txt"))
        self.assertTrue(hasattr(app, "DEEPSEEK_VENV_PYTHON"))

    def test_services_config_json_registration(self):
        """3. 检查 services_config.json 登记了 deepseek 服务 (port: 8087)"""
        cfg_path = os.path.join(BASE_DIR, "services_config.json")
        self.assertTrue(os.path.exists(cfg_path))
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        services = cfg.get("services", [])
        ds_service = next((s for s in services if s.get("id") == "deepseek"), None)
        self.assertIsNotNone(ds_service, "services_config.json 必须包含 id='deepseek' 的服务")
        self.assertEqual(ds_service.get("port"), 8087)

    def test_sync_ui_status_displays_both_engines(self):
        """4. 检查运行中状态文案体现双引擎端口 (8085 与 8087)"""
        card = app.UnifiedServiceCard.__new__(app.UnifiedServiceCard)
        card.status_icon = MagicMock()
        card.status_text = MagicMock()
        card.btn_toggle = MagicMock()

        card.sync_ui_status(True)
        calls = card.status_text.setText.call_args_list
        self.assertTrue(len(calls) > 0)
        status_msg = calls[-1][0][0]
        self.assertIn("8090", status_msg)
        self.assertIn("8085", status_msg)
        self.assertIn("8087", status_msg)
        self.assertIn("DeepSeek", status_msg)

    def test_save_deepseek_token_logic(self):
        """5. 检查 DeepSeek Token 凭据保存逻辑"""
        test_token_path = os.path.join(BASE_DIR, "test_token_tmp.txt")
        win = app.MainWindow.__new__(app.MainWindow)
        win.edit_deepseek_token = MagicMock()
        win.edit_deepseek_token.toPlainText.return_value = "dsk-test-token-value"
        win.append_log = MagicMock()

        with patch("app.DEEPSEEK_TOKEN_FILE", test_token_path), \
             patch("app.QMessageBox.information") as mock_info:
            try:
                win.save_deepseek_token()
                self.assertTrue(os.path.exists(test_token_path))
                with open(test_token_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertEqual(content, "dsk-test-token-value")
                mock_info.assert_called_once()
            finally:
                if os.path.exists(test_token_path):
                    os.remove(test_token_path)

    def test_save_deepseek_token_empty_warning(self):
        """6. 检查 DeepSeek Token 为空时给出提示警告并不予写入"""
        win = app.MainWindow.__new__(app.MainWindow)
        win.edit_deepseek_token = MagicMock()
        win.edit_deepseek_token.toPlainText.return_value = "   "
        win.append_log = MagicMock()

        with patch("app.QMessageBox.warning") as mock_warn:
            win.save_deepseek_token()
            mock_warn.assert_called_once()

    def test_start_service_launches_deepseek(self):
        """7. 检查 start_service 会正确清空 8087 并拉起 deepseek_server.py"""
        card = app.UnifiedServiceCard.__new__(app.UnifiedServiceCard)
        card.main_win = MagicMock()
        card.proc_gemini = None
        card.proc_deepseek = None
        card.proc_gateway = None
        card.sync_ui_status = MagicMock()

        killed_ports = []
        popen_cmds = []

        def fake_kill(port):
            killed_ports.append(port)

        class FakeProc:
            def poll(self):
                return None
            stdout = MagicMock()

        def fake_popen(cmd, **kwargs):
            popen_cmds.append(cmd)
            return FakeProc()

        with patch("app.kill_port_owner", side_effect=fake_kill), \
             patch("subprocess.Popen", side_effect=fake_popen), \
             patch("threading.Thread") as mock_thread, \
             patch("time.sleep"), \
             patch("app.os.path.exists", return_value=True), \
             patch("app.QTimer.singleShot"):
            card.start_service()

            # 验证杀死了三个端口
            self.assertIn(8085, killed_ports)
            self.assertIn(8087, killed_ports)
            self.assertIn(8090, killed_ports)

            # 验证启动了 DeepSeek 进程
            self.assertIsNotNone(card.proc_deepseek)
            deepseek_cmd = next((c for c in popen_cmds if any("deepseek_server" in str(arg) for arg in c)), None)
            self.assertIsNotNone(deepseek_cmd, "必须通过 Popen 启动 deepseek_server (exe 或 py)")
            self.assertIn("8087", [str(arg) for arg in deepseek_cmd])

    def test_stop_service_terminates_deepseek(self):
        """8. 检查 stop_service 会终止 proc_deepseek 并清理 8087 端口"""
        card = app.UnifiedServiceCard.__new__(app.UnifiedServiceCard)
        card.main_win = MagicMock()
        card.proc_gemini = MagicMock()
        card.proc_deepseek = MagicMock()
        card.proc_gateway = MagicMock()
        card.sync_ui_status = MagicMock()

        killed_ports = []
        def fake_kill(port):
            killed_ports.append(port)

        with patch("app.kill_port_owner", side_effect=fake_kill):
            card.stop_service()

            self.assertIsNone(card.proc_deepseek)
            self.assertIsNone(card.proc_gemini)
            self.assertIsNone(card.proc_gateway)
            self.assertIn(8087, killed_ports)
            self.assertIn(8085, killed_ports)
            self.assertIn(8090, killed_ports)

if __name__ == "__main__":
    unittest.main()
