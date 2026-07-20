# Orchestration Framework

The orchestration framework should provide shared infrastructure that all translation and transformation stages can use.

The primary goals are to:

* eliminate duplicated infrastructure across pipeline stages;
* make experiments easy to configure and reproduce;
* support rapid experimentation with different models, prompts, and context-retrieval strategies; and
* collect consistent metrics across the entire pipeline.

## Core Components

### 1. Vendor-Agnostic LLM API

Provide a single interface for all LLM interactions.

#### Requirements

* Support multiple providers, such as OpenAI, Anthropic, Gemini, and local models.
* Make switching models a configuration change rather than a code change.
* Allow individual pipeline stages to override the default provider or model when necessary.
* Expose a consistent request and response interface regardless of provider.
* Handle provider-specific details internally, including:

  * authentication;
  * API formats;
  * retry logic;
  * rate limiting.
* Manage context-window limits by either:

  * automatically truncating inputs using configurable strategies; or
  * returning a structured error indicating that the input exceeds the model's context limit.
* Return standardized metadata, including:

  * model name;
  * latency;
  * token usage;
  * finish reason;
  * provider-specific metadata, when available.

The initial implementation should prioritize simplicity while keeping the interface stable enough that providers can be swapped without modifying downstream code.

### 2. LLM Usage Tracker

Provide centralized accounting for every LLM invocation.

#### Recorded Information

At minimum, record:

* model and provider;
* pipeline stage;
* prompt identifier;
* input tokens;
* cached input tokens;
* output tokens;
* reasoning tokens, when available;
* latency.

#### Aggregation

The tracker should support aggregation over:

* a single pipeline run;
* an individual pipeline stage;
* an experiment;
* an entire benchmark.

This should make it straightforward to answer questions such as:

* Which stage consumes the most tokens?
* How much does an experiment cost?
* Which model provides the best accuracy per dollar?

### 3. Code Context Retrieval

Provide a reusable library for extracting source-code context for LLM prompts.

#### Initial Functionality

* Parse Rust code using the `syn` crate.
* Build an AST representation.
* Track dependencies between:

  * functions;
  * types;
  * traits;
  * modules;
  * implementations.

The framework should support multiple context-retrieval strategies.

Examples include:

* target function only;
* target function and referenced types;
* target function, referenced types, and one-hop callees, similar to Intel's approach;
* enclosing module;
* entire submodule;
* custom strategies.

Strategies should be implemented behind a common interface.

A caller should only need to specify:

* the strategy name;
* the target item, such as a function, type, or trait.

Example:

```python
retrieve_context(
    strategy="function_plus_one_hop",
    target="driver::tx::transmit",
)
```

Adding a new strategy should require implementing the strategy interface and registering it, without modifying existing pipeline code.

The first implementation may support only a simple strategy. However, the framework should support incremental expansion as we evaluate related work.

### 4. Pipeline Orchestrator

Implement a configurable pipeline runner.

The pipeline orchestrator is the most important component. Its interfaces must be clearly defined so that multiple contributors can develop stages independently.

#### Goals

* Make pipeline stages interchangeable.
* Support optional stages.
* Support rapid experimentation.
* Minimize hard-coded execution logic.
* Introduce minimal runtime overhead.

The pipeline should be configurable from a single configuration file.

Each stage should implement a common interface, such as:

```text
run(input, config) -> output
```

#### Required Capabilities

The orchestrator should support:

* enabling and disabling stages;
* changing execution order;
* stage-specific configuration;
* checkpointing intermediate outputs;
* resuming partially completed runs;
* logging stage execution.

The default pipeline should be:

```text
C2Rust
    ↓
CRAT
    ↓
Abstraction Recovery
    ↓
Discipline Repair
    ↓
Local Transformation
```

Future stages should be addable without changing the orchestration logic.

#### Stage Output

A stage output should contain:

* the transformed or translated code;
* the configuration parameters used for the run;
* model information, when applicable;
* token-usage information, when applicable;
* a generic metadata field that can contain additional stage-specific information.

The generic metadata field may contain arbitrary structured or textual information required by a stage.

### 5. Reusable Prompt Library

Centralize common prompt templates used throughout the pipeline.

#### Goals

* Eliminate prompt duplication.
* Encourage consistent prompting.
* Simplify prompt iteration.

Each prompt should:

* have a unique identifier;
* support parameter substitution;
* include version information;
* be reusable across multiple pipeline stages.

Example prompts include:

* wrapper preservation and update.

Prompt versions should be tracked so that experiments remain reproducible even as prompts evolve.

## Design Principles

### Configuration Over Code

Common experimental changes should require only configuration changes. These include:

* model selection;
* prompt versions;
* context-retrieval strategies;
* enabled pipeline stages;
* stage ordering.

### Extensibility

New providers, retrieval strategies, prompts, and pipeline stages should be addable with minimal changes to existing code.

### Reproducibility

Every pipeline run should record the following information:

* full configuration;
* prompt identifiers and versions;
* model and provider versions;
* token usage;
* intermediate outputs;
* stage-specific metadata required to reproduce the run.

### Modularity

Components should communicate through well-defined interfaces so that they can be developed, tested, and replaced independently.

### Low Runtime Overhead

The orchestration framework should introduce minimal overhead during end-to-end pipeline execution.

Once the pipeline design is stable, stages should execute through a lightweight runtime path without unnecessary:

* process creation;
* serialization;
* network communication;
* synchronous metric persistence;
* framework-level scheduling overhead.

## Integration Flexibility

The LLM API, prompt library, usage tracker, and context-retrieval library are convenience components. Their initial implementations may be sparse.

They exist primarily to prevent contributors from repeatedly implementing infrastructure that already works well.

Pipeline stages are not required to use these shared components internally. A stage may instead:

* implement its own LLM integration;
* manage its own prompts;
* use an external agent framework;
* invoke Claude Code or a similar coding agent;
* implement its complete workflow internally.

However, every stage must follow the orchestration framework's input and output interfaces.

A self-contained stage must return all required information through its output, including:

* transformed code or artifacts;
* configuration parameters;
* model information, when applicable;
* token usage, when available;
* relevant logs and metadata.

This flexibility is necessary because some stages may directly use external coding agents and therefore may not implement or orchestrate their own agent workflows through the shared framework.

## Future Work
We will also integrate a testing infrastructure which can be run after every stage in which test vectors are provided as well the expected output. 
Test Vectors will follow the DARPA TRACTOR Program Test Corpus.
https://github.com/DARPA-TRACTOR-Program/Test-Corpus/tree/main

You do not have to implement the testing infrastructure right now but keep it in mind with your design choices. 

## Current Repository
Ignore everything else in the repository besides the plan docs, they will be moved to a separate branch and this branch will only be for the orchestration framework. 
