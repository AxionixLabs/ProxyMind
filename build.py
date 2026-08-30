# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import sys
import time
import errno
import shutil
import typing
import asyncio
import plistlib
import textwrap
from pathlib import Path
from rich.console import Console
from rich.text import Text
from rich.progress import (
    BarColumn,
    TimeElapsedColumn,
    Progress,
    SpinnerColumn,
    TextColumn
)
from infrastructure.errors import AppError
from infrastructure.platform.terminal import Terminal
from metadata import const

nuitka_version = "2.8.9"  # 编译器版本

try:
    import nuitka
except ImportError:
    raise AppError(f"Use Nuitka {nuitka_version} for stable builds")

CONSOLE = Console()

RENAME_RETRY_ATTEMPTS  = 12
RENAME_RETRY_DELAY_SEC = 0.25

RENAME_RETRY_ERRNOS = frozenset({
    errno.EACCES,
    errno.EPERM,
    errno.EBUSY
})


def compile_log(value: typing.Any) -> None:
    """输出一条构建日志。"""
    message = Text(f"{'INFO': <8} | {value}", style="bold #ADD8E6")
    CONSOLE.print(const.PRINT_HEAD, message)


def show_build_result(state: str, color: str) -> None:
    """显示带边框的构建结果。"""
    result = textwrap.dedent(f"""\
        [bold {color}]
        ╭────────────────────────────────────────╮
        │             {const.APP_DESC} Task {state}             │
        ╰────────────────────────────────────────╯
    """)
    CONSOLE.print(result)


async def is_virtual_env() -> None:
    """
    检查当前 Python 运行环境是否为虚拟环境。
    """
    if sys.prefix != sys.base_prefix:
        return compile_log("[✓] 当前运行在虚拟环境中")

    raise AppError("[!] 当前不是虚拟环境")


async def check_architecture(ops: str) -> None:
    """
    仅在 Windows 下检测 Python 是否为 64 位。
    """
    if ops != "win32":
        return None

    is_64bit = sys.maxsize > 2 ** 32
    python_version = sys.version.split()[0]

    compile_log(f"<Python> {python_version} ({'64-bit' if is_64bit else '32-bit'})")

    if is_64bit:
        return compile_log(f"✅ 当前 Python 是 64 位，符合 {const.APP_DESC} 打包要求。")

    raise AppError(f"❌ 当前为 32 位 Python，建议更换为 64 位版本。")


async def find_site_packages() -> Path:
    """
    自动查找当前虚拟环境中的 `site-packages` 路径。
    """
    base_venv, base_libs, base_site = Path(sys.prefix), "lib", "site-packages"

    for lib_path in base_venv.iterdir():
        if base_libs in lib_path.name.lower():
            for sub in lib_path.iterdir():
                if base_site in sub.name.lower():
                    return sub.resolve()
                elif sub.name.lower().startswith("python"):
                    return (sub / base_site).resolve()

    raise AppError(f"[!] Site packages path not found in virtual environment")


async def find_vcvars64() -> str:
    """
    查找 vcvars64.bat 的完整路径，用于配置 MSVC 构建环境。
    """
    vswhere = Path(r"C:\Program Files (x86)", "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not vswhere.exists():
        raise AppError("未找到 vswhere.exe -> 请安装 Visual Studio Build Tools")

    cmd = [
        str(vswhere), "-latest", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-products", "*", "-property", "installationPath"
    ]

    find_result = await Terminal.cmd_line(cmd)

    vcvars = Path(find_result.strip()) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"

    if not vcvars.exists():
        raise AppError(f"找不到 vcvars64.bat -> {vcvars}")

    return str(vcvars)


async def find_dumpbin() -> str:
    """
    查找最新 MSVC 工具链中的 dumpbin.exe 路径。
    """
    vswhere = Path(r"C:\Program Files (x86)", "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not vswhere.exists():
        raise AppError("未找到 vswhere.exe -> 请安装 Visual Studio Build Tools")

    cmd = [
        str(vswhere), "-latest", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-products", "*", "-property", "installationPath"
    ]

    find_result = await Terminal.cmd_line(cmd)

    if not (tools_dir := Path(find_result.strip()) / "VC" / "Tools" / "MSVC").exists():
        raise AppError("找不到 MSVC 工具目录 -> VC/Tools/MSVC")

    if not (version_dirs := [d for d in tools_dir.iterdir() if d.is_dir()]):
        raise AppError("未检测到任何 VC 工具版本目录")

    if not (dumpbin := sorted(version_dirs)[-1] / "bin" / "Hostx64" / "x64" / "dumpbin.exe").exists():
        raise AppError(f"找不到 dumpbin.exe -> {dumpbin}")

    return str(dumpbin)


async def _rename_path_with_retry(src: Path, dst: Path) -> None:
    """在路径暂时不可写时按有限退避策略重试重命名。"""
    last_error: OSError | None = None

    for attempt in range(1, RENAME_RETRY_ATTEMPTS + 1):
        try:
            await asyncio.to_thread(src.rename, dst)
            return None
        except OSError as error:
            last_error = error
            retryable = (
                isinstance(error, PermissionError)
                or error.errno in RENAME_RETRY_ERRNOS
            )
            if not retryable or attempt >= RENAME_RETRY_ATTEMPTS:
                break

            delay = min(RENAME_RETRY_DELAY_SEC * attempt, 1.0)

            compile_log(
                f"[!] 路径暂时被占用，{delay:.2f}s 后重试 "
                f"({attempt}/{RENAME_RETRY_ATTEMPTS - 1})"
            )
            await asyncio.sleep(delay)

    detail = str(last_error or "permission denied")

    raise AppError(
        f"无法重命名 {src} -> {dst}: {detail}。"
        "请确认父目录可写，且路径未被占用后重试。"
    ) from last_error


async def rename_sensitive(src: Path, dst: Path) -> None:
    """通过临时路径完成可重试且可回滚的目录重命名。"""
    if not src.exists():
        raise AppError(f"待重命名路径不存在: {src}")
    if os.path.lexists(dst):
        try:
            same_entry = os.path.samestat(src.lstat(), dst.lstat())
        except OSError as error:
            raise AppError(
                f"无法确认重命名目标是否冲突: {src} -> {dst}: {error}"
            ) from error

        if not same_entry:
            raise AppError(f"目标路径已存在: {dst}")

    timestamp = time.strftime("%Y%m%d%H%M%S")
    nonce     = time.time_ns() % 1_000_000_000
    temporary = src.with_name(f"__temp_{timestamp}_{nonce:09d}__")
    if os.path.lexists(temporary):
        raise AppError(f"临时重命名路径已存在: {temporary}")

    compile_log(f"[✓] 生成临时目录 {temporary.name}")

    await _rename_path_with_retry(src, temporary)
    compile_log(f"[✓] Rename completed {src.name} → {temporary.name}")

    try:
        if os.path.lexists(dst):
            raise AppError(f"目标路径已存在: {dst}")
        await _rename_path_with_retry(temporary, dst)
    except BaseException:
        try:
            await _rename_path_with_retry(temporary, src)
            compile_log(f"[!] Rename rolled back {temporary.name} → {src.name}")
        except BaseException as rollback_error:
            compile_log(f"[✗] Rename rollback failed: {rollback_error}")
        raise

    compile_log(f"[✓] Rename completed {temporary.name} → {dst.name}")


async def sweep_cache_tree(target: Path) -> None:
    """
    清理指定路径下所有名为 `*build` 的缓存目录。
    """
    compile_log(f"[!] 准备清理删除缓存目录 {target.as_posix()}")

    caches = [
        cache for cache in target.iterdir() if cache.is_dir() and cache.name.lower().endswith("build")
    ]

    if not caches:
        return compile_log(f"[!] 未发现 *.build 跳过清理")

    for cache in await asyncio.gather(
        *(asyncio.to_thread(shutil.rmtree, cache, ignore_errors=True)
          for cache in caches), return_exceptions=True
    ):
        if isinstance(cache, Exception):
            compile_log(f"[✗] 无法清理 {cache}")
        else:
            compile_log(f"[✓] 构建缓存已清理 {cache}")


async def rename_so_files(ops: str, target: Path) -> None:
    """
    将 Darwin 系统下编译生成的 *.cpython-XXX-darwin.so 文件统一重命名为 *.so。
    """
    if ops != "darwin":
        return None

    compile_log(f"[!] 将目录中所有形如 xxx.cpython-XXX-darwin.so 的文件重命名为 xxx.so")

    pattern = re.compile(r"^(.*)\.cpython-\d{3}-darwin\.so$")

    for file in target.rglob("*.so"):
        if match := pattern.match(file.name):
            destination = file.with_name(new_name := f"{match.group(1)}.so")
            if os.path.lexists(destination):
                raise AppError(f"目标路径已存在: {destination}")
            await _rename_path_with_retry(file, destination)
            compile_log(f"[✓] Renamed {file.name} → {new_name}")


async def authorized_tools(ops: str, *args: Path, **__) -> None:
    """
    检查目录下的所有文件是否具备执行权限，如果文件没有执行权限，则自动添加 +x 权限。
    """
    if ops != "darwin":
        return None

    for resp in (ensure := [
        ["chmod", "-R", "+x", arg.as_posix()] if arg.is_dir() else [
            "chmod", "+x", arg.as_posix()] for arg in args
    ]):
        compile_log(f"[!] Authorizing {resp}")

    for resp in await asyncio.gather(
            *(Terminal.cmd_line(kit) for kit in ensure)
    ):
        compile_log(f"[!] Authorize resp={resp}")


async def edit_plist_fields(ops: str, app: Path, updates: dict[str, str]) -> None:
    """
    编辑 macOS 应用的 Info.plist 文件字段。
    """
    if ops != "darwin":
        return None

    if not (plist := app / "Contents" / "Info.plist").exists():
        raise AppError(f"未找到 Info.plist 文件: {plist}")

    # 读取原始 plist
    with plist.open("rb") as f:
        plist_data = plistlib.load(f)

    # 显示原始值
    compile_log(f"正在修改 Info.plist: {plist}")
    for key, new_value in updates.items():
        old_value = plist_data.get(key, "<未定义>")
        compile_log(f" - {key}: {old_value}  ->  {new_value}")
        plist_data[key] = new_value

    # 写入修改后的内容
    with plist.open("wb") as f:
        plistlib.dump(plist_data, f)

    compile_log(f"修改完成，已写入 Info.plist")


async def report_binary_info(command: list[str]) -> None:
    """输出构建产物的二进制格式和架构信息。"""
    binary = Path(command[-1])
    if not binary.exists():
        raise AppError(f"未找到待检查的二进制文件: {binary}")

    compile_log(f"[!] Binary information -> {binary}")
    if not (result := await Terminal.cmd_line(command)):
        raise AppError(f"二进制信息命令未返回内容: {binary}")
    compile_log(result)


SANDBOX_RUNTIME_ASSETS = {
    "win32": (
        "mind_sandbox_server.exe",
        "codex-command-runner.exe",
        "codex-windows-sandbox-setup.exe",
    ),
    "darwin": ("mind_sandbox_server",),
}


def validate_sidecar_assets(ops: str, sandbox: Path) -> tuple[Path, ...]:
    """校验当前平台打包所需的全部沙箱运行时文件。"""
    try:
        asset_names = SANDBOX_RUNTIME_ASSETS[ops]
    except KeyError as error:
        raise AppError(f"不支持的沙箱运行时平台: {ops}") from error

    assets = tuple(sandbox / "bin" / name for name in asset_names)
    missing = tuple(asset for asset in assets if not asset.is_file())
    if missing:
        paths = ", ".join(str(asset) for asset in missing)
        raise AppError(f"缺少 {ops} 沙箱运行时文件: {paths}")

    if ops == "darwin" and not os.access(assets[0], os.X_OK):
        raise AppError(f"macOS sidecar 不具备执行权限: {assets[0]}")
    return assets


async def packaging() -> tuple[
    str,
    Path,
    Path,
    Path,
    tuple[Path, Path],
    list[str],
    tuple[Path, Path],
    list[str],
    str
]:
    """
    构建独立应用的打包编译命令与目录结构信息。
    """
    if (app := Path(f"applications")).exists():
        shutil.rmtree(app)
    app.mkdir(exist_ok=True)

    site_packages = await find_site_packages()

    launcher_root = app.parent / const.SCHEMATIC / "resources" / "automation"

    compile_cmd = [exe := sys.executable, "-m", "nuitka"]

    if (ops := sys.platform) == "win32":
        await check_architecture(ops)
        _, dumpbin = await asyncio.gather(find_vcvars64(), find_dumpbin())

        target = app / f"{const.APP_NAME}.dist"
        rename = target, app / f"{const.APP_DESC}Engine"

        compile_cmd += [
            f"--mode=standalone",
            f"--product-name={const.APP_DESC}",
            f"--product-version={const.APP_VERSION}",
            f"--windows-icon-from-ico=schematic/resources/icons/mind_windows_icn.ico",
        ]

        launch = launcher_root / f"{const.APP_NAME}.bat", target.parent
        binary_file = rename[1] / f"{const.APP_NAME}.exe"
        arch_info = [dumpbin, "/headers", f"{str(Path(__file__).parent / binary_file)}"]

        support = "windows"

    elif ops == "darwin":
        target = app / f"{const.APP_NAME}.app" / f"Contents" / f"MacOS"
        rename = target.parent.parent, app / f"{const.APP_DESC}.app"

        compile_cmd += [
            f"--macos-create-app-bundle",
            f"--macos-app-name={const.APP_DESC}",
            f"--macos-app-version={const.APP_VERSION}",
            f"--macos-app-icon=schematic/resources/images/macos/mind_macos_icn.png",
        ]

        launch = launcher_root / f"{const.APP_NAME}.sh", target
        binary_file = rename[1] / "Contents" / "MacOS" / const.APP_NAME
        arch_info = ["file", f"{str(Path(__file__).parent / binary_file)}"]

        support = "macos"

    else:
        raise AppError(f"Unsupported platforms {ops}")

    compile_cmd += [
        f"--company-name={const.PUBLISHER}",
        f"--copyright={const.COPYRIGHT}"
    ]

    compile_cmd += [
        f"--assume-yes-for-downloads", f"--show-progress", f"--show-memory",
        f"--include-package=pygments",
        f"--include-data-dir=web=web",
        f"--include-data-dir=js_repl=js_repl",
        f"--output-dir={app}", f"{const.APP_NAME}.py"
    ]

    compile_log(f"system={ops}")
    compile_log(f"folder={app}")
    compile_log(f"packet={site_packages}")
    compile_log(f"target={target}")
    compile_log(f"rename={' -> '.join(str(path) for path in rename)}")
    compile_log(f"launch={' -> '.join(str(path) for path in launch)}")

    writer = await Terminal.cmd_line([exe, "-m", "pip", "show", compile_cmd[2]])
    if ver := re.search(r"(?<=Version:\s).*", writer):
        if ver.group().strip() != nuitka_version:
            raise AppError(f"Use Nuitka {nuitka_version} for stable builds")
        compile_log(f"writer={writer}")

    return ops, app, site_packages, target, rename, compile_cmd, launch, arch_info, support


async def post_build() -> None:
    """
    应用打包后的自动依赖检查与部署流程。
    """

    async def input_stream() -> None:
        """
        异步读取终端标准输出流内容，并打印实时构建日志。
        """
        async for line in transports.stdout:
            compile_log(line.decode(const.CHARSET, "ignore").strip())

    async def error_stream() -> None:
        """
        异步读取终端错误输出流内容，实时反馈构建异常信息。
        """
        async for line in transports.stderr:
            compile_log(line.decode(const.CHARSET, "ignore").strip())

    async def examine_dependencies() -> None:
        """
        检查所有指定依赖是否存在于虚拟环境中。
        """
        for key, value in dependencies.items():
            for src, dst in value:
                if src.exists():
                    done_list.append((src, dst))
                    compile_log(f"[✓] Read {key} -> {src.name}")
                else:
                    fail_list.append(f"{key}: {src.name}")
                    compile_log(f"[!] Dependency not found -> {src.name}")

        if fail_list:
            raise AppError(f"[!] Incomplete dependencies required {fail_list}")

    async def forward_dependencies() -> None:
        """
        规范化编译产物后拷贝依赖，并完成目录重命名与缓存清理。
        """
        await rename_so_files(ops, target)

        with Progress(
                TextColumn(text_format=f"[bold #80C0FF]{const.APP_DESC} | {{task.description}}", justify="right"),
                SpinnerColumn(style="bold #FFA07A", speed=1, finished_text="[bold #7CFC00]✓"),
                BarColumn(bar_width=None, style="bold #ADD8E6", complete_style="bold #90EE90", finished_style="bold #00CED1"),
                TimeElapsedColumn(),
                TextColumn(
                    "[progress.percentage][bold #F0E68C]{task.completed:>2.0f}[/]/[bold #FFD700]{task.total}[/]"
                ), expand=False, console=CONSOLE
        ) as progress:

            task = progress.add_task(description="Dependencies", total=len(done_list))
            for src, dst in done_list:
                shutil.copytree(
                    src, dst, dirs_exist_ok=True) if src.is_dir() else shutil.copy2(src, dst)
                progress.advance(task)

        # Notes: ==== macOS Only ====
        authorization_targets = [
            target / schematic.name / kit,
            target / const.APP_NAME,
            target / launch[0].name,
        ]
        sandbox_target = target / schematic.name / "sandbox" / support
        if sandbox_target.is_dir():
            authorization_targets.append(sandbox_target)
        await authorized_tools(ops, *authorization_targets)

        await rename_sensitive(*rename)

        if ops == "darwin":
            standalone_output = app / f"{const.APP_NAME}.dist"
            if standalone_output.exists():
                await asyncio.to_thread(shutil.rmtree, standalone_output)
                compile_log(f"[✓] 已清理独立模式中间产物 {standalone_output}")

        await sweep_cache_tree(app)

        structure_parent = (
            rename[-1] / "Contents"
            if ops == "darwin"
            else rename[-1].parent
        )
        for folder in [const.SRC_OPERA_PLACE]:
            if not (child := structure_parent / const.STRUCTURE / folder).exists():
                await asyncio.to_thread(child.mkdir, parents=True, exist_ok=True)

        await edit_plist_fields(ops, rename[-1], {"CFBundleExecutable": launch[0].name})
        await report_binary_info(arch_info)

    build_start_time = time.time()

    (
        ops,
        app,
        _site_packages,
        target,
        rename,
        compile_cmd,
        launch,
        arch_info,
        support,
    ) = await packaging()

    done_list, fail_list = [], []

    schematic, kit = app.parent / const.SCHEMATIC, "supports"
    r, s = schematic / "resources", schematic / kit / support
    skills = schematic / "skills"
    sandbox = schematic / "sandbox" / support
    validate_sidecar_assets(ops, sandbox)

    local_pack, local_file = [
        (r, target / schematic.name / r.name),
        (s, target / schematic.name / kit / s.name),
        (skills, target / schematic.name / skills.name)
    ], [
        launch
    ]

    if sandbox.is_dir():
        local_pack.append((
            sandbox,
            target / schematic.name / "sandbox" / support,
        ))

    dependencies = {
        "本地模块": local_pack,
        "本地文件": local_file,
        "第三方库": []
    }

    await examine_dependencies()

    # Notes: ==== Start compiling ====
    transports = await Terminal.cmd_link(compile_cmd)
    await asyncio.gather(
        *(asyncio.create_task(task()) for task in [input_stream, error_stream])
    )
    await transports.wait()

    await forward_dependencies()

    compile_log(f"TimeCost={(time.time() - build_start_time) / 60:.2f} m")


if __name__ == "__main__":
    try:
        asyncio.run(post_build())
    except AppError as _e:
        compile_log(_e)
        show_build_result("Fail", "#FF4444")
        sys.exit(1)
    except KeyboardInterrupt:
        show_build_result("Exit", "#FFEE55")
        sys.exit(130)
    else:
        show_build_result("Done", "#00FF88")
        sys.exit(0)
