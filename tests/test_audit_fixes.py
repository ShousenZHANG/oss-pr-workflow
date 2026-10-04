"""Gaps found by the 2026-10-04 audit of v1.0.0. Each test reproduces a case a gate let through."""

from conftest import git, run
from diff_check import added_lines
from findings_check import changed_ranges


class TestDiffParsing:
    def test_added_line_starting_with_plus_plus_is_scanned(self):
        """`++counter;` added becomes `+++counter;` in the diff and was taken for a file header."""
        diff = "diff --git a/a.cpp b/a.cpp\n--- a/a.cpp\n+++ b/a.cpp\n@@ -1,0 +2,2 @@\n+++counter;\n++++ x\n"
        assert added_lines(diff) == {"a.cpp": [(2, "++counter;"), (3, "+++ x")]}

    def test_changed_ranges_survive_plus_plus_lines(self):
        """An added `++ b.cpp` line reads `+++ b.cpp` and switched the parser to a file that does not exist."""
        diff = "--- a/a.cpp\n+++ b/a.cpp\n@@ -1,0 +2,1 @@\n+++ b.cpp\n--- a/c.py\n+++ b/c.py\n@@ -5 +5 @@\n-x\n+y\n"
        assert changed_ranges(diff) == {"a.cpp": [(2, 2)], "c.py": [(5, 5)]}

    def test_deleted_file_ranges_are_old_side(self):
        diff = "--- a/old.py\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-a\n-b\n-c\n"
        assert changed_ranges(diff) == {"old.py": [(1, 3)]}


def test_non_ascii_path_is_checked_under_its_real_name(setup):
    """git octal-escapes `docs/中文.md` by default, so the `docs/**` ASCII rule never matched it."""
    repo, home = setup
    (repo / "docs" / "中文.md").write_text("说明\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "docs: add a note\n\nSigned-off-by: T <t@example.com>")
    result = run("diff_check.py", repo, home, "acme/widgets", "--base", "main", "--plan", "pkg/a.py,docs/*")
    assert "docs/中文.md:1: non-ASCII" in result.stdout, result.stdout + result.stderr
    assert "not in the plan" not in result.stdout
