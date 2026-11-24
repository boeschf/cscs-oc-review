# CSCS OpenCode review

## Purpose

This repository provides on-demand, advisory reviews of GitHub pull requests for repositories in the `eth-cscs` organization.
GitHub-hosted runners in each participating repository execute OpenCode, which uses the CSCS inference service to inspect PR changes.
The workflow posts the resulting review as a PR Conversation comment.
Findings require human review and are not required merge checks.

## Consumer setup

A consumer is a repository that installs a small caller workflow referencing the shared implementation in [eth-cscs/cscs-oc-review](https://github.com/eth-cscs/cscs-oc-review).

### Requirements

- The consumer must belong to `eth-cscs` and use GitHub.com.
  Public and private repositories can use the public shared workflows, subject to organization and repository Actions policies.
- Allow the caller's `contents: read` and `pull-requests: write` token permissions.
- Provide a `CSCS_INFERENCE_KEY` Actions secret at repository level or grant access to an organization secret.
  Use separate inference keys where repositories need independent budgets or revocation.

Workflow runs, artifacts, comments, and runner charges belong to the consumer.
No GitHub App, hosted receiver, PAT, registry credential, or CSCS-CI registration is required in a consumer.

### Install the caller

1. Review the changes and limitations in the [latest release](https://github.com/eth-cscs/cscs-oc-review/releases/latest).
2. Run this from the consumer repository root to download one caller as `.github/workflows/cscs-review.yml`.
   This overwrites an existing caller at that path.

   ```bash
   caller=cscs-review.yml # Use cscs-review-web.yml to enable web tools.
   mkdir -p .github/workflows &&
     curl --fail --location --silent --show-error \
       "https://github.com/eth-cscs/cscs-oc-review/releases/latest/download/$caller" \
       --output .github/workflows/cscs-review.yml
   ```

3. Review the downloaded file, preserve any custom inputs, and configure the inference secret and optional project instructions described below.
4. Merge the caller into the default branch, then [request a review](#request-and-result-behavior).

The caller must be on the default branch because GitHub dispatches `issue_comment` events from that branch; a caller in an unmerged PR will not run.
It pins the shared workflow to a full commit SHA, records the release in a version comment, and forwards only `CSCS_INFERENCE_KEY`.
Do not replace that explicit secret mapping with `secrets: inherit`.

### Project instructions

Add optional review-specific guidance at `.github/cscs-review.md` on the consumer's default branch.
For example:

```markdown
# Project review guidance

Prioritize correctness of halo exchanges and asynchronous buffer lifetimes.
Report performance concerns only with a concrete affected code path.
```

The reviewer also loads optional root `AGENTS.md` as general repository guidance.
To select another review file, add this input to the caller's existing `jobs.review` invocation:

```yaml
    with:
      review-instructions-path: "docs/review.md"
```

If `jobs.review` already has a `with` block, add `review-instructions-path` inside it alongside existing inputs such as `allow-web`.
Otherwise, add the block shown above.
An omitted or empty `review-instructions-path` selects the default file; a nonempty value replaces it with a required file.
It does not replace `AGENTS.md`.
Missing optional files are skipped, but a missing explicit replacement or an invalid present file stops preparation.

Both files come from the default branch, never from the PR head.
Paths must be printable and repository-relative, without absolute paths or parent traversal.
Files must be readable UTF-8 regular files within that checkout.
Symlinks to internal regular files are allowed; broken, looping, or escaping symlinks fail.

Conflict priority is review-specific instructions, then general repository guidance, then default reviewer guidance.
This defines model behavior, not a guarantee of compliance.
Runtime controls are independent of these instructions.

### Web access and private source

Web search and fetch tools are disabled by default.
To opt in, choose the web-enabled release caller or add `allow-web: true` to the same `with` mapping.
Search uses TinyFish without a separately configured search credential; service availability is best-effort.

Queries and fetched URLs can disclose repository details outside CSCS.
Disabling web tools does not restrict networking from the agent's shell.
Approve CSCS inference access and external-data policy before reviewing private source; this is not an exfiltration-proof sandbox.
Private-fork access depends on organization and fork-network permissions and requires a deployment check.
Submodules and Git LFS objects are not fetched, and the workflow does not obtain access to other private dependency repositories.

### Upgrades

To upgrade, download the selected caller from the new release and update the shared-workflow SHA and version comment in `.github/workflows/cscs-review.yml`, preserving optional inputs.
Review and merge the change.
The latest-release download URL selects a release; installed callers remain pinned to full SHAs and do not update automatically.

## Request and result behavior

### Request a review

Anyone with effective repository write access can post a new comment in an open PR's Conversation:

```text
/cscs-review Focus on cache invalidation and registry permissions.
```

The command must start the comment and is case-insensitive.
Separate optional focus text with a space, tab, CR, or LF.
Comment edits and inline diff comments do not trigger reviews.
Same-repository and public-fork PRs use the same entry point.
Authorization checks current GitHub permissions rather than `author_association`; maintain access maps to write access.
Noncommands and missing collaborators are skipped, while other permission API failures fail authorization.

### Results and retries

- An `eyes` reaction acknowledges an authorized request in the normal entry flow, not a completed review.
- The final review comment identifies the reviewed commit and links to the workflow run.
- A `confused` reaction indicates acknowledgement, review, or publication failure.
  Select **Re-run failed jobs** in Actions or post another command to retry.
  A successful retry removes that token user's failure reactions from the request comment.
- Cancellation leaves existing reactions unchanged.
  Clicking a reaction does not restart a run.
- Post another command after updating the PR head.
  Publication skips a result if its reviewed SHA no longer matches the current head, retaining the result as an artifact.
  The head check and comment creation are separate API calls, so a push between them can still race publication.

If OpenCode produces no final review text, the workflow fails without posting a review comment.
If a review is too long for a GitHub comment, the workflow does not post it; download the complete text from the `opencode-review` artifact.

### Context and concurrency

Each review receives PR metadata, the full merge-base diff, and all pages of previous reviews, Conversation comments, and inline threads, including replies and resolution/outdated state.
The complete context is mounted read-only at `/input/context.md`; inspection is instructed to read it in bounded ranges.
Missing or malformed history stops preparation rather than silently omitting discussion.
OpenCode is instructed to check earlier findings against the current code before reporting them again.

An authorized request replaces an earlier review of the same repository and PR.
Unauthorized comments cannot cancel reviews.
Different PRs and repositories run independently, subject to GitHub runner and inference-service capacity.
There is no organization-wide inference concurrency or spending controller.

## Limits and diagnostics

Consumers cannot override shared model settings, execution budgets, or executable plugins.
Consult these configuration sources for the current limits:

| Control | Definition |
| --- | --- |
| Model/provider selection, agent steps, output-token limits, reasoning effort, provider timeouts | [config/opencode.json](config/opencode.json) |
| Phase deadlines, startup overhead, termination grace | [config/limits.json](config/limits.json) |
| Host execution deadline calculation and progress-reporting interval | [host/run.py](host/run.py) |
| GitHub job timeouts and artifact retention | [.github/workflows/review-worker.yml](.github/workflows/review-worker.yml) |

Agent `steps` count model iterations, not tool calls or total API requests.
Output-token limits apply per request, include reasoning, and do not define a review-wide token budget.
Provider timeouts limit waits for response headers and gaps between response chunks, not the overall duration of an active stream.
The host execution deadline includes phase deadlines, termination grace, and startup overhead.

Finalization requires an established session and at least one successful source-inspection tool call, which proves neither coverage nor correctness.
Inspection stopped by a deadline, iteration limit, or token truncation can finalize gathered evidence with an incomplete-coverage banner.
Other inspection errors and all finalization failures produce no publishable fallback.

The runner logs progress counts, tool summaries, and the current phase, not model text or tool arguments.
Workflow runs retain these artifacts:

| Artifact | Contents |
| --- | --- |
| `opencode-review` | Extracted final `review.md` |
| `opencode-review-diagnostics` | Counts, phase, exit/cleanup status, step outcomes, and bounded recent error excerpts |

Inspect `opencode-review-diagnostics` from the failed workflow run for details.
Diagnostics replace known secrets and values in common credential assignments with `[REDACTED]`.
Error excerpts can still contain source text.
Prepared context, raw model events, reasoning, and stderr are not uploaded.
The provider maps returned reasoning through `compatibility.reasoningField`; missing reasoning events do not establish that the model generated none.

## Implementation

### Components

| Location | Responsibility |
| --- | --- |
| [.github/workflows/review.yml](.github/workflows/review.yml) | Public entry point, authorization, and per-PR cancellation |
| [.github/workflows/review-worker.yml](.github/workflows/review-worker.yml) | Acknowledgement, preparation, execution, artifacts, publication, and failure reactions |
| [host/context.py](host/context.py), [host/history.py](host/history.py) | Request validation, context collection, diff rendering, and instruction assembly |
| [host/run.py](host/run.py) | Input snapshots, container lifecycle, deadlines, source-state checks, and diagnostics |
| [host/extract.py](host/extract.py) | Phase/session validation and final-response extraction |
| [config/](config/) | Model settings, tool permissions, and execution limits |
| [image/](image/) | Container definition, OpenCode phase runner, entrypoint, and reviewer policy |
| [tools/caller.py](tools/caller.py) | Consumer workflow generation |
| [tools/release.py](tools/release.py), [.github/workflows/prepare-release.yml](.github/workflows/prepare-release.yml) | Source-release eligibility checks and draft preparation |
| [.cscs/workflows/build-opencode-review-image.yaml](.cscs/workflows/build-opencode-review-image.yaml) | Image build, GHCR publication, and digest verification |
| [tools/post_release.py](tools/post_release.py), [.github/workflows/prepare-post-release.yml](.github/workflows/prepare-post-release.yml) | Published-release validation and upstream maintenance PR preparation |
| [tools/github.py](tools/github.py) | Shared GitHub API and release-asset transport |
| [.github/workflows/checks.yml](.github/workflows/checks.yml) | Regression suites and workflow/YAML/shell validation |

### Execution and trust boundaries

Inspection uses tools to gather evidence; finalization uses the same private session with tools denied and provider-level `tool_choice: none`.
The final task reapplies the image-baked [reviewer policy](image/grumpy-reviewer.md).
Extraction requires completed text from one final assistant message and a successful runner-controlled final phase.
OpenCode can reconcile text without `step_finish`; emitted finish events must report `stop` or `end_turn`.
Malformed phases, changed sessions, finalizer tool use, unfinished text, failed finish events, and continuation responses fail without publication.

| Review job | GitHub token permissions | Inference key |
| --- | --- | --- |
| `authorize` | `contents: read` | None |
| `acknowledge` | `pull-requests: write` | None |
| `review` | `contents: read`, `pull-requests: read` | Container execution step only |
| `finish` | `pull-requests: write` | None |

The write-enabled review jobs have no source checkout or model process.
The container receives the inference key but no GitHub/Actions token, SSH agent, host home directory, or container socket.
It runs as UID/GID 1000 with a read-only root filesystem, dropped capabilities, and `no-new-privileges`.
Source and supplied inputs are read-only mounts; `/work` and `/tmp` are private writable scratch space.
The host compares `HEAD` and Git status before and after execution and attempts bounded cleanup on failure or interruption.
Policy prohibits editing source, executing PR code or tests, installing packages, and agent-side publication.
The available shell can still execute readable code; read-only mounts are not an execution barrier.

| Runner directory | Selected source | Use |
| --- | --- | --- |
| `reviewer-source/` | Current reusable-workflow repository and commit | Trusted executable helpers and configuration |
| `repository-default/` | Consumer default branch | Instruction files only |
| `pr-source/` | API-selected PR-head SHA | Untrusted review data |

Trusted source checkouts use `job.workflow_repository` and `job.workflow_sha`.
The worker reference is relative to the entry workflow, keeping both at the consumer's selected release commit.
PR base/head comparisons use validated SHAs; helper Git commands disable hooks, external diff drivers, and text converters.
The PR's base branch determines the merge base and need not be its default branch.
Git credentials are not persisted in checkouts.
GitHub API endpoints are fixed to GitHub.com, so GitHub Enterprise Server is not supported.

Instruction assembly validates every selected source before creating an output outside the checkout.
It preserves content, labels sections and source paths, logs paths rather than contents, and emits a duplicate resolved source only as review-specific guidance.
Both phases read the same `/instructions/AGENTS.md` snapshot.
Direct worker callers bypass the entry authorization job, so preparation rechecks the command and requester permission before inference.
Keep cancellation in the entry workflow; reusing its concurrency group in a caller or worker can cancel the run itself.

### Generate a caller locally

Use Python 3.12 from a checkout of the intended shared revision.
Replace `FULL_COMMIT_SHA` below with a lowercase, 40-character SHA that contains the reusable workflows and is available in the public shared repository:

```bash
python3 tools/caller.py --revision FULL_COMMIT_SHA --release-version "$(cat VERSION)" > /tmp/cscs-review.yml
```

The generator prints YAML without modifying repositories, calling GitHub, or installing workflows.
It rejects branches, tags, abbreviated SHAs, and GitHub expressions in literal instruction paths.
Add `--allow-web` and/or `--review-instructions-path docs/review.md` for the corresponding optional inputs.
`--release-version` adds only a comment; the SHA selects executable code.

### Image publication

The runtime image contains OpenCode, the phase runner, and the reviewer policy.
The OpenCode version is set by `OPENCODE_VERSION` in [image/Containerfile](image/Containerfile).
The image is published as a GHCR package at `ghcr.io/eth-cscs/cscs-oc-review/opencode-review`.
Tags follow `opencode-<opencode-version>-amd64-v<image-revision>`.
Configuration and consumer instructions are mounted separately.
Runtime or policy changes require a new image build and digest update; configuration-only changes do not.

1. Set `OPENCODE_REVIEW_IMAGE` in the [image pipeline](.cscs/workflows/build-opencode-review-image.yaml) to an unused tag.
   The pipeline's `if-not-exists` policy reuses existing tags rather than rebuilding them.
2. Post this comment on a PR to build the image:

   ```text
   cscs-ci run build-reviewer-image
   ```

3. Set `jobs.review.env.OPENCODE_REVIEW_IMAGE` in [review-worker.yml](.github/workflows/review-worker.yml) to the reported `OPENCODE_REVIEW_IMAGE_PIN` value.

### Source releases

[tools/release.py](tools/release.py) reads and validates the source version in [VERSION](VERSION), then prefixes it with `v` for the release tag.
The version must use `MAJOR.MINOR.PATCH` without leading zeros, prerelease suffixes, or build metadata.
Source, image, and OpenCode versions are independent.
Use patch versions for compatible fixes, minor versions for compatible features, and major versions for incompatible changes.
Before the first major release, incompatible changes increment the minor version.
`tools/release.py` rejects an existing tag or release for the chosen version but does not check that version numbers increase.

1. Merge reviewed changes and any version update into the upstream default branch.
   Keep that branch protected and immutable releases enabled; the workflow does not configure or verify those settings.
2. Wait for the latest matching `checks.yml` **push** run to succeed for that exact SHA on the upstream default branch.
   PR runs, fork runs, and runs for other branches or commits do not qualify.
3. Run **Actions → Prepare release → Run workflow** on that branch.
   It reads `VERSION`, rejects existing tags/releases including drafts, generates both caller variants, and actionlints their YAML without rerunning source suites.
4. The workflow generates change notes, reserves the version tag at the dispatch SHA, creates a draft, and uploads the exact validated `cscs-review.yml` and `cscs-review-web.yml` files as release assets.
   Notes link to version-specific downloads and include source provenance, the checks link, and image pin.
5. Inspect the notes and verify both assets before manually publishing the draft and marking it as latest.

Preparation uses repository-scoped `GITHUB_TOKEN` permissions `contents: write` and `actions: read`, without a PAT or inference key.
A creation or upload failure can leave an incomplete draft, tag, or assets; there is no automatic retry or cleanup.
Inspect the result and deliberately remove only unpublished leftover state before retrying; never publish an incomplete draft.
Published immutable releases prevent adding or replacing assets, and their versions cannot be reused.

### Post-release maintenance

The upstream-only [Prepare post-release](.github/workflows/prepare-post-release.yml) workflow handles `release.published` for stable releases in `eth-cscs/cscs-oc-review`.
It loads trusted helpers from the current default branch, not the release asset or a subscriber checkout, and performs no inference.
[tools/post_release.py](tools/post_release.py) opens `maintenance/post-release-vX.Y.Z` against the current default branch and updates only these files:

- `VERSION` advances from the published `X.Y.Z` to `X.Y.(Z+1)` for the next development cycle.
- `.github/workflows/cscs-review.yml` receives the published release's `cscs-review-web.yml` asset unchanged, pinning the released full SHA and enabling web tools.

This installs the upstream repository's own review caller; subscriber installation and upgrades remain manual as described above.
The maintenance PR runs normal CI; a maintainer reviews and merges it manually.
There is no automatic merge or modification of the published release.

The workflow uses repository variable `POST_RELEASE_APP_CLIENT_ID` and secret `POST_RELEASE_APP_PRIVATE_KEY` to mint a short-lived App installation token scoped to this repository.
The token is revoked after the job, and the App bot creates the maintenance commit and PR.
App credentials are never forwarded to review jobs or inference.

#### Validation and reruns

Preparation rejects a tag/source-version mismatch, a released commit outside the default branch's ancestry, a caller downgrade, and missing or invalid caller assets.
A new PR also requires the current default-branch `VERSION` to equal the released version.
The asset must match the released SHA and web-enabled caller setting; it is validated as data and never executed.
Shared [GitHub transport](tools/github.py) rejects authenticated redirects; public release-asset downloads carry no credentials and allow only HTTPS redirects.

Reruns look up PR history by the deterministic branch even after branch deletion.
An existing open PR is reused without overwriting its contents, a merged PR is a no-op, and a closed unmerged PR is reported without reopening it.
A branch left without a PR is recoverable only when it has one commit based on the current default-branch head and its changes match the planned caller/version update.
Conflicting branch contents or incompatible default-branch state stop preparation for maintainer inspection rather than being overwritten.
Failures leave the published release unchanged; rerun the failed workflow after resolving the reported conflict or setup issue.

### Development checks

Run from the repository root with Node.js 24 and uv available:

```bash
uv run --python 3.12 --with pytest --with pyyaml pytest -q host/tests tools/tests
node --test image/test-run-review.mjs
bash -n image/entrypoint.sh
```

Tests use temporary repositories and controlled child processes, not inference credentials.
Tooling tests share API request recording and HTTP errors in [tools/tests/support.py](tools/tests/support.py).
[tools/tests/conftest.py](tools/tests/conftest.py) blocks production urllib clients; transport tests explicitly use a guarded loopback fixture.
CI additionally runs actionlint and parses CI YAML and embedded shell, including local composite actions.
Checks and both release workflows share [.github/actions/setup-validation-tools/](.github/actions/setup-validation-tools/), which owns Python/uv setup and the optional verified actionlint installation.
Repeated action references use file-local anchors where possible; bootstrap checkout pins remain literal across files and are maintained by the repository's Dependabot configuration.
Permissions, checkout inputs, and concurrency remain explicit rather than shared through anchors.

