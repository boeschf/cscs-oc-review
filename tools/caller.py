#!/usr/bin/env python3
"""Print a consumer workflow pinned to one immutable shared revision."""

import argparse
import re


def workflow(revision, allow_web=False):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be a full lowercase commit SHA")
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
        f"    uses: eth-cscs/cscs-oc-review/.github/workflows/review.yml@{revision}",
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="published cscs-oc-review commit SHA")
    parser.add_argument("--allow-web", action="store_true", help="enable external web search and fetch tools")
    args = parser.parse_args()
    try:
        print(workflow(args.revision, args.allow_web), end="")
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
