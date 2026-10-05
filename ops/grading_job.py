"""Grading loop for prediction_outcomes (spec §10, Phase 4).

Tất định, không LLM — chấm các dự báo đã đủ số phiên ở mỗi mốc trong
grading.horizons_days. Chạy ngoài giờ giao dịch, tần suất thấp vì chỉ có
thể có kết quả mới sau mỗi phiên đóng cửa.

Run as a long-lived process:
    python -m ops.grading_job
"""
from __future__ import annotations

import time

import yaml

from mcp_server.connection import get_rw_conn
from ops.alerting import log_event
from pipeline.grading import GradingConfig, grade_due_predictions

POLL_INTERVAL_S = 3600
RULES_PATH = "config/vn-rules.yaml"


def main() -> None:
    log_event("grading_job_started")
    rules = yaml.safe_load(open(RULES_PATH))
    cfg = GradingConfig.from_rules(rules)
    while True:
        with get_rw_conn() as conn:
            graded = grade_due_predictions(conn, cfg)
        if graded:
            log_event("grading_job_graded", count=graded)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
