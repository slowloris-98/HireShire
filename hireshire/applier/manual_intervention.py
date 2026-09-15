"""Rules and labels for applications that require a human to complete them."""
from __future__ import annotations

from hireshire.models.job import Job


MANUAL_INTERVENTION_STATUS = "manual_intervention_needed"
WORKDAY_MANUAL_INTERVENTION_MESSAGE = "Workday applications require manual intervention."
DIRECT_MANUAL_INTERVENTION_MESSAGE = "Direct career portal applications require manual intervention."


def requires_manual_intervention(job: Job) -> bool:
    """Return whether this job must not be attempted by the browser applier."""
    return job.source.lower() in {"workday", "direct"}


def manual_intervention_message(job: Job) -> str:
    """Explain why this source is intentionally left for the applicant."""
    if job.source.lower() == "direct":
        return DIRECT_MANUAL_INTERVENTION_MESSAGE
    return WORKDAY_MANUAL_INTERVENTION_MESSAGE
