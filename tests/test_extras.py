import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from glass_launcher.extras import calculate, calculator_row, window_rows  # noqa: E402


class Calculator(unittest.TestCase):
    def test_results(self):
        self.assertEqual(calculate("2*19.5"), "39")
        self.assertEqual(calculate("(3+4)^2"), "49")
        self.assertEqual(calculate("1000*1000"), "1,000,000")
        self.assertEqual(calculate("sqrt(2)"), "1.414213562")
        self.assertEqual(calculator_row("10/4").path, "2.5")

    def test_not_math(self):
        for text in ("firefox", "12", "", "__import__('os')", "2**99999", "1/0", "open(1)"):
            self.assertIsNone(calculate(text), text)


class Typos(unittest.TestCase):
    def test_near_misses(self):
        from glass_launcher.apps import typo_score
        self.assertGreater(typo_score("frifox", "Mozilla Firefox"), 0)
        self.assertGreater(typo_score("termnial", "COSMIC Terminal"), 0)
        self.assertEqual(typo_score("zzzz", "Firefox"), 0)
        self.assertEqual(typo_score("fir", "Firefox"), 0)   # short queries use normal matching


class Windows(unittest.TestCase):
    def test_match_and_skip_active_when_empty(self):
        windows = [{"id": 1, "app_id": "com.system76.CosmicTerm", "title": "~/launcher", "active": True},
                   {"id": 2, "app_id": "Opera developer", "title": "GitHub - Opera", "active": False}]
        self.assertEqual([r.id for r in window_rows(windows, "opera", [])], ["window:2"])
        self.assertEqual([r.id for r in window_rows(windows, "", [])], ["window:2"])
        self.assertEqual(window_rows(windows, "zzzz", []), [])


class Sections(unittest.TestCase):
    def test_headers_are_skipped_for_alt_numbers(self):
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        sys.path.insert(0, str(Path.home() / "Desktop/Genta"))
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from glass_launcher.apps import App
        from glass_launcher.window import AppModel
        row = lambda i: App(id=i, path="", name=i, generic="", keywords=(), executable="", comment="", icon_names=(), icon_path="")
        model = AppModel()
        model.set_apps([row("header:WINDOWS"), row("window:1"), row("header:APPS"), row("a.desktop"), row("b.desktop")])
        self.assertEqual(model.actionable(0), 1)
        self.assertEqual(model.actionable(1), 3)
        self.assertIsNone(model.actionable(5))
        self.assertEqual(model.ordinal(4), 2)


if __name__ == "__main__":
    unittest.main()
