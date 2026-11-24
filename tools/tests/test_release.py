"""Test release guards and draft creation with isolated files and no network.

The API fixture supplies paginated responses and failures; no test mutates GitHub.
"""

import copy
import importlib.util
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse

import pytest
import yaml


TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cscs_release_tests", TOOLS / "release.py", submodule_search_locations=[str(TOOLS)])
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)

REPOSITORY = "eth-cscs/cscs-oc-review"
REVISION = "a" * 40
IMAGE = "ghcr.io/eth-cscs/review@sha256:" + "b" * 64
INVALID_VERSIONS = [
    "v1.2.3", "1.2.3-rc.1", "1.2.3+build", "01.2.3", "1.02.3", "1.2.03",
    "1.2", "١.2.3", "1.2.3\njobs: injected", "1.2.3;$(touch injected)",
]


def successful_run(**changes):
    """Build an eligible CI run; override fields to exercise rejection paths."""
    run = {
        "id": 10, "run_attempt": 1, "head_sha": REVISION, "head_branch": "main",
        "event": "push", "path": ".github/workflows/checks.yml",
        "repository": {"full_name": REPOSITORY},
        "head_repository": {"full_name": REPOSITORY},
        "status": "completed", "conclusion": "success",
    }
    run.update(changes)
    return run


def http_error(code):
    """Create the urllib error type raised by the production GitHub client."""
    return urllib.error.HTTPError("https://api.github.com/test", code, "test error", {}, None)


class GitHub:
    """Record requests and serve configured run/release pages or injected failures."""

    def __init__(self):
        self.calls = []
        self.runs = {1: [successful_run()]}
        self.releases = {1: []}
        self.tag_error = http_error(404)
        self.repository = {"full_name": REPOSITORY, "default_branch": "main"}
        self.create_error = None

    @property
    def writes(self):
        """Return recorded POST requests, including requests that raised an error."""
        return [(path, payload) for path, payload in self.calls if payload is not None]

    def __call__(self, path, payload=None):
        """Record and dispatch fixture API calls; validate workflow query parameters."""
        self.calls.append((path, copy.deepcopy(payload)))
        base = f"/repos/{REPOSITORY}"
        if path == base and payload is None:
            return copy.deepcopy(self.repository)
        if path.startswith(base + "/actions/workflows/checks.yml/runs?") and payload is None:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            assert query == {
                "head_sha": [REVISION], "event": ["push"], "branch": ["main"],
                "per_page": ["100"], "page": query["page"],
            }
            return {"workflow_runs": copy.deepcopy(self.runs[int(query["page"][0])])}
        if path.startswith(base + "/git/ref/tags/") and payload is None:
            if self.tag_error is not None:
                raise self.tag_error
            return {"object": {"sha": REVISION}}
        if path.startswith(base + "/releases?") and payload is None:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            assert query["per_page"] == ["100"]
            return copy.deepcopy(self.releases[int(query["page"][0])])
        if path == base + "/releases/generate-notes" and payload is not None:
            return {"body": "Generated changes from merged pull requests."}
        if path == base + "/git/refs" and payload is not None:
            return {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
        if path == base + "/releases" and payload is not None:
            if self.create_error is not None:
                raise self.create_error
            return {"html_url": "https://github.com/eth-cscs/cscs-oc-review/releases/123"}
        pytest.fail(f"unexpected API request: {path}, {payload!r}")


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """Fail every test that attempts to construct the production HTTP client."""
    def forbidden(*args, **kwargs):
        pytest.fail("release tests must not access the network")

    monkeypatch.setattr(release.urllib.request, "build_opener", forbidden)


@pytest.fixture
def source(tmp_path, monkeypatch):
    """Provide a temporary source checkout with VERSION and the worker's image pin."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    worker = tmp_path / ".github" / "workflows" / "review-worker.yml"
    worker.parent.mkdir(parents=True)
    worker.write_text(yaml.safe_dump({
        "jobs": {"review": {"env": {"OPENCODE_REVIEW_IMAGE": IMAGE}}},
    }), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("version", INVALID_VERSIONS)
def test_version_validation_rejects_non_source_semver(version):
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        release.version_tag(version)


@pytest.mark.parametrize("changes", [
    {"head_sha": "c" * 40}, {"head_branch": "other"}, {"event": "pull_request"},
    {"path": ".github/workflows/other.yml"},
    {"repository": {"full_name": "contributor/fork"}},
    {"head_repository": {"full_name": "contributor/fork"}},
])
def test_success_from_an_untrusted_run_cannot_authorize_release(changes):
    api = GitHub()
    api.runs[1] = [successful_run(**changes)]
    with pytest.raises(ValueError, match="no checks.yml push run"):
        release.require_successful_checks(REPOSITORY, REVISION, "main", api)
    assert not api.writes


@pytest.mark.parametrize("runs", [
    [], [successful_run(status="in_progress", conclusion=None)],
    [successful_run(conclusion="failure")],
])
def test_missing_pending_or_failed_checks_block_release(runs):
    api = GitHub()
    api.runs[1] = runs
    with pytest.raises(ValueError):
        release.require_successful_checks(REPOSITORY, REVISION, "main", api)


@pytest.mark.parametrize("newer", [
    successful_run(id=11, conclusion="failure"),
    successful_run(id=10, run_attempt=2, conclusion="failure"),
])
def test_newer_failure_supersedes_older_success_even_when_listed_first(newer):
    api = GitHub()
    api.runs[1] = [newer, successful_run()]
    with pytest.raises(ValueError, match="latest checks.yml run"):
        release.require_successful_checks(REPOSITORY, REVISION, "main", api)


@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_checks_scan_later_pages_before_selecting_latest(conclusion):
    api = GitHub()
    api.runs = {
        1: [successful_run(id=number) for number in range(1, 101)],
        2: [successful_run(id=101, conclusion=conclusion)],
    }
    if conclusion == "success":
        url = release.require_successful_checks(REPOSITORY, REVISION, "main", api)
        assert url == f"https://github.com/{REPOSITORY}/actions/runs/101"
    else:
        with pytest.raises(ValueError, match="latest checks.yml run"):
            release.require_successful_checks(REPOSITORY, REVISION, "main", api)
    assert len(api.calls) == 2


def test_existing_tag_blocks_version_without_scanning_releases():
    api = GitHub()
    api.tag_error = None
    with pytest.raises(ValueError, match="tag v1.2.3 already exists"):
        release.require_unused_version(REPOSITORY, "v1.2.3", api)
    assert len(api.calls) == 1
    assert not api.writes


def test_existing_draft_on_later_release_page_blocks_version():
    api = GitHub()
    api.releases = {
        1: [{"tag_name": f"v0.0.{number}", "draft": False} for number in range(100)],
        2: [{"tag_name": "v1.2.3", "draft": True}],
    }
    with pytest.raises(ValueError, match="including drafts"):
        release.require_unused_version(REPOSITORY, "v1.2.3", api)
    assert api.calls[-1][0].endswith("page=2")
    assert not api.writes


def test_only_missing_tag_and_no_release_allow_a_new_version():
    api = GitHub()
    release.require_unused_version(REPOSITORY, "v1.2.3", api)
    assert [path for path, _ in api.calls] == [
        f"/repos/{REPOSITORY}/git/ref/tags/v1.2.3",
        f"/repos/{REPOSITORY}/releases?per_page=100&page=1",
    ]
    assert not api.writes


@pytest.mark.parametrize("error", [http_error(403), http_error(500), urllib.error.URLError("offline")])
def test_tag_lookup_errors_fail_closed(error):
    api = GitHub()
    api.tag_error = error
    with pytest.raises(type(error)) as caught:
        release.require_unused_version(REPOSITORY, "v1.2.3", api)
    assert caught.value is error
    assert len(api.calls) == 1
    assert not api.writes


def test_invalid_source_version_cannot_prepare_or_access_remote(source):
    (source / "VERSION").write_text("1.2.3\nmalicious: true\n", encoding="utf-8")
    api = GitHub()
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        release.prepare(REPOSITORY, REVISION, "refs/heads/main", source / "prepared", api)
    assert not api.calls
    assert not (source / "prepared").exists()


@pytest.mark.parametrize("repository,ref", [
    ("contributor/fork", "refs/heads/main"),
    (REPOSITORY, "refs/heads/feature"),
    (REPOSITORY, "refs/tags/v1.2.3"),
])
def test_wrong_repository_or_ref_cannot_prepare(source, repository, ref):
    api = GitHub()
    with pytest.raises(ValueError, match="upstream"):
        release.prepare(repository, REVISION, ref, source / "prepared", api)
    assert not api.writes
    assert not (source / "prepared").exists()


def test_api_repository_identity_must_match_upstream(source):
    api = GitHub()
    api.repository["full_name"] = "contributor/fork"
    with pytest.raises(ValueError, match="upstream default branch"):
        release.prepare(REPOSITORY, REVISION, "refs/heads/main", source / "prepared", api)
    assert not api.writes
    assert not (source / "prepared").exists()


@pytest.mark.parametrize("existing", ["tag", "draft"])
def test_existing_version_prevents_preparation_and_all_remote_writes(source, existing):
    api = GitHub()
    if existing == "tag":
        api.tag_error = None
    else:
        api.releases[1] = [{"tag_name": "v1.2.3", "draft": True}]
    with pytest.raises(ValueError, match="already exists"):
        release.prepare(REPOSITORY, REVISION, "refs/heads/main", source / "prepared", api)
    assert not api.writes
    assert not (source / "prepared").exists()


@pytest.mark.parametrize("version", ["0.0.0", "12.30.40"])
def test_prepare_then_create_pins_draft_and_both_caller_variants(source, version):
    (source / "VERSION").write_text(version + "\n", encoding="utf-8")
    api = GitHub()
    output = source / "prepared"
    metadata = release.prepare(REPOSITORY, REVISION, "refs/heads/main", output, api)
    assert metadata["version"] == version
    assert metadata["tag"] == "v" + version
    assert metadata["revision"] == REVISION
    assert not api.writes

    url = release.create_draft(output, api)
    assert url == "https://github.com/eth-cscs/cscs-oc-review/releases/123"
    generated, reserved, created = api.writes
    assert generated == (f"/repos/{REPOSITORY}/releases/generate-notes", {
        "tag_name": "v" + version, "target_commitish": REVISION,
    })
    assert reserved == (f"/repos/{REPOSITORY}/git/refs", {
        "ref": "refs/tags/v" + version, "sha": REVISION,
    })
    path, payload = created
    assert path == f"/repos/{REPOSITORY}/releases"
    assert payload["draft"] is True
    assert payload["prerelease"] is False
    assert payload["target_commitish"] == REVISION
    assert payload["tag_name"] == payload["name"] == "v" + version
    notes = payload["body"]
    assert (output / "release-notes.md").read_text(encoding="utf-8") == notes
    assert IMAGE in notes
    assert metadata["checks_url"] in notes
    assert "Generated changes from merged pull requests." in notes
    blocks = re.findall(r"```yaml\n(.*?)```", notes, flags=re.DOTALL)
    assert len(blocks) == 2
    for block, filename, allow_web in zip(blocks, release.CALLER_FILES, (False, True)):
        assert block == (output / filename).read_text(encoding="utf-8")
        caller = yaml.safe_load(block)
        job = caller["jobs"]["review"]
        assert job["uses"] == f"{REPOSITORY}/.github/workflows/review.yml@{REVISION}"
        assert job.get("with", {}).get("allow-web", False) is allow_web
        assert job["secrets"] == {"CSCS_INFERENCE_KEY": "${{ secrets.CSCS_INFERENCE_KEY }}"}
        assert caller["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert "disclose repository information outside CSCS" in notes


def test_draft_appearing_after_preparation_prevents_creation(source):
    api = GitHub()
    output = source / "prepared"
    release.prepare(REPOSITORY, REVISION, "refs/heads/main", output, api)
    api.releases[1] = [{"tag_name": "v1.2.3", "draft": True}]
    with pytest.raises(ValueError, match="including drafts"):
        release.create_draft(output, api)
    assert not api.writes
    assert not (output / "release-notes.md").exists()


def test_draft_creation_failure_after_tag_reservation_propagates_without_cleanup_or_retry(source):
    api = GitHub()
    output = source / "prepared"
    release.prepare(REPOSITORY, REVISION, "refs/heads/main", output, api)
    error = http_error(503)
    api.create_error = error
    with pytest.raises(urllib.error.HTTPError) as caught:
        release.create_draft(output, api)
    assert caught.value is error
    assert [path for path, _ in api.writes] == [
        f"/repos/{REPOSITORY}/releases/generate-notes",
        f"/repos/{REPOSITORY}/git/refs",
        f"/repos/{REPOSITORY}/releases",
    ]
    assert api.calls[-1] == api.writes[-1]
    assert (output / "release-notes.md").exists()
