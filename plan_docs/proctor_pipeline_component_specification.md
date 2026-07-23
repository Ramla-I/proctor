# Proctor Pipeline Component Specification

## 1. Scope

This document defines the initial interfaces between the major components of the C-to-Rust translation pipeline. It focuses on how artifacts are passed between independently developed components so that implementations can be reused, replaced, and composed.

### Initial assumptions

- Each C project has exactly one target.
- The target is either an executable or a library.
- Projects do not have multiple build configurations.
- Rust projects are built with `cargo build`.
- Library targets are built as `cdylib`.
- The platform is Linux.
- The Rust toolchain is specified by `rust-toolchain`.
- Libraries export functions only; exported global variables are not considered.

## 2. Shared Artifacts

### 2.1 C project

A C project follows the DARPA TRACTOR Test-Corpus format.

### 2.2 Rust project

A Rust project is a Cargo project containing files such as:

```
Cargo.toml
rust-toolchain
build.rs
lib.rs
src/
proctor.toml
```

### 2.3 Test package

A test package is a directory containing:

```
run_test.sh
test_data/
```

`test_data/` may be empty.

The test script is invoked as:

```bash
<test-package>/run_test.sh <test-package>/test_data <compiled-artifact>
```

Contract:

- The script may be invoked from any working directory.
- The first argument is the test-data directory path.
- The second argument is the compiled executable or shared-library path.
- Exit code `0` means success.
- Any nonzero exit code means failure.
- Diagnostic information may be written to standard output or standard error.

### 2.4 Rule-set file

The local-transformation rule set is stored in one file.

Its internal format is intentionally unspecified at this stage.

## 3. Project Manifest

Each Rust project contains `proctor.toml` at its root.

Example:

```toml
target_kind = "library"
target_name = "example"

api_functions = ["foo", "bar"]

wrappers = [
    { wrapped = "implementation::foo_impl", wrapper = "api::foo" },
]
```

### 3.1 Fields

#### `target_kind`

A string with one of two values:

```toml
target_kind = "executable"
```

or:

```toml
target_kind = "library"
```

#### `target_name`

The Cargo target name:

```toml
target_name = "example"
```

#### `api_functions`

A list of function names whose external APIs must be preserved:

```toml
api_functions = ["foo", "bar"]
```

For executable targets, this list is empty.

If multiple functions have the same name, all matching functions are treated as API functions.

#### `wrappers`

A list of current wrapper relationships:

```toml
wrappers = [
    { wrapped = "a::b::foo_impl", wrapper = "a::foo" },
]
```

Both functions are identified by crate-relative full Rust paths.

- `wrapped` is the underlying implementation.
- `wrapper` is the function that delegates to it.

The wrapper list records existing relationships; it does not prohibit later components from modifying either function.

A component must inspect existing wrapper entries before introducing another wrapper. For example, discipline repair may modify a wrapper created by abstraction recovery rather than creating a nested wrapper.

Abstraction recovery and discipline repair may add, update, replace, or remove wrapper entries as needed.

---

## 4. Directory-Path Conventions

Transformation components receive separate source and destination paths.

### Input Rust project directory path

This path must refer to an existing, populated Rust project. The component reads this project and must not modify it in place.

### Output Rust project destination path

This path identifies where the transformed Rust project must be created. It should be nonexistent or empty when the component starts.

A simple implementation may:

1. copy the entire input Rust project into the destination;
2. modify the copied project;
3. build and test the result.

### Test-package directory path

This path refers to an existing test package and is treated as read-only.

## 5. Component Interfaces

### 5.1 Test Generation

#### Input

- C project directory path.
- Output test-package destination path.

#### Output

A test package containing:

- `run_test.sh`;
- `test_data/`.

The generated test package must follow the contract in Section 2.3.

### 5.2 Translation

This component includes C2Rust and the required Crat plugins.

#### Input

- C project directory path.
- Output Rust project destination path.

#### Output

A Cargo-buildable Rust project containing:

- translated Rust source code;
- `Cargo.toml`;
- `rust-toolchain`;
- other required build files;
- `proctor.toml`.

For a library target, `proctor.toml` contains the API function names.

The initial wrapper list is empty:

```toml
wrappers = []
```

### 5.3 Abstraction Recovery

#### Input

- Input C project directory path (optionally used).
- Input Rust project directory path.
- Output Rust project destination path.
- Test-package directory path.

The component reads `proctor.toml` from the input Rust project.

#### Output

A Cargo-buildable and test-passing Rust project in the destination path.

The output project contains an updated `proctor.toml`, including any wrapper relationships introduced, changed, or removed by abstraction recovery.

### 5.4 Discipline Repair

#### Input

- Input C project directory path (optionally used).
- Input Rust project directory path.
- Output Rust project destination path.
- Test-package directory path.

The component reads the current `proctor.toml`, including wrappers introduced by abstraction recovery.

#### Output

A Cargo-buildable and test-passing Rust project in the destination path.

The output project contains an updated `proctor.toml`.

Discipline repair must account for existing wrappers. When the affected function already has a wrapper, it may modify that wrapper or its wrapped implementation rather than introducing another wrapper.

### 5.5 Local Transformation

#### Input

- Input Rust project directory path.
- Output Rust project destination path.
- Test-package directory path.
- Input rule-set file path.
- Output rule-set file path.

The component reads `proctor.toml` to identify:

- the target kind and name;
- API functions;
- wrappers introduced by non-local transformation stages.

#### Output

- Final Cargo-buildable and test-passing Rust project in the destination path.
- Updated rule-set file at the output rule-set path.

The local-transformation component does not return an updated project manifest as a pipeline output. The copied `proctor.toml` may remain in the final Rust project, but no later stage depends on an updated wrapper list.

Temporary wrappers used internally during local transformation do not need to be recorded in `proctor.toml`.