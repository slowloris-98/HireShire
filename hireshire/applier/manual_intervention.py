"""Rules and labels for applications that require a human to complete them."""
from __future__ import annotations

from hireshire.models.job import Job


MANUAL_INTERVENTION_STATUS = "manual_intervention_needed"
WORKDAY_MANUAL_INTERVENTION_MESSAGE = "Workday applications require manual intervention."


def requires_manual_intervention(job: Job) -> bool:
    """Return whether this job must not be attempted by the browser applier."""
    return job.source.lower() == "workday"
