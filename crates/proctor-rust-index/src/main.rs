//! proctor-rust-index: syn-based item/dependency index for a Rust project.
//!
//! Usage: proctor-rust-index <project-dir> [--output <file>]
//!
//! Emits JSON: items (path, kind, file, line span, source text) and edges
//! (contains, type_ref, call, method_call, impl_of). Name resolution is
//! syntactic and best-effort — `syn` has no type inference; consumers
//! resolve textual edge targets by suffix matching against item paths.
//! The interface is designed so this backend can be swapped for
//! rust-analyzer later without touching the Python strategies.

use serde::Serialize;
use std::path::{Path, PathBuf};
use syn::visit::Visit;

#[derive(Serialize)]
struct Index {
    schema_version: u32,
    items: Vec<Item>,
    edges: Vec<Edge>,
}

#[derive(Serialize)]
struct Item {
    path: String,
    kind: &'static str,
    file: String,
    start_line: usize,
    end_line: usize,
    text: String,
}

#[derive(Serialize)]
struct Edge {
    from: String,
    to: String,
    kind: &'static str,
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: proctor-rust-index <project-dir> [--output <file>]");
        std::process::exit(2);
    }
    let project = PathBuf::from(&args[1]);
    let output = args
        .iter()
        .position(|a| a == "--output")
        .and_then(|i| args.get(i + 1))
        .map(PathBuf::from);

    let mut index = Index {
        schema_version: 1,
        items: Vec::new(),
        edges: Vec::new(),
    };

    let mut files = Vec::new();
    collect_rs_files(&project, &project, &mut files);
    files.sort();

    for file in &files {
        let source = match std::fs::read_to_string(file) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("warning: cannot read {}: {e}", file.display());
                continue;
            }
        };
        let parsed = match syn::parse_file(&source) {
            Ok(p) => p,
            Err(e) => {
                eprintln!("warning: cannot parse {}: {e}", file.display());
                continue;
            }
        };
        let rel = file.strip_prefix(&project).unwrap_or(file);
        let module = module_path_for_file(rel);
        walk_items(
            &parsed.items,
            &module,
            &rel.to_string_lossy(),
            &source,
            &mut index,
        );
    }

    let json = serde_json::to_string_pretty(&index).expect("serialize");
    match output {
        Some(path) => std::fs::write(&path, json + "\n").expect("write output"),
        None => println!("{json}"),
    }
}

fn collect_rs_files(root: &Path, dir: &Path, out: &mut Vec<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if name != "target" && !name.starts_with('.') {
                collect_rs_files(root, &path, out);
            }
        } else if name.ends_with(".rs") {
            out.push(path);
        }
    }
}

/// src/lib.rs, src/main.rs, lib.rs, main.rs -> crate root; src/a.rs -> a;
/// src/a/mod.rs -> a; src/a/b.rs -> a::b.
fn module_path_for_file(rel: &Path) -> Vec<String> {
    let mut parts: Vec<String> = rel
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect();
    if parts.first().map(|p| p == "src").unwrap_or(false) {
        parts.remove(0);
    }
    if let Some(last) = parts.last_mut() {
        *last = last.trim_end_matches(".rs").to_string();
    }
    match parts.last().map(String::as_str) {
        Some("lib") | Some("main") | Some("mod") => {
            parts.pop();
        }
        _ => {}
    }
    parts
}

fn line_span(source: &str, span: proc_macro2::Span) -> (usize, usize, String) {
    let start = span.start().line;
    let end = span.end().line;
    let text: String = source
        .lines()
        .skip(start.saturating_sub(1))
        .take(end.saturating_sub(start) + 1)
        .collect::<Vec<_>>()
        .join("\n");
    (start, end, text)
}

fn joined(module: &[String], name: &str) -> String {
    if module.is_empty() {
        name.to_string()
    } else {
        format!("{}::{}", module.join("::"), name)
    }
}

fn type_last_ident(ty: &syn::Type) -> Option<String> {
    if let syn::Type::Path(p) = ty {
        p.path.segments.last().map(|s| s.ident.to_string())
    } else {
        None
    }
}

fn walk_items(
    items: &[syn::Item],
    module: &[String],
    file: &str,
    source: &str,
    index: &mut Index,
) {
    use syn::spanned::Spanned;
    let module_path = module.join("::");
    for item in items {
        let (name, kind): (String, &'static str) = match item {
            syn::Item::Fn(f) => (f.sig.ident.to_string(), "fn"),
            syn::Item::Struct(s) => (s.ident.to_string(), "struct"),
            syn::Item::Enum(e) => (e.ident.to_string(), "enum"),
            syn::Item::Trait(t) => (t.ident.to_string(), "trait"),
            syn::Item::Const(c) => (c.ident.to_string(), "const"),
            syn::Item::Static(s) => (s.ident.to_string(), "static"),
            syn::Item::Type(t) => (t.ident.to_string(), "type"),
            syn::Item::Mod(m) => {
                let name = m.ident.to_string();
                let path = joined(module, &name);
                if !module_path.is_empty() {
                    index.edges.push(Edge {
                        from: module_path.clone(),
                        to: path.clone(),
                        kind: "contains",
                    });
                }
                if let Some((_, inner)) = &m.content {
                    let mut nested = module.to_vec();
                    nested.push(name);
                    walk_items(inner, &nested, file, source, index);
                }
                continue;
            }
            syn::Item::Impl(imp) => {
                walk_impl(imp, module, file, source, index);
                continue;
            }
            _ => continue,
        };

        let path = joined(module, &name);
        let (start, end, text) = line_span(source, item.span());
        index.items.push(Item {
            path: path.clone(),
            kind,
            file: file.to_string(),
            start_line: start,
            end_line: end,
            text,
        });
        if !module_path.is_empty() {
            index.edges.push(Edge {
                from: module_path.clone(),
                to: path.clone(),
                kind: "contains",
            });
        }

        match item {
            syn::Item::Fn(f) => collect_fn_edges(&path, &f.sig, Some(&f.block), index),
            syn::Item::Struct(s) => {
                for field in &s.fields {
                    let mut v = TypeRefVisitor::default();
                    v.visit_type(&field.ty);
                    for target in v.refs {
                        index.edges.push(Edge {
                            from: path.clone(),
                            to: target,
                            kind: "type_ref",
                        });
                    }
                }
            }
            _ => {}
        }
    }
}

fn walk_impl(
    imp: &syn::ItemImpl,
    module: &[String],
    file: &str,
    source: &str,
    index: &mut Index,
) {
    use syn::spanned::Spanned;
    let self_name = match type_last_ident(&imp.self_ty) {
        Some(n) => n,
        None => return,
    };
    let self_path = joined(module, &self_name);
    for item in &imp.items {
        if let syn::ImplItem::Fn(f) = item {
            let path = format!("{self_path}::{}", f.sig.ident);
            let (start, end, text) = line_span(source, f.span());
            index.items.push(Item {
                path: path.clone(),
                kind: "fn",
                file: file.to_string(),
                start_line: start,
                end_line: end,
                text,
            });
            index.edges.push(Edge {
                from: path.clone(),
                to: self_path.clone(),
                kind: "impl_of",
            });
            collect_fn_edges(&path, &f.sig, None, index);
            let mut v = BodyVisitor::default();
            v.visit_block(&f.block);
            push_body_edges(&path, v, index);
        }
    }
}

fn collect_fn_edges(
    path: &str,
    sig: &syn::Signature,
    block: Option<&syn::Block>,
    index: &mut Index,
) {
    let mut v = TypeRefVisitor::default();
    for input in &sig.inputs {
        if let syn::FnArg::Typed(t) = input {
            v.visit_type(&t.ty);
        }
    }
    if let syn::ReturnType::Type(_, ty) = &sig.output {
        v.visit_type(ty);
    }
    for target in v.refs {
        index.edges.push(Edge {
            from: path.to_string(),
            to: target,
            kind: "type_ref",
        });
    }
    if let Some(block) = block {
        let mut bv = BodyVisitor::default();
        bv.visit_block(block);
        push_body_edges(path, bv, index);
    }
}

fn push_body_edges(path: &str, v: BodyVisitor, index: &mut Index) {
    for callee in v.calls {
        index.edges.push(Edge {
            from: path.to_string(),
            to: callee,
            kind: "call",
        });
    }
    for method in v.method_calls {
        index.edges.push(Edge {
            from: path.to_string(),
            to: method,
            kind: "method_call",
        });
    }
}

#[derive(Default)]
struct TypeRefVisitor {
    refs: Vec<String>,
}

impl<'ast> Visit<'ast> for TypeRefVisitor {
    fn visit_type_path(&mut self, node: &'ast syn::TypePath) {
        let text = node
            .path
            .segments
            .iter()
            .map(|s| s.ident.to_string())
            .collect::<Vec<_>>()
            .join("::");
        // skip primitives and common std generics wrappers by name length heuristics
        let last = node.path.segments.last().map(|s| s.ident.to_string());
        if let Some(last) = last {
            let primitive = matches!(
                last.as_str(),
                "i8" | "i16" | "i32" | "i64" | "i128" | "isize" | "u8" | "u16"
                    | "u32" | "u64" | "u128" | "usize" | "f32" | "f64" | "bool"
                    | "char" | "str" | "Self"
            );
            if !primitive {
                self.refs.push(text);
            }
        }
        syn::visit::visit_type_path(self, node);
    }
}

#[derive(Default)]
struct BodyVisitor {
    calls: Vec<String>,
    method_calls: Vec<String>,
}

impl<'ast> Visit<'ast> for BodyVisitor {
    fn visit_expr_call(&mut self, node: &'ast syn::ExprCall) {
        if let syn::Expr::Path(p) = &*node.func {
            let text = p
                .path
                .segments
                .iter()
                .map(|s| s.ident.to_string())
                .collect::<Vec<_>>()
                .join("::");
            self.calls.push(text);
        }
        syn::visit::visit_expr_call(self, node);
    }

    fn visit_expr_method_call(&mut self, node: &'ast syn::ExprMethodCall) {
        self.method_calls.push(node.method.to_string());
        syn::visit::visit_expr_method_call(self, node);
    }
}
