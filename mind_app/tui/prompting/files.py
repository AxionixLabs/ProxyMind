# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import shutil
import typing
import threading
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from prompt_toolkit.completion import Completion
from .skills import skill_query_token

FILE_SEARCH_RESULT_LIMIT = 20
FILE_SEARCH_BATCH_SIZE   = 256

_CHAR_WHITESPACE = 0
_CHAR_NON_WORD   = 1
_CHAR_DELIMITER  = 2
_CHAR_LOWER      = 3
_CHAR_UPPER      = 4
_CHAR_LETTER     = 5
_CHAR_NUMBER     = 6


@dataclass(frozen=True, slots=True)
class FileSearchEntry(object):
    """保存工作区中的一个文件或目录候选。"""
    relative: str
    display_name: str
    parent_display: str
    is_directory: bool
    match_indices: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _FileSearchSnapshot(object):
    """保存一次后台文件匹配快照。"""
    query: str
    matches: tuple[FileSearchEntry, ...]


class _FileSearchSession(object):
    """在后台维护一个工作区索引并响应连续查询更新。"""

    def __init__(
        self,
        root: Path,
        report: typing.Callable[["_FileSearchSession", _FileSearchSnapshot], None],
    ) -> None:
        self.root = root

        self._report    = report
        self._condition = threading.Condition()

        self._entries: dict[str, bool] = {}

        self._query: str     = ""
        self._query_revision = 0
        self._entry_revision = 0
        self._stopped: bool  = False

        self._process: subprocess.Popen[bytes] | None = None

        self._matcher = threading.Thread(
            target=self._match_worker,
            name="file-search-matcher",
            daemon=True,
        )
        self._walker = threading.Thread(
            target=self._walk_worker,
            name="file-search-walker",
            daemon=True,
        )
        self._matcher.start()
        self._walker.start()

    def update_query(self, query: str) -> None:
        """把最新查询发送给后台匹配线程。"""
        with self._condition:
            if self._stopped or query == self._query:
                return
            self._query = query
            self._query_revision += 1
            self._condition.notify_all()

    def close(self) -> None:
        """停止当前搜索会话和仍在运行的文件枚举进程。"""
        with self._condition:
            if self._stopped:
                return
            self._stopped = True
            process = self._process
            self._condition.notify_all()

        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _walk_worker(self) -> None:
        """使用项目随附的 ripgrep 在后台枚举可搜索路径。"""
        executable = shutil.which("rg")
        if executable is None:
            self._finish_walk()
            return

        command = [
            executable,
            "--files",
            "--hidden",
            "--follow",
            "--require-git",
            "--no-messages",
            "--glob",
            "!.git",
            "--null",
        ]
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except OSError:
            self._finish_walk()
            return

        with self._condition:
            if self._stopped:
                process.terminate()
                return
            self._process = process

        pending = b""
        batch: list[str] = []
        stdout = process.stdout
        if stdout is not None:
            while True:
                chunk = stdout.read(65536)
                if not chunk:
                    break
                pending += chunk
                parts = pending.split(b"\0")
                pending = parts.pop()
                batch.extend(os.fsdecode(part) for part in parts if part)
                if len(batch) >= FILE_SEARCH_BATCH_SIZE:
                    self._add_paths(batch)
                    batch.clear()
                with self._condition:
                    if self._stopped:
                        break

        if pending:
            batch.append(os.fsdecode(pending))
        if batch:
            self._add_paths(batch)

        try:
            process.wait()
        except OSError:
            pass
        self._walk_directories()
        self._finish_walk()

    def _walk_directories(self) -> None:
        """枚举文件结果无法覆盖的目录并按 Git ignore 规则剪枝。"""
        try:
            root_stat = self.root.stat()
        except OSError:
            return

        visited = {(root_stat.st_dev, root_stat.st_ino)}
        pending: list[tuple[Path, str]] = [(self.root, "")]
        while pending:
            discovered: list[tuple[Path, str, tuple[int, int]]] = []
            for current, parent_relative in pending:
                try:
                    children = os.scandir(current)
                except OSError:
                    continue
                with children:
                    for child in children:
                        if child.name == ".git":
                            continue
                        try:
                            if not child.is_dir(follow_symlinks=True):
                                continue
                            stat_result = child.stat(follow_symlinks=True)
                        except OSError:
                            continue
                        relative = (
                            f"{parent_relative}/{child.name}"
                            if parent_relative else child.name
                        )
                        normalized = _normalize_relative_path(relative)
                        identity = (stat_result.st_dev, stat_result.st_ino)
                        if not normalized:
                            continue
                        discovered.append((Path(child.path), normalized, identity))

            if not discovered:
                return

            ignored = _git_ignored_paths(
                self.root,
                (relative for _path, relative, _identity in discovered),
            )
            next_pending: list[tuple[Path, str]] = []
            visible: list[str] = []
            for path, relative, identity in discovered:
                if relative in ignored:
                    continue
                visible.append(relative)
                if identity in visited:
                    continue
                visited.add(identity)
                next_pending.append((path, relative))
            self._add_directories(visible)
            pending = next_pending

            with self._condition:
                if self._stopped:
                    return

    def _add_paths(self, paths: typing.Iterable[str]) -> None:
        """把一批文件及其父目录加入当前索引。"""
        additions: dict[str, bool] = {}
        for raw_path in paths:
            relative = _normalize_relative_path(raw_path)
            if not relative or _contains_git_metadata(relative):
                continue
            additions[relative] = False

            parent = relative.rpartition("/")[0]
            while parent:
                additions.setdefault(parent, True)
                parent = parent.rpartition("/")[0]

        if not additions:
            return

        with self._condition:
            if self._stopped:
                return
            changed = False
            for relative, is_directory in additions.items():
                if relative not in self._entries:
                    self._entries[relative] = is_directory
                    changed = True
            if changed:
                self._entry_revision += 1
                self._condition.notify_all()

    def _add_directories(self, paths: typing.Iterable[str]) -> None:
        """把经过 ignore 过滤的一批目录加入当前索引。"""
        additions = {
            relative: True
            for value in paths
            if (relative := _normalize_relative_path(value))
            and not _contains_git_metadata(relative)
        }
        if not additions:
            return

        with self._condition:
            if self._stopped:
                return
            changed = False
            for relative, is_directory in additions.items():
                if relative not in self._entries:
                    self._entries[relative] = is_directory
                    changed = True
            if changed:
                self._entry_revision += 1
                self._condition.notify_all()

    def _finish_walk(self) -> None:
        """通知匹配线程文件枚举已经完成。"""
        with self._condition:
            self._process = None
            if self._stopped:
                return
            self._entry_revision += 1
            self._condition.notify_all()

    def _match_worker(self) -> None:
        """合并快速查询更新并发布最新的前若干项。"""
        matched_query_revision = -1
        matched_entry_revision = -1

        while True:
            with self._condition:
                self._condition.wait_for(lambda: (
                    self._stopped
                    or self._query_revision != matched_query_revision
                    or self._entry_revision != matched_entry_revision
                ))
                if self._stopped:
                    return

                query = self._query
                query_revision = self._query_revision
                entry_revision = self._entry_revision
                entries: tuple[tuple[str, bool], ...] = tuple(
                    (relative, is_directory)
                    for relative, is_directory in self._entries.items()
                )

            matches = _ranked_entries(entries, query)

            with self._condition:
                if self._stopped:
                    return
                if query_revision != self._query_revision:
                    continue
                matched_query_revision = query_revision
                matched_entry_revision = entry_revision

            self._report(
                self,
                _FileSearchSnapshot(query=query, matches=matches),
            )


class FileSearchManager(object):
    """管理当前 `@` 文件搜索会话及可供界面读取的结果快照。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._root: Path | None = None
        self._pending_query = ""
        self._display_query = ""
        self._matches: tuple[FileSearchEntry, ...] = ()
        self._waiting = False
        self._session: _FileSearchSession | None = None
        self._refresh: typing.Callable[[], None] = lambda: None

    def bind_refresh(self, refresh: typing.Callable[[], None]) -> None:
        """绑定后台结果到达时使用的线程安全刷新动作。"""
        self._refresh = refresh

    def completions(
        self,
        text: str,
        *,
        workspace_root: Path | str | None,
    ) -> tuple[Completion, ...]:
        """更新当前查询并返回最近一次可展示的文件候选。"""
        token = skill_query_token(text)
        if token is None or not token.startswith("@"):
            self.cancel()
            return ()

        query = _normalize_query(token[1:])
        if not query or workspace_root is None:
            self.cancel()
            return ()

        root = Path(workspace_root).expanduser().resolve()
        with self._lock:
            if root != self._root:
                previous = self._session
                self._root = root
                self._session = None
                self._matches = ()
                self._display_query = ""
            else:
                previous = None

            if query != self._pending_query:
                self._pending_query = query
                self._waiting = True

            session = self._session
            if session is None:
                session = _FileSearchSession(root, self._receive_snapshot)
                self._session = session
            matches = self._matches

        if previous is not None:
            previous.close()
        session.update_query(query)
        return _completion_snapshot(token, matches)

    def cancel(self) -> None:
        """丢弃当前非空查询和对应后台会话。"""
        with self._lock:
            session = self._session
            self._session = None
            self._pending_query = ""
            self._display_query = ""
            self._matches = ()
            self._waiting = False
        if session is not None:
            session.close()

    def close(self) -> None:
        """关闭当前文件搜索会话。"""
        self.cancel()

    def empty_message(self, text: str) -> str:
        """返回当前 `@` 查询没有候选时应展示的状态文字。"""
        token = skill_query_token(text)
        if token is None or not token.startswith("@") or len(token) == 1:
            return "no matches"
        query = _normalize_query(token[1:])
        with self._lock:
            waiting = self._waiting and query == self._pending_query
        return "loading..." if waiting else "no matches"

    def match_indices(
        self,
        relative: str,
        display_name: str,
        query: str,
    ) -> tuple[int, ...] | None:
        """返回当前快照中位于文件名列内的最优命中位置。"""
        normalized_relative = _normalize_relative_path(relative)
        normalized_query = _normalize_query(query)
        with self._lock:
            if normalized_query != self._display_query:
                return None
            entry = next(
                (
                    candidate
                    for candidate in self._matches
                    if candidate.relative == normalized_relative
                ),
                None,
            )
        if entry is None or not entry.match_indices:
            return None

        name_start = len(entry.relative) - len(display_name)
        local = tuple(index - name_start for index in entry.match_indices)
        if local and min(local) >= 0 and max(local) < len(display_name):
            return local
        return None

    def _receive_snapshot(
        self,
        session: _FileSearchSession,
        snapshot: _FileSearchSnapshot,
    ) -> None:
        """接收活动会话的结果并拒绝旧查询回写。"""
        with self._lock:
            if (
                session is not self._session
                or snapshot.query != self._pending_query
                or not snapshot.query
            ):
                return
            changed = (
                snapshot.query != self._display_query
                or snapshot.matches != self._matches
                or self._waiting
            )
            self._display_query = snapshot.query
            self._matches = snapshot.matches
            self._waiting = False
        if changed:
            self._refresh()


def file_completions(
    text: str,
    *,
    workspace_root: Path | str | None,
    search: FileSearchManager
) -> tuple[Completion, ...]:
    """返回当前后台文件搜索快照对应的补全项。"""
    return search.completions(text, workspace_root=workspace_root)


def file_category(meta_text: str) -> str | None:
    """返回候选说明末尾的文件类型标签。"""
    _parent, separator, category = str(meta_text or "").strip().rpartition("  ")
    if separator and category in {"File", "Dir"}:
        return category
    return None


def _ranked_entries(
    entries: typing.Iterable[tuple[str, bool]],
    query: str
) -> tuple[FileSearchEntry, ...]:
    """按路径边界分数排列索引快照。"""
    matches: list[tuple[int, str, FileSearchEntry]] = []
    for relative, is_directory in entries:
        matched = _nucleo_path_match(relative, query)
        if matched is None:
            continue
        score, indices = matched
        entry = _entry(relative, is_directory, match_indices=indices)
        matches.append((-score, relative, entry))
    matches.sort(key=lambda item: (item[0], item[1]))
    return tuple(
        entry
        for _score, _relative, entry in matches[:FILE_SEARCH_RESULT_LIMIT]
    )


def _entry(
    relative: str,
    is_directory: bool,
    *,
    match_indices: tuple[int, ...] = ()
) -> FileSearchEntry:
    """把规范相对路径转换为菜单候选。"""
    display_name = relative.rpartition("/")[2]
    parent = relative.rpartition("/")[0]
    parent_display = "./" if not parent else parent.replace("/", os.sep) + os.sep
    return FileSearchEntry(
        relative=relative,
        display_name=display_name,
        parent_display=parent_display,
        is_directory=is_directory,
        match_indices=match_indices,
    )


def _nucleo_path_match(
    relative: str,
    query: str
) -> tuple[int, tuple[int, ...]] | None:
    """按 nucleo 路径配置计算最优模糊分数和命中位置。"""
    haystack = tuple(relative)
    needle = tuple(query)
    if not needle or len(needle) > len(haystack):
        return None

    normalized_haystack = tuple(_normalize_character(char) for char in haystack)
    normalized_needle = tuple(_normalize_character(char) for char in needle)
    classes = tuple(_character_class(char) for char in haystack)

    states: dict[
        tuple[int, bool, int, int],
        tuple[int, tuple[int, ...]],
    ] = {}
    best: tuple[int, tuple[int, ...]] | None = None

    for index, normalized_char in enumerate(normalized_haystack):
        char_class = classes[index]
        previous_class = classes[index - 1] if index else _CHAR_DELIMITER
        bonus = _boundary_bonus(previous_class, char_class)
        next_states: dict[
            tuple[int, bool, int, int],
            tuple[int, tuple[int, ...]],
        ] = {}

        if normalized_char == normalized_needle[0]:
            first = (16 + bonus * 2, (index,))
            if len(normalized_needle) == 1:
                best = _higher_score(best, first)
            else:
                _store_match_state(
                    next_states,
                    (1, False, 1, bonus),
                    first,
                )

        for state, value in states.items():
            matched, in_gap, consecutive, first_bonus = state
            score, indices = value
            penalty = 1 if in_gap else 3
            _store_match_state(
                next_states,
                (matched, True, 0, first_bonus),
                (max(0, score - penalty), indices),
            )

            if normalized_char != normalized_needle[matched]:
                continue
            match_bonus = bonus
            next_first_bonus = first_bonus
            if consecutive:
                if match_bonus >= 8 and match_bonus > next_first_bonus:
                    next_first_bonus = match_bonus
                match_bonus = max(match_bonus, next_first_bonus, 4)
            else:
                next_first_bonus = match_bonus

            candidate = (score + 16 + match_bonus, (*indices, index))
            if matched + 1 == len(normalized_needle):
                best = _higher_score(best, candidate)
            else:
                _store_match_state(
                    next_states,
                    (
                        matched + 1,
                        False,
                        consecutive + 1,
                        next_first_bonus,
                    ),
                    candidate,
                )
        states = next_states

    return best


def _character_class(char: str) -> int:
    """返回 nucleo 路径配置使用的字符类别。"""
    if "a" <= char <= "z":
        return _CHAR_LOWER
    if "A" <= char <= "Z":
        return _CHAR_UPPER
    if "0" <= char <= "9":
        return _CHAR_NUMBER
    if char.isspace():
        return _CHAR_WHITESPACE
    if char in "/\\":
        return _CHAR_DELIMITER
    if char.islower():
        return _CHAR_LOWER
    if char.isupper():
        return _CHAR_UPPER
    if char.isnumeric():
        return _CHAR_NUMBER
    if char.isalpha():
        return _CHAR_LETTER
    return _CHAR_NON_WORD


def _normalize_character(char: str) -> str:
    """按 nucleo 的智能规范化方式生成单个比较字符。"""
    decomposed = unicodedata.normalize("NFKD", char)
    normalized = next(
        (item for item in decomposed if not unicodedata.combining(item)),
        char,
    )
    folded = normalized.casefold()
    return folded[0] if folded else normalized


def _boundary_bonus(previous_class: int, char_class: int) -> int:
    """返回 nucleo `match_paths` 对当前位置给出的边界 bonus。"""
    if char_class > _CHAR_DELIMITER:
        if previous_class == _CHAR_WHITESPACE:
            return 8
        if previous_class == _CHAR_DELIMITER:
            return 9
        if previous_class == _CHAR_NON_WORD:
            return 8
    if (
        previous_class == _CHAR_LOWER and char_class == _CHAR_UPPER
        or previous_class != _CHAR_NUMBER and char_class == _CHAR_NUMBER
    ):
        return 5
    if char_class == _CHAR_WHITESPACE:
        return 8
    if char_class == _CHAR_NON_WORD:
        return 8
    return 0


def _store_match_state(
    states: dict[
        tuple[int, bool, int, int],
        tuple[int, tuple[int, ...]],
    ],
    state: tuple[int, bool, int, int],
    value: tuple[int, tuple[int, ...]]
) -> None:
    """仅保留相同匹配状态下分数最高的路径。"""
    current = states.get(state)
    if current is None or _higher_score(current, value) == value:
        states[state] = value


def _higher_score(
    current: tuple[int, tuple[int, ...]] | None,
    candidate: tuple[int, tuple[int, ...]]
) -> tuple[int, tuple[int, ...]]:
    """按分数和稳定位置选择更优的匹配路径。"""
    if current is None or candidate[0] > current[0]:
        return candidate
    if candidate[0] == current[0] and candidate[1] < current[1]:
        return candidate
    return current


def _completion_snapshot(
    token: str,
    matches: typing.Iterable[FileSearchEntry]
) -> tuple[Completion, ...]:
    """把文件匹配快照转换为 prompt_toolkit 补全项。"""
    return tuple(
        Completion(
            f"{_insert_path(entry.relative)} ",
            start_position=-len(token),
            display=entry.display_name,
            display_meta=(
                f"{entry.parent_display}  "
                f"{'Dir' if entry.is_directory else 'File'}"
            ),
        )
        for entry in matches
    )


def _normalize_query(value: str) -> str:
    """统一查询中的平台路径分隔符。"""
    return str(value or "").replace("\\", "/")


def _normalize_relative_path(value: str) -> str:
    """把枚举结果统一为不带当前目录前缀的正斜杠路径。"""
    text = str(value or "")
    if os.sep != "/":
        text = text.replace(os.sep, "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _contains_git_metadata(relative: str) -> bool:
    """判断相对路径是否进入 Git 元数据目录。"""
    return any(part == ".git" for part in relative.split("/"))


def _git_ignored_paths(
    root: Path,
    paths: typing.Iterable[str]
) -> frozenset[str]:
    """使用 Git 的 ignore 引擎返回需要从目录遍历中剪枝的路径。"""
    executable = shutil.which("git")
    values = tuple(dict.fromkeys(paths))
    if executable is None or not values:
        return frozenset()

    payload = b"\0".join(os.fsencode(value) for value in values) + b"\0"
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                executable,
                "-C",
                str(root),
                "check-ignore",
                "--no-index",
                "--stdin",
                "--null",
            ],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except OSError:
        return frozenset()

    return frozenset(
        _normalize_relative_path(os.fsdecode(value))
        for value in result.stdout.split(b"\0")
        if value
    )


def _insert_path(relative: str) -> str:
    """返回插入输入框时使用的平台路径。"""
    return relative.replace("/", os.sep)


if __name__ == "__main__":
    pass
