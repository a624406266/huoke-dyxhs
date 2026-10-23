"""Source archives must carry the documentation linked by their entry points."""
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from package_release import allowed_path, validate_document_links, self_test


class PackageReleaseTests(unittest.TestCase):
    def test_linked_evolution_document_is_allowed_without_git_tracking(self):
        self.assertTrue(allowed_path("docs/keyword-workbench-v3/evolution.md"))

    def test_relative_links_and_fragments_resolve_inside_archive(self):
        files = {"README.md": b"[docs](docs/guide.md#usage) [external](https://example.test) [local](#top)",
                 "docs/guide.md": b"[root](../README.md)"}
        validate_document_links(files)
        del files["docs/guide.md"]
        with self.assertRaisesRegex(ValueError, "链接目标缺失"):
            validate_document_links(files)

    def test_archive_whitelist_hashes_and_secret_rejection(self):
        self_test()


if __name__ == "__main__":
    unittest.main()
