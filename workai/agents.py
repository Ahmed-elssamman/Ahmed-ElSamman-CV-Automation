"""Bounded specialist responsibilities and their executable implementation targets."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Agent:
    name: str
    responsibility: str
    implementation: str


AGENTS = (
    Agent("Document Ingestion", "Preserve and extract source PDFs", "workai.profile.ingest_pdfs"),
    Agent("CV Structuring", "Normalize source claims with evidence", "workai.profile.normalize_profile"),
    Agent("Master Profile", "Load and validate canonical knowledge", "workai.profile.validate_profile"),
    Agent("LaTeX CV", "Render source-backed CV text", "workai.cv.render_latex"),
    Agent("Job Discovery", "Fetch public configured job sources", "workai.jobs.discover_jobs"),
    Agent("Job Analysis", "Extract requirements and hard eligibility", "workai.jobs.analyze_job"),
    Agent("Company Research", "Record dated, sourced company claims", "workai.jobs.research_company"),
    Agent("ATS Analysis", "Explain estimated description alignment", "workai.jobs.ats_analysis"),
    Agent("CV Tailoring", "Rank evidenced skills and bullets", "workai.cv.tailor_cv"),
    Agent("PDF Generation", "Compile and validate PDF extraction", "workai.cv.compile_pdf"),
    Agent("Application", "Fill audited forms and verify submission", "workai.browser.apply_job"),
    Agent("Application Questions", "Resolve scoped known answers", "workai.answers.resolve_question"),
    Agent("Profile Optimization", "Plan and apply truthful missing profile fields", "workai.profile_sync.sync_profile"),
    Agent("Application Tracker", "Persist normalized history and CSV", "workai.store.Store"),
    Agent("QA", "Validate source and compiled application artifacts", "workai.orchestrator.validate_cv_artifacts"),
    Agent("Security", "Check source control and redact logs", "workai.security.audit_tracked_files"),
    Agent("Duplicate Detection", "Claim unique jobs and detect duplicate roles", "workai.store.Store.create_application"),
    Agent("Recovery", "Reconcile interrupted external operations", "workai.store.Store.recover"),
)


def describe_agents() -> list[dict]:
    return [asdict(agent) for agent in AGENTS]
