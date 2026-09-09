# -*- coding: utf-8 -*-

import hashlib
import json
from pathlib import Path

from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewUncommittedTarget,
    parse_mind_review_request,
)

FIXTURE_SHA256 = "c9c0f7ce3169173dd62b2b3e5f48a5053b24b878970f545bfa44424be8a2b7e5"


def test_review_v1_shared_fixture_hash_matches_appserver_contract(
    fixtures_root: Path,
) -> None:
    """固定客户端与 AppServer 共同验收的原始 fixture。"""
    fixture_path = fixtures_root / "review_v1_requests.json"
    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == FIXTURE_SHA256


def test_review_v1_shared_fixture_parses_all_targets_and_fingerprints(
    fixtures_root: Path,
) -> None:
    """严格解析四类空 workspace 请求并核对服务端指纹。"""
    fixture_path = fixtures_root / "review_v1_requests.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["protocol"] == "mind-review/1"

    requests = [
        parse_mind_review_request(item["request"])
        for item in fixture["cases"]
    ]

    assert [type(request.target) for request in requests] == [
        ReviewUncommittedTarget,
        ReviewBaseBranchTarget,
        ReviewCommitTarget,
        ReviewCustomTarget,
    ]
    assert all(
        request.workspace == ClientReviewWorkspace.create()
        for request in requests
    )
    assert [request.request_fingerprint() for request in requests] == [
        item["expected_fingerprint"]
        for item in fixture["cases"]
    ]


if __name__ == '__main__':
    pass
