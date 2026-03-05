# a11y-autofix: A Multi-Model, Multi-Tool Framework for Automated Accessibility Remediation in React/TypeScript Codebases

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![WCAG 2.1/2.2](https://img.shields.io/badge/WCAG-2.1%2F2.2-green.svg)](https://www.w3.org/WAI/WCAG22/quickref/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![100% Local](https://img.shields.io/badge/inference-100%25%20local-orange.svg)](docs/ADDING_MODELS.md)

---

## Abstract

**a11y-autofix** is a scientific instrumentation framework for the automated detection and remediation of Web Content Accessibility Guidelines (WCAG) 2.1/2.2 violations in React and TypeScript source code. The system executes four independent static-analysis tools in parallel—pa11y, axe-core, Lighthouse, and Playwright+axe—applies a cross-tool consensus protocol to assign statistically grounded confidence levels to each detected issue, and subsequently routes the remediation task to one of three autonomous code-editing agents (OpenHands, SWE-agent, or a DirectLLM fallback). The agent selection is governed by a deterministic scoring matrix that evaluates issue complexity, volume, and semantic diversity. All inferential workloads are executed against locally hosted large language models (LLMs) through an OpenAI-compatible HTTP interface, ensuring zero dependency on paid cloud APIs and full reproducibility via SHA-256 content addressing of all artefacts. The framework further exposes a YAML-driven experiment configuration layer that enables controlled, multi-model comparative studies with per-model metric collection, ranking, and HTML/CSV/JSON reporting.

---

## Table of Contents

1. [Background and Motivation](#1-background-and-motivation)
2. [System Architecture](#2-system-architecture)
3. [Detection Protocol](#3-detection-protocol)
   - 3.1 [HTML Harness Generation](#31-html-harness-generation)
   - 3.2 [Parallel Multi-Tool Scanning](#32-parallel-multi-tool-scanning)
   - 3.3 [Cross-Tool Deduplication](#33-cross-tool-deduplication)
   - 3.4 [Confidence Computation](#34-confidence-computation)
   - 3.5 [WCAG Taxonomy Mapping](#35-wcag-taxonomy-mapping)
   - 3.6 [Complexity Classification](#36-complexity-classification)
   - 3.7 [Deterministic Issue Ordering](#37-deterministic-issue-ordering)
4. [Data Models](#4-data-models)
5. [LLM Subsystem](#5-llm-subsystem)
   - 5.1 [Backend Abstraction Layer](#51-backend-abstraction-layer)
   - 5.2 [Model Registry](#52-model-registry)
6. [Remediation Agents](#6-remediation-agents)
   - 6.1 [OpenHands Agent](#61-openhands-agent)
   - 6.2 [SWE-Agent](#62-swe-agent)
   - 6.3 [DirectLLM Agent](#63-directllm-agent)
   - 6.4 [Prompt Engineering](#64-prompt-engineering)
7. [Routing Engine](#7-routing-engine)
8. [Pipeline Orchestration](#8-pipeline-orchestration)
9. [Experiment Framework](#9-experiment-framework)
10. [Metrics and Observability](#10-metrics-and-observability)
    - 10.1 [Per-Execution Metrics](#101-per-execution-metrics)
    - 10.2 [Per-Model Experiment Metrics](#102-per-model-experiment-metrics)
    - 10.3 [Per-Issue-Type Metrics](#103-per-issue-type-metrics)
11. [Reproducibility Guarantees](#11-reproducibility-guarantees)
12. [Report Formats](#12-report-formats)
13. [Configuration Reference](#13-configuration-reference)
14. [Command-Line Interface](#14-command-line-interface)
15. [Testing Infrastructure](#15-testing-infrastructure)
16. [Installation and Quick Start](#16-installation-and-quick-start)
17. [Extensibility](#17-extensibility)
18. [Supported WCAG Criteria](#18-supported-wcag-criteria)
19. [Limitations and Threats to Validity](#19-limitations-and-threats-to-validity)
20. [Citation](#20-citation)

---

## 1. Background and Motivation

Web accessibility is mandated by international legislation (e.g., Section 508 in the United States, EN 301 549 in the European Union, ABNT NBR 17060 in Brazil) and codified in the W3C's WCAG specification. Despite this, automated accessibility audits consistently reveal that a substantial fraction of deployed web applications fail to satisfy even Level AA conformance requirements. Manual remediation is labour-intensive and error-prone, motivating the exploration of LLM-assisted automated repair.

Prior work on automated program repair (APR) for accessibility has been limited by several methodological weaknesses: (i) reliance on a single detection tool, which introduces tool-specific false-positive rates; (ii) dependence on proprietary cloud LLM APIs, precluding reproducibility for under-resourced research groups; (iii) absence of controlled multi-model experimental protocols; and (iv) lack of content-addressed artefact tracking, making run-to-run comparison unreliable.

**a11y-autofix** addresses all four shortcomings. It is designed explicitly as a research instrument rather than a production deployment tool, with every architectural decision oriented toward scientific rigour: deterministic outputs, versioned artefacts, structured logging, and a YAML-declarative experiment layer.

---

## 2. System Architecture

The system is organised as a layered pipeline with six primary subsystems communicating through well-typed Pydantic v2 data models:

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLI Layer (Typer)                          │
│    fix │ experiment │ models │ scanners │ analyze │ setup           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                     Pipeline Orchestrator                           │
│   File Discovery → Parallel Scan → Route → Fix (retry) → Report    │
└───┬──────────────────────┬────────────────────┬────────────────────┘
    │                      │                    │
┌───▼──────────┐  ┌────────▼──────────┐  ┌─────▼──────────────────┐
│  Scanner     │  │  Router Engine    │  │  Reporter              │
│  Subsystem   │  │  (Scoring Matrix) │  │  JSON │ HTML │ CSV     │
│              │  └────────┬──────────┘  └────────────────────────┘
│ pa11y        │           │
│ axe-core     │  ┌────────▼──────────────────────────────────────┐
│ lighthouse   │  │          Agent Subsystem                      │
│ playwright   │  │  OpenHands │ SWE-Agent │ DirectLLM (fallback)│
└──────────────┘  └────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────────┐
│                        LLM Subsystem                                │
│  ModelRegistry (YAML) │ BaseLLMClient │ Backend Adapters           │
│  Ollama │ LM Studio │ vLLM │ llama.cpp │ LocalAI │ Custom         │
└─────────────────────────────────────────────────────────────────────┘
```

All inter-layer communication uses immutable Pydantic models; no mutable shared state exists between subsystems. Concurrency is managed exclusively through `asyncio` primitives (`asyncio.gather`, `asyncio.Semaphore`), avoiding thread-safety issues.

---

## 3. Detection Protocol

The detection protocol is implemented in `a11y_autofix/protocol/detection.py` and constitutes the scientific core of the system. Its purpose is to transform heterogeneous, tool-specific raw findings into a canonical, deduplicated, confidence-annotated issue list suitable for downstream analysis.

### 3.1 HTML Harness Generation

React and TypeScript source files cannot be directly processed by browser-based accessibility tools, as they require transpilation and a DOM. The system resolves this by generating a self-contained HTML harness for each component file.

The harness generation pipeline (`a11y_autofix/utils/files.py`) applies the following transformations to the source TSX/JSX:

1. **ES6 import removal**: All `import ... from '...'` and side-effect import statements are stripped using MULTILINE-mode regular expressions.
2. **CommonJS require removal**: `const/let/var x = require(...)` patterns are eliminated.
3. **TypeScript annotation removal**: Type annotations of the form `: Type` preceding `=`, `,`, `)`, `{` are removed. Interface declarations (`interface Foo { ... }`) and type aliases (`type Foo = ...`) are excised via DOTALL-mode patterns. `as Type` cast expressions are stripped.
4. **Export normalisation**: `export default function Foo` is rewritten to `function __Component`, and bare `export default` to `const __Component =`. All remaining `export` keywords are removed.

The cleaned component is injected into a complete HTML5 document that embeds React 18 UMD builds, ReactDOM, and Babel standalone (for in-browser JSX transpilation), along with mocks for the most common React ecosystem libraries:

```
useNavigate, useParams, useLocation (react-router-dom)
Link, NavLink, Navigate, Route, Routes
clsx, cn, classNames
```

The harness mounts the component under `<div id="root" role="main">` using `ReactDOM.createRoot` within a `React.StrictMode` boundary. A two-second settling delay is imposed by browser-based scanners to allow React's asynchronous rendering to complete before the accessibility tree is evaluated.

**Design rationale**: This approach enables accessibility scanning of components without requiring a bundler, a running development server, or access to the full application context, making the scanning process self-contained and reproducible.

### 3.2 Parallel Multi-Tool Scanning

The `MultiToolScanner` orchestrator (`a11y_autofix/scanner/orchestrator.py`) executes all enabled scanner runners concurrently using `asyncio.gather`. Each runner is a subclass of `BaseScanner` and is responsible for:

1. **Availability check** (`check_available()`): Verifies that the required CLI binary or npm package is installed and records its version string.
2. **Execution** (`run(html_path: Path) → list[ToolFinding]`): Invokes the tool as a subprocess with a configurable timeout, parses its JSON output, and returns a list of `ToolFinding` objects.

The four bundled scanners are:

| Runner | Implementation | Invocation | Output Format |
|--------|---------------|------------|---------------|
| `Pa11yRunner` | `scanner/pa11y.py` | `pa11y --reporter json <url>` | JSON array of issues |
| `AxeRunner` | `scanner/axe.py` | `npx @axe-core/cli --stdout <url>` | axe JSON results |
| `LighthouseRunner` | `scanner/lighthouse.py` | `lighthouse --output json --only-categories accessibility <url>` | Lighthouse JSON report |
| `PlaywrightAxeRunner` | `scanner/playwright_axe.py` | Playwright Chromium + axe-core injected via CDN | axe JSON results |

**Note on exit codes**: pa11y exits with code 2 when accessibility issues are found (not an error condition). The runner explicitly treats exit code 2 as a successful scan with findings, not as a process failure.

**Lighthouse audit mapping**: Lighthouse organises results by audit ID rather than WCAG criterion. The runner includes a curated `_AUDIT_TO_WCAG` mapping table that translates Lighthouse audit identifiers (e.g., `color-contrast`, `image-alt`, `button-name`) to their corresponding WCAG 2.x criterion codes.

### 3.3 Cross-Tool Deduplication

After all runners complete, the `DetectionProtocol` aggregates their outputs through a deterministic deduplication procedure. The deduplication key for each `ToolFinding` is computed as:

```
key = normalize(selector) + "|" + (wcag_criteria if wcag_criteria else rule_id)
```

where `normalize` applies `.strip().lower()` to the CSS selector. Two findings from different tools sharing the same key are considered observations of the same violation on the same DOM element. They are merged into a single `A11yIssue`, with:

- `found_by`: the set of tools that detected it
- `tool_consensus`: the cardinality of `found_by`
- `findings`: the complete list of raw `ToolFinding` objects for traceability

The *primary* finding—used as the canonical representative—is selected by a multi-criterion ranking function that preferentially selects the finding that: (a) includes a WCAG criterion code, (b) has higher impact severity, and (c) provides more HTML context.

### 3.4 Confidence Computation

The confidence level of each issue is assigned according to the following multi-tool consensus rule:

| Condition | Confidence Level | Rationale |
|-----------|-----------------|-----------|
| `tool_consensus ≥ min_tool_consensus` (default: 2) | `HIGH` | Independent corroboration from multiple engines reduces the false-positive rate |
| `tool_consensus == 1` AND `impact ∈ {critical, serious}` | `MEDIUM` | Single-tool detection with high declared severity |
| All other cases | `LOW` | Single-tool, low-impact finding; highest false-positive probability |

The `min_tool_consensus` threshold is configurable via the `MIN_TOOL_CONSENSUS` environment variable, allowing researchers to calibrate the precision/recall trade-off for their experimental context.

### 3.5 WCAG Taxonomy Mapping

The protocol maintains two lookup tables that map WCAG criterion codes and tool-specific rule identifiers to the system's internal `IssueType` taxonomy:

**`WCAG_TO_ISSUE_TYPE`** covers 40+ WCAG 2.x criteria across all four principles (Perceivable, Operable, Understandable, Robust):

| IssueType | Example WCAG Criteria |
|-----------|----------------------|
| `CONTRAST` | 1.4.1, 1.4.3, 1.4.6, 1.4.11 |
| `ALT_TEXT` | 1.1.1 |
| `SEMANTIC` | 1.3.1–1.3.5, 2.4.6, 2.4.10, 3.1.1, 3.1.2, 3.2.1, 3.2.2, 4.1.1 |
| `LABEL` | 1.3.6, 2.4.2, 2.4.4, 2.5.3 |
| `ARIA` | 4.1.2, 4.1.3 |
| `KEYBOARD` | 2.1.1–2.1.4, 2.4.1, 2.4.3, 2.4.7, 2.4.11, 2.4.12 |
| `FOCUS` | 2.4.7, 2.4.11, 3.2.1 |
| `OTHER` | Multimedia criteria (1.2.x), visual/motion (1.4.2, 1.4.4, 1.4.5, 1.4.10, 1.4.12, 1.4.13) |

**`RULE_TO_ISSUE_TYPE`** provides a fallback mapping for 50+ tool-specific rule identifiers (e.g., axe-core rule IDs such as `color-contrast`, `aria-required-attr`, `landmark-one-main`).

Classification proceeds by first attempting an exact WCAG criterion lookup, then exact rule ID lookup, then partial rule ID substring match, and finally defaulting to `IssueType.OTHER`.

### 3.6 Complexity Classification

Each issue is assigned a remediation complexity level derived from its WCAG criterion via `WCAG_TO_COMPLEXITY`:

| Complexity | Criteria | Representative Repair Actions |
|------------|----------|-------------------------------|
| `SIMPLE` | 1.1.1, 2.4.2, 3.1.1, 4.1.1, 4.1.2 | Adding a single attribute (`alt`, `lang`, `aria-label`) |
| `MODERATE` | 1.3.1, 1.3.2, 2.1.1, 2.4.1, 2.4.3, 2.4.4, 2.4.6, 2.4.7, 4.1.3 | Partial structural refactoring, focus management, link purpose |
| `COMPLEX` | 1.4.3, 1.4.6, 1.4.10, 1.4.11, 1.4.12, 1.3.4, 2.1.2, 2.4.11, 2.4.12 | Design token changes, keyboard trap elimination, reflow |

The complexity classification is consumed by the routing engine to inform agent selection.

### 3.7 Deterministic Issue Ordering

To ensure that the ordered issue list is identical across runs given the same input (a requirement for scientific reproducibility), issues are sorted by the following compound key:

```python
key = (
    -CONFIDENCE_PRIORITY[issue.confidence],   # HIGH=3, MEDIUM=2, LOW=1 (descending)
    -IMPACT_PRIORITY[issue.impact],           # critical=4 → minor=1 (descending)
    issue.wcag_criteria or "9.9.9",           # ascending lexicographic, unknown last
    issue.selector,                            # tiebreaker: CSS selector alphabetical
)
```

This guarantees that high-confidence, high-impact issues appear first, with fully deterministic tiebreaking that does not depend on the order of tool execution or Python dict iteration.

---

## 4. Data Models

All data structures are implemented as Pydantic v2 `BaseModel` instances (`a11y_autofix/config.py`), ensuring validation, serialisation, and type safety throughout the pipeline.

### Core Enumerations

| Enum | Values | Description |
|------|--------|-------------|
| `LLMBackend` | `ollama`, `lm_studio`, `vllm`, `llamacpp`, `jan`, `localai`, `custom` | Available local LLM serving backends |
| `ScanTool` | `pa11y`, `axe-core`, `lighthouse`, `playwright+axe` | Accessibility scanning engines |
| `WCAGLevel` | `WCAG2A`, `WCAG2AA`, `WCAG2AAA` | WCAG conformance target level |
| `IssueType` | `aria`, `contrast`, `keyboard`, `label`, `semantic`, `alt-text`, `focus`, `other` | Semantic accessibility violation categories |
| `Complexity` | `simple`, `moderate`, `complex` | Estimated remediation effort |
| `Confidence` | `high`, `medium`, `low` | Multi-tool consensus confidence level |
| `AgentType` | `auto`, `openhands`, `swe-agent`, `direct-llm` | Code-editing agent selection |

### Primary Data Models

**`ToolFinding`** — Raw output of a single scanner run on a single element:
```
tool: ScanTool          | tool_version: str    | rule_id: str
wcag_criteria: str|None | message: str         | selector: str
context: str            | impact: str          | help_url: str
```

**`A11yIssue`** — Deduplicated, classified, confidence-annotated issue:
```
issue_id: str           | file: str            | selector: str
issue_type: IssueType   | complexity: Complexity | wcag_criteria: str|None
impact: str             | confidence: Confidence | found_by: list[ScanTool]
tool_consensus: int     | findings: list[ToolFinding] | message: str
context: str            | resolved: bool
```

The `issue_id` is computed as `SHA-256(file + ":" + selector + ":" + wcag_criteria + ":" + issue_type)[:16]`, yielding a 16-character hexadecimal identifier that is stable and unique across runs for the same logical violation.

**`ScanResult`** — Complete scan output for one file:
```
file: Path              | file_hash: str        | issues: list[A11yIssue]
scan_time: float        | tools_used: list[ScanTool] | tool_versions: dict[str,str]
error: str|None
```

The `file_hash` is computed as `"sha256:" + SHA-256(file_content_bytes).hexdigest()`.

**`FixAttempt`** — Single remediation attempt by an agent:
```
attempt_number: int     | agent: str            | model: str
timestamp: datetime     | success: bool         | diff: str
new_content: str        | tokens_used: int|None | time_seconds: float
error: str|None
```

**`FixResult`** — Complete remediation record for one file:
```
file: Path              | scan_result: ScanResult | attempts: list[FixAttempt]
final_success: bool     | issues_fixed: int       | issues_pending: int
total_time: float
```

**`RouterDecision`** — Agent routing outcome:
```
agent: str   | score: int   | reason: str
```

**`ExperimentResult`** — Aggregate multi-model experiment outcome:
```
experiment_id: str                      | experiment_name: str
timestamp: datetime                     | models_tested: list[str]
files_processed: int                    | results_by_model: dict[str, list[FixResult]]
success_rate_by_model: dict[str, float] | avg_time_by_model: dict[str, float]
issues_fixed_by_model: dict[str, int]   | config_snapshot: dict
tool_versions: dict[str, str]
```

---

## 5. LLM Subsystem

### 5.1 Backend Abstraction Layer

All LLM inference is routed through the `BaseLLMClient` abstract class (`a11y_autofix/llm/base.py`), which defines a minimal interface:

```python
async def complete(messages, *, temperature, max_tokens) → str
async def health_check() → bool
async def get_model_info() → dict
async def complete_with_metrics(messages, ...) → tuple[str, dict]
```

The `LocalLLMClient` concrete implementation (`a11y_autofix/llm/client.py`) issues HTTP `POST /v1/chat/completions` requests using `httpx.AsyncClient`, which is protocol-compatible with all OpenAI-compatible local serving solutions. Backend-specific default base URLs are resolved from an internal mapping:

| Backend | Default Base URL |
|---------|-----------------|
| `ollama` | `http://localhost:11434/v1` |
| `lm_studio` | `http://localhost:1234/v1` |
| `vllm` | `http://localhost:8000/v1` |
| `llamacpp` | `http://localhost:8080/v1` |
| `jan` | `http://localhost:1337/v1` |
| `localai` | `http://localhost:8080/v1` |

The `health_check()` method queries `GET /v1/models` and verifies that the target model ID appears in the response, distinguishing between a running server with an incorrect model and an unreachable server.

The `complete_with_metrics()` method extracts token usage statistics from the response body (`usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens`) when provided by the backend, returning them alongside the generated text.

### 5.2 Model Registry

The `ModelRegistry` class (`a11y_autofix/llm/registry.py`) implements a named-model registry backed by a YAML configuration file (`models.yaml`). It supports:

- **Static registration** via `models.yaml` — loaded at startup with full model configuration including backend, base URL, temperature, context length, family, size, quantization, and tags.
- **Programmatic registration** via `registry.register(name, config)`.
- **Filtered listing** via `registry.list_models(family, backend, size, tag)`.
- **Group management** — models can be organised into named groups (e.g., `small_models`, `recommended`), which are expanded by the experiment runner.
- **Auto-discovery** — for Ollama backends, `registry.auto_discover()` queries the running Ollama instance's `/v1/models` endpoint and registers all available models.
- **Persistence** — `registry.save_to_yaml()` writes the current in-memory registry back to `models.yaml`.

The `models.yaml` file ships with pre-configured entries for 10 models across five families:

| Family | Models |
|--------|--------|
| Qwen 2.5 Coder | 7B, 14B, 32B |
| DeepSeek Coder V2 | 16B, 236B (MoE) |
| CodeLlama | 7B, 13B, 34B |
| Llama 3.1 | 8B (instruct, Q4_K_M) |
| Codestral | 22B |

---

## 6. Remediation Agents

All agents implement the `BaseAgent` abstract interface (`a11y_autofix/agents/base.py`) and share a common set of utilities:

- **`extract_code_block(text)`**: Extracts code from Markdown fenced code blocks with language specifiers `tsx`, `jsx`, `ts`, `js` (in preference order).
- **`apply_surgical_patches(content, llm_output)`**: Parses `FIND:` / `REPLACE:` block pairs from the LLM response and applies them as surgical text substitutions, enabling targeted single-element modifications without full file rewrites.
- **`validate_tsx_basic(content)`**: Performs syntactic sanity checks (balanced braces, non-empty content) on the proposed patch before acceptance.

### 6.1 OpenHands Agent

`OpenHandsAgent` (`a11y_autofix/agents/openhands.py`) implements a two-stage strategy chain:

**Stage 1 — External agent invocation**: The agent attempts to execute the `openhands` CLI binary as a subprocess, passing the issue description and target file path. The LLM base URL is injected via the `OPENAI_BASE_URL` environment variable, redirecting OpenHands' inference traffic to the locally configured backend.

**Stage 2 — DirectLLM fallback**: If the OpenHands binary is unavailable or returns a non-zero exit code, the agent falls back to `_via_llm_direct()`, which constructs an OpenHands-style prompt and submits it directly to the LLM client.

**Selection rationale**: OpenHands is preferred for issues requiring broad file-context understanding—specifically, contrast violations (which may require modifying design token definitions scattered across the codebase) and semantic HTML issues (which may require restructuring component hierarchies).

### 6.2 SWE-Agent

`SWEAgent` (`a11y_autofix/agents/swe.py`) also implements a two-stage strategy chain:

**Stage 1 — External agent invocation**: Executes the `sweagent` CLI binary with the issue context, similarly injecting the LLM backend URL as an environment variable.

**Stage 2 — DirectLLM with surgical patching**: The fallback constructs an SWE-agent-style prompt requesting `FIND:` / `REPLACE:` format output rather than a full file rewrite. The `apply_surgical_patches()` utility then applies the targeted modifications.

**Selection rationale**: SWE-agent is preferred for localised, attribute-level fixes such as adding `aria-label`, `alt`, or `<label>` elements, where modifying only a small number of lines suffices.

### 6.3 DirectLLM Agent

`DirectLLMAgent` (`a11y_autofix/agents/direct_llm.py`) is the unconditional fallback agent. It constructs a minimal prompt, submits it to the LLM client, extracts the first valid code block from the response, computes a unified diff, and returns a `PatchResult`. It requires no external tooling and is always available.

### 6.4 Prompt Engineering

All prompt templates are centralised in `a11y_autofix/agents/prompts.py`. Each agent type has a dedicated system prompt that establishes the agent's role, constraints, and output format expectations:

- **OpenHands system prompt**: Frames the task as an autonomous agent operating in a file system environment, emphasising multi-file reasoning and design-system awareness.
- **SWE-agent system prompt**: Frames the task as a surgical code-editing operation, specifying the `FIND:` / `REPLACE:` output schema.
- **DirectLLM system prompt**: Minimal framing, requests a complete corrected file in a Markdown code block.

The `build_*_prompt()` functions compose the final user-turn prompt by interpolating:

- The complete source code of the target file
- A formatted issue list with WCAG criterion, selector, impact, confidence, and human-readable message
- The WCAG conformance level target
- File path and language context

The `format_issues()` function supports both verbose and concise rendering modes, controlled by an `include_context` flag.

---

## 7. Routing Engine

The `Router` (`a11y_autofix/router/engine.py`) implements a deterministic scoring matrix that maps the characteristics of a `ScanResult`'s issue set to an agent selection decision. Manual overrides (via `AgentType.OPENHANDS` or `AgentType.SWE_AGENT`) bypass the matrix entirely.

### Scoring Matrix

The score is initialised at zero and modified by additive/subtractive factors:

| Condition | Score Δ | Rationale |
|-----------|---------|-----------|
| Any issue of type `CONTRAST` or `SEMANTIC` | +4 | These types require cross-file context that SWE-agent cannot reliably handle |
| `len(issues) ≥ swe_max_issues` (default: 4) | +4 | High issue volume overwhelms SWE-agent's surgical approach |
| `len(issues) ≥ 2 × swe_max_issues` | +5 (additional) | Very high volume strongly favours comprehensive agent |
| Any issue with `complexity == COMPLEX` | +3 | Complex repairs require broad reasoning |
| `len(distinct issue types) ≥ 3` | +3 | Type diversity indicates systemic accessibility debt requiring holistic treatment |
| All issues are `ARIA`, `LABEL`, or `ALT_TEXT` AND `len(issues) < swe_max_issues` | −3 | Simple attribute additions are SWE-agent's strength |

**Decision rule**: `score ≥ 3` → OpenHands; `score < 3` → SWE-agent.

The scoring rationale is recorded in `RouterDecision.reason` as a human-readable concatenation of the triggering conditions (e.g., `"complex types (contrast, semantic) + 6 issues (threshold: 4) + 2 complex fixes needed"`).

---

## 8. Pipeline Orchestration

The `Pipeline` class (`a11y_autofix/pipeline.py`) is the top-level orchestrator. Its `run()` coroutine executes the following stages:

1. **File discovery**: `find_react_files()` recursively traverses the target paths, collecting files with extensions `.tsx`, `.jsx`, `.ts`, `.js`, excluding standard non-source directories (`node_modules`, `dist`, `build`, `.next`, `.nuxt`, `__pycache__`, `.git`, `coverage`, `.cache`, `.turbo`, `out`, `.svelte-kit`). Discovered paths are deduplicated while preserving order.

2. **Parallel scanning**: `MultiToolScanner.scan_files()` distributes the file list across all enabled runners under an `asyncio.Semaphore(max_concurrent_scans)` bound. Each file's scan runs all four tools concurrently via `asyncio.gather`.

3. **Dry-run path**: If `dry_run=True`, the pipeline returns `FixResult` objects with `final_success=False` and `issues_fixed=0` for all files, without invoking any agent.

4. **Parallel remediation**: Files with detected issues are dispatched to `_fix_file()` under an `asyncio.Semaphore(max_concurrent_agents)` bound.

5. **Per-file retry loop**: `_fix_file()` implements a configurable retry mechanism (up to `max_retries_per_agent`, default 3). On each iteration, the agent receives only the subset of issues not yet marked `resolved`. If a successful patch is returned, the modified file is written to disk and the loop terminates early. All attempt records (including failed ones) are preserved in `FixResult.attempts`.

6. **Report generation**: If `output_dir` is specified, `JSONReporter` and `HTMLReporter` are invoked sequentially to produce the audit trail and visual report.

---

## 9. Experiment Framework

The experiment framework (`a11y_autofix/experiments/`) provides a reproducible, YAML-declarative interface for multi-model comparative studies.

### Experiment Configuration Schema

`ExperimentConfig` (`experiments/config_schema.py`) is a Pydantic model validated from YAML with the following fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | — | Unique experiment identifier |
| `description` | `str` | `""` | Free-text experimental rationale |
| `models` | `list[str]` | — | Model names or group references (expanded at runtime) |
| `files` | `list[str]` | — | File paths, directory paths, or glob patterns |
| `wcag_level` | `str` | `"WCAG2AA"` | Conformance target (normalised from `"AA"` → `"WCAG2AA"`) |
| `tools` | `list[str]` | all available | Scanner subset selection |
| `min_tool_consensus` | `int` | 2 | Override for confidence computation |
| `temperature` | `float` | 0.1 | LLM sampling temperature |
| `max_tokens` | `int` | 4096 | Per-generation token budget |
| `runs_per_model` | `int` | 1 | Number of independent repetitions per model |
| `max_concurrent_models` | `int` | 1 | Degree of model-level parallelism |
| `agent_timeout` | `int` | 180 | Per-agent timeout in seconds |
| `output_dir` | `Path` | auto | Results output directory |

The `resolve_files()` method expands each entry in `files` by testing whether it is an existing file, an existing directory (recursively discovered), or a glob pattern.

### ExperimentRunner Execution Protocol

The `ExperimentRunner` (`experiments/runner.py`) proceeds as follows:

1. Loads and validates the `ExperimentConfig` from YAML.
2. Resolves model group references by expanding named groups from the `ModelRegistry`.
3. Discovers the complete set of target files.
4. Launches per-model pipeline executions concurrently under `asyncio.Semaphore(max_concurrent_models)`.
5. Collects `FixResult` lists from all models, tolerating per-model failures by logging them and recording empty result sets.
6. Computes aggregate metrics via `compute_experiment_metrics()`.
7. Serialises the full `ExperimentResult` to `experiment_result.json`.
8. Invokes `ComparisonReporter` to produce `comparison.html` and `metrics.csv`.

---

## 10. Metrics and Observability

### 10.1 Per-Execution Metrics

The JSON audit trail produced by `JSONReporter` includes the following fields in its `summary` section:

| Metric | Type | Description |
|--------|------|-------------|
| `total_files` | `int` | Number of source files processed |
| `files_with_issues` | `int` | Files containing at least one accessibility violation |
| `total_issues` | `int` | Total violations after cross-tool deduplication |
| `high_confidence_issues` | `int` | Violations confirmed by ≥ `min_tool_consensus` tools |
| `issues_fixed` | `int` | Violations for which a successful patch was produced |
| `issues_pending` | `int` | Violations not resolved after all retry attempts |
| `success_rate` | `float` | `issues_fixed / total_issues × 100` (%) |
| `openhands_used` | `int` | Files dispatched to OpenHands |
| `swe_agent_used` | `int` | Files dispatched to SWE-agent |
| `total_time_seconds` | `float` | Wall-clock time for the entire remediation phase |

At the per-file level, the following are recorded:

| Metric | Description |
|--------|-------------|
| `file_hash` | `sha256:<hex>` of the original file content |
| `scan_time_seconds` | Wall-clock time for multi-tool scanning of this file |
| `tools_used` | List of scanner tools that ran |
| `tool_versions` | Version string of each tool |

At the per-attempt level:

| Metric | Description |
|--------|-------------|
| `attempt_number` | 1-indexed attempt counter |
| `agent` | Agent name (`openhands`, `swe-agent`, `direct-llm`) |
| `model` | LLM model identifier |
| `timestamp` | ISO 8601 UTC timestamp |
| `success` | Boolean outcome |
| `time_seconds` | Wall-clock time for this attempt |
| `tokens_used` | Total tokens consumed (prompt + completion), if reported by backend |
| `diff` | Unified diff of the proposed modification |
| `error` | Error message if `success == false` |

At the per-issue level:

| Metric | Description |
|--------|-------------|
| `issue_id` | 16-character content-addressed identifier |
| `type` | `IssueType` value |
| `wcag_criteria` | WCAG criterion code (e.g., `"1.4.3"`) |
| `complexity` | `simple \| moderate \| complex` |
| `confidence` | `high \| medium \| low` |
| `tool_consensus` | Number of tools that detected this issue |
| `found_by` | List of tool names |
| `impact` | `critical \| serious \| moderate \| minor` |
| `selector` | CSS selector of the violating element |
| `message` | Human-readable violation description |
| `context` | Up to 300 characters of HTML context |
| `resolved` | Whether a fix was successfully applied |

### 10.2 Per-Model Experiment Metrics

`compute_experiment_metrics()` (`experiments/metrics.py`) computes the following aggregate statistics for each model over all processed files:

| Metric | Formula | Description |
|--------|---------|-------------|
| `success_rate` | `files_successful / files_processed × 100` | Percentage of files successfully remediated |
| `avg_time` | `Σ total_time / files_processed` | Mean wall-clock time per file (seconds) |
| `issues_fixed` | `Σ issues_fixed` | Total violations resolved |
| `issues_pending` | `Σ issues_pending` | Total unresolved violations |
| `total_attempts` | `Σ len(attempts)` | Cumulative agent invocations |
| `files_processed` | `len(results)` | Files in the evaluated set |
| `files_successful` | `Σ 1 if final_success` | Files with at least one successful patch |
| `total_tokens` | `Σ tokens_used` | Cumulative LLM tokens consumed (when reported) |
| `avg_tokens` | `total_tokens / total_attempts` | Mean tokens per agent invocation |

### 10.3 Per-Issue-Type Metrics

`compute_per_issue_type_metrics()` stratifies results by `IssueType`, computing for each model and each violation category:

| Metric | Description |
|--------|-------------|
| `total` | Issues of this type encountered |
| `fixed` | Issues of this type successfully resolved |
| `rate` | `fixed / total × 100` (%) |

This decomposition enables ablation analysis: e.g., determining whether a given model's low overall success rate is attributable specifically to contrast violations (inherently more difficult) rather than to ARIA attribute issues (typically simpler).

### 10.4 Model Ranking

`rank_models()` produces a sorted list of `(model_name, metric_value)` tuples. For all metrics except `avg_time`, higher values rank better. For `avg_time`, lower values rank better (inverse sort). The primary sort metric defaults to `success_rate` but can be set to any key present in the metrics dictionary.

---

## 11. Reproducibility Guarantees

The system provides the following reproducibility properties:

1. **Content-addressed file identity**: The SHA-256 hash of a file's byte content (`file_hash`) uniquely identifies its state, independent of filesystem metadata. Comparing `file_hash` values between two runs unambiguously determines whether the source file was modified externally.

2. **Content-addressed issue identity**: `issue_id = SHA-256(file + selector + wcag_criteria + issue_type)[:16]` is deterministic. The same logical violation in the same file always receives the same ID across runs, enabling longitudinal tracking of whether specific violations were resolved.

3. **Execution identity**: Each pipeline invocation receives a UUID v4 `execution_id` that serves as a globally unique experiment identifier, stored in the JSON report for unambiguous run identification.

4. **Tool version recording**: The version string of every scanner tool is recorded in `ScanResult.tool_versions` and propagated to the JSON audit trail under `environment.tool_versions`. Replicating an experiment on a different machine requires matching these versions.

5. **Environment recording**: The JSON report captures `python_version`, `os`, `llm_model`, and all tool versions, providing a complete specification of the execution environment.

6. **Configuration snapshotting**: `ExperimentResult.config_snapshot` stores a serialised copy of the `ExperimentConfig` Pydantic model used for that run, enabling exact reconstruction of experimental parameters.

7. **Deterministic sort order**: The multi-criterion sorting key described in §3.7 ensures that the ordered issue list is identical across runs given identical inputs, removing any dependency on Python's implementation-defined dictionary or set iteration order.

8. **Low-temperature inference**: The default `temperature=0.1` configuration reduces (but does not eliminate) non-determinism in LLM outputs. For maximum reproducibility, `temperature=0.0` can be used where supported by the backend.

9. **Structured logging**: All pipeline events are emitted as JSON-structured log records via `structlog`, providing a machine-readable execution trace for post-hoc analysis.

---

## 12. Report Formats

### JSON Audit Trail (`report.json`)

Schema version `2.0`. Complete machine-readable record of a pipeline execution. Top-level structure:

```json
{
  "schema_version": "2.0",
  "protocol_version": "1.0",
  "execution_id": "<uuid-v4>",
  "timestamp": "<ISO-8601-UTC>",
  "wcag_level": "WCAG2AA",
  "environment": {
    "python_version": "3.12.0",
    "os": "Darwin 24.2.0",
    "llm_model": "qwen2.5-coder:7b",
    "tool_versions": { "pa11y": "6.2.3", "axe": "4.9.1", ... }
  },
  "configuration": {
    "min_tool_consensus": 2,
    "swe_max_issues": 4,
    "max_retries": 3
  },
  "summary": { ... },
  "files": [ { "file": "...", "file_hash": "sha256:...", "issues": [...], "fix": {...} } ]
}
```

### HTML Visual Report (`report.html`)

Jinja2-rendered HTML document containing:
- Summary statistics grid
- Per-file cards with issue lists (selector, WCAG criterion, confidence badge, impact badge)
- Collapsible unified-diff blocks with syntax highlighting for each fix attempt

### Comparison Report (`comparison.html`, `metrics.csv`)

Generated by `ComparisonReporter` after multi-model experiments:
- **HTML**: Ranking table with progress bars, per-model success rate/time/token visualisations, per-file result grid
- **CSV**: Flat table with columns `model`, `success_rate`, `avg_time`, `issues_fixed`, `total_tokens`, `avg_tokens`, `run`, suitable for direct import into R, pandas, or statistical software

---

## 13. Configuration Reference

Runtime configuration is managed through Pydantic-settings (`Settings` class), reading from a `.env` file with the following precedence: environment variables > `.env` file > default values.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DEFAULT_MODEL` | `str` | `qwen2.5-coder-7b` | Model name from registry |
| `USE_PA11Y` | `bool` | `true` | Enable pa11y scanner |
| `USE_AXE` | `bool` | `true` | Enable axe-core scanner |
| `USE_LIGHTHOUSE` | `bool` | `false` | Enable Lighthouse scanner (slower) |
| `USE_PLAYWRIGHT` | `bool` | `true` | Enable Playwright+axe scanner |
| `MIN_TOOL_CONSENSUS` | `int` | `2` | Minimum tools for HIGH confidence |
| `MAX_CONCURRENT_SCANS` | `int` | `4` | File-level scan parallelism |
| `MAX_CONCURRENT_AGENTS` | `int` | `2` | Agent-level parallelism |
| `MAX_CONCURRENT_MODELS` | `int` | `3` | Model-level experiment parallelism |
| `SCAN_TIMEOUT` | `int` | `60` | Per-file per-tool timeout (seconds) |
| `AGENT_TIMEOUT` | `int` | `180` | Per-agent invocation timeout (seconds) |
| `SWE_MAX_ISSUES` | `int` | `4` | Router threshold for OpenHands vs SWE-agent |
| `OPENHANDS_COMPLEXITY_THRESHOLD` | `int` | `3` | Minimum COMPLEX issues to force OpenHands |
| `MAX_RETRIES_PER_AGENT` | `int` | `3` | Retry budget per file |
| `OPENHANDS_URL` | `str` | `http://localhost:3000` | OpenHands server base URL |
| `SWE_CLI_PATH` | `str` | `sweagent` | SWE-agent CLI binary path |
| `OUTPUT_DIR` | `Path` | `./a11y-report` | Default report output directory |
| `EXPERIMENTS_DIR` | `Path` | `./experiments` | Experiment YAML directory |
| `RESULTS_DIR` | `Path` | `./experiment-results` | Experiment results root |
| `LOG_LEVEL` | `str` | `INFO` | Logging verbosity |
| `ENABLE_BENCHMARKING` | `bool` | `false` | Enable internal timing instrumentation |

---

## 14. Command-Line Interface

The CLI is implemented with Typer and Rich (`a11y_autofix/cli.py`), providing the following command groups:

### `a11y-autofix fix <target>`

Executes the full scan-and-remediate pipeline on one or more targets.

| Option | Default | Description |
|--------|---------|-------------|
| `--model / -m` | `DEFAULT_MODEL` | LLM model identifier |
| `--output / -o` | None | Report output directory |
| `--tools / -t` | all | Scanner tools to use (repeatable) |
| `--wcag-level / -w` | `AA` | WCAG conformance level |
| `--dry-run` | `false` | Scan without applying fixes |
| `--create-branch` | None | Git branch name for fix commit |
| `--create-pr` | `false` | Create GitHub Pull Request via `gh` CLI |
| `--max-retries` | `3` | Retry budget per file |
| `--workers` | `4` | File-level parallelism |
| `--verbose / -v` | `false` | Detailed output |

### `a11y-autofix experiment <config>`

Executes a YAML-configured multi-model experiment.

| Option | Default | Description |
|--------|---------|-------------|
| `--output / -o` | from YAML | Results directory override |
| `--verbose / -v` | `false` | Detailed output |

### `a11y-autofix models <subcommand>`

Manages the model registry. Subcommands: `list`, `test`, `info`, `add`, `discover`.

### `a11y-autofix scanners list`

Lists all configured scanner runners with their availability status and version.

### `a11y-autofix analyze <reports...>`

Reads one or more JSON report files and prints comparative statistics in tabular or JSON format.

### `a11y-autofix setup`

Validates the environment and installs Node.js tools (pa11y, @axe-core/cli, lighthouse) and Playwright Chromium.

---

## 15. Testing Infrastructure

The test suite (`tests/`) is organised into unit and integration levels, implemented with pytest and `unittest.mock`.

### Unit Tests

| Module | Test Class | Coverage Focus |
|--------|-----------|----------------|
| `test_protocol.py` | `TestDetectionProtocol` | Deduplication correctness, hash stability, confidence levels, WCAG mapping, deterministic ordering |
| `test_router.py` | `TestRouter` | All scoring matrix branches: contrast/semantic → OpenHands, ARIA/label/alt-text → SWE-agent, volume thresholds, manual overrides |
| `test_llm_registry.py` | `TestModelRegistry` | Register/get, unknown model exception, `get_client` return type, list filtering by family/backend/tag, YAML round-trip, group management |
| `test_experiments.py` | `TestExperimentMetrics` | 0%/50%/100% success rates, `avg_time` computation, `issues_fixed` aggregation, empty result sets, model ranking (ascending/descending) |

### Integration Tests

`test_full_pipeline.py` exercises the complete pipeline against the bundled React fixture components (`tests/fixtures/sample_components/`), with all scanner tools mocked to return known findings, and the LLM client mocked to return a valid corrected code block. Verifies end-to-end data flow from `Pipeline.run()` through to `FixResult` construction.

### Test Fixtures

| File | Intentional Violations |
|------|----------------------|
| `Button.tsx` | Missing `aria-label` on icon button; insufficient colour contrast |
| `Form.tsx` | Form inputs without `<label>`; `<img>` without `alt`; non-semantic `<div>` acting as button |

---

## 16. Installation and Quick Start

**Prerequisites**: Python ≥ 3.10, Node.js ≥ 18, and at least one local LLM backend.

```bash
# Clone and install
git clone <repository-url>
cd a11y-autofix
pip install -e ".[dev]"

# Install Node.js tooling and Playwright
a11y-autofix setup

# Pull a language model (Ollama example)
ollama pull qwen2.5-coder:7b

# Verify scanner availability
a11y-autofix scanners list

# Scan and remediate a component directory
a11y-autofix fix ./src --model qwen2.5-coder:7b --output ./reports

# Run a comparative experiment
a11y-autofix experiment experiments/qwen_vs_deepseek.yaml
```

---

## 17. Extensibility

### Adding a New Scanner Tool

1. Create `a11y_autofix/scanner/<tool_name>.py` with a class inheriting `BaseScanner`.
2. Implement `check_available()` (subprocess version check) and `run(html_path)` (subprocess invocation + JSON parsing + WCAG mapping).
3. Register the runner in `MultiToolScanner._build_runners()` and add a corresponding `Settings` field and `.env` variable.

### Adding a New LLM Backend

1. Create `a11y_autofix/llm/backends/<backend_name>.py` implementing `BaseLLMClient`.
2. Add the backend value to the `LLMBackend` enum in `config.py`.
3. Register the concrete class in the backend dispatch table in `client.py`.

### Adding a New Model

Edit `models.yaml` — no code modification required:

```yaml
models:
  my-new-model:
    backend: ollama
    model_id: "my-model:7b"
    family: my-family
    size: "7b"
    temperature: 0.1
    max_tokens: 8192
    tags: [coding]
```

---

## 18. Supported WCAG Criteria

The system provides explicit detection-to-remediation coverage for the following WCAG 2.x success criteria, organised by principle:

**Perceivable (1.x)**: 1.1.1, 1.2.1–1.2.5, 1.3.1–1.3.6, 1.4.1–1.4.6, 1.4.10–1.4.13

**Operable (2.x)**: 2.1.1–2.1.4, 2.4.1–2.4.4, 2.4.6–2.4.7, 2.4.10–2.4.12

**Understandable (3.x)**: 3.1.1–3.1.2, 3.2.1–3.2.2, 3.3.1–3.3.2

**Robust (4.x)**: 4.1.1–4.1.3

Tool-specific rule IDs from axe-core (50+ rules), pa11y (HTML_CS rules), and Lighthouse audits are mapped to the appropriate criterion where WCAG codes are not directly provided by the tool.

---

## 19. Limitations and Threats to Validity

**Dynamic content**: The HTML harness approach evaluates the initial render state of a component. Accessibility violations that manifest only after user interaction (e.g., modal dialogs, dynamic form validation messages, focus traps) may not be detected.

**Component isolation**: Components that depend on complex React contexts, Redux stores, or custom providers may fail to render correctly in the harness, producing false negatives. The harness provides mocks only for the most common dependency patterns (react-router-dom, clsx).

**Selector stability**: CSS selectors generated by CSS-in-JS libraries (e.g., Styled Components, Emotion) that include content hashes may vary between renders, potentially degrading deduplication accuracy.

**Contrast analysis accuracy**: Browser-based contrast calculation tools may produce inaccurate results for elements with non-solid backgrounds (gradients, images, CSS `background-blend-mode`). Static CSS analysis without rendering context is inherently limited for this criterion.

**Patch correctness verification**: The system currently has no post-fix re-scanning loop. Whether a generated patch actually resolves the detected violation is not verified programmatically; this is a deliberate design choice that simplifies the experimental apparatus but means that `success` in the context of `FixResult` denotes that the agent returned a syntactically plausible patch, not that the patch is semantically correct.

**LLM non-determinism**: Even at `temperature=0.0`, some backends (particularly those using quantised models) may exhibit minor non-determinism due to floating-point rounding in GPU kernels. Multiple runs at identical configuration may produce marginally different outputs.

**Scope**: The system targets React and TypeScript/JavaScript source files. Server-side rendered frameworks, Vue, Angular, or Svelte components are not within the primary scope, though the HTML harness approach is extensible to other JSX-producing frameworks.

---

## 20. Citation

If this framework is used in academic work, please cite:

```bibtex
@software{a11y_autofix_2024,
  title        = {{a11y-autofix}: A Multi-Model, Multi-Tool Framework for
                  Automated Accessibility Remediation in React/TypeScript Codebases},
  year         = {2024},
  note         = {Master's research instrument. 100\% local inference, WCAG 2.1/2.2,
                  reproducible via SHA-256 content addressing.
                  Source: \url{<repository-url>}}
}
```

---

## Appendix A: Project Structure

```
a11y-autofix/
├── a11y_autofix/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py                   # All Pydantic models and enums
│   ├── pipeline.py                 # Top-level orchestrator
│   ├── cli.py                      # Typer CLI entry point
│   ├── protocol/
│   │   └── detection.py            # Scientific detection protocol
│   ├── scanner/
│   │   ├── base.py                 # BaseScanner ABC
│   │   ├── orchestrator.py         # MultiToolScanner
│   │   ├── pa11y.py
│   │   ├── axe.py
│   │   ├── lighthouse.py
│   │   └── playwright_axe.py
│   ├── llm/
│   │   ├── base.py                 # BaseLLMClient ABC
│   │   ├── client.py               # LocalLLMClient (httpx, OpenAI-compatible)
│   │   ├── registry.py             # ModelRegistry (YAML-backed)
│   │   └── backends/
│   │       ├── ollama.py
│   │       ├── lm_studio.py
│   │       ├── vllm.py
│   │       ├── llamacpp.py
│   │       └── custom.py
│   ├── agents/
│   │   ├── base.py                 # BaseAgent ABC + shared utilities
│   │   ├── prompts.py              # All prompt templates
│   │   ├── openhands.py
│   │   ├── swe.py
│   │   └── direct_llm.py
│   ├── router/
│   │   └── engine.py               # Scoring matrix router
│   ├── experiments/
│   │   ├── config_schema.py        # ExperimentConfig (Pydantic + YAML)
│   │   ├── metrics.py              # Metric computation and ranking
│   │   └── runner.py               # ExperimentRunner
│   ├── reporter/
│   │   ├── json_reporter.py        # Audit trail JSON
│   │   ├── html_reporter.py        # Visual HTML report
│   │   └── comparison_reporter.py  # Multi-model comparison HTML + CSV
│   └── utils/
│       ├── files.py                # File discovery + HTML harness
│       ├── hashing.py              # SHA-256 utilities
│       ├── git.py                  # Git branch/commit/PR operations
│       └── ui.py                   # Rich terminal components
├── tests/
│   ├── unit/
│   │   ├── test_protocol.py
│   │   ├── test_router.py
│   │   ├── test_llm_registry.py
│   │   └── test_experiments.py
│   ├── integration/
│   │   └── test_full_pipeline.py
│   └── fixtures/
│       └── sample_components/
│           ├── Button.tsx           # WCAG violations: contrast, aria-label
│           └── Form.tsx             # WCAG violations: label, alt-text, semantic
├── experiments/
│   ├── qwen_vs_deepseek.yaml
│   ├── all_models_comparison.yaml
│   └── ablation_study.yaml
├── docs/
│   ├── PROTOCOL.md
│   ├── ADDING_MODELS.md
│   ├── ADDING_TOOLS.md
│   └── EXPERIMENTS.md
├── scripts/
│   ├── setup.py
│   └── download_models.py
├── models.yaml                      # Model registry (10 models, 6 groups)
├── .env.example
├── pyproject.toml                   # PEP 517/518, ruff, mypy, pytest config
└── COMO_RODAR.md                    # Portuguese user guide
```

---

## Appendix B: Dependency Summary

| Package | Version Constraint | Role |
|---------|-------------------|------|
| `pydantic` | ≥ 2.7 | Data validation and serialisation |
| `pydantic-settings` | ≥ 2.3 | Environment-based configuration |
| `typer` | ≥ 0.12 | CLI framework |
| `rich` | ≥ 13.7 | Terminal formatting and progress |
| `httpx` | ≥ 0.27 | Async HTTP client for LLM backends |
| `playwright` | ≥ 1.45 | Chromium automation for dynamic scan |
| `structlog` | ≥ 24.1 | Structured logging (JSON output) |
| `pyyaml` | ≥ 6.0 | YAML configuration parsing |
| `jinja2` | ≥ 3.1 | HTML report templating |
| `pytest` | ≥ 8.2 (dev) | Test runner |
| `pytest-asyncio` | ≥ 0.23 (dev) | Async test support |
| `pytest-cov` | ≥ 5.0 (dev) | Coverage measurement |
| `ruff` | ≥ 0.4 (dev) | Linter and formatter |
| `mypy` | ≥ 1.10 (dev) | Static type checker |

Node.js runtime dependencies (installed globally via npm): `pa11y ≥ 6`, `@axe-core/cli ≥ 4`, `lighthouse ≥ 12`.
