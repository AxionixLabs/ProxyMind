# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import re
import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
)

from protocol.schema.identifiers import (
    normalize_request_id,
    normalize_turn_id,
    valid_session_ids,
)
from protocol.schema.json_value import (
    JsonObject,
    JsonValue,
)

ReviewDelivery: typing.TypeAlias = typing.Literal["inline", "detached"]
ReviewCorrectness: typing.TypeAlias = typing.Literal[
    "correct",
    "incorrect",
    "unknown",
]
ReviewReceiptStatus: typing.TypeAlias = typing.Literal[
    "accepted",
    "idempotent",
]

REVIEW_WORKSPACE_MAX_BYTES: typing.Final[int] = 4_000_000
REVIEW_PATCH_MAX_CHARS: typing.Final[int] = 1_500_000
REVIEW_FILE_MAX_CHARS: typing.Final[int] = 1_000_000
REVIEW_FILE_MAX_COUNT: typing.Final[int] = 256
REVIEW_EMPTY_WORKSPACE_REVISION: typing.Final[str] = (
    "sha256:266a24608d21b1d56e6f51f822b3a516b9908a006afd3cacf7366d07b730626f"
)

_BRANCH_INVALID_CHARS: typing.Final[frozenset[str]] = frozenset("~^:?*[\\")
_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_WORKSPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_REFERENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def canonical_review_digest(value: JsonValue) -> str:
    """计算与服务端一致的 Review 协议对象摘要。"""
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def workspace_revision(
    patch: str,
    files: typing.Sequence["ReviewWorkspaceFile"],
) -> str:
    """计算工作区 patch 与文件列表的规范 revision。"""
    payload: JsonObject = {
        "patch": patch,
        "files": [item.request_payload() for item in files],
    }
    return f"sha256:{canonical_review_digest(payload)}"


def normalize_review_path(value: str, *, label: str) -> str:
    """规范化并校验 Review 使用的相对路径。"""
    normalized = value.replace("\\", "/").strip()
    parts = normalized.split("/")
    if (
        not normalized
        or len(normalized) > 1024
        or normalized.startswith("/")
        or (len(normalized) >= 2 and normalized[1] == ":")
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise ValueError(f"{label} must be a normalized relative path")
    return normalized


def _copy_json_value(value: JsonValue) -> JsonValue:
    """复制 JSON 值，避免外部容器修改冻结协议对象。"""
    if isinstance(value, dict):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    return value


def _copy_json_object(value: Mapping[str, JsonValue]) -> JsonObject:
    """复制具名 JSON 对象。"""
    return {key: _copy_json_value(item) for key, item in value.items()}


def _mapping(value: JsonValue, label: str) -> dict[str, JsonValue]:
    """读取 JSON 对象并复制为可校验字典。"""
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return dict(value)


def _exact_fields(
    payload: Mapping[str, JsonValue],
    expected: frozenset[str],
    label: str,
) -> None:
    """拒绝协议对象缺失字段和未声明字段。"""
    missing = sorted(expected.difference(payload))
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")
    unknown = sorted(set(payload).difference(expected))
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _declared_fields(
    payload: Mapping[str, JsonValue],
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    label: str,
) -> None:
    """校验必填字段，同时允许正式声明的默认字段省略。"""
    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")
    unknown = sorted(set(payload).difference(allowed))
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _required_text(
    value: JsonValue,
    label: str,
    *,
    max_length: int,
) -> str:
    """读取有长度上限的非空文本。"""
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    if len(normalized) > max_length:
        raise ValueError(f"{label} exceeds the length limit")
    return normalized


def _strict_int(value: JsonValue, label: str) -> int:
    """读取不接受布尔值的整数。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    return value


def _strict_score(value: JsonValue, label: str) -> float:
    """读取零到一之间的严格数值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    score = float(value)
    if score < 0.0 or score > 1.0:
        raise ValueError(f"{label} must be between zero and one")
    return score


def _normalize_branch(value: str) -> str:
    """按服务端契约规范化并校验 Git branch ref。"""
    normalized = value.strip()
    segments = normalized.split("/")
    if (
        not normalized
        or len(normalized) > 255
        or normalized in {"@", "."}
        or normalized.startswith(("-", "/"))
        or normalized.endswith(("/", "."))
        or "//" in normalized
        or ".." in normalized
        or "@{" in normalized
        or any(ord(char) < 33 or ord(char) == 127 for char in normalized)
        or any(char in _BRANCH_INVALID_CHARS for char in normalized)
        or any(segment.startswith(".") or segment.endswith(".lock") for segment in segments)
    ):
        raise ValueError("branch is invalid")
    return normalized


@dataclass(frozen=True, slots=True)
class ReviewUncommittedTarget:
    """声明审查工作区中的未提交改动。"""

    type: typing.Literal["uncommitted_changes"] = field(
        default="uncommitted_changes",
        init=False,
    )

    def request_payload(self) -> JsonObject:
        """返回未提交改动目标的 wire 对象。"""
        return {"type": self.type}


@dataclass(frozen=True, slots=True)
class ReviewBaseBranchTarget:
    """声明以指定基础分支为审查基线。"""

    branch: str
    type: typing.Literal["base_branch"] = field(
        default="base_branch",
        init=False,
    )

    def __post_init__(self) -> None:
        """规范化基础分支名称。"""
        object.__setattr__(self, "branch", _normalize_branch(self.branch))

    def request_payload(self) -> JsonObject:
        """返回基础分支目标的 wire 对象。"""
        return {"type": self.type, "branch": self.branch}


@dataclass(frozen=True, slots=True)
class ReviewCommitTarget:
    """声明需要审查的不可变提交。"""

    sha: str
    title: str | None = None
    type: typing.Literal["commit"] = field(default="commit", init=False)

    def __post_init__(self) -> None:
        """规范化提交摘要和可选标题。"""
        normalized_sha = self.sha.strip().lower()
        if _SHA_PATTERN.fullmatch(normalized_sha) is None:
            raise ValueError("review commit sha is invalid")
        normalized_title = self.title.strip() if self.title is not None else None
        if normalized_title == "":
            normalized_title = None
        if normalized_title is not None and len(normalized_title) > 500:
            raise ValueError("review commit title exceeds the length limit")
        object.__setattr__(self, "sha", normalized_sha)
        object.__setattr__(self, "title", normalized_title)

    def request_payload(self) -> JsonObject:
        """返回提交目标的 wire 对象。"""
        return {"type": self.type, "sha": self.sha, "title": self.title}


@dataclass(frozen=True, slots=True)
class ReviewCustomTarget:
    """声明结构化自定义审查指令。"""

    instructions: str
    type: typing.Literal["custom"] = field(default="custom", init=False)

    def __post_init__(self) -> None:
        """去除指令首尾空白并拒绝空指令。"""
        normalized = self.instructions.strip()
        if not normalized:
            raise ValueError("review instructions are required")
        if len(normalized) > 20_000:
            raise ValueError("review instructions exceed the length limit")
        object.__setattr__(self, "instructions", normalized)

    def request_payload(self) -> JsonObject:
        """返回自定义目标的 wire 对象。"""
        return {"type": self.type, "instructions": self.instructions}


ReviewTarget: typing.TypeAlias = (
    ReviewUncommittedTarget
    | ReviewBaseBranchTarget
    | ReviewCommitTarget
    | ReviewCustomTarget
)


def parse_review_target(value: JsonValue) -> ReviewTarget:
    """严格解析 Review target 判别联合。"""
    payload = _mapping(value, "review target")
    target_type = payload.get("type")
    if target_type == "uncommitted_changes":
        _exact_fields(payload, frozenset({"type"}), "review target")
        return ReviewUncommittedTarget()
    if target_type == "base_branch":
        _exact_fields(payload, frozenset({"type", "branch"}), "review target")
        branch = payload["branch"]
        if not isinstance(branch, str):
            raise TypeError("review target branch must be text")
        return ReviewBaseBranchTarget(branch=branch)
    if target_type == "commit":
        _declared_fields(
            payload,
            required=frozenset({"type", "sha"}),
            allowed=frozenset({"type", "sha", "title"}),
            label="review target",
        )
        sha = payload["sha"]
        title = payload.get("title")
        if not isinstance(sha, str):
            raise TypeError("review target sha must be text")
        if title is not None and not isinstance(title, str):
            raise TypeError("review target title must be text or null")
        return ReviewCommitTarget(sha=sha, title=title)
    if target_type == "custom":
        _exact_fields(
            payload,
            frozenset({"type", "instructions"}),
            "review target",
        )
        instructions = payload["instructions"]
        if not isinstance(instructions, str):
            raise TypeError("review target instructions must be text")
        return ReviewCustomTarget(instructions=instructions)
    raise ValueError("review target type is invalid")


@dataclass(frozen=True, slots=True)
class ReviewWorkspaceFile:
    """描述审查快照中的单个不可变文件。"""

    path: str
    content: str
    sha256: str

    def __post_init__(self) -> None:
        """规范化路径并校验内容摘要和容量。"""
        normalized_path = normalize_review_path(
            self.path,
            label="workspace file path",
        )
        if len(self.content) > REVIEW_FILE_MAX_CHARS:
            raise ValueError("workspace file content exceeds the length limit")
        digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.sha256 != digest:
            raise ValueError("workspace file sha256 does not match content")
        object.__setattr__(self, "path", normalized_path)

    @classmethod
    def from_content(cls, path: str, content: str) -> "ReviewWorkspaceFile":
        """从路径和正文创建带摘要的不可变文件。"""
        return cls(
            path=path,
            content=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def request_payload(self) -> JsonObject:
        """返回工作区文件的 wire 对象。"""
        return {
            "path": self.path,
            "content": self.content,
            "sha256": self.sha256,
        }


def _validate_workspace(
    revision: str,
    patch: str,
    files: tuple[ReviewWorkspaceFile, ...],
) -> None:
    """校验共享工作区容量、唯一性和规范 revision。"""
    if not isinstance(files, tuple) or any(
        not isinstance(item, ReviewWorkspaceFile) for item in files
    ):
        raise TypeError("workspace files must be an immutable file tuple")
    if len(patch) > REVIEW_PATCH_MAX_CHARS:
        raise ValueError("workspace patch exceeds the length limit")
    if len(files) > REVIEW_FILE_MAX_COUNT:
        raise ValueError("workspace contains too many files")
    paths = tuple(item.path for item in files)
    if len(paths) != len(set(paths)):
        raise ValueError("workspace file paths must be unique")
    total_bytes = len(patch.encode("utf-8")) + sum(
        len(item.content.encode("utf-8")) for item in files
    )
    if total_bytes > REVIEW_WORKSPACE_MAX_BYTES:
        raise ValueError("workspace snapshot exceeds the total byte limit")
    if _REVISION_PATTERN.fullmatch(revision) is None:
        raise ValueError("workspace revision is invalid")
    if revision != workspace_revision(patch, files):
        raise ValueError("workspace revision does not match snapshot content")


def _workspace_payload(
    source: str,
    revision: str,
    patch: str,
    files: tuple[ReviewWorkspaceFile, ...],
) -> JsonObject:
    """构建工作区来源共享的 wire 字段。"""
    return {
        "source": source,
        "revision": revision,
        "patch": patch,
        "files": [item.request_payload() for item in files],
    }


@dataclass(frozen=True, slots=True)
class ClientReviewWorkspace:
    """声明由客户端采集并提交的不可变工作区。"""

    revision: str
    patch: str = ""
    files: tuple[ReviewWorkspaceFile, ...] = ()
    source: typing.Literal["client"] = field(default="client", init=False)

    def __post_init__(self) -> None:
        """校验客户端工作区快照。"""
        _validate_workspace(self.revision, self.patch, self.files)

    @classmethod
    def create(
        cls,
        *,
        patch: str = "",
        files: typing.Sequence[ReviewWorkspaceFile] = (),
    ) -> "ClientReviewWorkspace":
        """从快照正文创建带规范 revision 的客户端工作区。"""
        frozen_files = tuple(files)
        return cls(
            revision=workspace_revision(patch, frozen_files),
            patch=patch,
            files=frozen_files,
        )

    def request_payload(self) -> JsonObject:
        """返回客户端工作区的 wire 对象。"""
        return _workspace_payload(
            self.source,
            self.revision,
            self.patch,
            self.files,
        )


@dataclass(frozen=True, slots=True)
class ServerReviewWorkspace:
    """声明由服务端受控标识绑定的不可变工作区。"""

    revision: str
    workspace_id: str
    patch: str = ""
    files: tuple[ReviewWorkspaceFile, ...] = ()
    source: typing.Literal["server"] = field(default="server", init=False)

    def __post_init__(self) -> None:
        """校验服务端工作区标识和快照。"""
        if (
            not 8 <= len(self.workspace_id) <= 160
            or _WORKSPACE_ID_PATTERN.fullmatch(self.workspace_id) is None
        ):
            raise ValueError("workspace_id is invalid")
        _validate_workspace(self.revision, self.patch, self.files)

    def request_payload(self) -> JsonObject:
        """返回服务端工作区的 wire 对象。"""
        payload = _workspace_payload(
            self.source,
            self.revision,
            self.patch,
            self.files,
        )
        payload["workspace_id"] = self.workspace_id
        return payload


@dataclass(frozen=True, slots=True)
class ReferenceReviewWorkspace:
    """声明由签名引用绑定的外部不可变工作区。"""

    revision: str
    reference_id: str
    signature: str
    patch: str = ""
    files: tuple[ReviewWorkspaceFile, ...] = ()
    source: typing.Literal["reference"] = field(default="reference", init=False)

    def __post_init__(self) -> None:
        """校验引用标识、签名和快照。"""
        if (
            not 8 <= len(self.reference_id) <= 200
            or _REFERENCE_ID_PATTERN.fullmatch(self.reference_id) is None
        ):
            raise ValueError("reference_id is invalid")
        if not 16 <= len(self.signature) <= 1024:
            raise ValueError("reference signature is invalid")
        _validate_workspace(self.revision, self.patch, self.files)

    def request_payload(self) -> JsonObject:
        """返回引用工作区的 wire 对象。"""
        payload = _workspace_payload(
            self.source,
            self.revision,
            self.patch,
            self.files,
        )
        payload["reference_id"] = self.reference_id
        payload["signature"] = self.signature
        return payload


ReviewWorkspace: typing.TypeAlias = (
    ClientReviewWorkspace
    | ServerReviewWorkspace
    | ReferenceReviewWorkspace
)


def _parse_workspace_files(value: JsonValue) -> tuple[ReviewWorkspaceFile, ...]:
    """严格解析工作区文件列表。"""
    if not isinstance(value, list):
        raise TypeError("workspace files must be a list")
    files: list[ReviewWorkspaceFile] = []
    for raw_file in value:
        payload = _mapping(raw_file, "workspace file")
        _exact_fields(
            payload,
            frozenset({"path", "content", "sha256"}),
            "workspace file",
        )
        path = payload["path"]
        content = payload["content"]
        sha256 = payload["sha256"]
        if not isinstance(path, str) or not isinstance(content, str) or not isinstance(sha256, str):
            raise TypeError("workspace file fields must be text")
        files.append(ReviewWorkspaceFile(path=path, content=content, sha256=sha256))
    return tuple(files)


def parse_review_workspace(value: JsonValue) -> ReviewWorkspace:
    """严格解析 Review workspace 判别联合。"""
    payload = _mapping(value, "review workspace")
    source = payload.get("source")
    base_fields = frozenset({"source", "revision", "patch", "files"})
    required_fields = frozenset({"source", "revision"})
    allowed_fields = base_fields
    if source == "server":
        required_fields = required_fields | {"workspace_id"}
        allowed_fields = base_fields | {"workspace_id"}
    elif source == "reference":
        required_fields = required_fields | {"reference_id", "signature"}
        allowed_fields = base_fields | {"reference_id", "signature"}
    elif source != "client":
        raise ValueError("review workspace source is invalid")
    _declared_fields(
        payload,
        required=required_fields,
        allowed=allowed_fields,
        label="review workspace",
    )
    revision = payload["revision"]
    patch = payload.get("patch", "")
    if not isinstance(revision, str) or not isinstance(patch, str):
        raise TypeError("review workspace revision and patch must be text")
    files = _parse_workspace_files(payload.get("files", []))
    if source == "client":
        return ClientReviewWorkspace(revision=revision, patch=patch, files=files)
    if source == "server":
        workspace_id = payload["workspace_id"]
        if not isinstance(workspace_id, str):
            raise TypeError("workspace_id must be text")
        return ServerReviewWorkspace(
            revision=revision,
            workspace_id=workspace_id,
            patch=patch,
            files=files,
        )
    reference_id = payload["reference_id"]
    signature = payload["signature"]
    if not isinstance(reference_id, str) or not isinstance(signature, str):
        raise TypeError("review reference fields must be text")
    return ReferenceReviewWorkspace(
        revision=revision,
        reference_id=reference_id,
        signature=signature,
        patch=patch,
        files=files,
    )


@dataclass(frozen=True, slots=True)
class ReviewExecutionOptions:
    """描述 Review 专用的只读运行参数。"""

    llm_conf: JsonObject
    tools: tuple[JsonObject, ...] | None = None
    metadata: JsonObject | None = None

    def __post_init__(self) -> None:
        """复制运行参数并拒绝未声明为只读的工具。"""
        if not isinstance(self.llm_conf, dict):
            raise TypeError("review llm_conf must be an object")
        if self.tools is not None and not isinstance(self.tools, tuple):
            raise TypeError("review tools must be an immutable tuple or null")
        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise TypeError("review metadata must be an object or null")
        copied_llm_conf = _copy_json_object(self.llm_conf)
        copied_tools = (
            tuple(_copy_json_object(tool) for tool in self.tools)
            if self.tools is not None
            else None
        )
        for tool in copied_tools or ():
            annotations = tool.get("annotations")
            if not isinstance(annotations, dict) or annotations.get("readOnlyHint") is not True:
                raise ValueError("review tools must declare annotations.readOnlyHint=true")
        copied_metadata = (
            _copy_json_object(self.metadata)
            if self.metadata is not None
            else None
        )
        object.__setattr__(self, "llm_conf", copied_llm_conf)
        object.__setattr__(self, "tools", copied_tools)
        object.__setattr__(self, "metadata", copied_metadata)

    def request_payload(self) -> JsonObject:
        """返回只读 Review execution wire 对象。"""
        return {
            "llm_conf": _copy_json_object(self.llm_conf),
            "additional_context": [],
            "system_message": "",
            "attachments": None,
            "streaming": False,
            "tools": (
                [_copy_json_object(tool) for tool in self.tools]
                if self.tools is not None
                else None
            ),
            "hosted_tools": None,
            "skills": None,
            "sandbox_mode": "read-only",
            "metadata": (
                _copy_json_object(self.metadata)
                if self.metadata is not None
                else None
            ),
        }


_EXECUTION_FIELDS = frozenset({
    "llm_conf",
    "additional_context",
    "system_message",
    "attachments",
    "streaming",
    "tools",
    "hosted_tools",
    "skills",
    "sandbox_mode",
    "metadata",
})


def parse_review_execution(value: JsonValue) -> ReviewExecutionOptions:
    """严格解析客户端持久化的 Review execution 对象。"""
    payload = _mapping(value, "review execution")
    _exact_fields(payload, _EXECUTION_FIELDS, "review execution")
    if payload["additional_context"] != []:
        raise ValueError("review additional_context must be empty")
    for field_name, expected in (
        ("system_message", ""),
        ("attachments", None),
        ("streaming", False),
        ("hosted_tools", None),
        ("skills", None),
        ("sandbox_mode", "read-only"),
    ):
        if payload[field_name] != expected:
            raise ValueError(f"review {field_name} is invalid")
    llm_conf = _mapping(payload["llm_conf"], "review llm_conf")
    raw_tools = payload["tools"]
    tools: tuple[JsonObject, ...] | None
    if raw_tools is None:
        tools = None
    elif isinstance(raw_tools, list):
        tools = tuple(_mapping(item, "review tool") for item in raw_tools)
    else:
        raise TypeError("review tools must be a list or null")
    raw_metadata = payload["metadata"]
    metadata = (
        None
        if raw_metadata is None
        else _mapping(raw_metadata, "review metadata")
    )
    return ReviewExecutionOptions(
        llm_conf=llm_conf,
        tools=tools,
        metadata=metadata,
    )


@dataclass(frozen=True, slots=True)
class MindReviewRequest:
    """描述启动可靠代码审查 Turn 的冻结请求。"""

    request_id: str
    cid: str
    sid: str
    turn_id: str
    target: ReviewTarget
    workspace: ReviewWorkspace
    execution: ReviewExecutionOptions
    delivery: ReviewDelivery = "inline"

    def __post_init__(self) -> None:
        """校验请求坐标、目标快照边界和 metadata 身份。"""
        normalized_request_id = normalize_request_id(self.request_id)
        normalized_turn_id = normalize_turn_id(self.turn_id)
        normalized_cid = self.cid.strip()
        normalized_sid = self.sid.strip()
        if not valid_session_ids(normalized_cid, normalized_sid):
            raise ValueError("cid and sid must be valid related session identifiers")
        if self.delivery not in {"inline", "detached"}:
            raise ValueError("review delivery is invalid")
        if (
            not isinstance(self.target, ReviewCustomTarget)
            and not self.workspace.patch
            and not self.workspace.files
        ):
            raise ValueError("non-custom review workspace patch or files are required")
        metadata = self.execution.metadata or {}
        for key, expected in (("cid", normalized_cid), ("sid", normalized_sid)):
            supplied = metadata.get(key)
            if supplied is not None and supplied != expected:
                raise ValueError(f"execution.metadata.{key} does not match review context")
        object.__setattr__(self, "request_id", normalized_request_id)
        object.__setattr__(self, "turn_id", normalized_turn_id)
        object.__setattr__(self, "cid", normalized_cid)
        object.__setattr__(self, "sid", normalized_sid)

    def request_payload(self) -> JsonObject:
        """返回服务端 `/mind-review` 使用的严格请求对象。"""
        return {
            "request_id": self.request_id,
            "cid": self.cid,
            "sid": self.sid,
            "turn_id": self.turn_id,
            "target": self.target.request_payload(),
            "delivery": self.delivery,
            "workspace": self.workspace.request_payload(),
            "execution": self.execution.request_payload(),
        }

    def request_fingerprint(self) -> str:
        """生成不包含传输幂等键的稳定请求指纹。"""
        payload = self.request_payload()
        del payload["request_id"]
        return canonical_review_digest(payload)


def parse_mind_review_request(value: JsonValue) -> MindReviewRequest:
    """严格解析已持久化的 Review 请求。"""
    payload = _mapping(value, "mind review request")
    _exact_fields(
        payload,
        frozenset({
            "request_id",
            "cid",
            "sid",
            "turn_id",
            "target",
            "delivery",
            "workspace",
            "execution",
        }),
        "mind review request",
    )
    request_id = payload["request_id"]
    cid = payload["cid"]
    sid = payload["sid"]
    turn_id = payload["turn_id"]
    delivery = payload["delivery"]
    if not all(isinstance(item, str) for item in (request_id, cid, sid, turn_id)):
        raise TypeError("mind review request identity fields must be text")
    if delivery not in {"inline", "detached"}:
        raise ValueError("review delivery is invalid")
    return MindReviewRequest(
        request_id=request_id,
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        target=parse_review_target(payload["target"]),
        workspace=parse_review_workspace(payload["workspace"]),
        execution=parse_review_execution(payload["execution"]),
        delivery=delivery,
    )


@dataclass(frozen=True, slots=True)
class ReviewSession:
    """标识 Review 实际运行所在的会话。"""

    cid: str
    sid: str


@dataclass(frozen=True, slots=True)
class MindReviewReceipt:
    """描述 Review 命令完成持久登记后的回执。"""

    request_id: str
    status: ReviewReceiptStatus
    cid: str
    sid: str
    turn_id: str
    review_session: ReviewSession
    delivery: ReviewDelivery


def parse_review_response(value: JsonValue) -> MindReviewReceipt:
    """严格解析 `/mind-review` 的成功响应。"""
    payload = _mapping(value, "mind review response")
    _exact_fields(payload, frozenset({"ok", "data"}), "mind review response")
    if payload["ok"] is not True:
        raise ValueError("mind review response ok must be true")
    data = _mapping(payload["data"], "mind review response data")
    _exact_fields(
        data,
        frozenset({
            "request_id",
            "status",
            "cid",
            "sid",
            "turn_id",
            "review_session",
            "delivery",
        }),
        "mind review response data",
    )
    session = _mapping(data["review_session"], "review session")
    _exact_fields(session, frozenset({"cid", "sid"}), "review session")
    text_fields = {
        name: data[name]
        for name in ("request_id", "status", "cid", "sid", "turn_id", "delivery")
    }
    if not all(isinstance(item, str) for item in text_fields.values()):
        raise TypeError("mind review response fields must be text")
    review_cid = session["cid"]
    review_sid = session["sid"]
    if not isinstance(review_cid, str) or not isinstance(review_sid, str):
        raise TypeError("review session fields must be text")
    status = text_fields["status"]
    delivery = text_fields["delivery"]
    if status not in {"accepted", "idempotent"}:
        raise ValueError("mind review response status is invalid")
    if delivery not in {"inline", "detached"}:
        raise ValueError("mind review response delivery is invalid")
    request_id = normalize_request_id(text_fields["request_id"])
    turn_id = normalize_turn_id(text_fields["turn_id"])
    cid = text_fields["cid"].strip()
    sid = text_fields["sid"].strip()
    normalized_review_cid = review_cid.strip()
    normalized_review_sid = review_sid.strip()
    if not valid_session_ids(cid, sid) or not valid_session_ids(
        normalized_review_cid,
        normalized_review_sid,
    ):
        raise ValueError("mind review response contains invalid session identity")
    return MindReviewReceipt(
        request_id=request_id,
        status=status,
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        review_session=ReviewSession(
            cid=normalized_review_cid,
            sid=normalized_review_sid,
        ),
        delivery=delivery,
    )


@dataclass(frozen=True, slots=True)
class ReviewLineRange:
    """描述审查发现对应的闭区间行号。"""

    start: int
    end: int

    def __post_init__(self) -> None:
        """校验起止行号。"""
        if (
            isinstance(self.start, bool)
            or isinstance(self.end, bool)
            or not isinstance(self.start, int)
            or not isinstance(self.end, int)
            or self.start < 1
            or self.end < self.start
        ):
            raise ValueError("review line range is invalid")

    def payload(self) -> JsonObject:
        """返回行号范围的协议对象。"""
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class ReviewCodeLocation:
    """描述审查发现对应的受信代码位置。"""

    path: str
    line_range: ReviewLineRange

    def __post_init__(self) -> None:
        """规范化代码位置路径。"""
        object.__setattr__(
            self,
            "path",
            normalize_review_path(self.path, label="review code location"),
        )

    def payload(self) -> JsonObject:
        """返回代码位置的协议对象。"""
        return {"path": self.path, "line_range": self.line_range.payload()}


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """描述单条可定位的代码审查发现。"""

    title: str
    body: str
    confidence_score: float
    priority: int
    code_location: ReviewCodeLocation

    def __post_init__(self) -> None:
        """校验发现文本、置信度和优先级。"""
        normalized_title = self.title.strip()
        normalized_body = self.body.strip()
        if not normalized_title or len(normalized_title) > 500:
            raise ValueError("review finding title is invalid")
        if not normalized_body or len(normalized_body) > 20_000:
            raise ValueError("review finding body is invalid")
        if (
            isinstance(self.confidence_score, bool)
            or not isinstance(self.confidence_score, (int, float))
            or not 0.0 <= self.confidence_score <= 1.0
        ):
            raise ValueError("review finding confidence score is invalid")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 3
        ):
            raise ValueError("review finding priority is invalid")
        object.__setattr__(self, "title", normalized_title)
        object.__setattr__(self, "body", normalized_body)
        object.__setattr__(self, "confidence_score", float(self.confidence_score))

    def payload(self) -> JsonObject:
        """返回审查发现的协议对象。"""
        return {
            "title": self.title,
            "body": self.body,
            "confidence_score": self.confidence_score,
            "priority": self.priority,
            "code_location": self.code_location.payload(),
        }


@dataclass(frozen=True, slots=True)
class ReviewOutput:
    """描述 Review Turn 的唯一结构化结果。"""

    findings: tuple[ReviewFinding, ...]
    overall_correctness: ReviewCorrectness
    overall_explanation: str
    overall_confidence_score: float

    def __post_init__(self) -> None:
        """校验总体说明、置信度和发现数量。"""
        explanation = self.overall_explanation.strip()
        if not explanation or len(explanation) > 50_000:
            raise ValueError("review overall explanation is invalid")
        if self.overall_correctness not in {"correct", "incorrect", "unknown"}:
            raise ValueError("review overall correctness is invalid")
        if (
            isinstance(self.overall_confidence_score, bool)
            or not isinstance(self.overall_confidence_score, (int, float))
            or not 0.0 <= self.overall_confidence_score <= 1.0
        ):
            raise ValueError("review overall confidence score is invalid")
        if len(self.findings) > 200:
            raise ValueError("review output contains too many findings")
        object.__setattr__(self, "overall_explanation", explanation)
        object.__setattr__(
            self,
            "overall_confidence_score",
            float(self.overall_confidence_score),
        )

    def payload(self) -> JsonObject:
        """返回 Review 结果的协议对象。"""
        return {
            "findings": [item.payload() for item in self.findings],
            "overall_correctness": self.overall_correctness,
            "overall_explanation": self.overall_explanation,
            "overall_confidence_score": self.overall_confidence_score,
        }


def _parse_review_location(value: JsonValue) -> ReviewCodeLocation:
    """严格解析审查代码位置。"""
    payload = _mapping(value, "review code location")
    _exact_fields(
        payload,
        frozenset({"path", "line_range"}),
        "review code location",
    )
    path = payload["path"]
    if not isinstance(path, str):
        raise TypeError("review code location path must be text")
    raw_range = _mapping(payload["line_range"], "review line range")
    _exact_fields(
        raw_range,
        frozenset({"start", "end"}),
        "review line range",
    )
    return ReviewCodeLocation(
        path=path,
        line_range=ReviewLineRange(
            start=_strict_int(raw_range["start"], "review line range start"),
            end=_strict_int(raw_range["end"], "review line range end"),
        ),
    )


def _parse_review_finding(value: JsonValue) -> ReviewFinding:
    """严格解析单条 Review finding。"""
    payload = _mapping(value, "review finding")
    _exact_fields(
        payload,
        frozenset({
            "title",
            "body",
            "confidence_score",
            "priority",
            "code_location",
        }),
        "review finding",
    )
    return ReviewFinding(
        title=_required_text(payload["title"], "review finding title", max_length=500),
        body=_required_text(payload["body"], "review finding body", max_length=20_000),
        confidence_score=_strict_score(
            payload["confidence_score"],
            "review finding confidence_score",
        ),
        priority=_strict_int(payload["priority"], "review finding priority"),
        code_location=_parse_review_location(payload["code_location"]),
    )


def parse_review_output(value: JsonValue) -> ReviewOutput:
    """严格解析 ReviewOutput，拒绝未知字段。"""
    payload = _mapping(value, "review output")
    _declared_fields(
        payload,
        required=frozenset({
            "overall_correctness",
            "overall_explanation",
            "overall_confidence_score",
        }),
        allowed=frozenset({
            "findings",
            "overall_correctness",
            "overall_explanation",
            "overall_confidence_score",
        }),
        label="review output",
    )
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        raise TypeError("review findings must be a list")
    correctness = payload["overall_correctness"]
    if correctness not in {"correct", "incorrect", "unknown"}:
        raise ValueError("review overall correctness is invalid")
    return ReviewOutput(
        findings=tuple(_parse_review_finding(item) for item in raw_findings),
        overall_correctness=correctness,
        overall_explanation=_required_text(
            payload["overall_explanation"],
            "review overall explanation",
            max_length=50_000,
        ),
        overall_confidence_score=_strict_score(
            payload["overall_confidence_score"],
            "review overall confidence_score",
        ),
    )


if __name__ == '__main__':
    pass
