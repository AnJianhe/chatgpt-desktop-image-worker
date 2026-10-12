"""下载名称兼容性；不操作桌面。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from chatgpt_image_ui import preferred_download_candidates


class DownloadNamesTests(unittest.TestCase):
    def test_both_naming_styles_are_kept(self):
        files = {Path('chatgpt_download_20261007_141015.png'): (100, 1),
                 Path('ChatGPT 图像 2026年10月7日 14_26_10.png'): (200, 2)}
        self.assertEqual(preferred_download_candidates(files), files)

    def test_known_name_preferred_to_unrelated_new_picture(self):
        known = Path('ChatGPT 图像 2026年10月7日.png')
        self.assertEqual(preferred_download_candidates({known: (100, 1), Path('other.png'): (200, 2)}),
                         {known: (100, 1)})

    def test_arbitrary_renamed_file_still_supported(self):
        files = {Path('我的花型.png'): (100, 1)}
        self.assertEqual(preferred_download_candidates(files), files)


if __name__ == '__main__':
    unittest.main()
