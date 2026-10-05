//! recovery_metrics: symbolic structural quality passes for an
//! abstraction_recovery transform, comparing a crate BEFORE (crat) and AFTER
//! (recovered).
//!
//! Usage: recovery_metrics --before <crate_dir> --after <crate_dir>
//!
//! Emits JSON: the touched-file set (files whose normalized content differs),
//! per-touched-file structural signals computed on the AFTER version (raw-ptr
//! struct fields, `into_raw`/`from_raw` uses, `malloc`/`free`, std-collection
//! adoption, churn), and an `extern "C"`/`#[no_mangle]` ABI diff. Unsafe counts
//! live in `measure_unsafety`; clippy lints in `measure_idiomaticity`; a caller
//! (proctor.testing.recovery_quality, or the stage's in-loop assessor) composes
//! them into the quality panel. No name resolution / type inference — purely
//! syntactic, best-effort, like proctor-rust-index.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};

use clap::Parser;
use quote::ToTokens;
use serde::Serialize;
use syn::visit::Visit;

#[derive(Parser)]
#[command(about = "Symbolic structural quality metrics for a recovered Rust crate (before/after).")]
struct Cli {
    /// Crate directory BEFORE recovery (e.g. the crat output).
    #[arg(long)]
    before: PathBuf,
    /// Crate directory AFTER recovery (the abstraction_recovery output).
    #[arg(long)]
    after: PathBuf,
    /// Report per-file signals for EVERY after-file, not just the touched set
    /// (for whole-crate feedback). `touched_files` still lists only the changes.
    #[arg(long)]
    all_files: bool,
}

/// Std collections a recovery targets; presence => the file "adopts" a collection.
const TARGET_COLLECTIONS: &[&str] = &[
    "Vec",
    "VecDeque",
    "HashMap",
    "HashSet",
    "BinaryHeap",
    "BTreeMap",
    "BTreeSet",
];

/// C allocation primitives whose retention signals non-minimal recovery.
const C_ALLOC: &[&str] = &["malloc", "free", "calloc", "realloc"];

#[derive(Serialize)]
struct Report {
    schema_version: u32,
    touched_files: Vec<String>,
    abi: AbiDiff,
    files: BTreeMap<String, FileReport>,
}

#[derive(Serialize, Default)]
struct AbiDiff {
    /// extern-C / no_mangle fn names present before, gone after.
    removed: Vec<String>,
    /// extern-C / no_mangle fn names new in after.
    added: Vec<String>,
    /// same name, different normalized signature (an ABI break).
    changed: Vec<AbiChange>,
}

#[derive(Serialize)]
struct AbiChange {
    name: String,
    before: String,
    after: String,
}

#[derive(Serialize)]
struct FileReport {
    in_before: bool,
    in_after: bool,
    churn_added: u64,
    churn_removed: u64,
    // structural signals on the AFTER version (facade pre-filter):
    raw_ptr_fields: u64,
    into_from_raw: u64,
    malloc_free: u64,
    // deref expressions `*e` (a syntactic proxy for raw-pointer dereferences —
    // the actual dangerous op; safe auto-deref rarely needs an explicit `*`).
    // Fewer is better; tracks pointer-work density, the main residual driver.
    raw_derefs: u64,
    adopts_target: bool,
    // unsafe NOT inside an extern "C"/#[no_mangle] fn body (ideal 0 — the
    // boundary legitimately needs some, internal code should not): unsafe
    // blocks + non-boundary `unsafe fn` + `unsafe impl`.
    non_boundary_unsafe: u64,
    parse_ok: bool,
}

fn main() {
    let cli = Cli::parse();
    let before = collect_rs(&cli.before);
    let after = collect_rs(&cli.after);

    let mut all: BTreeSet<String> = BTreeSet::new();
    all.extend(before.keys().cloned());
    all.extend(after.keys().cloned());

    let mut touched: Vec<String> = Vec::new();
    let mut files: BTreeMap<String, FileReport> = BTreeMap::new();
    for f in &all {
        let b = before.get(f);
        let a = after.get(f);
        let is_touched = match (b, a) {
            (Some(bc), Some(ac)) => normalized_lines(bc) != normalized_lines(ac),
            _ => true, // present on only one side
        };
        if is_touched {
            touched.push(f.clone());
        }
        // Always emit touched files; with --all-files, also every after-file
        // (for whole-crate feedback). Skip untouched files otherwise.
        if !is_touched && !(cli.all_files && a.is_some()) {
            continue;
        }

        let (added, removed) = match (b, a) {
            (Some(bc), Some(ac)) => churn(bc, ac),
            (None, Some(ac)) => (normalized_lines(ac).len() as u64, 0),
            (Some(bc), None) => (0, normalized_lines(bc).len() as u64),
            (None, None) => (0, 0),
        };

        let mut stats = FileStats::default();
        let mut parse_ok = false;
        if let Some(ac) = a {
            match syn::parse_file(ac) {
                Ok(file) => {
                    stats.visit_file(&file);
                    parse_ok = true;
                }
                Err(_) => parse_ok = false,
            }
        }

        files.insert(
            f.clone(),
            FileReport {
                in_before: b.is_some(),
                in_after: a.is_some(),
                churn_added: added,
                churn_removed: removed,
                raw_ptr_fields: stats.raw_ptr_fields,
                into_from_raw: stats.into_from_raw,
                malloc_free: stats.malloc_free,
                raw_derefs: stats.raw_derefs,
                adopts_target: stats.adopts_target,
                non_boundary_unsafe: stats.non_boundary_unsafe,
                parse_ok,
            },
        );
    }

    let before_ext = collect_extern_fns(&before);
    let after_ext = collect_extern_fns(&after);
    let mut abi = AbiDiff::default();
    for name in before_ext.keys() {
        if !after_ext.contains_key(name) {
            abi.removed.push(name.clone());
        }
    }
    for name in after_ext.keys() {
        if !before_ext.contains_key(name) {
            abi.added.push(name.clone());
        }
    }
    for (name, bsig) in &before_ext {
        if let Some(asig) = after_ext.get(name) {
            if bsig != asig {
                abi.changed.push(AbiChange {
                    name: name.clone(),
                    before: bsig.clone(),
                    after: asig.clone(),
                });
            }
        }
    }
    abi.removed.sort();
    abi.added.sort();
    abi.changed.sort_by(|x, y| x.name.cmp(&y.name));

    let report = Report {
        schema_version: 1,
        touched_files: touched,
        abi,
        files,
    };
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
}

/// Map of rel-path -> source for every `*.rs` under `root` (excl. `target/`,
/// hidden dirs, and `build.rs`), matching unsafe_eval's file set.
fn collect_rs(root: &Path) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(_) => continue,
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

/// Trimmed, blank-stripped lines — for order-insensitive touched/churn compare
/// that ignores pure reformatting whitespace noise.
fn normalized_lines(s: &str) -> Vec<String> {
    s.lines()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
        .collect()
}

/// Churn as a multiset line difference: added = lines in after beyond before,
/// removed = lines in before beyond after. Order-insensitive (moved lines are
/// not churn), a coarse minimality proxy — not a true edit distance.
fn churn(before: &str, after: &str) -> (u64, u64) {
    let mut b: HashMap<String, i64> = HashMap::new();
    for l in normalized_lines(before) {
        *b.entry(l).or_default() += 1;
    }
    let mut a: HashMap<String, i64> = HashMap::new();
    for l in normalized_lines(after) {
        *a.entry(l).or_default() += 1;
    }
    let mut keys: BTreeSet<&String> = BTreeSet::new();
    keys.extend(a.keys());
    keys.extend(b.keys());
    let (mut added, mut removed) = (0i64, 0i64);
    for k in keys {
        let av = *a.get(k).unwrap_or(&0);
        let bv = *b.get(k).unwrap_or(&0);
        if av > bv {
            added += av - bv;
        } else if bv > av {
            removed += bv - av;
        }
    }
    (added as u64, removed as u64)
}

/// name -> normalized signature for every extern-C / #[no_mangle] fn in a crate.
fn collect_extern_fns(files: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let mut map = BTreeMap::new();
    for content in files.values() {
        if let Ok(file) = syn::parse_file(content) {
            let mut v = FileStats::default();
            v.visit_file(&file);
            for (name, sig) in v.extern_fns {
                map.insert(name, sig); // names are unique across a crate's ABI
            }
        }
    }
    map
}

#[derive(Default)]
struct FileStats {
    raw_ptr_fields: u64,
    into_from_raw: u64,
    malloc_free: u64,
    adopts_target: bool,
    extern_fns: Vec<(String, String)>, // (name, normalized signature)
    raw_derefs: u64,
    // >0 while visiting inside an extern "C"/#[no_mangle] fn body, where some
    // unsafe is unavoidable; unsafe seen at depth 0 is avoidable internal unsafe.
    boundary_depth: usize,
    non_boundary_unsafe: u64,
}

impl<'ast> Visit<'ast> for FileStats {
    fn visit_item_struct(&mut self, node: &'ast syn::ItemStruct) {
        for field in &node.fields {
            if type_has_ptr(&field.ty) {
                self.raw_ptr_fields += 1;
            }
        }
        syn::visit::visit_item_struct(self, node);
    }

    fn visit_item_fn(&mut self, node: &'ast syn::ItemFn) {
        let boundary = is_extern_c(&node.sig, &node.attrs);
        if boundary {
            self.extern_fns
                .push((node.sig.ident.to_string(), normalize_sig(&node.sig)));
            self.boundary_depth += 1;
        } else if node.sig.unsafety.is_some() {
            // a non-boundary `unsafe fn` is avoidable internal unsafe
            self.non_boundary_unsafe += 1;
        }
        syn::visit::visit_item_fn(self, node);
        if boundary {
            self.boundary_depth -= 1;
        }
    }

    fn visit_impl_item_fn(&mut self, node: &'ast syn::ImplItemFn) {
        let boundary = is_extern_c(&node.sig, &node.attrs);
        if boundary {
            self.boundary_depth += 1;
        } else if node.sig.unsafety.is_some() {
            self.non_boundary_unsafe += 1;
        }
        syn::visit::visit_impl_item_fn(self, node);
        if boundary {
            self.boundary_depth -= 1;
        }
    }

    fn visit_item_impl(&mut self, node: &'ast syn::ItemImpl) {
        if node.unsafety.is_some() {
            self.non_boundary_unsafe += 1; // `unsafe impl`
        }
        syn::visit::visit_item_impl(self, node);
    }

    fn visit_expr_unsafe(&mut self, node: &'ast syn::ExprUnsafe) {
        if self.boundary_depth == 0 {
            self.non_boundary_unsafe += 1; // an `unsafe { }` block in internal code
        }
        syn::visit::visit_expr_unsafe(self, node);
    }

    fn visit_expr_unary(&mut self, node: &'ast syn::ExprUnary) {
        if matches!(node.op, syn::UnOp::Deref(_)) {
            self.raw_derefs += 1;
        }
        syn::visit::visit_expr_unary(self, node);
    }

    fn visit_foreign_item_fn(&mut self, node: &'ast syn::ForeignItemFn) {
        if C_ALLOC.contains(&node.sig.ident.to_string().as_str()) {
            self.malloc_free += 1;
        }
        syn::visit::visit_foreign_item_fn(self, node);
    }

    fn visit_expr_method_call(&mut self, node: &'ast syn::ExprMethodCall) {
        let m = node.method.to_string();
        if m == "into_raw" || m == "from_raw" {
            self.into_from_raw += 1;
        }
        syn::visit::visit_expr_method_call(self, node);
    }

    fn visit_expr_call(&mut self, node: &'ast syn::ExprCall) {
        if let syn::Expr::Path(p) = &*node.func {
            if let Some(last) = p.path.segments.last() {
                let id = last.ident.to_string();
                if id == "into_raw" || id == "from_raw" {
                    self.into_from_raw += 1;
                }
                if C_ALLOC.contains(&id.as_str()) {
                    self.malloc_free += 1;
                }
            }
        }
        syn::visit::visit_expr_call(self, node);
    }

    fn visit_type_path(&mut self, node: &'ast syn::TypePath) {
        if let Some(last) = node.path.segments.last() {
            if TARGET_COLLECTIONS.contains(&last.ident.to_string().as_str()) {
                self.adopts_target = true;
            }
        }
        syn::visit::visit_type_path(self, node);
    }
}

/// True if the type contains a raw pointer anywhere (`*mut T`, `Option<*mut T>`, ...).
fn type_has_ptr(ty: &syn::Type) -> bool {
    #[derive(Default)]
    struct PtrVisitor {
        found: bool,
    }
    impl<'ast> Visit<'ast> for PtrVisitor {
        fn visit_type_ptr(&mut self, _n: &'ast syn::TypePtr) {
            self.found = true;
        }
    }
    let mut v = PtrVisitor::default();
    v.visit_type(ty);
    v.found
}

fn is_extern_c(sig: &syn::Signature, attrs: &[syn::Attribute]) -> bool {
    let abi_c = matches!(&sig.abi, Some(syn::Abi { name: Some(s), .. }) if s.value() == "C");
    // catches both #[no_mangle] and #[unsafe(no_mangle)]
    let no_mangle = attrs
        .iter()
        .any(|a| a.to_token_stream().to_string().contains("no_mangle"));
    abi_c || no_mangle
}

/// An ABI-precise rendering of a signature for change detection: name +
/// unsafety + abi + parameter TYPES (in order) + return type. Deliberately
/// ignores parameter binding patterns (names and binding `mut`) — `fn f(mut x:
/// T)` and `fn f(y: T)` have the same type signature and ABI, so they are NOT a
/// change. A change in a parameter's TYPE or the return type IS.
fn normalize_sig(sig: &syn::Signature) -> String {
    let mut parts: Vec<String> = Vec::new();
    if sig.unsafety.is_some() {
        parts.push("unsafe".into());
    }
    match &sig.abi {
        Some(syn::Abi { name: Some(s), .. }) => parts.push(format!("extern \"{}\"", s.value())),
        Some(_) => parts.push("extern".into()),
        None => {}
    }
    parts.push(format!("fn {}", sig.ident));
    let types: Vec<String> = sig
        .inputs
        .iter()
        .map(|arg| match arg {
            syn::FnArg::Receiver(r) => quote::quote!(#r).to_string(),
            syn::FnArg::Typed(pt) => {
                let ty = pt.ty.as_ref();
                quote::quote!(#ty).to_string()
            }
        })
        .collect();
    parts.push(format!("({})", types.join(", ")));
    if let syn::ReturnType::Type(_, ty) = &sig.output {
        parts.push(format!("-> {}", quote::quote!(#ty)));
    }
    parts.join(" ").split_whitespace().collect::<Vec<_>>().join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stats_of(src: &str) -> FileStats {
        let file = syn::parse_file(src).unwrap();
        let mut s = FileStats::default();
        s.visit_file(&file);
        s
    }

    #[test]
    fn facade_struct_flags_raw_ptr_into_raw_and_adoption() {
        // the real facade shape: a struct adopts a std collection BUT keeps
        // raw-pointer node plumbing (adoption via a `BinaryHeap<_>` type use,
        // not merely a `use` import).
        let src = r#"
            use std::collections::BinaryHeap;
            struct Heap { data: BinaryHeap<Entry>, head: *mut Node }
            struct Node { next: *mut Node, val: i32 }
            struct Entry { val: i32 }
            fn f(b: Box<Node>) { let _p = Box::into_raw(b); }
        "#;
        let s = stats_of(src);
        assert_eq!(s.raw_ptr_fields, 2); // Heap.head + Node.next
        assert!(s.into_from_raw >= 1);
        assert!(s.adopts_target);
    }

    #[test]
    fn clean_collection_file_has_no_facade_signals() {
        let src = r#"
            use std::collections::BinaryHeap;
            struct Entry { val: i32 }
            fn f() -> BinaryHeap<Entry> { BinaryHeap::new() }
        "#;
        let s = stats_of(src);
        assert_eq!(s.raw_ptr_fields, 0);
        assert_eq!(s.into_from_raw, 0);
        assert!(s.adopts_target);
    }

    #[test]
    fn extern_c_signature_is_captured() {
        let src = r#"
            #[no_mangle]
            pub extern "C" fn foo(x: i32) -> i32 { x }
        "#;
        let s = stats_of(src);
        assert_eq!(s.extern_fns.len(), 1);
        assert_eq!(s.extern_fns[0].0, "foo");
    }

    #[test]
    fn normalize_sig_ignores_binding_mut_and_param_names_but_not_types() {
        let a: syn::ItemFn =
            syn::parse_str(r#"unsafe extern "C" fn f(mut heap: *mut u8) {}"#).unwrap();
        let b: syn::ItemFn =
            syn::parse_str(r#"unsafe extern "C" fn f(heap: *mut u8) {}"#).unwrap();
        let c: syn::ItemFn =
            syn::parse_str(r#"unsafe extern "C" fn f(other: *mut u8) {}"#).unwrap();
        let d: syn::ItemFn =
            syn::parse_str(r#"unsafe extern "C" fn f(heap: *const u8) {}"#).unwrap();
        assert_eq!(normalize_sig(&a.sig), normalize_sig(&b.sig)); // binding `mut`
        assert_eq!(normalize_sig(&a.sig), normalize_sig(&c.sig)); // param name
        assert_ne!(normalize_sig(&a.sig), normalize_sig(&d.sig)); // TYPE change
    }

    #[test]
    fn retained_malloc_free_decls_are_counted() {
        let src = r#"
            extern "C" { fn malloc(n: usize) -> *mut u8; fn free(p: *mut u8); }
        "#;
        let s = stats_of(src);
        assert_eq!(s.malloc_free, 2);
    }

    #[test]
    fn raw_derefs_counts_deref_expressions() {
        let src = r#"unsafe fn f(p: *mut u8) { let _ = *p; let _ = *p + 1; }"#;
        let s = stats_of(src);
        assert_eq!(s.raw_derefs, 2);
    }

    #[test]
    fn non_boundary_unsafe_counts_only_internal_unsafe() {
        let src = r#"
            #[no_mangle]
            pub unsafe extern "C" fn boundary(p: *mut u8) { *p = 1; }
            unsafe fn helper() {}
            fn safe_fn() { unsafe { let _ = 1; } }
            unsafe impl Send for X {}
            struct X;
        "#;
        let s = stats_of(src);
        // helper (unsafe fn) + safe_fn's unsafe block + unsafe impl = 3;
        // the extern "C" boundary fn's unsafe is NOT counted.
        assert_eq!(s.non_boundary_unsafe, 3);
    }

    #[test]
    fn churn_is_multiset_line_difference() {
        let (added, removed) = churn("a\nb\nc\n", "a\nb\nd\ne\n");
        assert_eq!(added, 2); // d, e
        assert_eq!(removed, 1); // c
    }
}
