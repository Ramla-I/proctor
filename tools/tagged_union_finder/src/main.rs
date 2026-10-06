//! tagged_union_finder: a symbolic detector for "fat struct" tagged unions in a
//! C2Rust+CRAT crate -- a struct with an enum-discriminant field (`kind`/`type`)
//! plus per-variant payload fields, the AST/IR/token-node idiom (e.g. chibicc's
//! `Node` + `NodeKind`). An LLM identify scan tends to miss these in a big crate
//! (out-ranked by small, self-contained containers), so this finds them
//! deterministically to feed the abstraction_recovery stage as `tagged_union`
//! candidates it can't overlook.
//!
//! Usage: tagged_union_finder --crate <dir> [--min-variants N] [--json]
//!
//! Purely syntactic (syn v2), no name resolution / type inference -- same
//! best-effort posture as measure_unsafety / recovery_metrics. It does NOT catch
//! `void*`-payload tagged unions (no discriminant+typed-fields shape); those are
//! the void_finder's job.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use clap::Parser;
use serde::Serialize;
use syn::visit::Visit;

#[derive(Parser)]
#[command(about = "Find fat-struct tagged unions (enum discriminant + payload) in a crate.")]
struct Cli {
    /// Crate directory to scan.
    #[arg(long = "crate")]
    krate: PathBuf,
    /// Minimum discriminant-enum variants for a struct to count (filters bool-ish tags).
    #[arg(long, default_value_t = 3)]
    min_variants: usize,
    /// Emit JSON (the default); kept for symmetry with the other tools.
    #[arg(long)]
    json: bool,
}

#[derive(Serialize)]
struct Candidate {
    /// crate-root-relative path of the file defining the struct.
    file: String,
    /// the tagged-union struct's type name (what the recovery targets).
    #[serde(rename = "type")]
    ty: String,
    /// the discriminant field name (e.g. `kind`).
    discriminant: String,
    /// the discriminant field's enum type (e.g. `NodeKind`).
    discriminant_enum: String,
    /// number of variants in that enum.
    variants: usize,
    /// number of non-discriminant fields (the payload).
    payload_fields: usize,
    /// crate-wide `match` expressions (a dispatch-pervasiveness proxy).
    crate_match_sites: usize,
    /// ranking score: variants drive it (a 48-way AST node far outranks a 3-way tag).
    score: usize,
}

fn main() {
    let cli = Cli::parse();
    let files = collect_rs(&cli.krate);

    // Pass 1: every enum in the crate -> variant count.
    let mut enums: BTreeMap<String, usize> = BTreeMap::new();
    // crate-wide match-expression count (dispatch-pervasiveness proxy).
    let mut match_sites = 0usize;
    for src in files.values() {
        if let Ok(f) = syn::parse_file(src) {
            let mut v = EnumCollector {
                enums: &mut enums,
                matches: &mut match_sites,
            };
            v.visit_file(&f);
        }
    }

    // Pass 2: structs whose a field's type is one of those enums = a tagged union.
    let mut cands: Vec<Candidate> = Vec::new();
    for (rel, src) in &files {
        let Ok(f) = syn::parse_file(src) else { continue };
        let mut v = StructCollector {
            enums: &enums,
            file: rel.clone(),
            match_sites,
            min_variants: cli.min_variants,
            out: &mut cands,
        };
        v.visit_file(&f);
    }

    cands.sort_by(|a, b| b.score.cmp(&a.score).then(a.ty.cmp(&b.ty)));
    println!("{}", serde_json::to_string_pretty(&cands).unwrap());
}

struct EnumCollector<'a> {
    enums: &'a mut BTreeMap<String, usize>,
    matches: &'a mut usize,
}
impl<'ast> Visit<'ast> for EnumCollector<'_> {
    fn visit_item_enum(&mut self, node: &'ast syn::ItemEnum) {
        self.enums
            .insert(node.ident.to_string(), node.variants.len());
        syn::visit::visit_item_enum(self, node);
    }
    fn visit_expr_match(&mut self, node: &'ast syn::ExprMatch) {
        *self.matches += 1;
        syn::visit::visit_expr_match(self, node);
    }
}

struct StructCollector<'a> {
    enums: &'a BTreeMap<String, usize>,
    file: String,
    match_sites: usize,
    min_variants: usize,
    out: &'a mut Vec<Candidate>,
}
impl<'ast> Visit<'ast> for StructCollector<'_> {
    fn visit_item_struct(&mut self, node: &'ast syn::ItemStruct) {
        let mut discriminant: Option<(String, String, usize)> = None; // (field, enum, variants)
        let mut field_count = 0usize;
        for field in &node.fields {
            field_count += 1;
            let Some(name) = field.ident.as_ref() else { continue };
            if let Some(en) = enum_name_of(&field.ty) {
                if let Some(&variants) = self.enums.get(&en) {
                    // first enum-typed field is the discriminant; prefer the
                    // richest (most variants) if several.
                    let better = discriminant
                        .as_ref()
                        .map(|(_, _, v)| variants > *v)
                        .unwrap_or(true);
                    if better {
                        discriminant = Some((name.to_string(), en, variants));
                    }
                }
            }
        }
        if let Some((dfield, denum, variants)) = discriminant {
            let payload_fields = field_count.saturating_sub(1);
            if variants >= self.min_variants && payload_fields >= 1 {
                self.out.push(Candidate {
                    file: self.file.clone(),
                    ty: node.ident.to_string(),
                    discriminant: dfield,
                    discriminant_enum: denum,
                    variants,
                    payload_fields,
                    crate_match_sites: self.match_sites,
                    score: variants + payload_fields,
                });
            }
        }
        syn::visit::visit_item_struct(self, node);
    }
}

/// The enum type name of a field, if its type is a bare path (`NodeKind`),
/// ignoring pointers/options (a discriminant is a direct enum field, not `*mut`).
fn enum_name_of(ty: &syn::Type) -> Option<String> {
    if let syn::Type::Path(p) = ty {
        return p.path.segments.last().map(|s| s.ident.to_string());
    }
    None
}

/// rel-path -> source for every `*.rs` under `root` (excl. target/, hidden,
/// build.rs), matching the other tools' file set.
fn collect_rs(root: &Path) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
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
        let files: BTreeMap<String, String> =
            [("lib.rs".to_string(), src.to_string())].into_iter().collect();
        let mut enums = BTreeMap::new();
        let mut matches = 0;
        let f = syn::parse_file(src).unwrap();
        EnumCollector { enums: &mut enums, matches: &mut matches }.visit_file(&f);
        let mut out = Vec::new();
        StructCollector {
            enums: &enums,
            file: "lib.rs".into(),
            match_sites: matches,
            min_variants: 3,
            out: &mut out,
        }
        .visit_file(&f);
        let _ = &files;
        out
    }

    #[test]
    fn flags_fat_struct_tagged_union() {
        let src = r#"
            enum NodeKind { Add, Sub, Mul, Num }
            struct Node { kind: NodeKind, lhs: *mut Node, rhs: *mut Node, val: i64 }
            fn f(n: &Node) { match n.kind { NodeKind::Add => {}, _ => {} } }
        "#;
        let c = find(src);
        assert_eq!(c.len(), 1);
        assert_eq!(c[0].ty, "Node");
        assert_eq!(c[0].discriminant, "kind");
        assert_eq!(c[0].discriminant_enum, "NodeKind");
        assert_eq!(c[0].variants, 4);
        assert!(c[0].payload_fields >= 3);
        assert!(c[0].crate_match_sites >= 1);
    }

    #[test]
    fn ignores_small_tag_and_payloadless() {
        // a 2-variant tag (below min) and an enum-only struct are not tagged unions.
        let src = r#"
            enum Flag { On, Off }
            struct S { f: Flag, x: i32 }
            enum Big { A, B, C, D }
            struct JustTag { k: Big }
        "#;
        assert!(find(src).is_empty());
    }

    #[test]
    fn picks_richest_discriminant_and_ranks_by_variants() {
        let src = r#"
            enum Small { A, B, C }
            enum Huge { V0, V1, V2, V3, V4, V5 }
            struct Big { kind: Huge, a: i32, b: i32 }
            struct Lil { kind: Small, a: i32 }
        "#;
        let c = find(src);
        assert_eq!(c.len(), 2);
        assert_eq!(c[0].ty, "Big"); // higher variant count ranks first
        assert_eq!(c[0].variants, 6);
    }
}
