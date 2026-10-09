# A dedicated machine as the benchmark runner

GitHub-hosted runners are shared VMs whose CPU model changes from one run to
the next, so their numbers trend poorly. A dedicated self-hosted runner measures
every run on the same hardware, and its jobs are not capped at 6 hours, so the
standard profile fits. The workflow already supports one: this page registers it.

## The machine

- Linux x86_64, idle while it benchmarks: nothing else measured or compiled on it
  during a run. Four cores or more (the suites give half to the server, half to
  the clients); 4 GB of RAM limits the widest throughput cases, which the runner
  records as out-of-memory rather than measuring.
- Installed once, since the workflow does not use `sudo` there: Git, Python 3.11
  to 3.14 with `venv`, a C compiler, CMake 3.21+ and the OpenSSL headers. On
  Debian or Ubuntu: `sudo apt-get install build-essential cmake libssl-dev python3-venv git`.
- About 15 GB free: the pinned toolchains and builds stay in the runner's work
  folder between runs, so only a changed pin is rebuilt.

## Security

This repository is public. A self-hosted runner executes whatever a workflow
sends it, so it must only receive this repository's own workflows:

- No workflow here runs on `pull_request`. Keep it that way for every job that
  can reach the runner, and under *Settings, Actions, General*, require approval
  for workflows from outside collaborators.
- Run the runner as a dedicated unprivileged user with no `sudo` and no
  credentials beyond its registration.

## Register it

On the machine, as the runner user (here for this repository; replace the owner
and repository for another one). `gh` authenticated as a repository admin can
issue the one-hour registration token from any computer:

```bash
gh api -X POST repos/erossignon/opcua-benchmarks/actions/runners/registration-token --jq .token
```

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
version=2.338.0   # the latest release of github.com/actions/runner
archive=actions-runner-linux-x64-$version.tar.gz
curl -fsSLO https://github.com/actions/runner/releases/download/v$version/$archive
# compare with the SHA-256 printed in that release's notes
sha256sum $archive
tar xzf $archive
./config.sh --url https://github.com/erossignon/opcua-benchmarks --token <token> \
  --name bench-minipc --labels bench-minipc --work _work --unattended
```

## Keep it exclusive

Runs must not overlap with benchmarks started by hand on the same machine (they
share port 4840 and the CPUs). The runner's job hooks make it follow the
`/tmp/BENCH-BUSY-*` convention: a job waits until no marker and no
`python -m bench.<suite> sample` remain, claims the machine, and frees it when it ends.

```bash
cat > ~/actions-runner/job-started.sh <<'EOF'
#!/usr/bin/env bash
# Wait up to 6 h for the machine to be free, then claim it for this job.
for _ in $(seq 720); do
  if ! compgen -G '/tmp/BENCH-BUSY-*' > /dev/null && ! pgrep -f 'python.* -m bench\.[a-z_]+ sample' > /dev/null; then
    ( set -o noclobber; echo "github-runner $GITHUB_RUN_ID $GITHUB_JOB $(date -Is)" > /tmp/BENCH-BUSY-github-runner ) 2> /dev/null && exit 0
  fi
  sleep 30
done
echo "the machine stayed busy for 6 h: $(ls /tmp/BENCH-BUSY-* 2> /dev/null)" >&2
exit 1
EOF
cat > ~/actions-runner/job-completed.sh <<'EOF'
#!/usr/bin/env bash
rm -f /tmp/BENCH-BUSY-github-runner
EOF
chmod +x ~/actions-runner/job-*.sh
cat >> ~/actions-runner/.env <<EOF
ACTIONS_RUNNER_HOOK_JOB_STARTED=$HOME/actions-runner/job-started.sh
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=$HOME/actions-runner/job-completed.sh
EOF
```

A marker left by a crashed job blocks the next ones until it is deleted:
`ls -l /tmp/BENCH-BUSY-*` shows who wrote it.

## Start it as a service

Installing the service needs `sudo` once:

```bash
cd ~/actions-runner
sudo ./svc.sh install "$USER"
sudo ./svc.sh start
sudo ./svc.sh status
```

The runner then shows as *Idle* under *Settings, Actions, Runners*.

## Send the benchmarks to it

- One run: start the *benchmarks* workflow with `runner` set to `bench-minipc`.
- Every scheduled and push run: set the repository variable
  `gh variable set BENCH_RUNNER --body bench-minipc -R erossignon/opcua-benchmarks`.
  Delete it to go back to the hosted runners.

On one runner the suite jobs of a run execute one after the other. The first run
builds every worker (the .NET, Java and S2OPC builds are slow on a small machine);
later runs reuse them.

## Remove it

```bash
cd ~/actions-runner
sudo ./svc.sh stop && sudo ./svc.sh uninstall
./config.sh remove --token "$(gh api -X POST repos/erossignon/opcua-benchmarks/actions/runners/remove-token --jq .token)"
```
