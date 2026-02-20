"""PM output artifact schemas — validates PM task output JSON"""
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class BeliefUpdate:
    content: str
    category: Literal["identity", "goal", "preference", "decision", "fact"]
    confidence: float  # 0.5-1.0
    importance: float  # 1-10
    action_implication: str = ""
    evidence_for: str = ""
    evidence_against: str = ""


@dataclass
class MemoryOperation:
    op: Literal["store", "update", "archive"]
    content: str
    importance: float = 5.0


@dataclass
class PMTaskOutput:
    belief_updates: list[BeliefUpdate] = field(default_factory=list)
    memory_operations: list[MemoryOperation] = field(default_factory=list)
    proposals: list = field(default_factory=list)  # empty until Phase 3


def validate_pm_output(raw: dict) -> tuple[PMTaskOutput, list[str]]:
    """Returns (output, errors). Errors list is empty if valid."""
    errors = []
    belief_updates = []

    for i, b in enumerate(raw.get("belief_updates", [])):
        if not isinstance(b.get("content"), str) or len(b["content"]) < 10:
            errors.append(f"belief_updates[{i}].content invalid")
            continue
        if b.get("category") not in ("identity", "goal", "preference", "decision", "fact"):
            errors.append(f"belief_updates[{i}].category invalid: {b.get('category')}")
            continue
        conf = float(b.get("confidence", 0))
        if not 0.5 <= conf <= 1.0:
            errors.append(f"belief_updates[{i}].confidence out of range: {conf}")
            continue
        belief_updates.append(
            BeliefUpdate(**{k: v for k, v in b.items() if k in BeliefUpdate.__dataclass_fields__})
        )

    memory_ops = []
    for op in raw.get("memory_operations", []):
        if op.get("op") not in ("store", "update", "archive"):
            errors.append(f"memory_operations.op invalid: {op.get('op')}")
            continue
        memory_ops.append(
            MemoryOperation(**{k: v for k, v in op.items() if k in MemoryOperation.__dataclass_fields__})
        )

    return PMTaskOutput(belief_updates=belief_updates, memory_operations=memory_ops), errors
