from dataclasses import dataclass, field
from typing import Dict, List


BEHAVIORAL_AREAS = [
    "communication",
    "teamwork",
    "adaptability",
    "accountability",
    "decision_making",
    "leadership",
    "problem_solving",
]


@dataclass
class BehavioralEvidence:
    area: str
    evidence: str
    strength: str = "unknown"
    confidence: float = 0.0


@dataclass
class AssessmentState:
    candidate_introduction: str = ""
    transcript: List[Dict[str, str]] = field(default_factory=list)

    def add_transcript(self, role: str, text: str):
        self.transcript.append({
            "role": role,
            "text": text,
        })
        
    evidence: Dict[str, List[BehavioralEvidence]] = field(
        default_factory=lambda: {
            area: [] for area in BEHAVIORAL_AREAS
        }
    )

    questions_asked: int = 0
    completed: bool = False

    def add_evidence(
        self,
        area: str,
        evidence: str,
        strength: str = "unknown",
        confidence: float = 0.0,
    ):
        if area not in self.evidence:
            self.evidence[area] = []

        self.evidence[area].append(
            BehavioralEvidence(
                area=area,
                evidence=evidence,
                strength=strength,
                confidence=confidence,
            )
        )

    def areas_with_evidence(self) -> List[str]:
        return [
            area
            for area, items in self.evidence.items()
            if len(items) > 0
        ]

    def areas_needing_evidence(self) -> List[str]:
        return [
            area
            for area, items in self.evidence.items()
            if len(items) == 0
        ]

    def to_dict(self):
        return {
            "candidate_introduction": self.candidate_introduction,
            "behavioral_evidence": {
                area: [
                    {
                        "area": item.area,
                        "evidence": item.evidence,
                        "strength": item.strength,
                        "confidence": item.confidence,
                    }
                    for item in items
                ]
                for area, items in self.evidence.items()
            },
            "questions_asked": self.questions_asked,
            "completed": self.completed,
        }