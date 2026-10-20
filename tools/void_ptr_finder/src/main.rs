//! void_ptr_finder: a symbolic (syn v2) detector for the void-pointer class of
//! recoveries in a C2Rust+CRAT crate. In C, a tagged type is often a `void*`
//! payload selected by a type tag; and a generic container is a `void*`-erased
//! store. C2Rust emits these as explicit `core::ffi::c_void`, so a syntactic scan
//! finds them. This is the lightweight stand-in for crat's rustc-level
//! `void_finder` (which resolves types precisely); here we match `c_void`
//! syntactically.
//!
//! Usage: void_ptr_finder --crate <dir> [--json]
//!
//! Reports candidates of three shapes:
//!   - DISPATCH   fn with a `*mut/*const c_void` param AND an enum (tag) param
//!                -> `tagged_union` (match on the tag; e.g. `c2Collided(void*, void*, C2_TYPE)`)
//!   - UNION      struct with a `c_void` field AND an enum-discriminant field
//!                -> `tagged_union` (void* payload)
//!   - CONTAINER  struct with a `c_void` field and NO enum discriminant
//!                -> container / generics (void*-erased element store)
//! Purely syntactic, best-effort (no name resolution / type inference).

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use clap::Parser;
use serde::Serialize;
use syn::visit::Visit;

#[derive(Parser)]
#[command(about = "Find void-pointer recoveries (void*+tag dispatch / unions, void*-erased containers).")]
struct Cli {
    #[arg(long = "crate")]
    krate: PathBuf,
    #[arg(long)]
    json: bool,
}

#[derive(Serialize)]
struct Candidate {
    file: String,
    /// recovery kind for the stage: `tagged_union` (-> enum) or `container` (-> generics).
    kind: String,
    /// the fn or struct the recovery targets.
    target: String,
    /// which void-pointer shape was matched.
    shape: String,
    /// one-line explanation.
    signal: String,
}

fn main() {
    let cli = Cli::parse();
    let files = collect_rs(&cli.krate);

    // Pass 1: enum names (tag candidates).
    let mut enums: BTreeSet<String> = BTreeSet::new();
    for src in files.values() {
        if let Ok(f) = syn::parse_file(src) {
            let mut v = EnumNames(&mut enums);
            v.visit_file(&f);
        }
    }

    // Pass 2: fns and structs touching c_void.
    let mut cands: Vec<Candidate> = Vec::new();
    for (rel, src) in &files {
        let Ok(f) = syn::parse_file(src) else { continue };
        let mut v = Finder {
            enums: &enums,
            file: rel.clone(),
            out: &mut cands,
        };
        v.visit_file(&f);
    }

    cands.sort_by(|a, b| {
        (&a.kind, &a.file, &a.target).cmp(&(&b.kind, &b.file, &b.target))
    });
    println!("{}", serde_json::to_string_pretty(&cands).unwrap());
}

struct EnumNames<'a>(&'a mut BTreeSet<String>);
impl<'ast> Visit<'ast> for EnumNames<'_> {
    fn visit_item_enum(&mut self, node: &'ast syn::ItemEnum) {
        self.0.insert(node.ident.to_string());
        syn::visit::visit_item_enum(self, node);
    }
}

struct Finder<'a> {
    enums: &'a BTreeSet<String>,
    file: String,
    out: &'a mut Vec<Candidate>,
}

impl Finder<'_> {
    fn scan_sig(&mut self, name: String, sig: &syn::Signature) {
        let mut cvoid = 0usize;
        let mut tag = false;
        for arg in &sig.inputs {
            if let syn::FnArg::Typed(pt) = arg {
                if contains_c_void(&pt.ty) {
                    cvoid += 1;
                }
                if let Some(seg) = path_last(&pt.ty) {
                    if self.enums.contains(&seg) {
                        tag = true;
                    }
                }
            }
        }
        if cvoid >= 1 && tag {
            self.out.push(Candidate {
                file: self.file.clone(),
                kind: "tagged_union".into(),
                target: name,
                shape: "dispatch".into(),
                signal: "fn takes a void* payload + an enum tag -> match on the tag".into(),
            });
        }
    }
}

impl<'ast> Visit<'ast> for Finder<'_> {
    fn visit_item_fn(&mut self, node: &'ast syn::ItemFn) {
        self.scan_sig(node.sig.ident.to_string(), &node.sig);
        syn::visit::visit_item_fn(self, node);
    }
    fn visit_impl_item_fn(&mut self, node: &'ast syn::ImplItemFn) {
        self.scan_sig(node.sig.ident.to_string(), &node.sig);
        syn::visit::visit_impl_item_fn(self, node);
    }
    fn visit_item_struct(&mut self, node: &'ast syn::ItemStruct) {
        let mut has_void = false;
        let mut has_tag = false;
        for field in &node.fields {
            if contains_c_void(&field.ty) {
                has_void = true;
            }
            if let Some(seg) = path_last(&field.ty) {
                if self.enums.contains(&seg) {
                    has_tag = true;
                }
            }
        }
        if has_void {
            let (kind, shape, signal) = if has_tag {
                (
                    "tagged_union",
                    "union",
                    "struct has a void* payload field + an enum discriminant -> enum",
                )
            } else {
                (
                    "container",
                    "container",
                    "struct stores a void* element (type-erased) -> generics / typed collection",
                )
            };
            self.out.push(Candidate {
                file: self.file.clone(),
                kind: kind.into(),
                target: node.ident.to_string(),
                shape: shape.into(),
                signal: signal.into(),
            });
        }
        syn::visit::visit_item_struct(self, node);
    }
}

/// True if the type mentions `c_void` anywhere (`*mut c_void`, `*const
/// core::ffi::c_void`, `Option<*mut c_void>`, ...).
fn contains_c_void(ty: &syn::Type) -> bool {
    struct V(bool);
    impl<'ast> Visit<'ast> for V {
        fn visit_path_segment(&mut self, n: &'ast syn::PathSegment) {
            if n.ident == "c_void" {
                self.0 = true;
            }
            syn::visit::visit_path_segment(self, n);
        }
    }
    let mut v = V(false);
    v.visit_type(ty);
    v.0
}

/// Last path segment of a bare path type (`C2_TYPE`, `foo::NodeKind`), else None.
fn path_last(ty: &syn::Type) -> Option<String> {
    if let syn::Type::Path(p) = ty {
        return p.path.segments.last().map(|s| s.ident.to_string());
    }
    None
}

fn collect_rs(root: &Path) -> std::collections::BTreeMap<String, String> {
    let mut out = std::collections::BTreeMap::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if path.is_dir() {
                if name != "target" && !name.starts_with('.') {
                    stack.push(path);
                }
            } else if name.ends_with(".rs") && name != "build.rs" {
                if let Ok(content) = std::fs::read_to_string(&path) {
                    let rel = path
                        .strip_prefix(root)
                        .unwrap_or(&path)
                        .to_string_lossy()
                        .to_string();
                    out.insert(rel, content);
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn find(src: &str) -> Vec<Candidate> {
        let f = syn::parse_file(src).unwrap();
        let mut enums = BTreeSet::new();
        EnumNames(&mut enums).visit_file(&f);
        let mut out = Vec::new();
        Finder { enums: &enums, file: "lib.rs".into(), out: &mut out }.visit_file(&f);
        out
    }

    #[test]
    fn flags_void_plus_tag_dispatch_fn() {
        let src = r#"
            enum C2_TYPE { Circle, Aabb, Capsule }
            fn c2Collided(a: *const core::ffi::c_void, b: *const core::ffi::c_void, t: C2_TYPE) -> i32 { 0 }
        "#;
        let c = find(src);
        assert_eq!(c.len(), 1);
        assert_eq!(c[0].kind, "tagged_union");
        assert_eq!(c[0].target, "c2Collided");
        assert_eq!(c[0].shape, "dispatch");
    }

    #[test]
    fn classifies_void_container_vs_void_union() {
        let src = r#"
            enum Kind { A, B, C }
            struct Erased { data: *mut core::ffi::c_void, len: usize }           // container
            struct Tagged { kind: Kind, payload: *mut core::ffi::c_void }        // void* union
        "#;
        let c = find(src);
        let container = c.iter().find(|x| x.target == "Erased").unwrap();
        let union = c.iter().find(|x| x.target == "Tagged").unwrap();
        assert_eq!(container.kind, "container");
        assert_eq!(union.kind, "tagged_union");
    }

    #[test]
    fn ignores_void_free_code() {
        let src = r#"
            enum E { A, B }
            struct Plain { x: i32 }
            fn g(a: i32, b: E) {}
        "#;
        assert!(find(src).is_empty());
    }
}
