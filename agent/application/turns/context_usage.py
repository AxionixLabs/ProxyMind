# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import Callable

from agent.application.views.context_usage import ContextUsageView
from agent.protocol.context_usage import ContextUsageRecord


class ContextUsageProjection:
    """持有单个活动会话的用量投影，由根会话所有者驱动并负责持久化。

    订阅属于根会话展示生命周期，调用方负责解除；不创建任务或读取外部数据。
    重放只更新完整事实，在传输确认追平后发布最终值。
    """

    def __init__(self) -> None:
        """初始化尚未执行模型调用的新会话展示。"""
        self._identity: tuple[str, str] | None = None
        self._record: ContextUsageRecord | None = None
        self._retained_floor = 0
        self._view = ContextUsageView("initial")
        self._replaying = False
        self._listeners: list[Callable[[ContextUsageView], None]] = []

    @property
    def view(self) -> ContextUsageView:
        """返回当前可见快照。"""
        return self._view

    def subscribe(self, listener: Callable[[ContextUsageView], None]) -> Callable[[], None]:
        """立即交付当前值并返回幂等的取消订阅函数。"""
        self._listeners.append(listener)
        try:
            listener(self._view)
        except BaseException:
            self._listeners.remove(listener)
            raise

        def unsubscribe() -> None:
            """移除本次订阅，不影响其他展示端。"""
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def activate(self, cid: str, sid: str, *, initial: bool) -> None:
        """绑定目标会话并清空前一会话的用量。"""
        self._identity = (cid, sid)
        self._record = None
        self._retained_floor = 0
        self._replaying = not initial
        self._publish(ContextUsageView("initial" if initial else "pending"))

    def accepts(self, record: ContextUsageRecord) -> bool:
        """仅接收活动会话中比已保存事实更新的事件序号。"""
        return (
            self._identity == (record.cid, record.sid)
            and record.event_seq > self._retained_floor
            and (self._record is None or record.event_seq > self._record.event_seq)
        )

    def mark_started(self) -> None:
        """首个模型请求开始后，不把缺失用量继续展示为新会话。"""
        if self._view.status == "initial":
            self._publish(ContextUsageView("unknown"))

    def apply(self, record: ContextUsageRecord) -> bool:
        """替换最近和累计快照；不从本地事件累加计费。"""
        if not self.accepts(record):
            return False
        self._record = record
        if not self._replaying:
            self._publish_record()
        return True

    def begin_replay(self, cid: str, sid: str) -> None:
        """隐藏当前会话的中间恢复画面。"""
        if self._identity != (cid, sid):
            return
        self._replaying = True
        self._publish(ContextUsageView("pending"))

    def discard_retained_prefix(self, cid: str, sid: str, event_seq: int) -> None:
        """作废落在不可完整恢复区间内的旧快照，等待后续权威事件。"""
        if self._identity != (cid, sid):
            return
        self._retained_floor = max(self._retained_floor, event_seq)
        if self._record is not None and self._record.event_seq <= event_seq:
            self._record = None
            if not self._replaying:
                self._publish_record()

    def finish_replay(self, cid: str, sid: str) -> None:
        """在恢复完成边界一次发布最终事实，缺失时保持未知。"""
        if self._identity != (cid, sid) or not self._replaying:
            return
        self._replaying = False
        self._publish_record()

    def close(self, *, clear_listeners: bool = False) -> None:
        """释放活动会话事实，根展示结束时一并释放订阅。"""
        self._identity = None
        self._record = None
        self._retained_floor = 0
        self._replaying = False
        self._publish(ContextUsageView("unknown"))
        if clear_listeners:
            self._listeners.clear()

    def _publish_record(self) -> None:
        """根据可靠计数的可用性形成展示状态。"""
        record = self._record
        known = record is not None and (
            (record.model_context_window is not None and record.last_total_tokens is not None)
            or (record.model_context_window is None and record.total_tokens is not None)
        )
        self._publish(ContextUsageView("known" if known else "unknown", record))

    def _publish(self, view: ContextUsageView) -> None:
        """仅在快照变化时通知当前订阅者。"""
        if view == self._view:
            return
        self._view = view
        for listener in tuple(self._listeners):
            listener(view)


if __name__ == '__main__':
    pass
