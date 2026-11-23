# CSCS OpenCode review

## Purpose

This repository provides an on-demand PR reviewer for repositories in the `eth-cscs` GitHub organization.
The upstream source is [eth-cscs/cscs-oc-review](https://github.com/eth-cscs/cscs-oc-review).
A shared workflow authorizes a Conversation comment, inspects the PR in a read-only container, and posts a separate final response.
The [reviewer policy](image/grumpy-reviewer.md) defines the grumpy voice, evidence standards, coverage requirements, and response format.
The reviewer does not implement changes, execute PR code or tests, install packages, or publish its own output.
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
| [.github/workflows/generate-caller.yml](.github/workflows/generate-caller.yml) | Generate a pinned consumer YAML artifact through GitHub Actions |
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

Write-enabled jobs have no source checkout or model process.
The container receives no GitHub token, SSH agent, host home directory, or container socket.
The runtime runs as UID/GID 1000 with a read-only root filesystem, dropped capabilities, and `no-new-privileges`.
The source, instructions, configuration, and prepared context are read-only mounts; `/work` and `/tmp` are private writable scratch space.
The host checks `HEAD` and Git status before and after execution and attempts bounded process/container cleanup on failure or interruption.

### Source selection

| Runner directory | Source | Treatment |
| --- | --- | --- |
| `reviewer-source/` | Current job's reusable-workflow repository and commit | Trusted executable helpers and shared configuration |
| `repository-default/` | Consumer repository default branch | Trusted repository instructions only; no scripts executed |
| `pr-source/` | API-selected current PR-head SHA | Untrusted source, mounted read-only |

The default branch selects trusted instructions; the PR's base branch is its merge target and determines the merge-base diff.
The two branches may differ.
Checkout and diff operations use validated commit SHAs, with Git hooks, external diff drivers, and text converters disabled.

The consumer pins the entry workflow to one full release commit SHA.
Both central-source checkouts use GitHub's `job.workflow_repository` and `job.workflow_sha`, which identify the workflow that defines the current job rather than the consumer's workflow.
The worker is referenced relative to the entry workflow, so GitHub loads it at that entry's commit.
Helpers and configuration therefore come from the same release without a separate source revision input.
These job context properties are available on GitHub.com, not GitHub Enterprise Server.
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

1. Select a reviewed, published commit of this repository.
2. Make a `CSCS_INFERENCE_KEY` Actions secret available to the consumer.
   A repository secret is sufficient; an organization owner can instead grant selected repositories access to an organization secret.
3. In this repository's **Actions → Generate consumer workflow → Run workflow**, select a branch or tag at the reviewed release and choose whether to allow external web tools.
   The run pins the generated file to the selected ref's resolved commit, not to its mutable branch or tag name.
4. Download the `cscs-review-caller` artifact and review `cscs-review.yml`, including the commit recorded in the run summary.
5. Place it at `.github/workflows/cscs-review.yml` in the consumer and merge it into that repository's default branch.
6. Add repository-specific `AGENTS.md` instructions on that default branch if needed.
7. Request a review as described under [Request and result behavior](#request-and-result-behavior).

The generation workflow must be present on this shared repository's default branch before GitHub offers its manual trigger.
It requires no inference secret and has read-only repository permissions; it creates an artifact, not a commit or PR.
The selected release must contain the generation workflow and reviewer implementation.

For local generation from a checkout of the selected release, use the same generator:

```bash
python3 tools/caller.py --revision "$(git rev-parse HEAD)" > /tmp/cscs-review.yml
```

The generator only prints YAML; it does not edit repositories, commit, push, or call GitHub.
It rejects branch names, tags, and abbreviated SHAs.
The generated caller explicitly forwards only the inference secret, not `secrets: inherit`.
To upgrade, generate the caller again with the new published release SHA and review the resulting change.
Configuration-only changes require a new source release, not an image rebuild.

The caller must exist on the consumer's default branch before `issue_comment` can trigger it.
A caller that exists only in an unmerged PR will not receive Conversation-comment events.
The reusable workflow definitions must exist at the selected shared commit, but need not first be merged into the shared repository's default branch.

The generated file is a small local trigger that references the shared workflow; it does not copy the reviewer implementation.
GitHub receives comment events in each consumer repository, so a central workflow reference cannot eliminate that local trigger file.
Generating the artifact avoids maintaining the caller YAML by hand, but installation still requires a reviewed consumer-repository change.
The workflow does not have cross-repository write credentials and cannot install itself into other repositories.

### Web access and private source

Web search and fetch tools are disabled by default in the worker's staged configuration.
To enable them deliberately, select the generation workflow's `allow-web` option or add `--allow-web` to the local generator.
The worker applies only this typed permission choice to shared configuration; consumers do not supply arbitrary OpenCode JSON or executable plugins.
When enabled, [config/opencode.json](config/opencode.json) selects TinyFish search without a separately supplied key.
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
- The command is case-insensitive; optional request text is separated by a space, tab, CR, or LF.
- The requester must have effective repository write access, including maintain/admin access and qualifying team or custom-role permissions.
- Noncommands and missing collaborators are skipped; other permission API failures fail authorization.
- An `eyes` reaction confirms acceptance, not completion.
- A `confused` reaction indicates an acknowledgement, review, or publication failure, including a timeout.
- Select **Re-run failed jobs** in Actions or post another command to retry.
  A successful retry removes only the workflow identity's failure reaction; clicking a reaction does not restart it.
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
Empty or oversized output is rejected.
If the PR head changed during the run, publication is skipped and the artifact is retained.
Cancellation does not add a failure reaction, and reaction updates depend on GitHub API access.

## Limits and diagnostics

[config/opencode.json](config/opencode.json) is the single model/provider definition and supplies the agents' generation settings.
The production OpenAI-compatible endpoint streams reasoning through the explicit `reasoning` compatibility field and preserves it in assistant history.
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

Inspect the diagnostic artifact before changing budgets after a failure.
Raw events, reasoning, stderr, and prepared context remain private runner files and are not uploaded.
Missing reasoning events do not establish that the model generated none.

## Image releases

The worker currently uses an existing publicly published runtime by digest.
Its registry namespace does not determine which consumer repository is reviewed.
The upstream repository rename leaves that image pin unchanged.
Future builds target `ghcr.io/eth-cscs/cscs-oc-review/opencode-review`; they affect review jobs only after the worker's digest pin is updated.
[image/Containerfile](image/Containerfile) builds the equivalent standalone runtime without dependencies on another repository's package helpers.
Only runtime files and policy are copied into the image, not tests, repository instructions, or runtime configuration.

Image building remains separate from review jobs:

1. Register [.cscs/workflows/build-opencode-review-image.yaml](.cscs/workflows/build-opencode-review-image.yaml) in CSCS CI as `build-opencode-image` if this repository will publish its own images.
2. Supply `GITHUB_ACTOR` and a masked secret `GITHUB_TOKEN` with GHCR package-publishing permission to the build and report jobs.
   A classic PAT with `write:packages` and organization package access is sufficient; authorize organization SSO where required and avoid unnecessary `repo` scope.
3. Select a new image tag in that pipeline for runtime or policy changes; `if-not-exists` deliberately reuses an existing tag.
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
uv run --python 3.12 --with pytest pytest -q host/tests
node --test image/test-run-review.mjs
bash -n image/entrypoint.sh
podman build --format docker -f image/Containerfile -t localhost/cscs-oc-review .
```

The regression suites use temporary repositories and controlled child processes, not inference credentials.
[checks.yml](.github/workflows/checks.yml) runs them for pushes and PRs, validates GitHub workflow semantics with actionlint, and syntax-checks CI YAML and embedded shell.
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
