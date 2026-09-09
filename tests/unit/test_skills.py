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
