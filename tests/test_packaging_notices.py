from email.message import Message
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "collect_notices", ROOT / "packaging/collect_notices.py"
)
notices = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notices)


class Wheel:
    def __init__(self, root, name, files):
        self.root, self.files, self.version = root, files, "1.2.3"
        self.metadata = Message()
        self.metadata["Name"] = name
        self.metadata["License-Expression"] = "MIT"
        self.metadata["Project-URL"] = "Source, https://example.org/source"

    def locate_file(self, entry):
        return self.root / entry


class NoticeTests(unittest.TestCase):
    def test_retains_original_notices_and_records_missing_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = ["example.dist-info/licenses/LICENCE", "example/LICENSE.native.txt"]
            for name in files:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"Original copyright and terms\n")
            python_license = root / "python-license"
            python_license.write_text("Python terms")
            packages = [
                Wheel(root, "example", files),
                Wheel(root, "missing", []),
                Wheel(root, "thumbtalk-helper", []),
            ]
            inventory = notices.collect_notices(
                root / "out", packages, [python_license]
            )
            self.assertEqual(
                [item["name"] for item in inventory], ["example", "missing", "Python"]
            )
            for path in inventory[0]["notice_files"]:
                self.assertEqual(
                    (root / "out" / path).read_bytes(),
                    b"Original copyright and terms\n",
                )
            self.assertTrue(inventory[1]["needs_notice_review"])
            self.assertFalse(inventory[2]["needs_notice_review"])
            self.assertEqual(
                json.loads((root / "out/inventory.json").read_text()), inventory
            )
            self.assertIn("missing 1.2.3", (root / "out/README.txt").read_text())

    def test_rejects_traversal_absolute_paths_and_source_modules(self):
        for path in (
            "../LICENSE",
            "/etc/LICENSE",
            "C:\\LICENSE",
            "copyright.py",
            "licenses/helper.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(notices.notice_file(path))
        for path in (
            "pkg/LICENCE",
            "licenses/Apache-2.0.txt",
            "pkg/NOTICE",
            "pkg/COPYING.LESSER",
        ):
            with self.subTest(path=path):
                self.assertTrue(notices.notice_file(path))
