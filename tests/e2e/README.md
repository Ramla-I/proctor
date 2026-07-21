# End-to-end tests

```bash
uv run pytest -m e2e
```

Runs one public TRACTOR case (`001_helloworld`) through the real CRAT
stage: orchestrator → crat pass chain → output builds → executable runs
→ resume reuses the checkpoint.

## Requirements

- `rustup` (crat's `rust-toolchain.toml` auto-installs its pinned
  nightly and components on first build)
- the crat submodule: `git submodule update --init stages/crat`
- CRAT's build-time system deps: **libclang** (bindgen) and **z3**
  (z3-sys). The first build takes minutes; it is cached per crat commit.

With sudo, the simple path:

```bash
sudo apt install -y libclang-dev libz3-dev
```

Without sudo, the userspace recipe (all under `~/.cache/proctor`):

```bash
CACHE=~/.cache/proctor && mkdir -p $CACHE && cd $CACHE
curl -LsSf -o z3.zip https://github.com/Z3Prover/z3/releases/download/z3-4.13.4/z3-4.13.4-x64-glibc-2.35.zip
python3 -c "import zipfile; zipfile.ZipFile('z3.zip').extractall()"
mv z3-4.13.4-x64-glibc-2.35 z3 && rm z3.zip
uv pip install --target $CACHE/libclang libclang

export Z3_SYS_Z3_HEADER=$CACHE/z3/include/z3.h
export LIBCLANG_PATH=$CACHE/libclang/clang/native
export LIBRARY_PATH=$CACHE/z3/bin
export LD_LIBRARY_PATH=$CACHE/z3/bin
export RUSTFLAGS="-L native=$CACHE/z3/bin"
# bindgen needs compiler builtin headers (stdbool.h); point it at gcc's:
export BINDGEN_EXTRA_CLANG_ARGS="-isystem $(dirname $(find /usr/lib/gcc/x86_64-linux-gnu -name stdbool.h | head -1))"
```

These env vars are only needed while CRAT builds. Inside the eventual
framework container (M8) none of this applies — the image installs the
apt packages.
