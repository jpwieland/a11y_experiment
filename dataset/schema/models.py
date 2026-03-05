"""
Pydantic v2 data models for the a11y-autofix benchmark dataset.

These models define the canonical representation of all dataset artefacts:
project catalog entries, scan findings, ground-truth annotations,
inter-annotator agreement records, and dataset-level metadata.

All models are designed for bidirectional YAML/JSON serialisation
and are validated on construction to prevent catalog corruption.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════════════
# Enumerations
# ═══════════════════════════════════════════════════════════════════════════════


class ProjectDomain(str, Enum):
    """Application domain stratum for stratified sampling."""
    ECOMMERCE = "ecommerce"
    GOVERNMENT = "government"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    DEVELOPER_TOOLS = "developer_tools"
    DASHBOARD = "dashboard"
    SOCIAL = "social"
    OTHER = "other"


class ProjectSize(str, Enum):
    """Codebase size stratum (by JSX/TSX file count)."""
    SMALL = "small"        # 10–50 files
    MEDIUM = "medium"      # 51–300 files
    LARGE = "large"        # 301+ files


class ProjectPopularity(str, Enum):
    """GitHub stars popularity stratum."""
    EMERGING = "emerging"      # 100–999 stars
    ESTABLISHED = "established"  # 1,000–9,999 stars
    POPULAR = "popular"        # ≥ 10,000 stars


class ProjectStatus(str, Enum):
    """Lifecycle stage of a dataset entry."""
    CANDIDATE = "candidate"    # Discovered, not yet screened
    PENDING = "pending"        # Passed automated screening, awaiting manual review
    SNAPSHOTTED = "snapshotted"  # Cloned and commit-pinned
    SCANNED = "scanned"        # Multi-tool scan completed
    ANNOTATED = "annotated"    # Ground truth annotation complete
    EXCLUDED = "excluded"      # Failed screening or scan validity check
    ERROR = "error"            # Unrecoverable processing error


class AnnotationLabel(str, Enum):
    """Human annotator verdict for a disputed finding."""
    CONFIRMED = "CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    UNCERTAIN = "UNCERTAIN"


class InclusionStatus(str, Enum):
    """Result of inclusion/exclusion criterion check."""
    PASS = "pass"
    FAIL = "fail"
    NOT_CHECKED = "not_checked"


# ═══════════════════════════════════════════════════════════════════════════════
# GitHub Metadata
# ═══════════════════════════════════════════════════════════════════════════════


class GitHubMetadata(BaseModel):
    """Snapshot of GitHub repository metadata at crawl time."""

    stars: int = Field(default=0, ge=0, description="stargazers_count from GitHub API")
    forks: int = Field(default=0, ge=0)
    open_issues: int = Field(default=0, ge=0)
    watchers: int = Field(default=0, ge=0)
    language: str = Field(default="TypeScript")
    topics: list[str] = Field(default_factory=list)
    license_spdx: str = Field(default="", description="SPDX license identifier, e.g. MIT")
    default_branch: str = Field(default="main")
    created_at: str = Field(default="", description="ISO 8601 repository creation date")
    pushed_at: str = Field(default="", description="ISO 8601 date of last push")
    description: str = Field(default="")
    homepage: str = Field(default="")
    archived: bool = Field(default=False)
    fork: bool = Field(default=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Snapshot Metadata
# ═══════════════════════════════════════════════════════════════════════════════


class SnapshotMetadata(BaseModel):
    """Commit-pinning and clone metadata for reproducibility."""

    pinned_commit: str = Field(
        default="",
        description="Full 40-character SHA-1 of the pinned HEAD commit",
        pattern=r"^([0-9a-f]{40})?$",
    )
    snapshot_date: str = Field(
        default="",
        description="ISO 8601 UTC timestamp of when the snapshot was taken",
    )
    branch: str = Field(default="main", description="Default branch at time of snapshot")
    react_version: str = Field(default="", description="React version from package.json")
    typescript_version: str = Field(default="", description="TypeScript version from package.json")
    typescript: bool = Field(default=True)
    component_file_count: int = Field(
        default=0,
        ge=0,
        description="Number of .tsx/.jsx files discovered in scan_paths",
    )
    clone_size_mb: float = Field(default=0.0, description="Shallow clone size in MB")


# ═══════════════════════════════════════════════════════════════════════════════
# Screening Record
# ═══════════════════════════════════════════════════════════════════════════════


class ScreeningRecord(BaseModel):
    """
    Record of inclusion/exclusion criterion evaluation.

    Each criterion (IC1–IC7, EC1–EC7) is recorded with its outcome.
    This provides a complete audit trail for corpus selection decisions.
    """

    # Inclusion criteria
    ic1_stars: InclusionStatus = InclusionStatus.NOT_CHECKED
    ic2_last_commit: InclusionStatus = InclusionStatus.NOT_CHECKED
    ic3_license: InclusionStatus = InclusionStatus.NOT_CHECKED
    ic4_component_files: InclusionStatus = InclusionStatus.NOT_CHECKED
    ic5_buildability: InclusionStatus = InclusionStatus.NOT_CHECKED
    ic6_non_generated: InclusionStatus = InclusionStatus.NOT_CHECKED
    ic7_ui_rendering: InclusionStatus = InclusionStatus.NOT_CHECKED

    # Exclusion criteria
    ec1_starter_template: InclusionStatus = InclusionStatus.NOT_CHECKED
    ec2_course_project: InclusionStatus = InclusionStatus.NOT_CHECKED
    ec3_duplicate: InclusionStatus = InclusionStatus.NOT_CHECKED
    ec4_archived: InclusionStatus = InclusionStatus.NOT_CHECKED
    ec5_private_dependency: InclusionStatus = InclusionStatus.NOT_CHECKED
    ec6_non_browser: InclusionStatus = InclusionStatus.NOT_CHECKED
    ec7_generated_ui: InclusionStatus = InclusionStatus.NOT_CHECKED

    # Failure reason (for excluded projects)
    exclusion_criterion: str = Field(
        default="",
        description="Code of the criterion that caused exclusion, e.g. 'EC1'",
    )
    exclusion_reason: str = Field(default="", description="Human-readable exclusion justification")

    @property
    def passes_all(self) -> bool:
        """True iff all checked inclusion criteria pass and no exclusion criterion fires."""
        inclusion_fields = [
            self.ic1_stars, self.ic2_last_commit, self.ic3_license,
            self.ic4_component_files, self.ic5_buildability,
        ]
        exclusion_fields = [
            self.ec1_starter_template, self.ec2_course_project,
            self.ec3_duplicate, self.ec4_archived,
        ]
        all_ic_pass = all(
            s in (InclusionStatus.PASS, InclusionStatus.NOT_CHECKED)
            for s in inclusion_fields
        )
        no_ec_fires = all(
            s in (InclusionStatus.PASS, InclusionStatus.NOT_CHECKED)
            for s in exclusion_fields
        )
        return all_ic_pass and no_ec_fires


# ═══════════════════════════════════════════════════════════════════════════════
# Scan Summary
# ═══════════════════════════════════════════════════════════════════════════════


class FindingSummary(BaseModel):
    """Aggregated finding counts for a scanned project."""

    total_issues: int = Field(default=0, ge=0)
    high_confidence: int = Field(default=0, ge=0, description="tool_consensus ≥ 2")
    medium_confidence: int = Field(default=0, ge=0)
    low_confidence: int = Field(default=0, ge=0)

    # By IssueType
    by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Issue count keyed by IssueType value (aria, contrast, ...)",
    )

    # By WCAG principle
    by_principle: dict[str, int] = Field(
        default_factory=dict,
        description="Issue count keyed by WCAG principle: perceivable, operable, understandable, robust",
    )

    # By impact
    by_impact: dict[str, int] = Field(
        default_factory=dict,
        description="Issue count keyed by impact: critical, serious, moderate, minor",
    )

    # By WCAG criterion
    by_criterion: dict[str, int] = Field(
        default_factory=dict,
        description="Issue count keyed by WCAG criterion code, e.g. '1.4.3'",
    )

    # Files with issues
    files_scanned: int = Field(default=0, ge=0)
    files_with_issues: int = Field(default=0, ge=0)

    # Tools
    tools_succeeded: list[str] = Field(default_factory=list)
    tools_failed: list[str] = Field(default_factory=list)
    tool_versions: dict[str, str] = Field(default_factory=dict)

    scan_duration_seconds: float = Field(default=0.0)
    scan_date: str = Field(default="")


class ProjectScanSummary(BaseModel):
    """Scan execution status and aggregated findings for a project."""

    status: str = Field(
        default="pending",
        description="pending | success | partial | error",
    )
    findings: FindingSummary = Field(default_factory=FindingSummary)
    error_message: str = Field(default="")


# ═══════════════════════════════════════════════════════════════════════════════
# Individual Scan Finding (raw, pre-annotation)
# ═══════════════════════════════════════════════════════════════════════════════


class ScanFinding(BaseModel):
    """
    Individual accessibility finding from the multi-tool scanner.
    Stored per-project in dataset/results/<id>/findings.json.
    """

    finding_id: str = Field(description="16-char SHA-256 content-addressed ID")
    project_id: str = Field(description="<owner>__<repo>")
    file: str = Field(description="Relative path from repo root")
    selector: str
    message: str
    wcag_criteria: str | None = None
    rule_id: str = Field(default="")
    issue_type: str = Field(default="other")
    impact: str = Field(default="moderate")
    complexity: str = Field(default="moderate")

    # Multi-tool consensus
    tool_consensus: int = Field(default=1, ge=1)
    found_by: list[str] = Field(default_factory=list)
    confidence: str = Field(default="low")

    # Raw findings from each tool
    raw_findings: list[dict[str, Any]] = Field(default_factory=list)

    # Snapshot context
    pinned_commit: str = Field(default="")
    scan_date: str = Field(default="")


# ═══════════════════════════════════════════════════════════════════════════════
# Ground Truth Annotation
# ═══════════════════════════════════════════════════════════════════════════════


class GroundTruthFinding(BaseModel):
    """
    Ground-truth annotation record for an accessibility finding.

    Findings with tool_consensus ≥ 2 are automatically CONFIRMED
    without human review. Single-tool findings require annotation
    by two independent expert reviewers.
    """

    finding_id: str
    project_id: str
    file: str
    selector: str
    wcag_criteria: str | None = None
    issue_type: str
    impact: str
    complexity: str
    confidence: str
    tool_consensus: int

    # Auto-accepted (tool_consensus ≥ 2) or human-annotated
    auto_accepted: bool = Field(
        default=False,
        description="True if accepted automatically via tool consensus ≥ 2",
    )
    auto_accept_basis: str = Field(
        default="",
        description=(
            "Human-readable rationale for auto-acceptance. "
            "Set to '[AUTO-ACCEPTED — detected by ≥2 independent scanners, High confidence]' "
            "when auto_accepted is True. Empty for human-annotated findings."
        ),
    )

    # Human annotation (only for tool_consensus == 1)
    ground_truth_label: AnnotationLabel = AnnotationLabel.UNCERTAIN
    annotator_1_label: str = Field(default="")
    annotator_1_id: str = Field(default="", description="Anonymised annotator identifier")
    annotator_2_label: str = Field(default="")
    annotator_2_id: str = Field(default="")
    agreement: bool = Field(default=False)
    annotation_notes: str = Field(default="")
    annotation_date: str = Field(default="")

    @model_validator(mode="after")
    def set_auto_label(self) -> "GroundTruthFinding":
        """Auto-accept findings confirmed by multiple independent tools."""
        if self.tool_consensus >= 2 and not self.annotator_1_label:
            self.auto_accepted = True
            self.ground_truth_label = AnnotationLabel.CONFIRMED
            self.agreement = True
        return self

    @property
    def is_confirmed(self) -> bool:
        return self.ground_truth_label == AnnotationLabel.CONFIRMED

    @property
    def is_evaluable(self) -> bool:
        """True if finding should be included in primary evaluation."""
        return self.ground_truth_label == AnnotationLabel.CONFIRMED


# ═══════════════════════════════════════════════════════════════════════════════
# Project Catalog Entry
# ═══════════════════════════════════════════════════════════════════════════════


class ProjectEntry(BaseModel):
    """
    Complete catalog entry for one project in the benchmark corpus.

    Represents all information about a project from initial discovery
    through scanning and annotation. This is the central record that
    all dataset scripts read and update.
    """

    # Identity
    id: str = Field(
        description="Unique identifier: <owner>__<repo> (double underscore)",
        pattern=r"^[a-zA-Z0-9_.-]+__[a-zA-Z0-9_.-]+$",
    )
    owner: str
    repo: str
    github_url: str = Field(description="https://github.com/<owner>/<repo>")

    # Stratification
    domain: ProjectDomain
    size_category: ProjectSize = Field(default=ProjectSize.MEDIUM)
    popularity_tier: ProjectPopularity = Field(default=ProjectPopularity.EMERGING)

    # Inclusion rationale (required for transparency)
    inclusion_rationale: str = Field(
        description="Human-written justification for including this project in the corpus",
    )

    # Scan configuration
    scan_paths: list[str] = Field(
        default_factory=lambda: ["src/"],
        description="Relative paths within the repo to scan for components",
    )
    exclude_paths: list[str] = Field(
        default_factory=lambda: [
            "node_modules/", "dist/", "build/", ".next/",
            "coverage/", "storybook-static/", "**/*.test.tsx",
            "**/*.spec.tsx", "**/*.stories.tsx",
        ],
    )

    # Status lifecycle
    status: ProjectStatus = Field(default=ProjectStatus.CANDIDATE)

    # Sub-records populated progressively
    github: GitHubMetadata = Field(default_factory=GitHubMetadata)
    snapshot: SnapshotMetadata = Field(default_factory=SnapshotMetadata)
    screening: ScreeningRecord = Field(default_factory=ScreeningRecord)
    scan: ProjectScanSummary = Field(default_factory=ProjectScanSummary)

    # Annotation summary
    annotation_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Populated by annotate.py: total, confirmed, false_positives, uncertain, kappa",
    )

    @field_validator("id")
    @classmethod
    def id_must_match_owner_repo(cls, v: str, info: Any) -> str:
        """Verify that id equals <owner>__<repo>."""
        data = info.data
        if "owner" in data and "repo" in data:
            expected = f"{data['owner']}__{data['repo']}"
            if v != expected:
                raise ValueError(f"id must equal '<owner>__<repo>' = '{expected}', got '{v}'")
        return v

    @field_validator("github_url")
    @classmethod
    def url_must_contain_owner_repo(cls, v: str, info: Any) -> str:
        data = info.data
        if "owner" in data and "repo" in data:
            expected_suffix = f"{data['owner']}/{data['repo']}"
            if expected_suffix not in v:
                raise ValueError(f"github_url must contain '{expected_suffix}'")
        return v

    def to_catalog_dict(self) -> dict[str, Any]:
        """Serialise to a clean dict suitable for YAML catalog storage.

        Uses mode="json" so Pydantic converts Enum instances to their string
        values before the dict reaches yaml.dump().  Without this, PyYAML
        emits !!python/object/apply: tags that yaml.safe_load() cannot parse.
        """
        return self.model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=False,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Dataset-level Metadata
# ═══════════════════════════════════════════════════════════════════════════════


class AnnotationAgreement(BaseModel):
    """Inter-annotator agreement statistics for the annotation phase."""

    annotator_1_id: str = Field(default="A1")
    annotator_2_id: str = Field(default="A2")
    total_items: int = Field(default=0)
    agreed_items: int = Field(default=0)
    kappa: float = Field(default=0.0, description="Cohen's κ")
    kappa_interpretation: str = Field(default="")
    computed_at: str = Field(default="")


class DatasetSplit(BaseModel):
    """Train/validation/test split (if used for ML evaluation)."""

    train: list[str] = Field(default_factory=list, description="Project IDs in train split")
    validation: list[str] = Field(default_factory=list)
    test: list[str] = Field(default_factory=list)
    split_strategy: str = Field(default="stratified_random")
    split_date: str = Field(default="")


class DatasetMetadata(BaseModel):
    """
    Dataset-level metadata record stored in dataset/metadata/dataset_info.json.

    Captures the aggregate state of the corpus: version, statistics,
    tool versions, construction timeline, and quality indicators.
    """

    version: str = Field(default="1.0.0")
    protocol_version: str = Field(default="1.0")
    schema_version: str = Field(default="1.0")

    # Construction timeline
    discovery_date: str = Field(default="", description="ISO 8601 UTC date of initial GitHub crawl")
    snapshot_date: str = Field(default="", description="ISO 8601 UTC date snapshots were taken")
    scan_date: str = Field(default="", description="ISO 8601 UTC date scans were run")
    annotation_date: str = Field(default="", description="ISO 8601 UTC date annotation was completed")
    release_date: str = Field(default="")

    # Corpus statistics
    total_projects_discovered: int = Field(default=0)
    total_projects_screened: int = Field(default=0)
    total_projects_excluded: int = Field(default=0)
    total_projects_final: int = Field(default=0)

    # Finding statistics
    total_raw_findings: int = Field(default=0)
    total_confirmed_findings: int = Field(default=0)
    total_false_positives: int = Field(default=0)
    total_uncertain: int = Field(default=0)
    false_positive_rate: float = Field(default=0.0)

    # Tool versions (frozen at scan time)
    tool_versions: dict[str, str] = Field(
        default_factory=dict,
        description="e.g. {'pa11y': '6.2.3', 'axe': '4.9.1', ...}",
    )
    python_version: str = Field(default="")
    node_version: str = Field(default="")

    # Quality indicators
    annotation_agreement: AnnotationAgreement = Field(
        default_factory=AnnotationAgreement,
    )
    wcag_principle_coverage: dict[str, int] = Field(
        default_factory=dict,
        description="Confirmed findings per principle: perceivable, operable, understandable, robust",
    )
    issue_type_coverage: dict[str, int] = Field(
        default_factory=dict,
        description="Confirmed findings per IssueType value",
    )
    domain_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Project count per domain stratum",
    )

    # Dataset split
    split: DatasetSplit = Field(default_factory=DatasetSplit)

    # Checksums for integrity verification
    catalog_sha256: str = Field(default="", description="SHA-256 of projects.yaml")
    findings_sha256: str = Field(default="", description="SHA-256 of concatenated findings JSON")
