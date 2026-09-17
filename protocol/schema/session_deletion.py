# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass

from protocol.schema.identifiers import (
    CID_RE,
    REQUEST_ID_PATTERN,
    SID_RE,
)
from protocol.schema.json_value import (
    JsonObject,
    JsonValue,
)


@dataclass(frozen=True, slots=True, order=True)
class SessionDeletionTarget:
    """表示正式删除契约中的会话身份，不持有运行时或关闭资源。"""

    cid: str
    sid: str

    def __post_init__(self) -> None:
        """校验原始身份，不通过去空格或强制转换改变删除目标。"""
        if not isinstance(self.cid, str) or not isinstance(self.sid, str):
            raise ValueError("invalid deletion target")
        cid_match = CID_RE.fullmatch(self.cid)
        sid_match = SID_RE.fullmatch(self.sid)
        if (
            len(self.cid) > 128 or len(self.sid) > 128
            or cid_match is None or sid_match is None
            or cid_match.group(1) != sid_match.group(1)
        ):
            raise ValueError("invalid deletion target")

    def payload(self) -> JsonObject:
        """返回只包含正式身份字段的线上对象。"""
        return {"cid": self.cid, "sid": self.sid}


@dataclass(frozen=True, slots=True)
class SessionDeletionRequest:
    """冻结一次删除意图；未知结果及重试始终复用此身份和完整集合。"""

    request_id: str
    root: SessionDeletionTarget
    descendants: tuple[SessionDeletionTarget, ...] = ()

    def __post_init__(self) -> None:
        """拒绝非法幂等身份、可变集合、重复目标及超出服务端限制的范围。"""
        if not isinstance(self.request_id, str) or not REQUEST_ID_PATTERN.fullmatch(self.request_id):
            raise ValueError("invalid deletion request identity")
        if (
            not isinstance(self.root, SessionDeletionTarget)
            or not isinstance(self.descendants, tuple)
            or len(self.descendants) > 255
            or any(not isinstance(item, SessionDeletionTarget) for item in self.descendants)
        ):
            raise ValueError("invalid deletion scope")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("deletion targets must be unique")

    @property
    def targets(self) -> tuple[SessionDeletionTarget, ...]:
        """返回与传入后代顺序无关的完整目标集合。"""
        return tuple(sorted((self.root, *self.descendants)))

    def payload(self) -> JsonObject:
        """生成正式 POST 请求，调用方不能传入所有者或隐含范围。"""
        return {
            **self.root.payload(), "request_id": self.request_id,
            "descendants": [item.payload() for item in self.descendants],
        }


@dataclass(frozen=True, slots=True)
class SessionDeletionReceipt:
    """保存已校验的远端完成证据，不表示客户端本地清理已完成。"""

    request_id: str
    root: SessionDeletionTarget
    targets: tuple[SessionDeletionTarget, ...]


def parse_session_deletion_receipt(
    body: JsonValue, expected: SessionDeletionRequest,
) -> SessionDeletionReceipt:
    """严格核对正式回执及整个目标集合，非法成功响应仍属于未知结果。"""
    if not isinstance(body, dict) or set(body) != {"request_id", "cid", "sid", "status", "targets"}:
        raise ValueError("invalid deletion receipt")
    if (
        body.get("request_id") != expected.request_id
        or body.get("cid") != expected.root.cid
        or body.get("sid") != expected.root.sid
        or body.get("status") != "deleted"
    ):
        raise ValueError("deletion receipt identity mismatch")
    raw_targets = body.get("targets")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 256:
        raise ValueError("invalid deletion receipt targets")
    targets: list[SessionDeletionTarget] = []
    for raw in raw_targets:
        if not isinstance(raw, dict) or set(raw) != {"cid", "sid"}:
            raise ValueError("invalid deletion receipt target")
        cid, sid = raw.get("cid"), raw.get("sid")
        if not isinstance(cid, str) or not isinstance(sid, str):
            raise ValueError("invalid deletion receipt target")
        targets.append(SessionDeletionTarget(cid, sid))
    if tuple(sorted(targets)) != expected.targets:
        raise ValueError("deletion receipt scope mismatch")
    return SessionDeletionReceipt(expected.request_id, expected.root, expected.targets)


if __name__ == '__main__':
    pass
