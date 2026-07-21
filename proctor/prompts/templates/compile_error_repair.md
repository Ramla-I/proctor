+++
id = "compile_error_repair"
version = 1
description = "Repair a Rust compile error with a minimal, scoped change."
variables = ["errors", "source_context", "constraints"]
+++
A Rust project in a C-to-Rust translation pipeline fails to compile.

Compiler errors:

```
{{ errors }}
```

Relevant source:

```rust
{{ source_context }}
```

Constraints on the repair:
{{ constraints }}

Make the smallest change that fixes the errors without altering
behavior. Do not restructure unrelated code, change public signatures,
or introduce new dependencies. Reply with the corrected source for the
affected item(s) only, each in its own ```rust code block, preceded by
a `// file: <path>` comment line.
