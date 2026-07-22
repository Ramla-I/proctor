+++
id = "translation_notes"
version = 1
description = "Summarize the unsafe patterns in a c2rust-translated file."
variables = ["file_name", "source"]
+++
The following Rust file was machine-translated from C by c2rust and
symbolically cleaned by CRAT. Write concise notes for the engineer who
will make it idiomatic:

1. list each unsafe pattern present (raw pointers, transmutes, extern
   calls, manual memory management) with the function it appears in;
2. flag anything that looks like a C idiom with a well-known safe Rust
   replacement (dynamic array, linked list, manual string handling);
3. keep it under 40 lines; plain Markdown, no preamble.

File `{{ file_name }}`:

```rust
{{ source }}
```
