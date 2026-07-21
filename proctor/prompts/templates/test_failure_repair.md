+++
id = "test_failure_repair"
version = 1
description = "Repair a behavioral regression revealed by the test package."
variables = ["test_output", "source_context", "change_summary"]
+++
A transformed Rust program compiles but fails its test suite. The tests
compare observable behavior against the original C program.

Failing test output:

```
{{ test_output }}
```

The transformation that introduced the regression:
{{ change_summary }}

Relevant source:

```rust
{{ source_context }}
```

Repair the regression while keeping the transformation's intent — do
not simply revert it. Modify only the affected scope. Reply with the
corrected source for the affected item(s) only, each in its own
```rust code block, preceded by a `// file: <path>` comment line.
