#!/usr/bin/env python3
"""Render subscriber workflow YAML pinned to a full upstream commit SHA.

The optional source version is a comment, not the executable reference.
Generation does not install files or call GitHub.
"""

import argparse
import re


def version_tag(version):
    """Return vMAJOR.MINOR.PATCH; raise ValueError for other version formats.

    Reject prefixes, leading zeros, prerelease suffixes, and build metadata.
    """
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version, re.ASCII):
        raise ValueError("release version must be MAJOR.MINOR.PATCH without a leading v")
    return "v" + version


def workflow(revision, allow_web=False, release_version=None):
    """Return caller YAML; reject an invalid SHA or source version with ValueError.

    revision must be a lowercase, 40-character commit SHA. release_version adds
    only a version comment. allow_web opts into external search/fetch tools.
    The caller forwards only CSCS_INFERENCE_KEY.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be a full lowercase commit SHA")
    version_comment = "" if release_version is None else " # " + version_tag(release_version)
    lines = [
        "name: CSCS review",
        "",
        "on:",
        "  # Conversation commands run the default branch's workflow, including for fork PRs.",
        "  issue_comment:",
        "    types: [created]",
        "",
        "permissions:",
        "  contents: read",
        "  pull-requests: write",
        "",
        "jobs:",
        "  review:",
        "    # Central authorization runs before per-PR cancellation or inference.",
        f"    uses: eth-cscs/cscs-oc-review/.github/workflows/review.yml@{revision}{version_comment}",
    ]
    if allow_web:
        lines.extend([
            "    with:",
            "      # Search and fetch queries may disclose repository details to external services.",
            "      allow-web: true",
        ])
    lines.extend([
        "    # Do not forward other repository or organization secrets.",
        "    secrets:",
        "      CSCS_INFERENCE_KEY: ${{ secrets.CSCS_INFERENCE_KEY }}",
    ])
    return "\n".join(lines) + "\n"


def main():
    """Print caller YAML to stdout; report invalid arguments through argparse."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="published cscs-oc-review commit SHA")
    parser.add_argument("--allow-web", action="store_true", help="enable external web search and fetch tools")
    parser.add_argument("--release-version", help="source release version for the SHA reference comment")
    args = parser.parse_args()
    try:
        print(workflow(args.revision, args.allow_web, args.release_version), end="")
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
