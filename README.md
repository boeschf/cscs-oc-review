# CSCS OpenCode review

## Purpose

This repository provides an on-demand PR reviewer for repositories in the `eth-cscs` GitHub organization.
The upstream source is [eth-cscs/cscs-oc-review](https://github.com/eth-cscs/cscs-oc-review).
A shared workflow authorizes a Conversation comment, inspects the PR in a read-only container, and posts a separate final response.
The [reviewer policy](image/grumpy-reviewer.md) defines the grumpy voice, evidence standards, coverage requirements, and response format.
Review policy prohibits code changes, PR-code execution, package installation, and agent-side publication.
Model findings still require human review.

Each participating repository runs the shared workflows with its own GitHub Actions token and inference secret.
No custom GitHub App, cross-repository write token, or consumer CSCS webhook integration is required.
Runs, artifacts, comments, and GitHub-hosted runner charges belong to the participating repository, not this shared repository.

### Scope

The current scope is on-demand, advisory review on GitHub.com.
Each subscriber repository adds one small caller workflow; the setup instructions below call it a consumer.
Reviews are not required merge checks.
Hosted services, GitHub Apps, CSCS-CI middleware changes, and GitLab support are outside the current scope.
Subscriber repositories do not need CSCS-CI registration.

## Components and trust boundaries

| Location | Responsibility |
| --- | --- |
| [.github/workflows/review.yml](.github/workflows/review.yml) | Public reusable entry point: validate the command, check current write access, then enter per-PR concurrency |
| [.github/workflows/review-worker.yml](.github/workflows/review-worker.yml) | Acknowledge, prepare context, run the reviewer, extract output, publish, and report failure |
| [host/](host/) | Python helpers executed on the GitHub runner, with regression tests in `host/tests/` |
| [config/](config/) | Shared model, tool permissions, reasoning settings, steps, and execution deadlines |
| [image/](image/) | Container definition, runtime, reviewer policy, and phase-runner tests |
| [tools/caller.py](tools/caller.py) | Generate a consumer workflow from one release revision |
| [.github/workflows/prepare-release.yml](.github/workflows/prepare-release.yml) | Manually prepare a draft source release with validated, pinned caller blocks |
| [.github/actions/setup-validation-tools/](.github/actions/setup-validation-tools/) | Shared Python, uv, and checksum-verified actionlint setup for checks and release preparation |
| [tools/release.py](tools/release.py) | Check release eligibility, generate release notes, reserve the tag, and create a draft |
| [VERSION](VERSION) | Source release version, initially `0.1.0` |
| [.cscs/workflows/build-opencode-review-image.yaml](.cscs/workflows/build-opencode-review-image.yaml) | Separate CSCS image build and public-digest verification |

The reviewer has an inspection phase with tools and a final-response phase with tools denied.
Both phases use the same private session and the policy baked into the image.
The final task reapplies the policy so prior comments and inspection summaries do not replace it.
Repository-specific instructions are a separate root `AGENTS.md` from the consumer's default branch.
PR descriptions, discussions, source-local instructions, and files from the PR head are untrusted review material.

| Job | GitHub token permissions | Inference key |
| --- | --- | --- |
| `authorize` | `contents: read` | None |
| `acknowledge` | `pull-requests: write` | None |
| `review` | `contents: read`, `pull-requests: read` | Inference step only |
| `finish` | `pull-requests: write` | None |

In the review path, the write-enabled `acknowledge` and `finish` jobs have no source checkout, model process, or inference key.
The container receives no GitHub token, SSH agent, host home directory, or container socket.
The runtime runs as UID/GID 1000 with a read-only root filesystem, dropped capabilities, and `no-new-privileges`.
The source, instructions, configuration, and prepared context are read-only mounts; `/work` and `/tmp` are private writable scratch space.
The host checks `HEAD` and Git status before and after execution and attempts bounded process/container cleanup on failure or interruption.
The agent still has a shell; read-only mounts do not prevent it from executing readable PR code.

### Source selection

| Runner directory | Source | Treatment |
| --- | --- | --- |
| `reviewer-source/` | Current job's reusable-workflow repository and commit | Trusted executable helpers and shared configuration |
| `repository-default/` | Consumer repository default branch | Trusted repository instructions only; no scripts executed |
| `pr-source/` | API-selected current PR-head SHA | Untrusted source, mounted read-only |

The default branch selects trusted instructions; the PR's base branch is its merge target and determines the merge-base diff.
The two branches may differ.
The PR-head checkout and merge-base comparison use API-selected commit SHAs.
Host-helper Git commands disable hooks, external diff drivers, and text converters.

The consumer pins the entry workflow to one full release commit SHA.
Both central-source checkouts use GitHub's `job.workflow_repository` and `job.workflow_sha`, which identify the workflow that defines the current job rather than the consumer's workflow.
The worker is referenced relative to the entry workflow, so GitHub loads it at that entry's commit.
Helpers and configuration therefore come from the same release without a separate source revision input.
This implementation targets GitHub.com; its GitHub API endpoints are not configurable for GitHub Enterprise Server.
Review changes to the consumer's workflow pin as executable-code changes.
The image is independently pinned by digest in the worker.

Root repository instructions are optional.
If `AGENTS.md` is absent, the helper logs that fact and creates an empty trusted instructions file outside the checkout.
A present file must resolve to a regular file inside `repository-default/`; broken, looping, or escaping symlinks fail preparation.
Staging uses a new output file and does not overwrite consumer files.

## Consumer setup

The shared repository must be public and available as [eth-cscs/cscs-oc-review](https://github.com/eth-cscs/cscs-oc-review).
Its reusable workflows must be allowed by the consumer's repository and organization Actions policies.
Both public and private consumers can call a public shared workflow under GitHub's documented access rules.
The consumer must permit the caller's requested `contents: read` and `pull-requests: write` permissions; the shared workflow cannot increase them.
The entry workflow runs only for repositories owned by `eth-cscs`.

1. Open the [latest published release](https://github.com/eth-cscs/cscs-oc-review/releases/latest) and review its changes and limitations.
2. Make a `CSCS_INFERENCE_KEY` Actions secret available to the consumer.
   A repository secret is sufficient; an organization owner can instead grant selected repositories access to an organization secret.
3. Choose **one** of the two complete caller YAML code blocks in the release notes: **web disabled** is recommended; **web enabled** is an explicit opt-in with an external-data disclosure warning.
4. Copy that complete block into `.github/workflows/cscs-review.yml` in the consumer and merge it into the consumer's default branch.
   Do not install both variants.
5. Add repository-specific `AGENTS.md` instructions on that default branch if needed.
6. Request a review as described under [Request and result behavior](#request-and-result-behavior).

Installation uses the release notes, not downloadable workflow artifacts or release assets.
No generated caller file is committed to this upstream repository.
Each block pins the reusable workflow to a full commit SHA, with a `# vVERSION` comment for human-readable version tracking, and explicitly forwards only `CSCS_INFERENCE_KEY`, not `secrets: inherit`.
The notes also record the source version, exact commit, successful CI run, and independently pinned runtime image.

For local generation from a checkout of the selected release, supply its full 40-character commit SHA:

```bash
python3 tools/caller.py --revision SHA > /tmp/cscs-review.yml
```

Optionally add `--release-version "$(cat VERSION)"` for the version comment and/or `--allow-web` to opt into external web tools.
The generator only prints YAML; it does not edit repositories, commit, push, or call GitHub.
It rejects branch names, tags, and abbreviated SHAs.
To upgrade, copy one caller block from the new published release and review the resulting change.
Configuration-only changes require a new source release, not an image rebuild.

The caller must exist on the consumer's default branch before `issue_comment` can trigger it.
A caller that exists only in an unmerged PR will not receive Conversation-comment events.
The reusable workflow definitions must exist at the selected published release commit.

The generated file is a small local trigger that references the shared workflow; it does not copy the reviewer implementation.
GitHub receives comment events in each consumer repository, so a central workflow reference cannot eliminate that local trigger file.
Generated release-note blocks avoid maintaining the caller YAML by hand, but installation still requires a reviewed consumer-repository change.
The workflow does not have cross-repository write credentials and cannot install itself into other repositories.

### Web access and private source

Web search and fetch tools are disabled by default in the worker's staged configuration.
To enable them deliberately, choose the web-enabled release-note block or add `--allow-web` to the local generator.
The worker applies only this typed permission choice to shared configuration; consumers do not supply arbitrary OpenCode JSON or executable plugins.
The staged [configuration](config/opencode.json) selects TinyFish search and does not define a separate search credential.
Search queries and fetched URLs can disclose repository details to services outside CSCS.
Keyless access is best-effort and is not a repository-specific search quota.

Disabling the web tools is not a network firewall for the container's shell.
Approve the inference resource and external-data policy before using private source; do not treat tool permissions as an exfiltration-proof sandbox.
Private fork access also depends on actual organization/fork-network permissions and must be checked in the consumer.
The workflow does not fetch submodules or Git LFS objects and does not gain access to other private dependency repositories.

## Request and result behavior

```text
/cscs-review Focus on cache invalidation and registry permissions.
```

- Same-repository and public-fork PRs use the same Conversation-comment entry point.
- Inline diff comments and edits to existing comments do not trigger reviews.
- Start the comment with `/cscs-review`; matching is case-insensitive.
  Separate optional request text with a space, tab, CR, or LF.
- GitHub's collaborator-permission API must return `write` or `admin` for the requester.
  GitHub maps maintain access to `write` in that response.
- Noncommands and missing collaborators are skipped; other permission API failures fail authorization.
- In the normal entry flow, an `eyes` reaction confirms authorization, not successful inference or publication.
  Direct worker calls add the reaction before their authorization recheck.
- A `confused` reaction indicates an acknowledgement, review, or publication failure, including a timeout.
- Select **Re-run failed jobs** in Actions or post another command to retry.
  A successful retry removes failure reactions by the same token user on the request comment.
  Clicking a reaction does not restart the workflow.
- Post another command after changing the PR head to request a new review.

Only authorized requests can cancel an earlier review for the same repository and PR.
The consumer wrapper and nested worker must not add the entry workflow's concurrency group themselves, because that can cancel their own run.
Different PRs and repositories run independently, subject to GitHub runner and inference-service capacity.
There is no organization-wide inference spending or concurrency limit in this implementation.
Use separately budgeted inference keys if repositories need independent attribution or revocation.

The host collects PR metadata, a full merge-base diff, and all pages of earlier reviews, Conversation comments, and inline threads with replies and resolution/outdated status.
Missing or malformed history stops preparation rather than silently omit discussion.
Earlier findings are evidence to verify against the current code, not drafts to repeat.

Publication reads extracted text as data and validates the trusted PR number and reviewed SHA.
The publisher rejects empty review text and a header-plus-review body longer than 65,536 JavaScript UTF-16 code units.
If the PR head changed during the run, publication is skipped and the artifact is retained.
The head check and comment creation are separate API calls, so a push between them can race publication.
Cancellation does not add a failure reaction, and reaction updates depend on GitHub API access.

## Limits and diagnostics

[config/opencode.json](config/opencode.json) is the single model/provider definition and supplies the agents' generation settings.
The OpenAI-compatible provider uses `compatibility.reasoningField: reasoning` to identify reasoning returned by the service.
The inspection agent cannot launch subagents; the finalizer denies tool permissions and sets provider-level `tool_choice: none`.

| Setting | Meaning |
| --- | --- |
| Agent `steps` | Model-iteration allowance for each phase, not individual tool calls |
| Model/variant `body.max_tokens` | Per-request output allowance including reasoning, not a review-wide token quota |
| Model/variant `body.reasoning_effort` | Qualitative inspection effort and finalizer override, not a hard time/token quota |
| Provider `headerTimeout`, `chunkTimeout` | Milliseconds allowed for headers and between response chunks |
| [limits.json](config/limits.json) phase durations | Separate inspection and finalization deadlines in seconds |
| `execution_overhead_seconds` | Additional host allowance for startup and phase transitions |
| `termination_grace_seconds` | Grace before force-killing a stopped phase |
| Worker `timeout-minutes` | GitHub job timeout, configured separately |

There is no absolute provider request timeout; active streaming can continue until the phase deadline.
The host deadline is `inspection_seconds + finalization_seconds + 2 × termination_grace_seconds + execution_overhead_seconds`.
Leave time within the GitHub job limit for setup, cleanup, and uploads.
Each phase can finish before its allowance, and retries do not extend the phase deadline.

Finalization requires a session and at least one successful source-inspection tool call, which proves neither coverage nor correctness.
An inspection deadline, exhausted step allowance, or token-truncated response can finalize existing evidence with a fixed incomplete-coverage warning.
Other inspection failures and all finalization failures do not produce a publishable fallback.
The extractor rejects malformed phase order, unexpected sessions, finalizer tool use, and incomplete output.

The runner logs a heartbeat every 30 seconds with byte counts, fixed event/tool summaries, and the current phase, never model text or tool arguments.
Only these artifacts are uploaded, with seven-day retention:

- `opencode-review`: extracted `review.md` for publication.
- `opencode-review-diagnostics`: sanitized counts, phase, exit/cleanup status, step outcomes, and bounded recent error details.

Diagnostic error excerpts redact known credentials and common credential syntax; they are not a general source-content filter.

Inspect the diagnostic artifact before changing budgets after a failure.
Raw events, reasoning, stderr, and prepared context remain private runner files and are not uploaded.
Missing reasoning events do not establish that the model generated none.

## Source releases and upgrades

[VERSION](VERSION) contains the source version as `MAJOR.MINOR.PATCH`, without a `v` prefix, leading zeros, prerelease suffix, or build metadata.
The release tag adds the `v` prefix.
Maintainers use patch increments for compatible fixes, minor increments for compatible features, and major increments for incompatible changes.
During `0.x`, incompatible changes increment the minor version.
The helper validates the format and rejects reused versions; it does not enforce increasing versions.
Source, runtime image, and OpenCode versions are independent.

Maintainers prepare releases manually:

1. Merge the reviewed source and any `VERSION` change into the upstream default branch.
   Keep the default branch protected and immutable releases enabled.
   The workflow does not configure or verify those repository settings.
2. Wait for the existing [.github/workflows/checks.yml](.github/workflows/checks.yml) **push** run to succeed for that exact commit on the default branch in `eth-cscs/cscs-oc-review`.
   A PR run, another branch, another SHA, or a fork's run is not sufficient; the latest matching run must be completed and successful.
3. Run **Actions → Prepare release → Run workflow** on the upstream default branch only.
   There is no version input: the workflow reads `VERSION` from the selected commit and rejects an existing tag or release for that version.
4. The workflow generates both complete caller files, validates them with actionlint, and includes them in draft release notes with full-SHA workflow pins and `# vVERSION` comments, the exact source SHA, CI link, and current image digest pin.
   It reuses the successful CI result rather than duplicating the test suites.
5. Release preparation explicitly reserves `refs/tags/vVERSION` at that exact SHA through the GitHub API **before** creating the draft release.
   Do not tag manually.
6. Review the draft's generated changelog, installation blocks, image pin, and known limitations.
   Edit the notes as needed, then publish the release and mark it as latest.
   Publication is never automatic.

The workflow uses its repository-scoped `GITHUB_TOKEN` (`GH_TOKEN` for the helper), with `contents: write` and `actions: read`.
It needs no PAT or inference key, does not rebuild the image, and does not commit generated files or upload installation assets.
If draft creation fails after tag reservation, preparation does not automatically delete or overwrite anything.
A maintainer must inspect the tag and any draft and deliberately remove only an **unpublished leftover** tag/draft before retrying.
Published immutable releases cannot reuse their version/tag; release a new version instead.

### Optional Dependabot updates

Subscribers may add this optional `.github/dependabot.yml` to propose updates to pinned GitHub Actions references:

```yaml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
    groups:
      github-actions:
        patterns:
          - "*"
```

This repository uses the same configuration.
Review proposed changes manually; no auto-merge or extra settings are ordinarily needed.
Weekly GitHub Actions version updates are grouped into one PR; security-update grouping is unchanged.
Dependabot does not automatically manage the runtime image digest stored in the workflow's environment value or the model/provider configuration.
Those changes need deliberate review and a new source release.

## Image releases

The worker uses this repository's publicly published AMD64 runtime, pinned by digest.
Its registry namespace does not determine which consumer repository is reviewed.
New builds target `ghcr.io/eth-cscs/cscs-oc-review/opencode-review`; review jobs use them only after the worker's digest pin is updated.
[image/Containerfile](image/Containerfile) builds the equivalent standalone runtime without dependencies on another repository's package helpers.
Only runtime files and policy are copied into the image, not tests, repository instructions, or runtime configuration.

Image building remains separate from review jobs:

1. Register [.cscs/workflows/build-opencode-review-image.yaml](.cscs/workflows/build-opencode-review-image.yaml) in CSCS CI as `build-opencode-image` if this repository will publish its own images.
2. Supply `GITHUB_ACTOR` and a masked secret `GITHUB_TOKEN` with GHCR package-publishing permission to the build and report jobs.
   A classic PAT needs `write:packages` and organization package access.
   Authorize organization SSO where required and avoid unnecessary `repo` scope.
3. Select a new image tag in that pipeline for runtime or policy changes; `if-not-exists` deliberately reuses an existing tag.
   Policy and runtime code are baked into the image; a source release alone does not deploy changes to them.
4. Build from a reviewed commit, because the builder receives registry write credentials.
5. Make the new GHCR package public, then run or retry the reporting job.
6. The report must confirm AMD64 architecture and matching authenticated/anonymous digests.
   Copy its `OPENCODE_REVIEW_IMAGE_PIN` into the worker, then publish a source release containing that pin.

The pipeline publishes directly to GHCR and requires no JFrog registry configuration.
Consumer repositories need neither registry credentials nor their own CSCS CI registration.
No image or source release is published automatically by installing a consumer workflow.

## Local verification

Run from this repository root:

```bash
uv run --python 3.12 --with pytest --with pyyaml pytest -q host/tests tools/tests
node --test image/test-run-review.mjs
bash -n image/entrypoint.sh
podman build --format docker -f image/Containerfile -t localhost/cscs-oc-review .
```

The regression suites use temporary repositories and controlled child processes, not inference credentials.
[checks.yml](.github/workflows/checks.yml) runs them for pushes and PRs, validates GitHub workflow semantics with actionlint, and syntax-checks CI YAML and embedded shell.
The checks and release-preparation workflows share [.github/actions/setup-validation-tools/](.github/actions/setup-validation-tools/).
That action owns the Python version, third-party setup action pins, and actionlint download URL/checksum.
It exports `UV_PYTHON` and adds actionlint to `PATH`.
The CI shell-syntax check also covers local composite actions.
Review authorization, source checkouts, permissions, and concurrency remain explicit in the review workflows; the shared validation action is not used by consumer review jobs.
`review-worker.yml` anchors repeated action references at their first use, where the full SHA and version comment remain visible.
Anchors cannot span files and do not share permissions or step inputs.
Dependabot maintains the literal bootstrap checkout pins repeated across workflows.
The image builds for the builder's native architecture; the separate release pipeline uses AMD64 for GitHub's runner.
A local ARM64 smoke is runtime/layout evidence, not verification of an AMD64 publication or GitHub event permissions.
Single-UID rootless environments cannot switch to Debian's `_apt` user during a build.
For local verification there, mount a temporary APT configuration containing `APT::Sandbox::User "root";` into `/etc/apt/apt.conf.d/` with `podman build --volume`; do not bake this host-specific override into the release image.

Before broad rollout, exercise a deployed consumer with authorized and unauthorized comments, same-repository and public-fork PRs, failure/retry reactions, stale-head publication, and concurrent requests in two repositories.
GitHub secret propagation and private-fork policy cannot be established by local fixtures alone.

## GitHub references

- [Reusable workflow access, caller context, token permissions, and billing](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).
- [Repository-scoped GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token).
- [Explicit secrets and same-commit relative workflow references](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).
- [Current-job workflow identity and reusable-workflow source checkout](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).
- [Default-branch requirement for issue_comment](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#issue_comment).
- [Repository and organization secret management](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).
