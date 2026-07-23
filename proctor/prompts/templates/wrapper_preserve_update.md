+++
id = "wrapper_preserve_update"
version = 1
description = "Update a wrapper while preserving the public API signature."
variables = ["api_signature", "wrapper_source", "impl_signature", "diagnostics"]
+++
You are updating a Rust wrapper function in a C-to-Rust translation
pipeline. The wrapper's public signature is part of the crate's
external API and MUST NOT change:

```rust
{{ api_signature }}
```

Current wrapper implementation:

```rust
{{ wrapper_source }}
```

The wrapped implementation now has this signature:

```rust
{{ impl_signature }}
```

{% if diagnostics %}
Compiler/test diagnostics to address:

```
{{ diagnostics }}
```
{% endif %}

Rewrite the wrapper so it delegates correctly to the new implementation
while keeping the public signature byte-identical. Do not modify any
other function. Reply with only the new wrapper source in a single
```rust code block.
