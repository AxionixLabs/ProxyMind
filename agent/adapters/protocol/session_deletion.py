# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports.session_deletion import (
    RemoteDeletionReceipt,
    RemoteDeletionRequest,
    RemoteDeletionTarget,
    SessionDeletionRemoteError,
)
from protocol.client.session_deletion import (
    SessionDeletionRequestError,
    delete_sessions,
    get_session_deletion,
)
from protocol.schema.session_deletion import (
    SessionDeletionRequest,
    SessionDeletionTarget,
)


class ProtocolSessionDeletionAdapter:
    """把正式 Session Deletion SDK 适配为 Harness 的远端端口。"""

    async def delete(self, request: RemoteDeletionRequest) -> RemoteDeletionReceipt:
        """提交冻结请求并返回已验证的本地端口回执。"""
        return await self._send(request, query=False)

    async def recover(self, request: RemoteDeletionRequest) -> RemoteDeletionReceipt:
        """查询同一请求身份，不生成新的幂等请求。"""
        return await self._send(request, query=True)

    async def _send(
        self,
        request: RemoteDeletionRequest,
        *,
        query: bool,
    ) -> RemoteDeletionReceipt:
        """在协议 SDK 与 Harness 类型之间完成一次严格转换。"""
        try:
            formal = SessionDeletionRequest(
                request_id=request.request_id,
                root=SessionDeletionTarget(request.root.cid, request.root.sid),
                descendants=tuple(
                    SessionDeletionTarget(item.cid, item.sid)
                    for item in request.descendants
                ),
            )
            receipt = (
                await get_session_deletion(formal)
                if query
                else await delete_sessions(formal)
            )
        except SessionDeletionRequestError as error:
            raise SessionDeletionRemoteError(
                outcome=error.outcome,
                code=error.code,
            ) from error
        except (TypeError, ValueError) as error:
            raise SessionDeletionRemoteError(
                outcome="unknown",
                code="invalid_request",
            ) from error

        return RemoteDeletionReceipt(
            request_id=receipt.request_id,
            root=RemoteDeletionTarget(receipt.root.cid, receipt.root.sid),
            targets=tuple(
                RemoteDeletionTarget(item.cid, item.sid)
                for item in receipt.targets
            ),
        )


if __name__ == '__main__':
    pass
