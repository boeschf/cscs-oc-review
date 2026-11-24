#!/usr/bin/env python3
"""Prepare release callers and create an unpublished GitHub release.

Run prepare from the trusted source checkout, lint both generated callers, then
run create on the same output directory. API calls use GH_TOKEN; source CI is
queried, not rerun. A tag is not rolled back if draft creation fails.
"""

import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

import yaml

if __package__:
    from .caller import version_tag, workflow
else:
    from caller import version_tag, workflow


# Release preparation and creation accept only this upstream repository.
REPOSITORY = "eth-cscs/cscs-oc-review"
CHECKS_WORKFLOW = ".github/workflows/checks.yml"
# Positional order matches the disabled/enabled allow_web values in prepare.
CALLER_FILES = ("cscs-review.yml", "cscs-review-web.yml")
SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
IMAGE = re.compile(r"ghcr\.io/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}\Z", re.ASCII)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects so authenticated API requests stay on the configured host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirects are not allowed")


def github_api(path, payload=None):
    """GET path or POST a JSON payload to api.github.com; return decoded JSON.

    Callers supply a leading-slash API path. Require GH_TOKEN, reject redirects,
    and propagate HTTP, network, and JSON decoding errors.
    """
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("GH_TOKEN is required")
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "cscs-oc-review-release",
                 "Content-Type": "application/json"},
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        method="GET" if payload is None else "POST",
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        return json.load(response)


def require_unused_version(repository, tag, api=github_api):
    """Raise ValueError if a tag or release, including a draft, already uses tag.

    Scan all release pages. Only HTTP 404 means the tag is absent; other API
    failures propagate rather than allowing release creation.
    """
    try:
        api(f"/repos/{repository}/git/ref/tags/{tag}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    else:
        raise ValueError(f"tag {tag} already exists; existing versions are never overwritten")
    # The tag lookup alone cannot find an unpublished draft with no Git tag yet.
    page = 1
    while True:
        releases = api(f"/repos/{repository}/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise ValueError("malformed GitHub releases response")
        if any(release["tag_name"] == tag for release in releases):
            raise ValueError(f"release {tag} already exists, including drafts")
        if len(releases) < 100:
            return
        page += 1


def require_successful_checks(repository, revision, branch, api=github_api):
    """Require the latest matching checks.yml run to succeed; return its URL.

    Match the exact SHA, push event, branch, workflow path, and run/head
    repositories across all pages. Rank by run ID and attempt, not success,
    so an older success cannot hide a newer failure or pending run.
    Raise ValueError if no eligible run exists or the latest is not successful.
    """
    candidates = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"head_sha": revision, "event": "push", "branch": branch,
                                        "per_page": 100, "page": page})
        response = api(f"/repos/{repository}/actions/workflows/checks.yml/runs?{query}")
        runs = response["workflow_runs"]
        if not isinstance(runs, list):
            raise ValueError("malformed GitHub workflow runs response")
        candidates.extend(run for run in runs
                          if run.get("head_sha") == revision and run.get("head_branch") == branch
                          and run.get("event") == "push" and run.get("path") == CHECKS_WORKFLOW
                          and run.get("repository", {}).get("full_name") == repository
                          and run.get("head_repository", {}).get("full_name") == repository)
        if len(runs) < 100:
            break
        page += 1
    if not candidates:
        raise ValueError(f"no checks.yml push run found for {revision} on {branch}; run upstream CI first")
    # A rerun keeps its run ID; its attempt number determines recency within that run.
    latest = max(candidates, key=lambda run: (run["id"], run.get("run_attempt", 1)))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError(f"latest checks.yml run for {revision} is not successful "
                         f"({latest.get('status')}/{latest.get('conclusion')}); finish or fix CI first")
    return f"https://github.com/{repository}/actions/runs/{latest['id']}"


def prepare(repository, revision, ref, output, api=github_api):
    """Validate release eligibility; write both callers and metadata.json.

    Run from the trusted checkout of revision. Require the upstream default
    branch, a stable VERSION, successful existing CI, an unused version, and a
    digest-pinned GHCR image. output must not exist. Return release metadata.
    This phase reads GitHub state but does not create tags or releases.
    """
    if repository != REPOSITORY or not SHA.fullmatch(revision):
        raise ValueError("release preparation requires the upstream repository and a full commit SHA")
    version = Path("VERSION").read_text(encoding="utf-8").strip()
    tag = version_tag(version)
    source = api(f"/repos/{repository}")
    branch = source["default_branch"]
    if source["full_name"] != repository or ref != "refs/heads/" + branch:
        raise ValueError("release preparation is restricted to the upstream default branch")
    checks_url = require_successful_checks(repository, revision, branch, api)
    require_unused_version(repository, tag, api)
    worker = yaml.safe_load(Path(".github/workflows/review-worker.yml").read_text(encoding="utf-8"))
    image = worker["jobs"]["review"]["env"]["OPENCODE_REVIEW_IMAGE"]
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise ValueError("the review worker must select a digest-pinned GHCR image")
    metadata = {"repository": repository, "revision": revision, "version": version,
                "tag": tag, "image": image, "checks_url": checks_url}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for filename, allow_web in zip(CALLER_FILES, (False, True)):
        (output / filename).write_text(workflow(revision, allow_web, version), encoding="utf-8")
    (output / "metadata.json").write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    return metadata


def release_notes(metadata, standard, web, changes):
    """Return release Markdown with both supplied caller texts embedded verbatim.

    Callers must lint the YAML first; this function does not validate or
    regenerate it. Include source provenance, image pin, and generated changes.
    """
    repository, revision = metadata["repository"], metadata["revision"]
    return (
        f"## Installation\n\n"
        f"Source version: **{metadata['tag']}** · "
        f"[Commit `{revision}`](https://github.com/{repository}/commit/{revision}) · "
        f"[Successful checks]({metadata['checks_url']})\n\n"
        "Choose **one** workflow below. Copy the complete block into "
        "`.github/workflows/cscs-review.yml` in the subscriber repository and merge it into its default branch. "
        "Provide the `CSCS_INFERENCE_KEY` Actions secret, then post `/cscs-review` on a PR. "
        "The requester needs repository write access. Installed callers remain pinned until you update them.\n\n"
        "### Standard installation — web tools disabled\n\n"
        "Recommended default.\n\n```yaml\n" + standard + "```\n\n"
        "### Optional installation — web tools enabled\n\n"
        "> [!WARNING]\n"
        "> Web search queries and fetched URLs can disclose repository information outside CSCS. "
        "Enable this variant only if your repository's data policy permits it. "
        "Disabling web tools is not a firewall for the agent's shell.\n\n"
        "```yaml\n" + web + "```\n\n"
        "## Runtime image\n\n```text\n" + metadata["image"] + "\n```\n\n"
        "The image is independently pinned by digest. This source release does not rebuild it.\n\n"
        "## Changes\n\n" + changes + "\n"
    )


def create_draft(output, api=github_api):
    """Create a tag and draft from trusted prepare output; return the draft URL.

    Caller files must have passed actionlint; this function does not lint or
    rerun source CI. Recheck version availability and generate the changelog,
    then create the tag at the captured SHA before creating the draft.
    Never publish or overwrite a version. Failures propagate without cleanup;
    a tag can remain if the subsequent draft request fails.
    """
    output = Path(output)
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    repository, revision, tag = metadata["repository"], metadata["revision"], metadata["tag"]
    if repository != REPOSITORY or not SHA.fullmatch(revision) or tag != version_tag(metadata["version"]):
        raise ValueError("invalid prepared release metadata")
    standard, web = ((output / filename).read_text(encoding="utf-8") for filename in CALLER_FILES)
    require_unused_version(repository, tag, api)
    generated = api(f"/repos/{repository}/releases/generate-notes",
                    {"tag_name": tag, "target_commitish": revision})
    notes = release_notes(metadata, standard, web, generated["body"])
    (output / "release-notes.md").write_text(notes, encoding="utf-8")
    # Reserve the exact tag target now, rather than leaving a draft's future tag mutable by default-branch movement.
    api(f"/repos/{repository}/git/refs", {"ref": "refs/tags/" + tag, "sha": revision})
    release = api(f"/repos/{repository}/releases",
                  {"tag_name": tag, "target_commitish": revision, "name": tag,
                   "body": notes, "draft": True, "prerelease": False})
    return release["html_url"]


def main(argv=None):
    """CLI: prepare prints a status line; create prints the draft URL for the job summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("prepare", help="validate existing CI and generate both caller variants")
    check.add_argument("--repository", required=True)
    check.add_argument("--revision", required=True)
    check.add_argument("--ref", required=True)
    check.add_argument("--output", required=True, type=Path)
    create = commands.add_parser("create", help="create a draft after actionlint validates the caller files")
    create.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            metadata = prepare(args.repository, args.revision, args.ref, args.output)
            print(f"Prepared {metadata['tag']} at {metadata['revision']}; existing checks passed")
        else:
            print(create_draft(args.output))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"release preparation: {error}\n")


if __name__ == "__main__":
    main()
