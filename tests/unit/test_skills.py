from __future__ import annotations

from app.services.matching.skills import extract_skills_from_text, normalize_skill, score_skills


def test_normalize_skill_resolves_known_aliases() -> None:
    assert normalize_skill("Node") == "node.js"
    assert normalize_skill("NodeJS") == "node.js"
    assert normalize_skill("Node.js") == "node.js"
    assert normalize_skill("Postgres") == "postgresql"
    assert normalize_skill("PostgreSQL") == "postgresql"
    assert normalize_skill("JS") == "javascript"
    assert normalize_skill("Amazon Web Services") == "aws"


def test_normalize_skill_passes_through_unknown_terms() -> None:
    assert normalize_skill("  Some Obscure Tool  ") == "some obscure tool"


def test_extract_skills_from_text_finds_multiple_canonical_skills() -> None:
    text = "We use Python, Node.js, Docker and AWS. Experience with REST APIs a plus."
    skills = extract_skills_from_text(text)
    assert skills == {"python", "node.js", "docker", "aws", "rest api"}


def test_extract_skills_from_text_uses_word_boundaries() -> None:
    # "go" must not match inside "going" / "mongo" must not match "go"
    text = "We are going to use MongoDB for storage."
    skills = extract_skills_from_text(text)
    assert "go" not in skills
    assert skills == {"mongodb"}


def test_extract_skills_from_text_empty_input() -> None:
    assert extract_skills_from_text("") == set()


def test_ordinary_english_words_are_not_skills() -> None:
    """Reproduced on a real non-technical posting: "go-to-market", "send
    your CV", "the rest of the team", "jobs@company.net" and "spring
    internship" used to yield {go, computer vision, rest api, c#, spring}
    - and a bogus "go experience requested" concern on every match."""
    text = (
        "Join our go-to-market team. Please send your CV to jobs@company.net. "
        "You'll work with the rest of the team, react quickly to change, and "
        "go the extra mile. Spring 2026 internship. Express yourself."
    )
    assert extract_skills_from_text(text) == set()


def test_ambiguous_aliases_match_in_technical_context() -> None:
    assert extract_skills_from_text("Languages: Python, Go, Java") >= {"python", "go", "java"}
    assert "go" in extract_skills_from_text("2+ years of experience with Go")
    assert "go" in extract_skills_from_text("Go developer wanted")
    assert "go" in extract_skills_from_text("Golang microservices")
    assert "react" in extract_skills_from_text("Frontend: React/Redux, TypeScript")
    assert "react" in extract_skills_from_text("Experience with React and Node.js")
    assert "spring" in extract_skills_from_text("Java, Spring Boot, Hibernate")
    assert "go" in extract_skills_from_text("Skills\nPython\nGo\nDocker")  # <li> per line
    assert "c#" in extract_skills_from_text("C# / .NET Core backend")
    assert "c#" in extract_skills_from_text("ASP.NET developer")
    assert "rest api" in extract_skills_from_text("Design RESTful services")
    assert "computer vision" in extract_skills_from_text("Computer vision pipelines")


def test_score_skills_full_match() -> None:
    score, matched, missing = score_skills({"python", "docker"}, {"python", "docker"})
    assert score == 1.0
    assert matched == ["docker", "python"]
    assert missing == []


def test_score_skills_partial_match() -> None:
    score, matched, missing = score_skills({"python"}, {"python", "docker", "aws"})
    assert score == 1 / 3
    assert matched == ["python"]
    assert missing == ["aws", "docker"]


def test_score_skills_no_job_skills_is_neutral() -> None:
    score, matched, missing = score_skills({"python"}, set())
    assert score == 0.5
    assert matched == []
    assert missing == []


def test_score_skills_no_overlap_scores_zero() -> None:
    score, matched, missing = score_skills({"java"}, {"python"})
    assert score == 0.0
    assert missing == ["python"]
