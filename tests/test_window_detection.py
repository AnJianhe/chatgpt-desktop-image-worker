"""窗口识别回归：用 Win32 枚举输入测试真实 find()，不激活任何窗口。"""
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from chatgpt_image_ui import Windows


NATIVE_PATH = r"C:\Program Files\WindowsApps\OpenAI.Codex_26.930.2377.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe"


class FakeUser32:
    def __init__(self, windows, foreground):
        self.windows = {item[0]: item for item in windows}
        self.foreground = foreground

    def EnumWindows(self, visit, argument):
        for hwnd in self.windows:
            if not visit(hwnd, argument):
                break
        return True

    def GetWindowTextLengthW(self, hwnd):
        return len(self.windows[hwnd][1])

    def IsWindowVisible(self, hwnd):
        return True

    def GetWindowTextW(self, hwnd, buffer, capacity):
        buffer.value = self.windows[hwnd][1][:capacity - 1]
        return len(buffer.value)

    def GetForegroundWindow(self):
        return self.foreground


def detector(windows, foreground=0):
    instance = Windows.__new__(Windows)
    instance.u = FakeUser32(windows, foreground)
    instance.callback = lambda visit: visit
    instance.process_path = lambda hwnd: instance.u.windows[hwnd][2]
    return instance


class WindowDetectionTests(unittest.TestCase):
    def test_real_installed_package_is_not_excluded(self):
        window = detector([(201514, "ChatGPT", NATIVE_PATH)]).find("ChatGPT")
        self.assertIsNotNone(window)
        self.assertEqual(window.hwnd, 201514)
        self.assertEqual(window.path, NATIVE_PATH)

    def test_native_process_with_nonmatching_title_is_excluded(self):
        self.assertIsNone(detector([(1, "Codex", NATIVE_PATH)], 1).find("ChatGPT"))

    def test_matching_title_may_contain_codex(self):
        window = detector([(1, "ChatGPT - codex 自动化", NATIVE_PATH)]).find("ChatGPT")
        self.assertIsNotNone(window)
        self.assertEqual(window.title, "ChatGPT - codex 自动化")

    def test_browser_windows_are_not_native_targets(self):
        windows = [
            (1, "ChatGPT - Microsoft Edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            (2, "ChatGPT - Google Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ]
        self.assertIsNone(detector(windows, 1).find("ChatGPT"))

    def test_matching_foreground_window_has_priority(self):
        windows = [(1, "ChatGPT - 第一窗口", NATIVE_PATH), (2, "ChatGPT - 第二窗口", NATIVE_PATH)]
        window = detector(windows, 2).find("ChatGPT")
        self.assertIsNotNone(window)
        self.assertEqual(window.hwnd, 2)

    def test_nonmatching_foreground_cannot_override_matching_window(self):
        windows = [(1, "Codex", NATIVE_PATH), (2, "ChatGPT", NATIVE_PATH)]
        window = detector(windows, 1).find("ChatGPT")
        self.assertIsNotNone(window)
        self.assertEqual(window.hwnd, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
