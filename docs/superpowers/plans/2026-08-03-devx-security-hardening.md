# DevX Security Hardening via SonarCloud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get a security read on the `devx` repo itself via SonarCloud, using the `sonar` DevX overlay already built and validated against a WebGoat pilot, and fix confirmed-real security findings.

**Architecture:** Same manual-scan pattern proven in the WebGoat pilot (`docs/superpowers/plans/2026-08-01-sonarqube-integration.md`, Task 5): bring up the sandbox with the `sonar` overlay, clone `devx` into the sandbox (it uses a named volume, not a host bind mount), run `sonar-scanner` with CLI flags (no committed config file), then triage and fix.

**Tech Stack:** SonarCloud, `sonar-scanner-cli` (already installed via the `sonar` overlay), DevX sandbox, GitHub CLI.

## Global Constraints

- SonarCloud project key `mlessley_devx`, organization `mlessley` (same org as the WebGoat pilot).
- No `sonar-project.properties` committed to the repo — pass `-Dsonar.projectKey=mlessley_devx -Dsonar.organization=mlessley` as CLI flags on every scan invocation.
- A fresh SonarCloud token scoped to this project (not the WebGoat pilot's token). Sandbox-local only — no `gh secret set`, since this plan adds no CI.
- Scope is security-only: only confirmed Vulnerabilities and Security Hotspots get fixed. Bugs and Code Smells are out of scope for this pass.
- Any fix lands in an isolated worktree/branch (e.g. `security-hardening` / `worktree-security-hardening`), never directly on `main`.
- `devx` has no automated test suite. Verify each fix by manually exercising the specific command/code path it changes (e.g. `./devx up`/`down`/`status`), the same way Tasks 1-2 of the sonar-overlay plan were verified.
- No permanent `.github/workflows/sonar.yml` is added to `devx` in this pass.
- Spec reference: `docs/superpowers/specs/2026-08-03-devx-sonar-security-hardening-design.md`.

---

### Task 1: Set up the SonarCloud project and token for `devx`

**Files:** none — external state only.

**Interfaces:**
- Produces: SonarCloud project `mlessley_devx` (org `mlessley`) and a scan token. Task 2 consumes both.

- [ ] **Step 1: Confirm `devx` is public**

```bash
gh repo view mlessley/devx --json visibility
```

Expected: `{"visibility":"PUBLIC"}` — required for SonarCloud's free tier.

- [ ] **Step 2: Import into SonarCloud (manual, browser)**

1. Go to sonarcloud.io, signed in as the `mlessley` GitHub account.
2. Click "+" → "Analyze new project" → select `mlessley/devx` from the GitHub repo list → import it.
3. Note the exact **Project Key** and **Organization Key** from the project's Information page (expected `mlessley_devx` / `mlessley`, but confirm — SonarCloud sometimes suffixes keys to avoid collisions).

- [ ] **Step 3: Generate a token (manual, browser)**

1. SonarCloud → avatar → My Account → Security.
2. Generate a token named `devx-security-scan`, scoped to the `devx` project if that option is offered.
3. Copy the token value immediately — SonarCloud only shows it once. Do not commit it anywhere; keep it for exporting into a shell in Task 2.

No commit — nothing in this task touches the `devx` repo's tracked files.

---

### Task 2: Run the initial scan from inside the DevX sandbox

**Files:** none — verification/data-gathering task, no commits.

**Interfaces:**
- Consumes: the project key/org from Task 1, Step 2, and the token from Task 1, Step 3.
- Produces: a SonarCloud analysis of `devx`'s current `main` branch. Task 3 consumes its findings.

- [ ] **Step 1: Bring up the sandbox with the `sonar` overlay**

The `sonar` overlay (`core/docker-compose.sonar.yml`) currently only exists on the `worktree-sonarqube-integration` branch (that plan's Tasks 6-7 aren't merged to `main` yet), so run this from that worktree checkout:

```bash
cd /mnt/c/source/devx/.claude/worktrees/sonarqube-integration
export SONAR_TOKEN=<paste the devx-scoped token from Task 1, Step 3>
./devx up sonar
```

`java` isn't needed here — unlike the WebGoat pilot, `devx` has no Maven build.

- [ ] **Step 2: Sanity-check the scanner binary**

```bash
ssh devx "sonar-scanner -v"
```

Expected: a version banner (`SonarScanner CLI 8.1.0.6389` or similar), confirming the overlay installed correctly.

- [ ] **Step 3: Clone `devx` into the sandbox**

```bash
ssh devx "git clone https://github.com/mlessley/devx.git /devx/repos/devx"
```

The sandbox's `/devx` is a named Docker volume (`devx_workspace`), not a bind mount of the host checkout, so it needs its own clone — same as the WebGoat pilot's Task 4/5.

- [ ] **Step 4: Export the token inside the SSH session**

```bash
ssh devx
```

Then, inside that interactive session:

```bash
export SONAR_TOKEN=<paste the same devx-scoped token>
```

This is a known workaround, not a mistake: `docker-compose.sonar.yml`'s `environment:` block only reaches the container's PID 1, not SSH login sessions (`core/entrypoint.sh` never writes it to `/etc/environment`, and `sshd`'s `AcceptEnv` doesn't allow it through) — discovered and worked around the same way during the WebGoat pilot's Task 5.

- [ ] **Step 5: Run the scan**

Still inside the SSH session:

```bash
cd /devx/repos/devx
sonar-scanner \
  -Dsonar.projectKey=mlessley_devx \
  -Dsonar.organization=mlessley \
  -Dsonar.sources=. \
  -Dsonar.token=$SONAR_TOKEN \
  -Dsonar.exclusions=".claude/**,.superpowers/**"
```

Expected: scanner log ends with `EXECUTION SUCCESS` and a link to the analysis on SonarCloud. The exclusions keep prior pilot/agent scratch state (`.claude/`, `.superpowers/`) out of the analysis — everything else in the repo (Python, shell, Dockerfile, YAML) is in scope.

- [ ] **Step 6: Verify the analysis landed**

From the host (not the sandbox):

```bash
curl -s "https://sonarcloud.io/api/project_analyses/search?project=mlessley_devx&ps=1"
```

Expected: one analysis entry with a timestamp matching this run.

No commit — this task only produces SonarCloud state.

---

### Task 3: Findings triage pass (Vulnerabilities + Security Hotspots only)

**Files:** none — manual analysis. If a fix is warranted, it becomes a new task worked through `superpowers:subagent-driven-development`'s normal task loop (brief → implementer → task review → ledger entry) inside an isolated worktree — do not add placeholder "fix" tasks here, since which findings need fixing can't be known until Task 2's scan results exist. Non-security findings (Bugs, Code Smells) are out of scope per the Global Constraints and get skipped even if present.

- [ ] **Step 1: List Vulnerabilities**

```bash
curl -s "https://sonarcloud.io/api/issues/search?componentKeys=mlessley_devx&types=VULNERABILITY&ps=100"
```

(Or the SonarCloud UI's Issues tab, filtered to type = Vulnerability.) For each: read the "Why is this an issue?" explanation, locate the flagged line in the `devx` source, and record a verdict — real finding vs. false positive.

- [ ] **Step 2: List Security Hotspots**

```bash
curl -s "https://sonarcloud.io/api/hotspots/search?projectKey=mlessley_devx&ps=100"
```

(Or the SonarCloud UI's Security Hotspots tab.) Hotspots require an explicit Safe/Fixed/To Review status in SonarCloud (they don't fail a Quality Gate on their own, and there is no CI Quality Gate in this plan anyway) — set a status with a one-line justification for each.

- [ ] **Step 3: Record the outcome**

Relay a short summary back in conversation: total Vulnerabilities and Hotspots found, how many were confirmed real vs. false-positive/won't-fix, and which files/lines the real ones touch.

- [ ] **Step 4: Stand up the fix worktree, if any findings are real**

If Steps 1-2 produced at least one confirmed-real finding:

```bash
# from superpowers:using-git-worktrees, e.g.:
git worktree add .claude/worktrees/security-hardening -b worktree-security-hardening main
```

Then work each confirmed finding through `superpowers:subagent-driven-development`'s task loop: write a brief describing the specific finding and the fix, dispatch an implementer, verify manually (per the Global Constraints — no automated test suite exists), task-review, ledger entry. Merge via `superpowers:finishing-a-development-branch` once the batch is done.

No commit for this task itself — worktree creation and fix commits happen as part of moving into the fix loop, once real findings are known.
