# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import typing
from pathlib import Path
from backend.utilities import const


def fail_tip(msg: str, *, code: str, hint: str, **meta) -> RuntimeError:
    """
    统一错误格式：模型/日志都好读。
    """
    payload = {"code": code, "message": msg, "hint": hint, "meta": meta}
    return RuntimeError(f"[{code}] {msg} | hint={hint} | meta={payload['meta']}")


def ensure_f(path: os.PathLike[str] | str, field: str) -> str:
    """确认 path 存在且为文件，且必须带扩展名；失败抛出统一提示。"""
    p = Path(path).expanduser()

    if not p.exists():
        raise fail_tip(
            f"{field} does not exist; a file path is required.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="f",
            got=str(p),
            reason="not_exists"
        )

    if not p.is_file():
        raise fail_tip(
            f"{field} has an invalid type; a file is required.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="f",
            got=str(p),
            reason="not_file"
        )

    # 必须有扩展名（容器/类型明确）
    if not p.suffix:
        raise fail_tip(
            f"{field} requires a file extension to determine the file type.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="suffix",
            got=str(p),
            reason="no_suffix"
        )

    return str(p.resolve())


def ensure_d(path: os.PathLike[str] | str, field: str) -> str:
    """确认 path 存在且为目录；失败抛出统一提示。"""
    p = Path(path)

    if not p.exists():
        raise fail_tip(
            f"{field} does not exist; a directory path is required.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="d",
            got=str(p),
            reason="not_exists"
        )

    if not p.is_dir():
        raise fail_tip(
            f"{field} has an invalid type; a directory is required.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="d",
            got=str(p),
            reason="not_dir"
        )

    return str(p.resolve())


def ensure_o(
    input_file: os.PathLike[str] | str,
    output_file: os.PathLike[str] | str | None,
    field: str,
    *,
    suffix: typing.Optional[str] = None,
    overwrite: bool = True
) -> str:
    """
    确认输出文件路径可用，并在需要时按输入文件推导默认输出名。
    确认输出路径可用（文件路径），支持 output_file 自动推导：
      - output_file 为空：输出到 input_file 同目录，文件名 <stem>_out<suffix>
      - output_file 为“已存在目录”：输出到该目录下，文件名 <stem>_out<suffix>
      - output_file 为文件路径：必须有扩展名；父目录不存在则创建
      - overwrite=False 时已存在则失败
    """
    in_p = Path(input_file).expanduser()

    is_blank: typing.Callable[
        [typing.Optional[str]], bool
    ] = lambda x: x is None or (isinstance(x, str) and x.strip() == "")

    # 1) 计算默认后缀：优先 suffix，其次沿用 input_file 后缀
    if is_blank(suffix):
        suf = in_p.suffix
    else:
        suf = str(suffix).strip()
        if not suf.startswith("."):
            suf = "." + suf

    if not suf:
        raise fail_tip(
            f"{field} requires an extension; suffix was not provided and input_file has no extension.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="out",
            got=str(in_p),
            reason="no_suffix"
        )

    # 默认输出文件名：避免覆盖输入
    default_name = f"{in_p.stem}_out{suf}"

    # 2) 推导 out_p
    if is_blank(output_file):
        # 没传：同目录输出
        out_p = in_p.with_name(default_name)

    else:
        cand = Path(output_file).expanduser()

        # 传的是有效目录：输出到该目录下
        if cand.exists() and cand.is_dir():
            out_p = cand / default_name

        else:
            # 当作文件路径：必须有后缀
            out_p = cand
            if not out_p.suffix:
                raise fail_tip(
                    f"{field} requires an extension to determine the output container.",
                    code=const.CODE_EXC,
                    hint=const.HINT_HLT,
                    field=field,
                    expect="out",
                    got=str(out_p),
                    reason="no_suffix"
                )

    parent = out_p.parent

    # 3) 父路径如果存在但不是目录：失败
    if parent.exists() and not parent.is_dir():
        raise fail_tip(
            f"{field} parent path has an invalid type; a directory is required.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="d",
            got=str(parent),
            reason="parent_not_dir"
        )

    # 4) 仅当父目录不存在时创建（默认同目录/有效目录分支不会走到这里创建）
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)

    # 5) 输出路径若已存在且是目录：失败
    if out_p.exists() and out_p.is_dir():
        raise fail_tip(
            f"{field} is a directory; a file path is required.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="f",
            got=str(out_p),
            reason="is_dir"
        )

    # 6) 不允许覆盖：已存在即失败
    if out_p.exists() and (not overwrite):
        raise fail_tip(
            f"{field} already exists and overwrite=False.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            expect="new",
            got=str(out_p),
            reason="exists_no_overwrite"
        )

    return str(out_p.resolve())


def ensure_i(state: typing.Optional[typing.Union[list, dict, set]], field: str, **meta: str) -> typing.Any:
    """
    确认内部状态非空（list/dict/set）；为空视为“内部未就绪/未回填”。
    """
    if not (empty := (state is None)):
        try:
            empty = (len(state) == 0)
        except TypeError:
            empty = False  # 没有 len 的对象不判空

    if empty:
        raise fail_tip(
            f"{field} is empty; internal state is not ready.",
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            field=field,
            got=repr(state),
            **meta
        )

    return state


def subproc_fail(*, source: str, out_ring: typing.Iterable[str], **meta: str) -> RuntimeError:
    """
    子进程错误统一出口：只用 out_ring（固定最后 10 行）。调用处用：raise subproc_fail(...)。
    """

    meta.pop("tail", None)  # 防止调用方重复传 tail

    tail = "\n".join(list(out_ring)[-10:])

    return fail_tip(
        "Subprocess reported an error.",
        code=const.CODE_EXC,
        hint=const.HINT_HLT,
        source=source,
        tail=tail,
        **meta
    )


def except_tip(reason: str, exc: BaseException, **meta: typing.Any) -> RuntimeError:
    """
    把捕获到的异常统一包装成 fail_tip；reason 由调用方自己写，调用处用：raise except_tip(...)。
    """
    return fail_tip(
        reason,
        code=const.CODE_EXC,
        hint=const.HINT_HLT,
        exc_type=type(exc).__name__,
        exc=str(exc),
        **meta
    )


def port_busy(port: int, field: str, *, host: str = "127.0.0.1", **meta: str) -> RuntimeError:
    """
    端口占用统一提示。
    用法：raise port_busy(3300, "liveness_port", host="127.0.0.1")
    """
    return fail_tip(
        f"{field} port is already in use and cannot be bound.",
        code=const.CODE_EXC,
        hint=const.HINT_HLT,
        field=field,
        host=str(host),
        port=int(port),
        **meta
    )


if __name__ == '__main__':
    pass
