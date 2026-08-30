# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import sys
import typing
import argparse
from dataclasses import dataclass
from metadata import const
from .commands import (
    CompletionCommand,
    CompletionShell
)
from .help import CliArgumentParser
from .invocation import (
    CONFIG_FLAGS,
    PROFILE_FLAGS
)


@dataclass(frozen=True, slots=True)
class _Candidate(object):
    value: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class _OptionSpec(object):
    flags: tuple[str, ...]
    description: str
    takes_value: bool
    choices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _NodeSpec(object):
    path: tuple[str, ...]
    commands: tuple[_Candidate, ...]
    positionals: tuple[_Candidate, ...]
    options: tuple[_OptionSpec, ...]

    @property
    def key(self) -> str:
        return " ".join(self.path)

    def candidate_values(self) -> tuple[str, ...]:
        """返回当前命令层级的命令和选项候选值。"""
        return tuple(dict.fromkeys((
            *self.selectable_values(),
            *self.option_values(),
        )))

    def selectable_values(self) -> tuple[str, ...]:
        """返回尚未消费位置参数时可用的候选值。"""
        return tuple(dict.fromkeys((
            *(candidate.value for candidate in self.commands),
            *(candidate.value for candidate in self.positionals),
        )))

    def option_values(self) -> tuple[str, ...]:
        """返回当前命令层级的选项 flag。"""
        return tuple(dict.fromkeys(
            flag
            for option in self.options
            for flag in option.flags
        ))


def generate_completion_script(
    shell: CompletionShell,
    *,
    parser: CliArgumentParser | None = None,
) -> str:
    """根据现有命令树生成指定 shell 的补全脚本。"""
    if parser is None:
        from .arguments import create_cli_parser

        parser = create_cli_parser()

    nodes = _completion_nodes(parser)

    if shell == "bash":
        return _bash_script(nodes)
    if shell == "elvish":
        return _elvish_script(nodes)
    if shell == "fish":
        return _fish_script(nodes)
    if shell == "powershell":
        return _powershell_script(nodes)
    if shell == "zsh":
        return _zsh_script(nodes)

    typing.assert_never(shell)


def run_completion_command(
    command: CompletionCommand,
    *,
    output_stream: typing.TextIO | None = None,
) -> int:
    """向标准输出写入完整的 shell 补全脚本。"""
    stream = sys.stdout if output_stream is None else output_stream
    stream.write(generate_completion_script(command.shell))
    return 0


def _completion_nodes(parser: CliArgumentParser) -> tuple[_NodeSpec, ...]:
    """从帮助解析器登记表构建补全节点。"""
    registered = parser.registered_command_parsers()

    parser_by_path = {
        (): parser,
        **registered,
    }

    global_flags = frozenset((*CONFIG_FLAGS, *PROFILE_FLAGS))

    global_options = tuple(
        option
        for option in _option_specs(parser)
        if global_flags.intersection(option.flags)
    )

    nodes: list[_NodeSpec] = []
    for path, target in parser_by_path.items():
        commands = tuple(
            _Candidate(
                value=child_path[-1],
                description=_summary(child_parser.help_summary),
            )
            for child_path, child_parser in registered.items()
            if len(child_path) == len(path) + 1
            and child_path[:-1] == path
        )

        options = _merge_options(
            _option_specs(target),
            () if not path else global_options,
        )

        nodes.append(_NodeSpec(
            path=path,
            commands=commands,
            positionals=_positional_candidates(target),
            options=options,
        ))

    return tuple(nodes)


def _option_specs(parser: CliArgumentParser) -> tuple[_OptionSpec, ...]:
    """把 argparse 选项动作转换为稳定补全规格。"""
    options: list[_OptionSpec] = []

    for action in parser.completion_actions():
        if action.help == argparse.SUPPRESS:
            continue
        choices = (
            tuple(str(choice) for choice in action.choices)
            if action.choices is not None
            else ()
        )
        options.append(_OptionSpec(
            flags=tuple(action.option_strings),
            description=_summary(action.help),
            takes_value=action.nargs != 0,
            choices=choices,
        ))

    return tuple(options)


def _positional_candidates(
    parser: CliArgumentParser,
) -> tuple[_Candidate, ...]:
    """返回位置参数声明的固定候选值。"""
    candidates: list[_Candidate] = []

    for action in parser.completion_positionals():
        description = _summary(action.help)
        candidates.extend(
            _Candidate(str(choice), description)
            for choice in action.choices or ()
        )

    return tuple(candidates)


def _merge_options(
    options: tuple[_OptionSpec, ...],
    inherited: tuple[_OptionSpec, ...],
) -> tuple[_OptionSpec, ...]:
    """合并命令选项并按 flag 去除重复项。"""
    merged: list[_OptionSpec] = list(options)

    known_flags = {
        flag
        for option in options
        for flag in option.flags
    }

    for option in inherited:
        if known_flags.isdisjoint(option.flags):
            merged.append(option)
            known_flags.update(option.flags)

    return tuple(merged)


def _summary(value: object) -> str:
    """把说明文本压缩成单行。"""
    return " ".join(str(value or "").split())


def _shell_symbol() -> str:
    """返回适合作为 shell 函数名片段的应用标识。"""
    value = re.sub(r"[^0-9A-Za-z_]", "_", const.APP_NAME).strip("_")
    return value.lower() or "cli"


def _shell_single_quote(value: str) -> str:
    """转义 POSIX shell 单引号文本。"""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _powershell_quote(value: str) -> str:
    """转义 PowerShell 单引号文本。"""
    return "'" + value.replace("'", "''") + "'"


def _fish_quote(value: str) -> str:
    """转义 Fish 单引号文本。"""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _node_candidate_cases(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 POSIX case 使用的节点候选分支。"""
    return "\n".join(
        "    "
        + _shell_single_quote(node.key)
        + ") printf '%s' "
        + _shell_single_quote(" ".join(node.candidate_values()))
        + " ;;"
        for node in nodes
    )


def _node_selectable_cases(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 POSIX case 使用的子命令和位置值分支。"""
    return "\n".join(
        "    "
        + _shell_single_quote(node.key)
        + ") printf '%s' "
        + _shell_single_quote(" ".join(node.selectable_values()))
        + " ;;"
        for node in nodes
    )


def _node_option_cases(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 POSIX case 使用的选项分支。"""
    return "\n".join(
        "    "
        + _shell_single_quote(node.key)
        + ") printf '%s' "
        + _shell_single_quote(" ".join(node.option_values()))
        + " ;;"
        for node in nodes
    )


def _option_value_cases(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 POSIX case 使用的选项值分支。"""
    lines: list[str] = []
    for node in nodes:
        for option in node.options:
            if not option.choices:
                continue
            values = _shell_single_quote(" ".join(option.choices))
            for flag in option.flags:
                key = _shell_single_quote(f"{node.key}|{flag}")
                lines.append(f"    {key}) printf '%s' {values} ;;")
    return "\n".join(lines)


def _takes_value_cases(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 POSIX case 使用的带值选项分支。"""
    lines: list[str] = []
    for node in nodes:
        for option in node.options:
            if not option.takes_value:
                continue
            for flag in option.flags:
                key = _shell_single_quote(f"{node.key}|{flag}")
                lines.append(f"    {key}) printf '1' ;;")
    return "\n".join(lines)


def _bash_script(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 Bash 补全脚本。"""
    symbol         = _shell_symbol()
    candidates_fn  = f"_{symbol}_completion_candidates"
    selectables_fn = f"_{symbol}_completion_selectables"
    options_fn     = f"_{symbol}_completion_options"
    values_fn      = f"_{symbol}_completion_values"
    takes_fn       = f"_{symbol}_completion_takes_value"
    complete_fn    = f"_{symbol}_completion"

    return f"""# bash completion for {const.APP_DESC}
{candidates_fn}() {{
  case "$1" in
{_node_candidate_cases(nodes)}
  esac
}}

{selectables_fn}() {{
  case "$1" in
{_node_selectable_cases(nodes)}
  esac
}}

{options_fn}() {{
  case "$1" in
{_node_option_cases(nodes)}
  esac
}}

{values_fn}() {{
  case "$1" in
{_option_value_cases(nodes)}
  esac
}}

{takes_fn}() {{
  case "$1" in
{_takes_value_cases(nodes)}
  esac
}}

{complete_fn}() {{
  local cur prev path word candidate values candidates
  local skip_next=0 positional_used=0
  local index
  cur="${{COMP_WORDS[COMP_CWORD]}}"
  prev=""
  if (( COMP_CWORD > 0 )); then
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
  fi
  path=""

  for (( index=1; index<COMP_CWORD; index++ )); do
    word="${{COMP_WORDS[index]}}"
    if (( skip_next )); then
      skip_next=0
      continue
    fi
    if [[ "$word" == -* ]]; then
      if [[ "$({takes_fn} "$path|$word")" == "1" ]]; then
        skip_next=1
      fi
      continue
    fi
    candidate="${{path:+$path }}$word"
    if [[ -n "$({candidates_fn} "$candidate")" ]]; then
      path="$candidate"
    else
      positional_used=1
    fi
  done

  values="$({values_fn} "$path|$prev")"
  if [[ -n "$values" ]]; then
    candidates="$values"
  else
    candidates="$({options_fn} "$path")"
    if (( ! positional_used )); then
      candidates="$({selectables_fn} "$path") $candidates"
    fi
  fi
  COMPREPLY=( $(compgen -W "$candidates" -- "$cur") )
}}

complete -F {complete_fn} {_shell_single_quote(const.APP_NAME)}
"""


def _zsh_script(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 Zsh 补全脚本。"""
    symbol = _shell_symbol()
    candidates_fn = f"_{symbol}_completion_candidates"
    selectables_fn = f"_{symbol}_completion_selectables"
    options_fn = f"_{symbol}_completion_options"
    values_fn = f"_{symbol}_completion_values"
    takes_fn = f"_{symbol}_completion_takes_value"
    complete_fn = f"_{symbol}_completion"

    return f"""#compdef {const.APP_NAME}
{candidates_fn}() {{
  case "$1" in
{_node_candidate_cases(nodes)}
  esac
}}

{selectables_fn}() {{
  case "$1" in
{_node_selectable_cases(nodes)}
  esac
}}

{options_fn}() {{
  case "$1" in
{_node_option_cases(nodes)}
  esac
}}

{values_fn}() {{
  case "$1" in
{_option_value_cases(nodes)}
  esac
}}

{takes_fn}() {{
  case "$1" in
{_takes_value_cases(nodes)}
  esac
}}

{complete_fn}() {{
  local path word candidate candidate_text prev
  local skip_next=0 positional_used=0
  local index
  local -a candidates
  path=""

  for (( index=2; index<CURRENT; index++ )); do
    word="${{words[index]}}"
    if (( skip_next )); then
      skip_next=0
      continue
    fi
    if [[ "$word" == -* ]]; then
      if [[ "$({takes_fn} "$path|$word")" == "1" ]]; then
        skip_next=1
      fi
      continue
    fi
    candidate="${{path:+$path }}$word"
    if [[ -n "$({candidates_fn} "$candidate")" ]]; then
      path="$candidate"
    else
      positional_used=1
    fi
  done

  prev="${{words[CURRENT-1]}}"
  candidate_text="$({values_fn} "$path|$prev")"
  if [[ -z "$candidate_text" ]]; then
    candidate_text="$({options_fn} "$path")"
    if (( ! positional_used )); then
      candidate_text="$({selectables_fn} "$path") $candidate_text"
    fi
  fi
  candidates=(${{=candidate_text}})
  compadd -- "${{candidates[@]}}"
}}

compdef {complete_fn} {_shell_single_quote(const.APP_NAME)}
"""


def _powershell_array(values: typing.Iterable[str]) -> str:
    """生成 PowerShell 字符串数组。"""
    return "@(" + ", ".join(_powershell_quote(value) for value in values) + ")"


def _powershell_table(
    items: typing.Iterable[tuple[str, typing.Iterable[str]]],
) -> str:
    """生成 PowerShell 字符串数组哈希表。"""
    lines = ["@{"]

    for key, values in items:
        lines.append(
            f"  {_powershell_quote(key)} = {_powershell_array(values)}"
        )
    lines.append("}")

    return "\n".join(lines)


def _powershell_script(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 PowerShell 补全脚本。"""
    node_table = _powershell_table(
        (node.key, node.candidate_values())
        for node in nodes
    )

    selectable_table = _powershell_table(
        (node.key, node.selectable_values())
        for node in nodes
    )

    option_table = _powershell_table(
        (node.key, node.option_values())
        for node in nodes
    )

    value_table = _powershell_table(
        (f"{node.key}|{flag}", option.choices)
        for node in nodes
        for option in node.options
        if option.choices
        for flag in option.flags
    )

    takes_value = tuple(
        f"{node.key}|{flag}"
        for node in nodes
        for option in node.options
        if option.takes_value
        for flag in option.flags
    )

    return f"""# PowerShell completion for {const.APP_DESC}
Register-ArgumentCompleter -Native -CommandName {_powershell_quote(const.APP_NAME)} -ScriptBlock {{
  param($wordToComplete, $commandAst, $cursorPosition)

  $nodes = {node_table}
  $selectables = {selectable_table}
  $options = {option_table}
  $values = {value_table}
  $takesValue = {_powershell_array(takes_value)}
  $elements = @($commandAst.CommandElements | ForEach-Object {{ $_.Extent.Text }})
  $path = ''
  $skipNext = $false
  $positionalUsed = $false
  $end = $elements.Count
  if ($wordToComplete -ne '' -and $end -gt 1) {{
    $end -= 1
  }}

  for ($index = 1; $index -lt $end; $index += 1) {{
    $token = $elements[$index]
    if ($skipNext) {{
      $skipNext = $false
      continue
    }}
    if ($token.StartsWith('-')) {{
      if ($takesValue -contains "$path|$token") {{
        $skipNext = $true
      }}
      continue
    }}
    $candidatePath = if ($path) {{ "$path $token" }} else {{ $token }}
    if ($nodes.ContainsKey($candidatePath)) {{
      $path = $candidatePath
    }} else {{
      $positionalUsed = $true
    }}
  }}

  $previousIndex = if ($wordToComplete -ne '') {{ $elements.Count - 2 }} else {{ $elements.Count - 1 }}
  $previous = if ($previousIndex -ge 0) {{ $elements[$previousIndex] }} else {{ '' }}
  $valueKey = "$path|$previous"
  if ($values.ContainsKey($valueKey)) {{
    $candidates = $values[$valueKey]
  }} else {{
    $candidates = @($options[$path])
    if (-not $positionalUsed) {{
      $candidates = @($selectables[$path]) + $candidates
    }}
  }}

  foreach ($candidate in $candidates) {{
    if ($candidate -like "$wordToComplete*") {{
      [System.Management.Automation.CompletionResult]::new(
        $candidate,
        $candidate,
        'ParameterValue',
        $candidate
      )
    }}
  }}
}}
"""


def _fish_script(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 Fish 补全脚本。"""
    symbol  = _shell_symbol()
    path_fn = f"__{symbol}_completion_at_path"

    known_paths = " ".join(
        _fish_quote(node.key)
        for node in nodes
        if node.path
    )

    takes_value = " ".join(
        _fish_quote(f"{node.key}|{flag}")
        for node in nodes
        for option in node.options
        if option.takes_value
        for flag in option.flags
    )

    lines = [
        f"# Fish completion for {const.APP_DESC}",
        f"function {path_fn}",
        "  set -l tokens (commandline -opc)",
        "  if test (count $tokens) -gt 0",
        "    set -e tokens[1]",
        "  end",
        "  set -l path ''",
        "  set -l skip_next 0",
        "  set -l positional_used 0",
        "  for token in $tokens",
        "    if test $skip_next -eq 1",
        "      set skip_next 0",
        "      continue",
        "    end",
        "    if string match -q -- '-*' $token",
        f"      if contains -- (string join '|' $path $token) {takes_value}",
        "        set skip_next 1",
        "      end",
        "      continue",
        "    end",
        "    set -l candidate (string trim (string join ' ' $path $token))",
        f"    if contains -- $candidate {known_paths}",
        "      set path $candidate",
        "    else",
        "      set positional_used 1",
        "    end",
        "  end",
        "  if test \"$path\" != \"$argv[1]\"",
        "    return 1",
        "  end",
        "  if test \"$argv[2]\" = select",
        "    test $positional_used -eq 0",
        "  end",
        "end",
        "",
    ]

    for node in nodes:
        condition = _fish_quote(f'{path_fn} "{node.key}"')

        select_condition = _fish_quote(
            f'{path_fn} "{node.key}" select'
        )

        for command in node.commands:
            lines.append(
                f"complete -c {_fish_quote(const.APP_NAME)} -f "
                f"-n {select_condition} -a {_fish_quote(command.value)} "
                f"-d {_fish_quote(command.description)}"
            )

        for positional in node.positionals:
            lines.append(
                f"complete -c {_fish_quote(const.APP_NAME)} -f "
                f"-n {select_condition} -a {_fish_quote(positional.value)} "
                f"-d {_fish_quote(positional.description)}"
            )

        for option in node.options:
            parts = [
                "complete",
                "-c",
                _fish_quote(const.APP_NAME),
                "-n",
                condition,
            ]
            for flag in option.flags:
                if flag.startswith("--"):
                    parts.extend(("-l", _fish_quote(flag[2:])))
                elif flag.startswith("-") and len(flag) == 2:
                    parts.extend(("-s", _fish_quote(flag[1:])))
                else:
                    parts.extend(("-o", _fish_quote(flag.lstrip("-"))))
            if option.takes_value:
                parts.append("-r")
            if option.choices:
                parts.extend(("-f", "-a", _fish_quote(" ".join(option.choices))))

            parts.extend(("-d", _fish_quote(option.description)))
            lines.append(" ".join(parts))

    return "\n".join(lines) + "\n"


def _elvish_script(nodes: tuple[_NodeSpec, ...]) -> str:
    """生成 Elvish 补全脚本。"""
    lines = [
        f"# Elvish completion for {const.APP_DESC}",
        f"edit:completion:arg-completer[{const.APP_NAME}] = {{|@words|",
        "  var path = (joins ' ' $words)",
    ]

    for node in nodes:
        candidates = " ".join(
            f"'{value}'"
            for value in node.candidate_values()
        )
        lines.extend((
            f"  if (eq $path '{node.key}') {{",
            f"    put {candidates}",
            "  }",
        ))
    lines.extend(("}", ""))

    return "\n".join(lines)


if __name__ == '__main__':
    pass
