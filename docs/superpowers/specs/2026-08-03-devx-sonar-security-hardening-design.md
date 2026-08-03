# DevX Security Hardening via SonarCloud — Design

## Goal

Use the `sonar` DevX overlay (built and validated against a WebGoat pilot in
`docs/superpowers/plans/2026-08-01-sonarqube-integration.md`) to get a
security read on the `devx` repo itself, and fix what's a real security
issue. One bounded pass — not a permanent CI commitment, not a general
code-quality cleanup.

## Scope

- **In scope:** Vulnerabilities and Security Hotspots surfaced by a SonarCloud
  scan of `devx`. Each gets a real-vs-noise verdict before any fix is
  attempted.
- **Out of scope:** a permanent `.github/workflows/sonar.yml` on `devx` (may
  come later, not now); non-security Bugs/Code Smells; the WebGoat fork's own
  Task 6 findings-triage (parked separately, for later academic/known-issue
  practice — see the sonarqube-integration plan's ledger).

## Setup (one-time)

- New SonarCloud project: key `mlessley_devx`, org `mlessley` (the same
  organization used for the WebGoat pilot).
- A fresh SonarCloud token scoped to this project, generated the same way as
  the pilot's `SONAR_TOKEN` (My Account → Security, shown once). Kept
  sandbox-local only — no `gh secret set`, since there's no CI here.
- No `sonar-project.properties` committed to the repo. Scans pass
  `-Dsonar.projectKey=mlessley_devx -Dsonar.organization=mlessley` as CLI
  flags each run. This keeps the repo's footprint at zero for the scanning
  setup itself — only actual security fixes land as commits. (A properties
  file is an easy, low-risk follow-up later if manual scans become a
  recurring habit across repos.)

## Scan mechanics

Same proven pattern as the WebGoat pilot's Task 5 (manual scan from inside
the DevX sandbox):

1. `./devx up sonar java` (or `sonar` alone, if `java` isn't needed for
   analysis — devx is primarily Python/shell/Docker, no Maven build step) with
   `SONAR_TOKEN` exported in the same shell.
2. `ssh devx`, then clone `https://github.com/mlessley/devx.git` into
   `/devx/repos/devx` — the sandbox's `/devx` is a named Docker volume, not a
   bind mount of the host checkout, so the repo needs its own clone inside the
   sandbox, exactly as WebGoat did.
3. From inside that clone, run `sonar-scanner` with the project key/org flags
   above.
4. Read results via the SonarCloud dashboard/API for `mlessley_devx`.

If the initial `sonar-scanner` invocation needs a `mvn`-equivalent fix (as the
WebGoat pilot's plugin-prefix issue did), diagnose it the same way: read the
actual failure, don't guess.

## Triage → fix loop

For the resulting Vulnerabilities + Security Hotspots list:

1. Read each finding's "Why is this an issue?" panel and the flagged
   `devx` source line; record a real-vs-false-positive verdict.
2. Hotspots get an explicit Safe/Fixed/To Review status with a one-line
   justification, same as the WebGoat pilot's Task 6 pattern.
3. For each confirmed-real finding, implement the fix in an isolated
   worktree/branch (e.g. `security-hardening` /
   `worktree-security-hardening`), following `superpowers:using-git-worktrees`
   + `superpowers:subagent-driven-development` conventions already
   established in this repo (task brief → implementer → task review → ledger
   entry, per task).
4. **Verification:** `devx` has no automated test suite today. Verify each
   fix the same way Tasks 1-2 of the sonar-overlay plan did — rebuild/run the
   affected path (e.g. `./devx up`/`down`/`status`, exercising the specific
   command or code path the fix touches) and confirm expected behavior
   directly, rather than via a test command.
5. Once the batch of confirmed fixes is in and reviewed, merge via
   `superpowers:finishing-a-development-branch`.

## Error handling

No new failure modes beyond what the WebGoat pilot already surfaced and
resolved (SonarCloud Automatic-Analysis-vs-CI conflict — not applicable here
since there's no CI; the sandbox's SSH-session env-var propagation gap for
`SONAR_TOKEN` — already known, worked around by exporting the token directly
inside the SSH session rather than relying on docker-compose env
passthrough).

## Testing

No automated tests exist in `devx`. Each security fix is verified manually
against the specific behavior it changes, as described above. This design
doesn't add a test suite — that would be a separate, unrelated project.
