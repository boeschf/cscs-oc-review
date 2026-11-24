"""Exercise literal instruction-path serialization and the caller CLI."""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "caller.py"
spec = importlib.util.spec_from_file_location("cscs_caller_tests", SCRIPT)
caller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(caller)
REVISION = "a" * 40


@pytest.mark.parametrize("allow_web", [False, True])
@pytest.mark.parametrize("path", [
    "docs/review rules.md",
    'rules/Review: "don\'t #skip" 📋.md',
    "rules/$(touch should-not-exist).md",
    r"rules/back\slash.md",
])
def test_literal_paths_cannot_change_workflow_structure(path, allow_web):
    generated = yaml.safe_load(caller.workflow(
        REVISION, allow_web=allow_web, review_instructions_path=path))
    expected = {"review-instructions-path": path}
    if allow_web:
        expected["allow-web"] = True
    assert generated["jobs"]["review"]["with"] == expected
    assert set(generated["jobs"]) == {"review"}


@pytest.mark.parametrize("path", [
    "rules/a\nb.md", "rules/a\rb.md", "rules/a\tb.md", "rules/a\x00b.md",
    "rules/a\x85b.md", "rules/a\u2028b.md", "rules/${{ secrets.CSCS_INFERENCE_KEY }}.md",
])
def test_paths_with_expression_or_line_injection_are_rejected(path):
    with pytest.raises(ValueError, match="printable literal"):
        caller.workflow(REVISION, review_instructions_path=path)


def test_cli_generates_a_literal_override_alongside_web_opt_in(tmp_path):
    path = 'docs/review: "project # rules".md'
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--revision", REVISION, "--allow-web",
         "--review-instructions-path", path],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    workflow = yaml.safe_load(result.stdout)
    assert workflow["jobs"]["review"]["with"] == {
        "allow-web": True, "review-instructions-path": path,
    }
    assert list(tmp_path.iterdir()) == []
