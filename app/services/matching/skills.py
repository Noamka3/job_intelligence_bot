"""Skill normalization: a canonical vocabulary + alias layer (spec §10).

"Node", "NodeJS", "Node.js" must compare equal; matching required/
preferred skills against a candidate's skills must never be a naive
case-sensitive substring search. Everything here works on the canonical
form; extract_skills_from_text() is how a canonical skill is detected
inside free text (a job description, a resume) without needing every
source to already provide a clean structured skill list - most don't.
"""

from __future__ import annotations

import re

# canonical name -> every surface form (including itself) that should
# resolve to it. Not exhaustive - a real, actively maintained vocabulary
# grows over time; unmatched terms simply aren't recognized rather than
# breaking anything.
_SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "python": ("python", "python3", "py"),
    "javascript": ("javascript", "js", "ecmascript"),
    "typescript": ("typescript", "ts"),
    "node.js": ("node.js", "node", "nodejs", "node js"),
    "react": ("react", "react.js", "reactjs", "react native"),
    "vue": ("vue", "vue.js", "vuejs"),
    "angular": ("angular", "angular.js", "angularjs"),
    "java": ("java",),
    "c#": ("c#", "csharp", "c sharp", ".net", "dotnet", "asp.net", "vb.net"),
    "c++": ("c++", "cpp"),
    "go": ("go", "golang"),
    "rust": ("rust",),
    "php": ("php",),
    "ruby": ("ruby", "ruby on rails", "rails"),
    "sql": ("sql",),
    "postgresql": ("postgresql", "postgres", "psql"),
    "mysql": ("mysql",),
    "mongodb": ("mongodb", "mongo"),
    "redis": ("redis",),
    "rest api": (
        "rest api",
        "rest apis",
        "restful",
        "restful api",
        "restful apis",
        "rest services",
        "rest endpoints",
        "rest interfaces",
    ),
    "graphql": ("graphql",),
    "grpc": ("grpc",),
    "docker": ("docker", "containerization"),
    "kubernetes": ("kubernetes", "k8s"),
    "aws": ("aws", "amazon web services"),
    "gcp": ("gcp", "google cloud", "google cloud platform"),
    "azure": ("azure", "microsoft azure"),
    "ci/cd": ("ci/cd", "cicd", "ci-cd", "continuous integration", "continuous deployment"),
    "terraform": ("terraform",),
    "linux": ("linux", "unix"),
    "git": ("git", "github", "gitlab", "version control"),
    "html": ("html", "html5"),
    "css": ("css", "css3"),
    "django": ("django",),
    "flask": ("flask",),
    "fastapi": ("fastapi",),
    "spring": ("spring", "spring boot", "spring framework"),
    "express": ("express.js", "expressjs", "express js"),
    "kafka": ("kafka",),
    "rabbitmq": ("rabbitmq",),
    "machine learning": ("machine learning", "ml"),
    "deep learning": ("deep learning",),
    "pytorch": ("pytorch",),
    "tensorflow": ("tensorflow",),
    "computer vision": ("computer vision",),
    "nlp": ("nlp", "natural language processing"),
    "data science": ("data science",),
    "pandas": ("pandas",),
    "numpy": ("numpy",),
    "microservices": ("microservices", "microservice architecture"),
    "agile": ("agile", "scrum"),
    "testing": ("testing", "unit testing", "test automation", "qa"),
}

_ALIAS_TO_CANONICAL: dict[str, str] = {
    alias: canonical for canonical, aliases in _SKILL_ALIASES.items() for alias in aliases
}

# Longest alias first, so "node.js" matches before a hypothetical shorter
# overlapping alias would.
_ALIASES_BY_LENGTH_DESC: list[str] = sorted(_ALIAS_TO_CANONICAL, key=len, reverse=True)

_WORD_BOUNDARY_UNSAFE = re.compile(r"[.+#]")

# Word boundaries alone aren't enough for aliases that are also ordinary
# English words. Seen on real postings: "go-to-market", "react quickly",
# "spring 2026 internship". These only count in a technical context: as
# a whole list item ("Python, Go, Java", a "<li>Go</li>" line), next to
# a tech noun ("Go developer"), or after a skill preposition ("experience
# with Go"). A comma alone is not a list - prose is full of them.
_LIST_DELIMITER = r"[,/(|&:;•·]"
_TECH_NOUNS = {
    "go": r"developer|engineer|programming|language|lang|services?|microservices|backend|code",
    "react": (
        r"developer|engineer|components?|hooks|framework|library|applications?|apps?"
        r"|frontend|front-end"
    ),
    "spring": r"boot|framework|mvc|cloud|data|security|batch",
}


def _contextual_pattern(word: str, tech_nouns: str) -> str:
    w = re.escape(word)
    return (
        rf"(?:(?<={_LIST_DELIMITER})|^)[ \t]*{w}\b(?!-)[ \t]*(?={_LIST_DELIMITER}|[.)]|$)"
        rf"|\b{w}\s+(?:{tech_nouns})\b"
        rf"|\b(?:in|with|using|of|on)\s+{w}\b(?!-)"
    )


_ALIAS_PATTERN_OVERRIDES: dict[str, str] = {
    word: _contextual_pattern(word, nouns) for word, nouns in _TECH_NOUNS.items()
}
# A literal ".net" would also match inside "jobs@company.net" / "site.net".
_ALIAS_PATTERN_OVERRIDES[".net"] = r"(?<![\w.@])\.net\b"


def _alias_pattern(alias: str) -> re.Pattern[str]:
    if alias in _ALIAS_PATTERN_OVERRIDES:
        return re.compile(_ALIAS_PATTERN_OVERRIDES[alias], re.MULTILINE)
    if _WORD_BOUNDARY_UNSAFE.search(alias):
        return re.compile(re.escape(alias))
    return re.compile(rf"\b{re.escape(alias)}\b")


_ALIAS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (_ALIAS_TO_CANONICAL[alias], _alias_pattern(alias)) for alias in _ALIASES_BY_LENGTH_DESC
]


def normalize_skill(raw: str) -> str:
    """Canonical form of a single skill string, or the lowercased/
    trimmed input unchanged if it's not in the known vocabulary (an
    unrecognized skill is still a real skill - just not one we can
    alias-match against variant spellings).
    """
    cleaned = " ".join(raw.strip().lower().split())
    return _ALIAS_TO_CANONICAL.get(cleaned, cleaned)


def score_skills(
    candidate_skills: set[str], job_skills: set[str]
) -> tuple[float, list[str], list[str]]:
    """(score in [0,1], matched skills, missing skills). Score is the
    fraction of skills the job actually mentions that the candidate
    demonstrably has - a job silent on a skill costs nothing (spec §10:
    differentiate what's actually asked for from what merely isn't
    mentioned), but a job mentioning ten skills the candidate has none of
    scores 0, not neutral.
    """
    if not job_skills:
        return 0.5, [], []
    matched = sorted(candidate_skills & job_skills)
    missing = sorted(job_skills - candidate_skills)
    return len(matched) / len(job_skills), matched, missing


def extract_skills_from_text(text: str) -> set[str]:
    """Canonical skills mentioned anywhere in free text (job description,
    resume). Word-boundary matching for plain alphanumeric aliases; a
    plain literal match for aliases containing regex-special punctuation
    (c++, node.js, ci/cd, ...) where \\b doesn't apply cleanly - and, since
    punctuation itself counts as a word boundary, "js" inside "node.js"
    would otherwise also match as its own alias (for "javascript") once
    checked. Each match is blanked out of the working text (longest alias
    first) so a shorter alias can never re-match inside a span a longer
    one already claimed.
    """
    if not text:
        return set()
    lowered = text.lower()
    found: set[str] = set()
    for canonical, pattern in _ALIAS_PATTERNS:
        if canonical in found:
            continue
        lowered, replacements = pattern.subn(lambda m: " " * len(m.group()), lowered)
        if replacements:
            found.add(canonical)
    return found
