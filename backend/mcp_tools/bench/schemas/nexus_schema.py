# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt
)
from backend.models.model_nexus import NexusKind


class NexusToolSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class BatchItemBase(NexusToolSchemaModel):
    name: typing.Optional[str] = Field(default=None, description="可选用例名，便于在结果、日志和批量输出中识别当前请求。")
    extract: typing.Optional[dict[str, str]] = Field(default=None, description="结果提取规则，键为输出名，值为提取表达式或路径。")
    asserts: typing.Optional[list[dict[str, typing.Any]]] = Field(
        default=None,
        description="断言列表。每项通常描述比较目标、操作符和期望值。"
    )


class BatchArgsBase(NexusToolSchemaModel):
    template_vars: typing.Optional[dict[str, typing.Any]] = Field(
        default=None,
        description="模板变量字典，用于渲染请求中的占位符。"
    )
    concurrency: StrictInt = Field(
        default=1,
        ge=1,
        description="批量执行的最大并发数。若预期并发执行，必须显式传入整数；不要因校验失败而省略字段回退到默认值 `1`。"
    )
    fail_fast: StrictBool = Field(
        default=True,
        description="批量执行时遇到首个失败是否立即停止剩余请求。若预期继续执行剩余项，必须显式传入布尔值；不要因校验失败而省略字段回退到默认值 `true`。"
    )


class FlatBatchItemBase(BatchItemBase):
    pass


class HttpLikeBodyMixin(NexusToolSchemaModel):
    json_body: typing.Optional[typing.Any] = Field(
        default=None,
        alias="json",
        validation_alias=AliasChoices("json", "json_body")
    )
    body_text: typing.Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("body_text", "body")
    )


class BodyTextAliasMixin(NexusToolSchemaModel):
    body_text: typing.Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("body_text", "body")
    )


class GraphqlAliasMixin(NexusToolSchemaModel):
    operation_name: typing.Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("operation_name", "operationName")
    )


class GenericSharedEnv(HttpLikeBodyMixin, GraphqlAliasMixin):
    method: typing.Optional[str] = None
    url: typing.Optional[str] = None
    base_url: typing.Optional[str] = None
    headers: typing.Optional[dict[str, typing.Any]] = None
    params: typing.Optional[typing.Any] = None
    form: typing.Optional[dict[str, typing.Any]] = None
    files: typing.Optional[list[typing.Any]] = None
    timeout: typing.Optional[float | int] = None
    retries: typing.Optional[int] = None
    follow_redirects: typing.Optional[bool] = None
    max_events: typing.Optional[int] = None
    media_index: typing.Optional[int] = None
    media_path: typing.Optional[str] = None
    query: typing.Optional[str] = None
    variables: typing.Optional[dict[str, typing.Any]] = None
    host: typing.Optional[str] = None
    port: typing.Optional[int] = None
    sends: typing.Optional[list[str] | str] = None
    encoding: typing.Optional[str] = None
    read_size: typing.Optional[int] = None
    close_write: typing.Optional[bool] = None
    max_reads: typing.Optional[int] = None
    read_until: typing.Optional[str] = None
    action: typing.Optional[str] = None
    username: typing.Optional[str] = None
    password: typing.Optional[str] = None
    use_ssl: typing.Optional[bool] = None
    use_tls: typing.Optional[bool] = None
    from_addr: typing.Optional[str] = None
    to_addrs: typing.Optional[list[str] | str] = None
    subject: typing.Optional[str] = None
    html_body: typing.Optional[str] = None
    attachments: typing.Optional[list[typing.Any]] = None
    mailbox: typing.Optional[str] = None
    criteria: typing.Optional[str] = None
    message_set: typing.Optional[str] = None
    fetch_parts: typing.Optional[str] = None
    parse_messages: typing.Optional[bool] = None
    path: typing.Optional[str] = None
    payload_text: typing.Optional[str] = None
    payload_base64: typing.Optional[str] = None
    max_messages: typing.Optional[int] = None


class GenericBatchItem(FlatBatchItemBase, GenericSharedEnv):
    pass


class HttpSharedEnv(HttpLikeBodyMixin):
    method: typing.Optional[str] = None
    url: typing.Optional[str] = None
    base_url: typing.Optional[str] = None
    headers: typing.Optional[dict[str, typing.Any]] = None
    params: typing.Optional[typing.Any] = None
    form: typing.Optional[dict[str, typing.Any]] = None
    files: typing.Optional[list[typing.Any]] = None
    timeout: typing.Optional[float | int] = None
    retries: typing.Optional[int] = None
    follow_redirects: typing.Optional[bool] = None


class HttpFlatBatchItem(FlatBatchItemBase, HttpSharedEnv):
    pass


class SseSharedEnv(HttpSharedEnv):
    max_events: typing.Optional[int] = None
    media_index: typing.Optional[int] = None
    media_path: typing.Optional[str] = None


class SseFlatBatchItem(FlatBatchItemBase, SseSharedEnv):
    pass


class GraphqlSharedEnv(GraphqlAliasMixin):
    url: typing.Optional[str] = None
    base_url: typing.Optional[str] = None
    headers: typing.Optional[dict[str, typing.Any]] = None
    params: typing.Optional[typing.Any] = None
    query: typing.Optional[str] = None
    variables: typing.Optional[dict[str, typing.Any]] = None
    timeout: typing.Optional[float | int] = None
    retries: typing.Optional[int] = None
    follow_redirects: typing.Optional[bool] = None
    media_path: typing.Optional[str] = None


class GraphqlFlatBatchItem(FlatBatchItemBase, GraphqlSharedEnv):
    pass


class WsSharedEnv(NexusToolSchemaModel):
    url: typing.Optional[str] = None
    headers: typing.Optional[dict[str, typing.Any]] = None
    sends: typing.Optional[list[str] | str] = None
    timeout: typing.Optional[float | int] = None
    max_messages: typing.Optional[int] = None
    media_index: typing.Optional[int] = None
    media_path: typing.Optional[str] = None


class WsFlatBatchItem(FlatBatchItemBase, WsSharedEnv):
    pass


class TcpSharedEnv(BodyTextAliasMixin):
    host: typing.Optional[str] = None
    port: typing.Optional[int] = None
    sends: typing.Optional[list[str] | str] = None
    encoding: typing.Optional[str] = None
    timeout: typing.Optional[float | int] = None
    read_size: typing.Optional[int] = None
    close_write: typing.Optional[bool] = None
    max_reads: typing.Optional[int] = None
    read_until: typing.Optional[str] = None


class TcpFlatBatchItem(FlatBatchItemBase, TcpSharedEnv):
    pass


class UdpSharedEnv(BodyTextAliasMixin):
    host: typing.Optional[str] = None
    port: typing.Optional[int] = None
    encoding: typing.Optional[str] = None
    timeout: typing.Optional[float | int] = None
    read_size: typing.Optional[int] = None


class UdpFlatBatchItem(FlatBatchItemBase, UdpSharedEnv):
    pass


class SmtpSharedEnv(BodyTextAliasMixin):
    host: typing.Optional[str] = None
    port: typing.Optional[int] = None
    action: typing.Optional[str] = None
    username: typing.Optional[str] = None
    password: typing.Optional[str] = None
    use_ssl: typing.Optional[bool] = None
    use_tls: typing.Optional[bool] = None
    from_addr: typing.Optional[str] = None
    to_addrs: typing.Optional[list[str] | str] = None
    subject: typing.Optional[str] = None
    html_body: typing.Optional[str] = None
    attachments: typing.Optional[list[typing.Any]] = None
    timeout: typing.Optional[float | int] = None


class SmtpFlatBatchItem(FlatBatchItemBase, SmtpSharedEnv):
    pass


class ImapSharedEnv(NexusToolSchemaModel):
    host: typing.Optional[str] = None
    port: typing.Optional[int] = None
    username: typing.Optional[str] = None
    password: typing.Optional[str] = None
    action: typing.Optional[str] = None
    mailbox: typing.Optional[str] = None
    criteria: typing.Optional[str] = None
    message_set: typing.Optional[str] = None
    fetch_parts: typing.Optional[str] = None
    parse_messages: typing.Optional[bool] = None
    use_ssl: typing.Optional[bool] = None
    timeout: typing.Optional[float | int] = None
    media_path: typing.Optional[str] = None


class ImapFlatBatchItem(FlatBatchItemBase, ImapSharedEnv):
    pass


class FtpSharedEnv(NexusToolSchemaModel):
    host: typing.Optional[str] = None
    port: typing.Optional[int] = None
    username: typing.Optional[str] = None
    password: typing.Optional[str] = None
    action: typing.Optional[str] = None
    path: typing.Optional[str] = None
    payload_text: typing.Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("payload_text", "body_text", "body")
    )
    payload_base64: typing.Optional[str] = None
    encoding: typing.Optional[str] = None
    use_tls: typing.Optional[bool] = None
    timeout: typing.Optional[float | int] = None
    media_path: typing.Optional[str] = None


class FtpFlatBatchItem(FlatBatchItemBase, FtpSharedEnv):
    pass


def _request_arg_desc(protocol: str, fields_hint: str) -> str:
    return (
        f"单次 {protocol} 请求定义。必须传结构化对象；协议字段直接放在 `request` 中，例如 {fields_hint}。"
        "不要传字符串化 JSON。"
    )


def _batch_items_arg_desc(protocol: str) -> str:
    return (
        f"{protocol} 批量请求项列表。每项直接包含 {protocol} 协议字段以及可选 `name`、`extract`、`asserts`；"
        "无需再包一层 `request`。必须传原生数组对象，不要传字符串化 JSON。"
    )


def _batch_env_arg_desc(protocol: str) -> str:
    return (
        f"{protocol} 批量共享默认值。执行时会先应用这里的字段，再由当前项覆盖同名字段。"
        "必须传原生对象，不要传字符串化 JSON。"
    )


NexusKindArg = typing.Annotated[
    NexusKind,
    Field(description="Nexus 协议类型，如 http、sse、ws、graphql、tcp、udp、smtp、imap 或 ftp。"),
]
NexusRequestArg = typing.Annotated[
    GenericSharedEnv,
    Field(description="单次标准化请求定义。协议相关字段都放在这里，例如 url、method、headers、json/json_body、body/body_text、sends 或 action。必须传结构化对象，不要传字符串化 JSON。"),
]
NexusEnvArg = typing.Annotated[
    typing.Optional[GenericSharedEnv],
    Field(description="批量或预执行阶段的共享默认值。执行或校验时会先应用这里的字段，再由当前 `request` 覆盖同名字段。必须传结构化对象，不要传字符串化 JSON。"),
]
NexusTemplateVarsArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="模板变量字典，用于渲染请求中的占位符。"),
]
NexusExtractArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="结果提取规则，键为输出名，值为提取表达式或路径。"),
]
NexusAssertsArg = typing.Annotated[
    typing.Optional[list[dict[str, typing.Any]]],
    Field(description="断言列表。每项通常描述比较目标、操作符和期望值。"),
]
NexusNameArg = typing.Annotated[
    typing.Optional[str],
    Field(description="可选用例名，便于在结果、日志和批量输出中识别当前请求。"),
]
GenericBatchItemsArg = typing.Annotated[
    list[GenericBatchItem],
    Field(description="批量请求项列表。每项直接包含协议字段以及可选 `name`、`extract` 和 `asserts`；无需再包一层 `request`。必须传原生数组对象，不要传字符串化 JSON。"),
]
GenericBatchEnvArg = typing.Annotated[
    typing.Optional[GenericSharedEnv],
    Field(description="批量或预执行阶段的共享默认值。执行或校验时会先应用这里的字段，再由当前项覆盖同名字段。必须传原生对象，不要传字符串化 JSON。"),
]
HttpBatchItemsArg = typing.Annotated[list[HttpFlatBatchItem], Field(description=_batch_items_arg_desc("HTTP"))]
HttpBatchEnvArg = typing.Annotated[typing.Optional[HttpSharedEnv], Field(description=_batch_env_arg_desc("HTTP"))]
SseBatchItemsArg = typing.Annotated[list[SseFlatBatchItem], Field(description=_batch_items_arg_desc("SSE"))]
SseBatchEnvArg = typing.Annotated[typing.Optional[SseSharedEnv], Field(description=_batch_env_arg_desc("SSE"))]
GraphqlBatchItemsArg = typing.Annotated[list[GraphqlFlatBatchItem], Field(description=_batch_items_arg_desc("GraphQL"))]
GraphqlBatchEnvArg = typing.Annotated[typing.Optional[GraphqlSharedEnv], Field(description=_batch_env_arg_desc("GraphQL"))]
WsBatchItemsArg = typing.Annotated[list[WsFlatBatchItem], Field(description=_batch_items_arg_desc("WebSocket"))]
WsBatchEnvArg = typing.Annotated[typing.Optional[WsSharedEnv], Field(description=_batch_env_arg_desc("WebSocket"))]
TcpBatchItemsArg = typing.Annotated[list[TcpFlatBatchItem], Field(description=_batch_items_arg_desc("TCP"))]
TcpBatchEnvArg = typing.Annotated[typing.Optional[TcpSharedEnv], Field(description=_batch_env_arg_desc("TCP"))]
UdpBatchItemsArg = typing.Annotated[list[UdpFlatBatchItem], Field(description=_batch_items_arg_desc("UDP"))]
UdpBatchEnvArg = typing.Annotated[typing.Optional[UdpSharedEnv], Field(description=_batch_env_arg_desc("UDP"))]
SmtpBatchItemsArg = typing.Annotated[list[SmtpFlatBatchItem], Field(description=_batch_items_arg_desc("SMTP"))]
SmtpBatchEnvArg = typing.Annotated[typing.Optional[SmtpSharedEnv], Field(description=_batch_env_arg_desc("SMTP"))]
ImapBatchItemsArg = typing.Annotated[list[ImapFlatBatchItem], Field(description=_batch_items_arg_desc("IMAP"))]
ImapBatchEnvArg = typing.Annotated[typing.Optional[ImapSharedEnv], Field(description=_batch_env_arg_desc("IMAP"))]
FtpBatchItemsArg = typing.Annotated[list[FtpFlatBatchItem], Field(description=_batch_items_arg_desc("FTP"))]
FtpBatchEnvArg = typing.Annotated[typing.Optional[FtpSharedEnv], Field(description=_batch_env_arg_desc("FTP"))]
HttpRequestArg = typing.Annotated[HttpSharedEnv, Field(description=_request_arg_desc("HTTP", "`url`、`method`、`headers`、`json`/`json_body`、`body`/`body_text`"))]
SseRequestArg = typing.Annotated[SseSharedEnv, Field(description=_request_arg_desc("SSE", "`url`、`method`、`headers`、`max_events`、`media_index`、`media_path`"))]
GraphqlRequestArg = typing.Annotated[GraphqlSharedEnv, Field(description=_request_arg_desc("GraphQL", "`url`、`query`、`variables`、`operation_name`"))]
WsRequestArg = typing.Annotated[WsSharedEnv, Field(description=_request_arg_desc("WebSocket", "`url`、`headers`、`sends`、`max_messages`"))]
TcpRequestArg = typing.Annotated[TcpSharedEnv, Field(description=_request_arg_desc("TCP", "`host`、`port`、`body_text`、`sends`、`read_until`"))]
UdpRequestArg = typing.Annotated[UdpSharedEnv, Field(description=_request_arg_desc("UDP", "`host`、`port`、`body_text`、`encoding`、`read_size`"))]
SmtpRequestArg = typing.Annotated[SmtpSharedEnv, Field(description=_request_arg_desc("SMTP", "`host`、`port`、`action`、`from_addr`、`to_addrs`、`body_text`"))]
ImapRequestArg = typing.Annotated[ImapSharedEnv, Field(description=_request_arg_desc("IMAP", "`host`、`port`、`username`、`password`、`action`、`message_set`"))]
FtpRequestArg = typing.Annotated[FtpSharedEnv, Field(description=_request_arg_desc("FTP", "`host`、`port`、`action`、`path`、`payload_text`、`payload_base64`"))]
NexusConcurrencyArg = typing.Annotated[StrictInt, Field(description="批量执行的最大并发数。")]
NexusFailFastArg = typing.Annotated[StrictBool, Field(description="批量执行时遇到首个失败是否立即停止剩余请求。")]


if __name__ == '__main__':
    pass
