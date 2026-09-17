"""Import every model module so Base.metadata is fully populated.

Alembic's env.py (and anything else that needs the complete schema, e.g.
`Base.metadata.create_all` in tests) imports this package rather than
individual model modules.
"""

from app.models.application import Application
from app.models.candidate_profile import CandidateProfile
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.crawl_run import CrawlRun
from app.models.job_feedback import JobFeedback
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.models.notification_log import NotificationLog
from app.models.target_role import TargetRole

__all__ = [
    "Application",
    "CandidateProfile",
    "CareerSource",
    "Company",
    "CrawlRun",
    "JobFeedback",
    "JobMatch",
    "JobPosting",
    "NotificationLog",
    "TargetRole",
]
