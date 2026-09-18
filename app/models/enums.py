"""Enumerations shared by the ORM models.

enum.StrEnum members serialize cleanly through Pydantic/JSON and map to
native Postgres ENUM types via SQLAlchemy's Enum().
"""

from __future__ import annotations

import enum


class CareerSourceType(enum.StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    SMARTRECRUITERS = "smartrecruiters"
    WORKABLE = "workable"
    COMEET = "comeet"
    WORKDAY = "workday"
    TALEO = "taleo"
    JSONLD = "jsonld"
    # A JSON feed the company's own site publishes (adapters/site_feed.py).
    SITE_FEED = "site_feed"
    # Jobs exposed as posts by a WordPress site's REST API (adapters/wordpress.py).
    WORDPRESS = "wordpress"
    GENERIC_HTML = "generic_html"
    PLAYWRIGHT = "playwright"
    UNSUPPORTED = "unsupported"


class JobStatus(enum.StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SeniorityLevel(enum.StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    LEAD = "lead"
    MANAGER = "manager"
    DIRECTOR = "director"
    UNKNOWN = "unknown"


class RemoteType(enum.StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class EmploymentType(enum.StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class JobFeedbackAction(enum.StrEnum):
    INTERESTED = "interested"
    APPLIED = "applied"
    NOT_RELEVANT = "not_relevant"
    TOO_SENIOR = "too_senior"
    WRONG_FIELD = "wrong_field"
    WRONG_LOCATION = "wrong_location"
    SAVED = "saved"
    REJECTED = "rejected"


class ApplicationStatus(enum.StrEnum):
    """Where an application the user actually sent stands."""

    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    ASSIGNMENT = "assignment"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class NotificationChannel(enum.StrEnum):
    CONSOLE = "console"
    WHATSAPP = "whatsapp"


class CrawlRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
