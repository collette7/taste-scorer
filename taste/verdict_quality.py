from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, NotRequired, TypedDict


class EvidenceDimension(TypedDict):
    name: str
    score: int
    weight: float
    reason: str


class EvidenceVerdict(TypedDict):
    weighted_score: int
    confidence: str
    one_liner: str
    dimensions: NotRequired[list[EvidenceDimension]]


@dataclass(frozen=True, slots=True)
class VerdictQualityError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


MISSING_CORE_EVIDENCE_RE: Final = re.compile(
    r"\b(?:no|zero|without|missing|insufficient|undocumented|unverified|unknown)\b"
    r".{0,30}\b(?:product|inventory|menu|drinks?|food|cuisine|format|offering|"
    r"curation|execution)\b",
    re.IGNORECASE,
)
LOCATION_AS_BASIS_RE: Final = re.compile(
    r"(?:\b(?:strong|beloved|perfect|promising|excellent|proven|favorite|trusted)\b"
    r".{0,15}\b(?:neighbou?rhood|location|district|area|city)\b)"
    r"|(?:\b(?:neighbou?rhood|location|district|area|city)\b.{0,55}"
    r"\b(?:signal|fit|backing|pedigree|makes this worth|warrants? a visit)\b)",
    re.IGNORECASE,
)
UNSUPPORTED_TRAIT_RE: Final = re.compile(
    r"\b(?:likely|probably|suggests?|potentially|could be)\b.{0,60}"
    r"\b(?:warm|curat|aesthetic|craft|authentic|owner-driven|intimate|soul|character)\w*\b",
    re.IGNORECASE,
)
DEFERRED_DECISION_RE: Final = re.compile(
    r"\b(?:probe|investigate|research|verify|check)\b.{0,55}"
    r"\b(?:before committing|before deciding|deeper|further|on visit|first)\b"
    r"|\bneeds?\b.{0,45}\b(?:to confirm|confirmation|more research|in-person browse)\b",
    re.IGNORECASE,
)
ACTION_BY_SCORE: Final = {
    7: ("destination",),
    6: ("destination", "route stop"),
    5: ("route stop", "nearby-only"),
    4: ("skip",),
    3: ("skip",),
    2: ("skip",),
    1: ("skip",),
}


def validate_evidence_consistency(verdict: EvidenceVerdict) -> None:
    score = verdict["weighted_score"]
    confidence = verdict["confidence"]
    one_liner = verdict["one_liner"]

    if score > 5 and confidence == "low":
        raise VerdictQualityError("low-confidence verdict cannot score above 5")
    if score > 5 and MISSING_CORE_EVIDENCE_RE.search(one_liner):
        raise VerdictQualityError("high score contradicts missing venue evidence")
    if score > 5 and LOCATION_AS_BASIS_RE.search(one_liner):
        raise VerdictQualityError("location cannot be the basis for a score above 5")
    if UNSUPPORTED_TRAIT_RE.search(one_liner):
        raise VerdictQualityError("unsupported venue-trait speculation")
    if DEFERRED_DECISION_RE.search(one_liner):
        raise VerdictQualityError("one_liner must make the decision now")
    if not one_liner.lower().startswith(ACTION_BY_SCORE[score]):
        raise VerdictQualityError("one_liner action does not match score band")

    for dimension in verdict.get("dimensions", []):
        if dimension["name"] == "neighborhood_context" and dimension["weight"] > 0.15:
            raise VerdictQualityError(
                "neighborhood_context weight cannot exceed 0.15"
            )
        if (
            score > 5
            and dimension["name"] == "product_quality"
            and MISSING_CORE_EVIDENCE_RE.search(dimension["reason"])
        ):
            raise VerdictQualityError("high score contradicts missing product evidence")
