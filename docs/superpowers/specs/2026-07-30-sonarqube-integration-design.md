# SonarQube Integration: SonarCloud-Backed Static Analysis via DevX

## Problem

DevX (this repo) has no static-analysis tooling and no CI/CD of its own. It
does have an established pattern for optional per-project toolchains (`java`,
`dotnet` stack overlays selected via `./devx up <stack>`) and service
overlays (`postgres`, `chroma`, `ztp`). This design adds SonarQube-based
static analysis to that toolchain set, and validates it end-to-end — local
scans and a CI/CD quality gate — against a known-vulnerable test corpus
before pointing it at real projects.

**Non-goals:** running/administering an on-prem SonarQube server, exposing
any service publicly, and scanning private repos (SonarCloud's free tier is
unlimited only for public repos).

## Approaches considered

- **Lightweight `sonar-scanner` DevX overlay + SonarCloud backend (chosen).**
  Add a `sonar` stack to DevX (same pattern as `java`/`dotnet`) that installs
  the `sonar-scanner` CLI into the sandbox image. All scans — manual, from
  inside the sandbox, and automated, via GitHub Actions — point at the same
  SonarCloud project. No server to run or administer.
- **Manual per-session setup, no DevX changes.** Install `sonar-scanner` by
  hand each time. Rejected: doesn't persist across sandbox sessions/rebuilds,
  and the whole point of DevX is reusable, one-command environments.
- **Self-hosted SonarQube Community Edition as a DevX service overlay
  (`docker-compose.sonar.yml` running a real server), used alongside
  SonarCloud for CI.** Rejected for now: two separate Sonar backends (local
  server vs. SonarCloud) can drift in rule versions and produce different
  findings for the same code. Also brings on-prem/exposure complexity
  (public reachability for GitHub-hosted runners) that isn't needed for a
  first pilot. Worth revisiting later if self-hosted administration
  (quality profiles, project admin, upgrades) becomes a separate requirement.

## Design

### 1. Architecture

One SonarCloud organization/project is the single source of truth. A public
fork of `WebGoat/WebGoat` (Java/Maven, a well-known intentionally-vulnerable
test application with a documented/cataloged set of vulnerabilities) is the
pilot target — a good corpus for validating that the scanner configuration
actually surfaces real findings before it's applied to other repos. Two
trigger paths feed the same SonarCloud project:

- **Manual scans** from inside the DevX sandbox, for fast iteration while
  tuning scanner/quality-profile configuration.
- **Automated scans** via GitHub Actions on push/PR, for the CI/CD path
  (PR decoration, Quality Gate pass/fail status check).

Because both paths hit the same backend, results are always consistent
between local runs and CI.

### 2. SonarCloud + WebGoat project setup

- Fork `WebGoat/WebGoat` to a GitHub account (public, so SonarCloud stays
  free/unlimited).
- Create a SonarCloud account linked to GitHub; import the fork as a new
  SonarCloud project.
- Generate a SonarCloud token. Store it as a GitHub Actions secret
  (`SONAR_TOKEN`) on the fork, and separately as an env var inside the DevX
  sandbox for manual scans — never committed to the repo.
- Keep the default "Sonar way" Quality Gate initially; it's the standard
  baseline and a reasonable default for a first pilot.

### 3. DevX `sonar` overlay

- New Dockerfile build arg `INSTALL_SONAR=true` in `core/Dockerfile`,
  following the existing curl-and-install pattern already used for the
  Terraform/Docker CLI steps: downloads the `sonar-scanner` CLI for the
  container's architecture into `/usr/local/bin`.
- New `core/docker-compose.sonar.yml` overlay (mirrors
  `core/docker-compose.java.yml`), setting `INSTALL_SONAR: true` as a build
  arg. No running service — this is a toolchain addition, not a service
  overlay, consistent with how `java`/`dotnet` work today.
- Usage: `./devx up sonar java` (or via the interactive stack-selection menu)
  gives a sandbox with both the JDK/Maven toolchain and `sonar-scanner`
  available, which is what WebGoat needs.
- `SONAR_TOKEN` and `SONAR_HOST_URL=https://sonarcloud.io` are set as
  environment variables inside the running sandbox (not baked into the image
  and not committed anywhere).

### 4. Local/manual scan workflow

Inside the sandbox, against the WebGoat fork:

```
mvn clean verify
mvn sonar:sonar -Dsonar.token=$SONAR_TOKEN
```

`mvn sonar:sonar` (the Sonar Maven plugin) is preferred over the standalone
`sonar-scanner` CLI here because it inherits the Maven build's classpath and
test/coverage output automatically. Results land in the SonarCloud project
within seconds.

### 5. CI/CD workflow (GitHub Actions)

A `.github/workflows/sonar.yml` added to the WebGoat fork, triggered on push
and pull_request:

- Checkout, set up JDK 17 (current WebGoat releases require it — the
  implementation plan should verify this against the exact fork commit before
  writing the workflow), run `mvn clean verify`, then run the scan via
  `mvn sonar:sonar` (or the official `sonarsource/sonarcloud-github-action`),
  using the `SONAR_TOKEN` secret.
- On PRs, SonarCloud posts inline review comments and a commit status check
  reflecting the Quality Gate result.

### 6. Findings review workflow

- Work the SonarCloud Issues list by type: **Vulnerabilities** and
  **Security Hotspots** first, then Bugs, then Code Smells.
- For each finding: read Sonar's "Why is this an issue?" explanation, locate
  it in WebGoat's source, decide real vs. false positive, and either fix it
  (small PR against the fork) or mark it Won't Fix / False Positive with a
  written justification.
- Cross-check findings against WebGoat's own documented/cataloged
  vulnerability list where relevant, to validate whether the scanner
  configuration is actually catching what each WebGoat lesson demonstrates —
  useful signal on the scanner's coverage before pointing it at other repos.
- Re-run scans after fixes to confirm the Quality Gate and finding count
  move as expected.

### 7. Verification

- `./devx up sonar java` succeeds; `mvn -v` and `sonar-scanner -v` both work
  inside the sandbox.
- A manual `mvn sonar:sonar` run against the WebGoat fork populates the
  SonarCloud project with findings.
- A throwaway PR against the fork shows SonarCloud's automated PR comment and
  status check produced by the GitHub Actions workflow.
