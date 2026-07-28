# Running the TRACTOR vectors without Falco — status, design, results

**Status: `--no-falco` is implemented and verified.** The newer TRACTOR
corpus's own orchestrator (`tools/test_runner`) now runs Falco-free via a
`--no-falco` flag on **Yale-PROCTOR/Test-Corpus** branch
[`no-falco`](https://github.com/Yale-PROCTOR/Test-Corpus/tree/no-falco)
(`c627c09`, = DARPA `main` @ `3e3b487` (B03) + our changes). This unblocks
**state / stdout / library-state** vectors on B01/B02/**B03** — including
the newer-corpus library cases — with **no Falco, no privileged container,
no eBPF**. The only remaining Falco-only capability is **file-change
vectors** (see §5).

This replaces the old "everything is deferred" plan: what used to be the
recommended unblock (§7 of the previous version) is done.

## 1. What Falco did, and what `--no-falco` changes

The orchestrator ran a privileged **Falco** sidecar (`modern_ebpf`) to
capture each vector's **filesystem side-effects** (files created / modified
/ deleted) and diff them against the vector's `file_changes.tar.gz`. That is
the **only** capability Falco provides. Binary (stdout/stderr/rc) and
library (cando lib-state) comparison is done by **cando**, not Falco.

Falco touched the harness on exactly two axes, both now conditioned on
`--no-falco`:

1. **Orchestrator** starts the Falco container (`FalcoManager.start()`) and
   mounts its log dir into every per-vector container.
2. **Exec side** reads that log (`get_filesystem_changes()`), unconditionally.

`--no-falco` neutralizes both: no sidecar (so none of Falco's privileges,
eBPF, or the `falco.yaml` bind-mount), and the exec side doesn't read the
log. Vectors that assert on filesystem changes (carry `file_changes.tar.gz`)
are **skipped** — not falsely passed or failed.

## 2. The change set (Test-Corpus `no-falco` branch)

Localized to `tools/test_runner`, ~85 lines:

| File | Change |
| --- | --- |
| `orchestrator/cli.py`, `exec_test_vector/cli.py` | the `--no-falco` flag |
| `orchestrator/falco.py` | `NullFalcoManager` — same `start`/`stop`/`get_host_log_dir` interface, but starts no container and never mounts `falco.yaml`; hands out an empty (unused) log dir so per-vector containers still mount a valid path |
| `orchestrator/__main__.py` | pick `NullFalcoManager` when `--no-falco` |
| `orchestrator/run_phases.py`, `container.py` | thread `no_falco` → the per-vector container args (alongside the existing `no_compare`) |
| `exec_test_vector/__main__.py` | under `--no-falco`, don't read the Falco log; **skip** file-change vectors instead of misreporting them |

Plus one **robustness fix** needed to run on hosts where the orchestrator's
`TMPDIR` isn't `/tmp` (see §6):

| File | Change |
| --- | --- |
| `orchestrator/container.py` | force `TMPDIR=/tmp` in the vector-container env (the container has its own tmpfs `/tmp`); don't inherit the host's `TMPDIR`, which need not exist in-container |

Nothing else in the harness is modified — cando, discovery, build, and the
JUnit reporter are untouched. We drive their runner; we don't reimplement it.

## 3. Using it from PROCTOR

The newer orchestrator spawns a Docker container per vector, so it runs at
**host level** (needs `nix` + `docker`) — it cannot run nested inside the
framework container. Three entry points:

```bash
# 1. Fetch the newer corpus (Yale @ no-falco) -> tractor-test-corpus-newer/
./fetch_corpus.sh --no-falco

# 2. Verify a case, Falco-free (C reference, or a translation with --rust):
./no_falco_verify.sh Public-Tests/B03_organic/array_list
./no_falco_verify.sh Public-Tests/B01_synthetic/001_helloworld <translated_rust_dir>

# 3. Programmatic: proctor.testing.vector_harness.run_vectors_no_falco(...)
#    stages translated_rust into the case slot and drives
#    `nix run tools/test_runner -- --rust --no-falco`, parsing the JUnit
#    (config/build phase entries folded into the build outcome).
```

`fetch_corpus.sh` pins the exact `no-falco` commit; `run_vectors_no_falco`
reuses the same `parse_junit` as the vendored direct harness.

## 4. Verified results (this host)

`nix run ./tools/test_runner -- --no-falco …`, Docker 29.3.1, no Falco:

| Case | kind | vectors |
| --- | --- | --- |
| `B01_synthetic/001_helloworld` | binary | 3 pass |
| `B01_synthetic/002_stdin_echo` | binary | 4 pass |
| `Examples/filesystem_example` | binary | 2 pass, **6 file-change skip** |
| `B01_synthetic/001_helloworld_lib` | library | 1 pass |
| `Examples/filesystem_example_lib` | library | 2 pass, **8 file-change skip** |
| `B03_organic/array_list` | binary | 20 pass |
| `B03_organic/array_list_lib` | library | 20 pass |
| `B01_synthetic/001_helloworld` **`--rust`** | translation | 3 pass |

**Totals: 55 vectors pass, 14 file-change skip, 0 fail.** The `--rust` row is
the full workflow: a real c2rust→crat translation staged into the case,
built by the newer harness (using the translation's own nested nightly
toolchain — the corpus's `1.94.1` root pin is for cando2/runners), driver
run in a container, stdout vectors compared. No Falco container starts in
any run; file-change vectors are cleanly skipped, everything else compares
exactly as with Falco.

Note the **B03 library** cases (`array_list_lib`): these were previously
*blocked* on the old direct harness (new cando2 needs rustc 1.94.1 and isn't
behavior-compatible with the 0319ab0 harness). On the newer harness with
`--no-falco` they pass — the newer harness natively matches that corpus era.

## 5. Remaining limitation — file-change vectors (Falco-only)

Vectors carrying `file_changes.tar.gz` (55 across the corpus: `Examples/`,
`B03_organic/chibicc`, …) assert on filesystem side-effects, which only
Falco can observe. Under `--no-falco` they are **skipped**. Verifying them
still needs the full Falco stack; the setup that got furthest is preserved
in §7 for whenever that gap is worth closing. This is the *only* thing
`--no-falco` gives up.

## 6. Host requirements & the snap-Docker/`TMPDIR` gotcha

The host-level runner needs:
- **Docker** at host level (spawns a container per vector).
- **Nix** to build/load the `exec_test_vector` image and provide
  cmake/ninja/cargo/clang (`nix run ./tools/test_runner`). Single-user Nix
  is fine (see §7).

**Gotcha (snap Docker):** the `docker` snap is AppArmor-confined and cannot
bind-mount sources under `/tmp` (they arrive empty in-container). The
orchestrator makes its per-vector volumes under `$TMPDIR`, so on such hosts
you **must** run with `TMPDIR=$HOME/…` — otherwise every `/workspace` mount
is empty ("`cando_runner doesn't exist`"). `no_falco_verify.sh` defaults
`TMPDIR` to a `$HOME` path for this reason. The corpus source can stay
anywhere host-readable; only the volume sources (`$TMPDIR`) must be
snap-mountable. The container-`TMPDIR` fix in §2 is what lets a `$HOME`
`TMPDIR` work without leaking into cando. (A non-snap Docker with
`TMPDIR=/tmp`, as in TRACTOR CI, avoids the gotcha entirely.)

Retire note: once this newer harness is the vector engine, the vendored
`tools/tractor_runtests` (the Falco-free *direct* harness) can be removed —
it exists only to give a Falco-free path independent of the corpus, which
`--no-falco` now provides on the corpus's own runner. See
`tools/tractor_runtests/PROVENANCE.md`.

## 7. Full-Falco setup (only for the file-change-vector gap)

Kept for whenever file-change vectors are worth verifying. The blessed
invocation (corpus CI) is:

```
nix run ./tools/test_runner -- --rust --subset <case> --junit-xml out.xml --keep-going --asan
```

which provisions cmake/ninja/cargo via the flake, builds+loads the
`exec_test_vector` image, starts a **Falco** sidecar
(`falcosecurity/falco:0.43.1`, `cap_add=[SYS_ADMIN, SYS_RESOURCE,
SYS_PTRACE]`, mounting `/sys/kernel/tracing`, host `/proc`, `/etc`, the
docker socket, and `falco.yaml`), and runs one container per vector.

### Known blocker if you re-enable Falco

Starting the Falco sidecar mounts `falco.yaml` (a single **file**) into the
container. On **Docker 29.x** this fails:

```
error mounting ".../falco.yaml" to rootfs at "/etc/falco/falco.yaml":
flags=MS_BIND|MS_REC: not a directory
```

(both ends are regular files; works on TRACTOR's older-Docker CI). And a
snap Docker additionally can't mount the `/nix`-store `falco.yaml` path. So
re-enabling Falco here needs either a non-snap, older Docker, or a fix to
how `falco.yaml` is mounted. `--no-falco` sidesteps all of this — which is
why it's the path for everything except file-change vectors.

### Setup that was proven to work (single-user Nix)

- One-time `sudo mkdir -m 0755 /nix && sudo chown $USER /nix`, then the
  official installer `--no-daemon`; every `nix run` after is rootless.
- Docker usable by the user; unprivileged userns on; kernel ≥ ~5.8.
- Lay each case out under `<corpus>/Public-Tests/<Bxx>/<case>/` with
  `test_case/`, `test_vectors/`, `translated_rust/` (the stage output), and
  `runner/` for library cases.
