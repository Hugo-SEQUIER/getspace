# CI runner

The `CI` workflow (`.github/workflows/ci.yml`) runs most of its jobs on a self-hosted GitHub Actions runner on the project's own server rather than on GitHub's machines.

## Where each job runs

| Job | Runner | Why |
|---|---|---|
| `backend` (Ruff and the unit tests) | self-hosted, label `getspace` | The unit tests need no database and no Docker; database tests are skipped without `SPACE_RUN_DATABASE_TESTS=1`. |
| `web` (unit tests, type check, build) | self-hosted, label `getspace` | `actions/setup-node` installs the Node.js version the job asks for, so the server's own Node.js does not matter. |
| `skills` | self-hosted, label `getspace` | Only Git and the shell. |
| `security` (gitleaks, osv-scanner) | self-hosted, label `getspace` | The shared workflow from `Hugo-SEQUIER/ci-workflows`. |
| `database-tests` | GitHub-hosted `ubuntu-24.04` | Starts `pgvector/pgvector:pg17` containers, so it needs Docker. |
| `images` | GitHub-hosted `ubuntu-24.04` | Builds the Docker images. |

The self-hosted runner has no Docker on purpose: access to the Docker daemon amounts to root on that server, so every job that needs Docker stays on GitHub's runners. The `Release` workflow (`.github/workflows/release.yml`), which publishes the images to the GitHub Container Registry, also stays on GitHub's runners.

## The runner

- Name `getspace-host-1`, labels `self-hosted` and `getspace`, registered to this repository only.
- It runs as the Linux user `gha-getspace`, which has no sudo and is not in the `docker` group.
- systemd limits it to `Nice=10`, `MemoryMax=4G` and `CPUQuota=300%`, so CI cannot starve the other services on the server.
- One runner runs one job at a time; the other self-hosted jobs of a run wait in the queue.

Upkeep, as root on the server:

```sh
systemctl status actions.runner.Hugo-SEQUIER-getspace.getspace-host-1.service
systemctl restart actions.runner.Hugo-SEQUIER-getspace.getspace-host-1.service
journalctl -u actions.runner.Hugo-SEQUIER-getspace.getspace-host-1.service --since today
```

`gh api repos/Hugo-SEQUIER/getspace/actions/runners` shows whether GitHub sees the runner online. While it is offline, the self-hosted jobs wait in the queue, for up to 24 hours, rather than fail.

## Pull requests from forks

The repository is public, so a pull request from a fork must never run on the server. Two safeguards apply:

- Every self-hosted job chooses its runner with `github.event.pull_request.head.repo.fork && 'ubuntu-24.04' || fromJSON('["self-hosted","getspace"]')`: a fork's pull request goes to GitHub's hosted runners, everything else to the server.
- A fork's pull request runs the workflow file from the fork, which its author can edit, so the expression alone is not enough. The repository requires a maintainer's approval before workflows run for any outside contributor. Before approving, read the pull request's changes to `.github/`: a changed `runs-on` that targets `self-hosted` or `getspace` would run that code on the server.
