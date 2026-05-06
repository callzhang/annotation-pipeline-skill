from __future__ import annotations

import math


def validate_qc_sample_options(qc_sample_count: int | None, qc_sample_ratio: float | None) -> None:
    if qc_sample_count is not None and qc_sample_ratio is not None:
        raise ValueError("--qc-sample-count and --qc-sample-ratio are mutually exclusive")
    if qc_sample_count is not None and qc_sample_count <= 0:
        raise ValueError("--qc-sample-count must be > 0")
    if qc_sample_ratio is not None and not (0 < qc_sample_ratio <= 1):
        raise ValueError("--qc-sample-ratio must be > 0 and <= 1")


def build_qc_policy(
    *,
    row_count: int,
    qc_sample_count: int | None = None,
    qc_sample_ratio: float | None = None,
) -> dict:
    policy = {
        "row_count": row_count,
        "sample_scope": "per_task",
        "selection": "deterministic_from_task_payload_order",
        "feedback_loop": "annotator_may_accept_or_dispute_qc_items",
    }
    if qc_sample_count is not None:
        sample_count = min(qc_sample_count, row_count)
        return {
            "mode": "sample_count",
            **policy,
            "requested_sample_count": qc_sample_count,
            "sample_count": sample_count,
            "required_correct_rows": sample_count,
        }
    if qc_sample_ratio is not None:
        sample_count = max(1, math.ceil(row_count * qc_sample_ratio)) if row_count else 0
        return {
            "mode": "sample_ratio",
            **policy,
            "sample_ratio": qc_sample_ratio,
            "sample_count": sample_count,
            "required_correct_rows": sample_count,
        }
    return {
        "mode": "all_rows",
        "required_correct_rows": row_count,
        "feedback_loop": "annotator_may_accept_or_dispute_qc_items",
    }
