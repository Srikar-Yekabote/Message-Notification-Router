"""Tests for building code.zip (no network)."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from bundle import SecretFound, build_package, package_files
from llm import REPO_ROOT

# Built at runtime so this file itself never looks like it holds a key.
KEY_NAME = "GROQ_API_" + "KEY"
FAKE_KEY = "gsk_" + "a1B2" * 10


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name, text in {
            "code/main.py": "print('hi')\n",
            "code/README.md": f"{KEY_NAME}=your-key-here\n",
            "code/.env": f"{KEY_NAME}={FAKE_KEY}\n",
            "code/__pycache__/main.cpython-39.pyc": "x",
            "cache/media/img_001.json": "{}",
            "cache/llm/abc.json": "{}",
            ".env.example": f"{KEY_NAME}=\n",
            ".env": f"{KEY_NAME}={FAKE_KEY}\n",
            "dataset/messages.csv": "message_id\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_secrets_caches_and_data_are_handled(self):
        names = {p.as_posix() for p in package_files(self.root)}
        self.assertEqual(names, {"code/main.py", "code/README.md", "cache/media/img_001.json", "cache/llm/abc.json",
                                 ".env.example"})
        self.assertNotIn("cache/llm/abc.json", {p.as_posix() for p in package_files(self.root, False)})

    def test_zip_is_written(self):
        path, count, _ = build_package(self.root)
        with zipfile.ZipFile(path) as bundle:
            self.assertEqual(len(bundle.namelist()), count)
            self.assertIn("code/main.py", bundle.namelist())

    def test_a_key_in_a_packaged_file_stops_the_build(self):
        (self.root / "code" / "notes.txt").write_text(f"key: {FAKE_KEY}\n", encoding="utf-8")
        with self.assertRaises(SecretFound):
            build_package(self.root)
        self.assertFalse((self.root / "code.zip").exists())
        (self.root / "code" / "notes.txt").write_text(f"{KEY_NAME}=real-looking-value\n", encoding="utf-8")
        with self.assertRaises(SecretFound):
            build_package(self.root)

    def test_the_real_package_has_the_entry_point_and_no_env_file(self):
        names = {p.as_posix() for p in package_files(REPO_ROOT)}
        self.assertIn("code/main.py", names)
        self.assertIn("code/README.md", names)
        self.assertFalse(any(Path(n).name == ".env" for n in names))
        self.assertFalse(any(n.startswith(("dataset/", "traces/")) for n in names))


if __name__ == "__main__":
    unittest.main()
