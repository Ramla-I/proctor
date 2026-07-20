# Hybrid Symbolic–Neural C-to-Rust Translation

## 1. Motivation

Our team has developed a sequence of static-analysis-based plugins that transform C2Rust-generated code by replacing unsafe features and unidiomatic patterns with safer and more idiomatic Rust.

Recent DARPA TRACTOR evaluations exposed two limitations of a predominantly symbolic approach:

1. Many C programs contain patterns that do not satisfy Rust's ownership, borrowing, and memory-safety disciplines. Handling them often requires pattern-specific, non-local transformations that are difficult to encode with purely symbolic rewriting.
2. Even when static analysis can determine the desired Rust types and invariants, manually implementing all corresponding rewrites is difficult because of the large number of type and expression combinations.

Current LLM-based systems can achieve comparable or better test-suite correctness and substantially better idiomaticity. They can also generate tests and iteratively repair translations. However, they are expensive in both runtime and monetary cost.

Our goal is therefore to combine symbolic and neural methods to achieve:

- correctness close to or better than current symbolic approaches;
- idiomaticity close to neural approaches; and
- translation cost close to symbolic approaches.

The central strategy is to decompose translation into non-local and local transformations, use symbolic analyses to constrain and validate LLM behavior, and extract reusable typed rewriting rules from successful LLM translations.

## 2. Overall Pipeline

The pipeline starts from C2Rust-generated unsafe Rust rather than directly from C. Working in Rust makes Rust abstractions easier to introduce and allows source and target rewrites to remain within the same language.

Before the main transformation steps, we apply existing Crat plugins:

- **extern**: replace C2Rust-introduced intraproject external calls with direct Rust imports;
- **preprocess**: repair trivial problematic syntactic patterns and remove dead code;
- **simpl**: remove unnecessary casts and other avoidable complexity.

The main translation then proceeds in two steps:

1. **Non-local transformation**
    - abstraction recovery;
    - discipline repair.
2. **Local type-directed transformation**
    - whole-program type analysis and skeleton generation;
    - constrained LLM body translation;
    - reusable rule extraction and application.

A test suite is assumed to be available during translation. It may come from the original program or from external test-generation work within TRACTOR.

## 3. Step 1: Non-Local Transformation

This step handles patterns that cannot be repaired reliably through statement-local rewriting. Such repairs may require changing types, function signatures, control flow, data representation, statement order, or the number of parameters.

### 3.1 Abstraction Recovery

This substep identifies C implementations of well-known abstractions and replaces them with established Rust abstractions where appropriate.

Examples include:

- dynamic arrays;
- linked lists;
- hash tables;
- intrusive data structures;
- self-referential structures;
- memory pools;
- arenas.

The preferred result is not a direct transliteration of the C implementation, but replacement with a standard-library or well-established crate implementation when semantics permit.

The initial plan is to use LLM agents to:

1. identify candidate abstractions;
2. determine the relevant implementation boundary;
3. select a suitable Rust abstraction;
4. replace the implementation while minimizing changes to unrelated code;
5. compile and test the result;
6. repair failures within a bounded number of iterations.

### 3.2 Discipline Repair

This substep handles patterns that violate Rust's ownership, borrowing, or memory-access disciplines but do not necessarily correspond to a reusable high-level abstraction.

pExamples include:

- negative pointer offsets;
- aliased pointers without a unique owner;
- simultaneously active mutable aliases;
- pointers whose correct Rust representation requires non-local state or signature changes.

The main principle is to use symbolic information to localize the problem and guide a scoped LLM transformation.

#### Negative-offset example

C permits pointer arithmetic with negative offsets, while Rust slices require non-negative indices. A naive pointer-to-slice conversion may therefore introduce runtime panics.

A repair may require keeping the original base pointer and representing the current position as a separate integer offset. Uses can then compute valid positive indices relative to the base.

For this pattern, the proposed workflow is:

1. instrument the program;
2. execute the test suite;
3. report concrete negative-offset occurrences and code locations;
4. ask the LLM to make a surgical repair;
5. recompile and rerun tests;
6. rerun the instrumentation to check whether the problematic behavior remains.

Dynamic analysis is preferred when it provides precise evidence and avoids false positives. Static analysis can supplement it when needed, for example to determine mutability or identify potentially conflicting aliases.

### 3.3 Validation and Iteration

Each non-local transformation is validated through:

- compilation;
- test-suite execution;
- symbolic or dynamic checks when available.

The LLM receives concrete feedback and may repair its transformation. Iteration stops when:

- the problematic pattern has been eliminated; or
- a predefined iteration bound has been reached.

Efficiency is a central requirement. LLMs should receive precise diagnostics and be instructed to modify only the affected scope. Local transformations are deferred to Step 2.

### 3.4 Executables and Libraries

Executable programs permit arbitrary internal signature and representation changes.

Library programs must preserve public API signatures. When an internal transformation changes an API function, the system should introduce a wrapper that preserves the original interface and delegates to the transformed implementation.

Some wrappers may remain inherently unsafe. For example, a wrapper may need to recover a base pointer from an incoming pointer before calling a negative-offset-free implementation. Such wrappers are treated as explicit compatibility boundaries and are excluded from later safe-body transformation.

Example:

```rust
// before
unsafe fn foo(p: *mut i32, x: i32) -> i32 {
    return *p.offset(0) + *p.offset(x as isize);
}
unsafe fn bar() -> i32 {
    let mut arr: [i32; 3] = [1, 2, 3];
    return foo((&raw mut arr as *mut i32).offset(1), -1);
}
```

```rust
// after
unsafe fn foo_from_base(p: *mut i32, x: usize) -> i32 {
    return *p.add(0) + *p.add(x);
}
unsafe fn foo(p: *mut i32, x: i32) -> i32 {
    if x < 0 {
        let x = x.unsigned_abs() as usize;
        return foo_from_base(p.sub(x), x);
    }
    return foo_from_base(p, x as usize);
}
unsafe fn bar() -> i32 {
    let mut arr: [i32; 3] = [1, 2, 3];
    return foo_from_base(&raw mut arr as *mut i32, 1);
}
```

Step 1 therefore records all introduced wrappers and passes that list to Step 2.

## 4. Step 2: Local Type-Directed Transformation

After non-local problems have been repaired, the remaining code should largely admit statement-by-statement transformation.

### 4.1 Whole-Program Analysis

The system first runs static analysis over the full program. The initial focus is pointer analysis, though the framework may later incorporate additional analyses.

The analysis determines target Rust types for:

- structure fields;
- function parameters and return values;
- global variables;
- local variables.

Possible pointer-related target types include:

- references;
- mutable references;
- slices;
- mutable slices;
- `Box`;
- boxed slices or `Vec`;
- optional variants of these types.

### 4.2 Skeleton Generation

The analysis results are used to generate a program skeleton containing:

- all target type definitions;
- all transformed function signatures;
- all global and local variable types;
- unimplemented function bodies.

The skeleton fixes the global transformation decisions before body translation begins. This prevents the LLM from independently redesigning types and signatures for each function.

### 4.3 Function-Body Translation

For each function, the LLM receives:

- the original body;
- the target skeleton;
- transitively referenced type definitions;
- callee signatures.

Bodies are independent in principle once the skeleton is fixed. For incremental validation, translation proceeds from leaf SCCs of the call graph upward. The initial design assumes no function pointers.

After a callee is transformed, callers may still use its old signature. To preserve compilability during migration, the system introduces a temporary wrapper with the old signature that calls the transformed function. Callers are temporarily redirected to the wrapper.

When callers are later transformed, they call the new function directly.

At completion:

- temporary wrappers are removed for executable programs;
- API-preserving wrappers remain for library programs;
- wrappers created during Step 1 are never transformed.

Some wrappers can be generated by predefined rules. Others, such as conversions requiring inferred slice bounds, may require LLM assistance.

### 4.4 Constrained Statement Alignment

To support rule extraction, each original statement or expression region is annotated with a unique label.

The LLM must preserve label correspondence:

- labels cannot be reordered;
- labels cannot be removed or duplicated;
- new unlabeled code cannot be introduced;
- preserved regions must remain unchanged.

Example:

```rust
// before
fn foo(p: *mut i32, q: *mut i32) -> i32 {
    #[proctor(0)]
    let x: i32 = *p.offset(1);
    #[proctor(1)]
    let y: i32 = if q.is_null() {
        #[proctor(preserve)]
        0
    } else {
        #[proctor(2)]
        *q
    };
    #[proctor(preserve)]
    return x + y;
}
```

```rust
// skeleton
fn foo(p: &[i32], q: Option<&i32>) -> i32 {
    #[proctor(0)]
    let x: i32 = ...;
    #[proctor(1)]
    let y: i32 = if ... {
        #[proctor(preserve)]
        0
    } else {
        #[proctor(2)]
        ...
    };
    #[proctor(preserve)]
    return x + y;
}
```

```rust
// after
fn foo(p: &[i32], q: Option<&i32>) -> i32 {
    #[proctor(0)]
    let x: i32 = p[1];
    #[proctor(1)]
    let y: i32 = if q.is_none() {
        #[proctor(preserve)]
        0
    } else {
        #[proctor(2)]
        *q.unwrap()
    };
    #[proctor(preserve)]
    return x + y;
}
```

Label preservation is checked automatically. Violations are returned to the LLM together with compiler and test failures.

## 5. Reusable Rule Extraction

The key contribution of Step 2 is to extract reusable transformation rules from successful LLM translations.

### 5.1 Rule Construction

After each function is translated, the system collects aligned before/after regions. Similar pairs are grouped by syntactic structure, and anti-unification is used to derive generalized rules.

Rules may be learned across multiple functions and programs.

A rule must include both syntax and context:

source pattern + context constraints → target pattern

Important context includes:

- source and target types;
- base-type properties such as `Copy`;
- relevant callee signatures.

### 5.2 Rule Application

Before invoking an LLM on a function, the system applies all valid known rules.

- If all regions are covered, no LLM call is required.
- If only some regions are covered, the LLM completes the remaining holes.
- Presolved and preserved regions reduce both search space and reasoning cost.

All rule applications remain subject to compilation and test validation.

The intended long-term benefit is amortized translation cost: LLM usage should decrease as the rule library grows.