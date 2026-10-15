"""Offline immutable model metadata and explicit reversible research status."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Status = Literal[
    "NOT_TRAINED",
    "TRAINED_UNVALIDATED",
    "VALIDATION_FAILED",
    "VALIDATED_RESEARCH",
    "SHADOW_ELIGIBLE",
]


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    model_family: str
    model_version: str
    training_dataset_sha256: str
    validation_dataset_sha256: str | None
    source_kind: str
    feature_schema_version: str
    trained_at_ns: int | None
    train_sessions: tuple[str, ...]
    holdout_sessions: tuple[str, ...]
    hyperparameters: dict[str, float | int | str]
    calibration_method: str | None
    validation_results: dict[str, float | int | str]
    instruments: tuple[str, ...]
    horizons_ns: tuple[int, ...]
    environment: str
    latency_ns: int | None
    limitations: tuple[str, ...]
    status: Status

    def identity(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class ResearchRegistry:
    """No policy hot path reads this directory; status changes require evidence."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def register(self, artifact: ModelArtifact) -> str:
        if artifact.status not in {
            "NOT_TRAINED",
            "TRAINED_UNVALIDATED",
            "VALIDATION_FAILED",
        }:
            raise ValueError("validated status requires transition evidence")
        if set(artifact.train_sessions) & set(artifact.holdout_sessions):
            raise ValueError("holdout contamination")
        identity = artifact.identity()
        path = self.root / f"{identity}.json"
        if path.exists():
            raise FileExistsError("artifact already registered")
        path.write_text(json.dumps(asdict(artifact), sort_keys=True) + "\n")
        self._write_status(identity, artifact.status)
        return identity

    def _write_status(
        self, identity: str, status: Status, evidence_sha256: str | None = None
    ) -> None:
        path = self.root / f"{identity}.status.json"
        temp = self.root / f"{identity}.status.tmp"
        temp.write_text(
            json.dumps(
                {
                    "artifact_sha256": identity,
                    "status": status,
                    "evidence_sha256": evidence_sha256,
                }
            )
            + "\n"
        )
        os.replace(temp, path)
        with (self.root / f"{identity}.history.jsonl").open("a") as history:
            history.write(
                json.dumps({"status": status, "evidence_sha256": evidence_sha256})
                + "\n"
            )
            history.flush()
            os.fsync(history.fileno())

    def status(self, identity: str) -> Status:
        if not (self.root / f"{identity}.json").exists():
            raise FileNotFoundError(identity)
        value: Status = json.loads((self.root / f"{identity}.status.json").read_text())[
            "status"
        ]
        return value

    def transition(
        self,
        identity: str,
        target: Status,
        *,
        evidence_path: Path | None = None,
    ) -> None:
        artifact = ModelArtifact(
            **json.loads((self.root / f"{identity}.json").read_text())
        )
        current = self.status(identity)
        if target == "TRAINED_UNVALIDATED" and current in {
            "VALIDATED_RESEARCH",
            "SHADOW_ELIGIBLE",
        }:
            self._write_status(identity, target)  # explicit rollback
            return
        if target == "VALIDATION_FAILED" and current == "TRAINED_UNVALIDATED":
            self._write_status(identity, target)
            return
        if target not in {"VALIDATED_RESEARCH", "SHADOW_ELIGIBLE"}:
            raise ValueError("invalid research model status transition")
        if evidence_path is None:
            raise ValueError("independent held-out evidence required")
        raw = evidence_path.read_bytes()
        evidence = json.loads(raw)
        evidence_hash = hashlib.sha256(raw).hexdigest()
        if target == "VALIDATED_RESEARCH" and current == "TRAINED_UNVALIDATED":
            if (
                artifact.source_kind != "REAL_VERIFIED"
                or artifact.validation_dataset_sha256 is None
                or artifact.validation_dataset_sha256
                == artifact.training_dataset_sha256
                or evidence.get("dataset_sha256") != artifact.validation_dataset_sha256
                or evidence.get("source_kind") != "REAL_VERIFIED"
                or set(evidence.get("holdout_sessions", []))
                != set(artifact.holdout_sessions)
                or set(artifact.train_sessions) & set(artifact.holdout_sessions)
                or evidence.get("validation_status") != "PASS"
                or not artifact.validation_results
            ):
                raise ValueError("independent held-out evidence required")
            self._write_status(identity, target, evidence_hash)
            return
        if target == "SHADOW_ELIGIBLE" and current == "VALIDATED_RESEARCH":
            if (
                evidence.get("review_type") != "SHADOW"
                or evidence.get("artifact_sha256") != identity
            ):
                raise ValueError("explicit independent shadow review required")
            self._write_status(identity, target, evidence_hash)
            return
        raise ValueError("invalid research model status transition")
