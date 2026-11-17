# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""QNX SDP 8.0 host environment and builds (qcc, make, cmake)."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .parsers import elf_info, parse_diagnostics
from .util import WORKSPACE_HINT, Config, QnxMcpError, check_host_path, private_dir, run, tail, within, write_atomic

ARCHES = {
    # arch name used by mkqnximage/$QNX_TARGET -> (qcc variant, gdb prefix, cmake processor)
    "x86_64": ("gcc_ntox86_64", "ntox86_64", "x86_64"),
    "aarch64le": ("gcc_ntoaarch64le", "ntoaarch64", "aarch64"),
}

_env_lock = threading.Lock()
_env_cache: dict[str, dict[str, str]] = {}


def check_arch(arch: str) -> str:
    if arch not in ARCHES:
        raise QnxMcpError("invalid_argument", f"unsupported arch {arch!r}", hint=f"Use one of {sorted(ARCHES)}.")
    return arch


def sdk_env(cfg: Config) -> dict[str, str]:
    """Environment produced by sourcing qnxsdp-env.sh (cached per script path)."""
    key = str(cfg.sdp_env)
    with _env_lock:
        if key in _env_cache:
            return dict(_env_cache[key])
        if not cfg.sdp_env.is_file():
            raise QnxMcpError(
                "sdk_missing",
                f"QNX SDP environment script not found: {cfg.sdp_env}",
                hint="Install QNX SDP 8.0 (QNX Software Center) and set QNX_MCP_SDP_ENV to its qnxsdp-env.sh.",
            )
        # The script path is passed as $1; nothing user-controlled is interpolated into shell text.
        r = run(["bash", "-c", 'source "$1" >/dev/null 2>&1 || exit 97; env -0', "qnx-mcp", str(cfg.sdp_env)], timeout=60)
        if r.returncode != 0:
            raise QnxMcpError("sdk_missing", "sourcing qnxsdp-env.sh failed", details_tail=tail(r.err))
        env = dict(item.split("=", 1) for item in r.out.split("\0") if "=" in item)
        for var in ("QNX_HOST", "QNX_TARGET"):
            if not env.get(var):
                raise QnxMcpError("sdk_missing", f"{var} not set after sourcing {cfg.sdp_env}")
        _env_cache[key] = env
        return dict(env)


def sdk_tool(env: dict[str, str], name: str) -> str:
    path = shutil.which(name, path=env.get("PATH"))
    if not path:
        raise QnxMcpError("tool_missing", f"{name} not found in the QNX SDP PATH", hint="Check the SDP installation.")
    return path


# ---------------------------------------------------------------- runner helper


def runner_source() -> Path:
    return Path(__file__).with_name("runner") / "qnx-mcp-run.c"


def runner_binary(cfg: Config, arch: str) -> tuple[Path, str]:
    """Build (once per source hash and arch) the target-side runner with qcc."""
    src = runner_source()
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
    out = cfg.state_dir / "runner" / arch / digest / "qnx-mcp-run"
    if out.is_file():
        return out, digest
    env = sdk_env(cfg)
    private_dir(out.parent)
    tmp = out.with_name(f".qnx-mcp-run.{uuid.uuid4().hex[:8]}.tmp")
    variant = ARCHES[check_arch(arch)][0]
    r = run(
        [sdk_tool(env, "qcc"), f"-V{variant}", "-O2", "-Wall", f'-DQMR_VERSION="{digest}"', "-o", str(tmp), str(src)],
        timeout=120,
        env=env,
    )
    if r.returncode != 0:
        raise QnxMcpError("build_failed", "could not build the target runner helper", output=tail(r.out + r.err))
    tmp.replace(out)
    return out, digest


# ---------------------------------------------------------------- build argument allow-list
#
# cflags, ldflags and make_args come from the agent. make and gcc both have options that run a
# program or read a file of commands (make -f/--eval/SHELL=, gcc -wrapper/-specs=/-fplugin=/-B/@file,
# -Wl,/-Wa,/-Wp, passthrough), so every argument must match one of the forms below; anything else
# is refused before a tool starts. Values never contain whitespace, '$', quotes, backticks or ';'.

_CC_FLAG_RE = re.compile("|".join((
    r"-O[0-3sgz]?|-Ofast",
    r"-g|-g[0-3]|-ggdb[0-3]?|-gdwarf(-[2-5])?",
    r"-W(no-)?(error=)?[a-z][a-z0-9+-]*(=[a-z0-9+-]+)?",  # no comma: -Wl, -Wa, -Wp, never match
    r"-D[A-Za-z_][A-Za-z0-9_]*(=[A-Za-z0-9_.,+:-]*)?",
    r"-U[A-Za-z_][A-Za-z0-9_]*",
    r"-l[A-Za-z0-9_][A-Za-z0-9_.+-]*",
    r"-std=(c|gnu|c\+\+|gnu\+\+)[0-9a-z]{2}",
    r"-f(no-)?[A-Za-z0-9][A-Za-z0-9+-]*(=[A-Za-z0-9_.,+-]+)?",
    r"-m[a-z0-9][a-z0-9.+-]*(=[A-Za-z0-9_.,+-]+)?",
    r"-pthread|-static|-shared|-pie|-no-pie|-rdynamic|-pedantic|-pedantic-errors|-w|-ansi|-v|-MD|-MMD|-MP",
)))
# -f options that load code, choose another linker or write files at a given place.
_CC_F_REFUSED = ("-fplugin", "-fno-plugin", "-fdump", "-fuse-ld", "-fprofile-generate=", "-fprofile-use=", "-fprofile-dir=",
                 "-fauto-profile", "-fcallgraph-info=", "-fstack-usage=")
_CC_PATH_RE = re.compile(r"-([IL])([A-Za-z0-9._+/-]+)")
_LD_PASS = {"--build-id", "--build-id=none", "--build-id=sha1", "--build-id=md5", "--build-id=uuid", "--gc-sections", "--as-needed",
            "--no-as-needed", "--no-undefined", "--export-dynamic", "-E", "-O1"}
_LD_Z = {"relro", "norelro", "now", "lazy", "noexecstack", "defs"}
_INPUT_FILE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+/-]*\.(o|a|so)|/[A-Za-z0-9._+/-]+\.(o|a|so)")
# GNU ld reads any input it does not recognise as a linker script, so inputs are checked by content.
# Thin archives are refused: their members are paths that ld opens at link time.
_INPUT_MAGIC = {".o": b"\x7fELF", ".so": b"\x7fELF", ".a": b"!<arch>\n"}
# gcc hands a file with any other extension to the linker, for the same effect.
_SOURCE_EXT = (".c", ".cc", ".cpp", ".cxx", ".s", ".S")
_PATH_SAFE_RE = re.compile(r"[A-Za-z0-9._+/-]+")
_MAKE_TARGET_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+/-]*")
_MAKE_OPTS = {"-k", "--keep-going", "-s", "--silent", "--quiet", "-B", "--always-make", "-n", "--dry-run", "-w", "--no-print-directory"}
_MAKE_FLAG_VARS = {"CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "LDLIBS"}  # each word must be an allowed compiler flag
_MAKE_SIMPLE_VARS = {"V", "VERBOSE", "DEBUG"}  # a small number or yes/no
_SIMPLE_VALUE_RE = re.compile(r"[0-9]{1,3}|yes|no|true|false|on|off")
_JOBS_RE = re.compile(r"[1-9][0-9]{0,3}")
_CMAKE_TARGET_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*")
# Separate-argument forms: the option, then one argument that is checked like the joined form.
_PAIR_PATH = {"-I", "-L", "-isystem", "-iquote"}  # a directory inside the workspace
_PAIR_FILE = {"-include"}                          # a file inside the workspace
_MACRO_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# -D values with double quotes (a C string such as -DVERSION="1.2"), for qcc mode only, where the
# argument goes to qcc as one argv element. No spaces or shell metacharacters: whether qcc hands its
# arguments to gcc without a shell is not checked here, so the value must be harmless if one sees it.
# make and cmake hand CFLAGS to a shell in their recipes, so there the plain -D form applies.
_QUOTED_DEFINE_RE = re.compile(r'-D[A-Za-z_][A-Za-z0-9_]*="[A-Za-z0-9_.,+:/=-]*"')
# -f options that only rewrite paths recorded in the output: old=new, both plain paths.
_PREFIX_MAP_RE = re.compile(r"-f(debug|file|macro)-prefix-map=([A-Za-z0-9._+/-]+)=([A-Za-z0-9._+/-]*)")
_MAKE_PATH_VARS = {"PREFIX", "DESTDIR"}  # a directory inside the workspace (make install writes there)
_CMAKE_CONFIGS = {"Debug", "Release", "RelWithDebInfo", "MinSizeRel"}


def _refuse(field: str, arg: str, why: str) -> QnxMcpError:
    return QnxMcpError(
        "invalid_argument",
        f"{field} argument {arg!r} is not allowed: {why}",
        hint="build accepts only plain compiler, linker and make arguments (see the build tool description). "
        "Put anything else in the project's own Makefile or CMakeLists.txt.",
        field=field,
        argument=arg,
    )


def _no_dotdot(path: str) -> bool:
    return ".." not in path.split("/")


def _check_in_workspace(cfg: Config, root: Path, field: str, arg: str, path: str) -> None:
    if not _PATH_SAFE_RE.fullmatch(path) or not _no_dotdot(path):
        raise _refuse(field, arg, "paths may use letters, digits and . _ + - / only, without '..'")
    p = Path(path) if path.startswith("/") else root / path
    if not within(p, cfg.workspace):
        raise QnxMcpError("path_not_allowed", f"{field} argument {arg!r}: {os.path.realpath(p)} is outside the workspace",
                          hint=WORKSPACE_HINT, field=field, argument=arg)


def _check_ld_items(cfg: Config, root: Path, field: str, a: str) -> None:
    items = a[4:].split(",")
    i = 0
    while i < len(items):
        it = items[i]
        if it in _LD_PASS:
            i += 1
        elif it == "-z" and i + 1 < len(items) and items[i + 1] in _LD_Z:
            i += 2
        elif it.startswith("-Map="):
            _check_in_workspace(cfg, root, field, a, it[5:])  # the linker writes the map file there
            i += 1
        else:
            raise _refuse(field, a, f"linker option {it!r} is not in the allowed set {sorted(_LD_PASS)}, -z {sorted(_LD_Z)}, "
                          "or -Map=<workspace path>")


def _check_cc_flag(cfg: Config, root: Path, field: str, a: str, *, files: bool, quoted_defines: bool = False) -> None:
    """One compiler or linker argument. files: plain .o/.a/.so inputs are allowed (qcc mode).
    quoted_defines: a -D value may be a double-quoted string (qcc mode only)."""
    if not isinstance(a, str) or not a:
        raise _refuse(field, str(a), "empty")
    m = _CC_PATH_RE.fullmatch(a)
    if m:
        _check_in_workspace(cfg, root, field, a, m.group(2))
        return
    if a.startswith("-Wl,"):
        _check_ld_items(cfg, root, field, a)
        return
    if a.startswith(("-Wa,", "-Wp,", "-Xlinker", "-Xassembler", "-Xpreprocessor")):
        raise _refuse(field, a, "assembler, preprocessor and linker passthrough is not allowed (only listed -Wl, options)")
    pm = _PREFIX_MAP_RE.fullmatch(a)
    if pm:
        if not (_no_dotdot(pm.group(2)) and _no_dotdot(pm.group(3))):
            raise _refuse(field, a, "prefix maps take plain paths without '..'")
        return
    if a.startswith("-f") and (a.startswith(_CC_F_REFUSED) or ".." in a or "/" in a):
        raise _refuse(field, a, "this -f option loads code or writes files at a chosen place")
    if _CC_FLAG_RE.fullmatch(a):
        return
    if a.startswith("-D") and _QUOTED_DEFINE_RE.fullmatch(a):
        if quoted_defines:
            return
        raise _refuse(field, a, "quoted -D values are accepted only in qcc mode; make and cmake pass flags through a shell")
    if files and not a.startswith(("-", "@")) and _INPUT_FILE_RE.fullmatch(a):
        _check_in_workspace(cfg, root, field, a, a)
        magic = _INPUT_MAGIC[os.path.splitext(a)[1]]
        try:
            with open(Path(a) if a.startswith("/") else root / a, "rb") as f:
                head = f.read(len(magic))
        except OSError:
            head = b""
        if head != magic:
            raise _refuse(field, a, "an object or shared library must be an ELF file and an archive an ar file "
                          "(anything else would be read as a linker script)")
        return
    raise _refuse(field, a, "not an allowed compiler or linker flag")


def _check_cc_flags(cfg: Config, root: Path, field: str, flags: list[str], *, files: bool, quoted_defines: bool = False) -> None:
    """A list of compiler or linker arguments, where an option may take the next element as its argument."""
    i = 0
    while i < len(flags):
        a = flags[i]
        if a in _PAIR_PATH | _PAIR_FILE | {"-D", "-U"}:
            if i + 1 >= len(flags) or not isinstance(flags[i + 1], str):
                raise _refuse(field, str(a), "it needs an argument after it")
            v = flags[i + 1]
            shown = f"{a} {v}"
            if v.startswith(("-", "@")) or not v:
                raise _refuse(field, shown, f"{a} takes a value, not an option")
            if a in _PAIR_PATH:
                _check_in_workspace(cfg, root, field, shown, v)
            elif a in _PAIR_FILE:
                _check_in_workspace(cfg, root, field, shown, v)
                if not (Path(v) if v.startswith("/") else root / v).is_file():
                    raise _refuse(field, shown, f"{a} takes a file inside the workspace")
            elif a == "-U":
                if not _MACRO_RE.fullmatch(v):
                    raise _refuse(field, shown, "-U takes a macro name")
            else:
                _check_cc_flag(cfg, root, field, "-D" + v, files=False, quoted_defines=quoted_defines)
            i += 2
            continue
        _check_cc_flag(cfg, root, field, a, files=files, quoted_defines=quoted_defines)
        i += 1


def _check_make_args(cfg: Config, root: Path, args: list[str]) -> None:
    field = "make_args"
    prev = None
    for a in args:
        if not isinstance(a, str) or not a:
            raise _refuse(field, str(a), "empty")
        if prev in ("-j", "--jobs") and _JOBS_RE.fullmatch(a):
            prev = None
            continue
        prev = a
        if a in _MAKE_OPTS or a in ("-j", "--jobs") or re.fullmatch(r"-j[1-9][0-9]{0,3}|--jobs=[1-9][0-9]{0,3}", a):
            continue
        name, eq, value = a.partition("=")
        if eq and re.fullmatch(r"[A-Za-z_.][A-Za-z0-9_.]*", name):
            if name in _MAKE_FLAG_VARS:
                try:
                    _check_cc_flags(cfg, root, field, [w for w in value.split(" ") if w], files=False)
                except QnxMcpError as e:
                    raise _refuse(field, a, f"{name}: {e.message.split(': ', 1)[-1]}") from None
                continue
            if name in _MAKE_PATH_VARS:
                _check_in_workspace(cfg, root, field, a, value)
                continue
            if name in _MAKE_SIMPLE_VARS:
                if _SIMPLE_VALUE_RE.fullmatch(value):
                    continue
                raise _refuse(field, a, f"{name} takes a small number or yes/no")
            raise _refuse(field, a, f"only {sorted(_MAKE_FLAG_VARS | _MAKE_SIMPLE_VARS | _MAKE_PATH_VARS)} may be set on the command line")
        if not a.startswith("-") and _MAKE_TARGET_RE.fullmatch(a) and _no_dotdot(a):
            continue
        raise _refuse(field, a, "make accepts target names, -j N, -k, -s, -B, -n, -w and allowed VAR=value only")


def _check_cmake_build_args(args: list[str]) -> None:
    field = "make_args"
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a in ("--clean-first", "-v", "--verbose") or re.fullmatch(r"-j[1-9][0-9]{0,3}|--parallel=[1-9][0-9]{0,3}", str(a)):
            i += 1
        elif a in ("--target", "-t") and isinstance(nxt, str) and _CMAKE_TARGET_RE.fullmatch(nxt):
            i += 2
        elif a in ("-j", "--parallel") and isinstance(nxt, str) and _JOBS_RE.fullmatch(nxt):
            i += 2
        elif a == "--config" and nxt in _CMAKE_CONFIGS:
            i += 2
        else:
            raise _refuse(field, str(a), "cmake --build accepts --target NAME, -j N, --parallel N, --config NAME, --clean-first and -v only")


def _check_library_search(cfg: Config, root: Path, flags: list[str]) -> None:
    """For -lNAME, ld opens libNAME.so or libNAME.a in each -L directory and reads a file it does not
    recognise as a linker script, so these candidates are checked by content like direct inputs."""
    dirs: list[str] = []
    names: list[str] = []
    i = 0
    while i < len(flags):
        a = flags[i]
        if a == "-L" and i + 1 < len(flags):
            dirs.append(flags[i + 1])
            i += 2
            continue
        if isinstance(a, str) and a.startswith("-L") and _CC_PATH_RE.fullmatch(a):
            dirs.append(a[2:])
        elif isinstance(a, str) and a.startswith("-l"):
            names.append(a[2:])
        i += 1
    for d in dirs:
        base = Path(d) if d.startswith("/") else root / d
        for name in names:
            for ext in (".so", ".a"):
                cand = base / f"lib{name}{ext}"
                if not os.path.lexists(cand):
                    continue
                shown = f"-L{d} -l{name}"
                if not within(cand, cfg.workspace):
                    raise QnxMcpError("path_not_allowed", f"ldflags argument {shown!r}: {os.path.realpath(cand)} is outside the workspace",
                                      hint=WORKSPACE_HINT, field="ldflags", argument=shown)
                try:
                    with open(cand, "rb") as f:
                        head = f.read(len(_INPUT_MAGIC[ext]))
                except OSError:
                    head = b""
                if head != _INPUT_MAGIC[ext]:
                    raise _refuse("ldflags", shown, f"{cand} must be an ELF shared library or an ar archive "
                                  "(anything else would be read as a linker script)")


def check_build_args(cfg: Config, root: Path, system: str, cflags: list[str] | None, ldflags: list[str] | None,
                     make_args: list[str] | None, sources: list[str] | None = None) -> None:
    """Refuse any agent-supplied build argument outside the allow-list (QnxMcpError naming the argument)."""
    files = system == "qcc"
    for field, flags in (("cflags", cflags), ("ldflags", ldflags)):
        _check_cc_flags(cfg, root, field, list(flags or []), files=files, quoted_defines=system == "qcc")
    if files:
        _check_library_search(cfg, root, list(cflags or []) + list(ldflags or []))
    for s_ in sources or []:
        if not isinstance(s_, str) or not s_ or s_.startswith(("-", "@")) or "\n" in s_:
            raise _refuse("sources", str(s_), "a source is a file path, never an option")
        if system == "qcc" and not s_.endswith(_SOURCE_EXT):
            raise _refuse("sources", s_, f"qcc mode compiles C, C++ and assembly sources only ({', '.join(_SOURCE_EXT)})")
    if system == "make":
        # make gets no flags from the server, so they would be dropped while the build reports ok.
        for field, flags, var in (("cflags", cflags, "CFLAGS=..."), ("ldflags", ldflags, "LDFLAGS=... or LDLIBS=...")):
            if flags:
                raise QnxMcpError("invalid_argument", f"{field} is not used in make mode, so it is refused",
                                  hint=f"Pass make_args=['{var}'] instead; the Makefile decides how it uses them.", field=field)
        _check_make_args(cfg, root, list(make_args or []))
    elif system == "cmake":
        _check_cmake_build_args(list(make_args or []))
    elif make_args:
        raise _refuse("make_args", make_args[0], "make_args are for system=make or cmake")


# ---------------------------------------------------------------- build tool

_CMAKE_TOOLCHAIN = """# Generated by qnx-mcp
set(CMAKE_SYSTEM_NAME QNX)
set(CMAKE_SYSTEM_PROCESSOR {proc})
set(CMAKE_C_COMPILER "{qcc}")
set(CMAKE_C_COMPILER_TARGET {variant})
set(CMAKE_CXX_COMPILER "{qxx}")
set(CMAKE_CXX_COMPILER_TARGET {variant}_cxx)
set(CMAKE_SYSROOT "{sysroot}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
"""


def _artifacts(paths: list[Path], since: float) -> list[dict[str, Any]]:
    arts = []
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_mtime + 1 < since:
            continue
        info = elf_info(str(p))
        if info and info["type"] in ("executable", "shared-object-or-pie"):
            arts.append({"path": str(p), **info})
    return arts


def _scan_elves(root: Path, since: float, limit: int = 2000) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "CMakeFiles", "__pycache__")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if fn.endswith((".o", ".a", ".c", ".h", ".cpp", ".txt", ".cmake", ".py", ".md")):
                continue
            found.append(p)
            if len(found) >= limit:
                return found
    return found


def build(
    cfg: Config,
    path: str,
    system: str = "auto",
    arch: str = "x86_64",
    sources: list[str] | None = None,
    output: str | None = None,
    cflags: list[str] | None = None,
    ldflags: list[str] | None = None,
    make_args: list[str] | None = None,
    timeout_s: float = 300,
) -> dict[str, Any]:
    check_arch(arch)
    variant, _, proc = ARCHES[arch]
    root = check_host_path(cfg, path, "path", kind="any")
    if root.is_file():
        sources = [str(root)] + list(sources or [])
        root = root.parent
        system = "qcc" if system == "auto" else system
    if system == "auto":
        if (root / "CMakeLists.txt").is_file():
            system = "cmake"
        elif any((root / n).is_file() for n in ("Makefile", "makefile", "GNUmakefile")):
            system = "make"
        else:
            system = "qcc"
    if system not in ("qcc", "make", "cmake"):
        raise QnxMcpError("invalid_argument", f"unknown build system {system!r}", hint="Use auto, qcc, make or cmake.")
    check_build_args(cfg, root, system, cflags, ldflags, make_args, sources)
    if system == "qcc":
        if not sources:
            sources = sorted(str(p) for p in root.glob("*.c")) + sorted(str(p) for p in root.glob("*.cpp"))
        if not sources:
            raise QnxMcpError("invalid_argument", f"no C/C++ sources in {root}", hint="Pass sources=[...] or point path at a file.")
        out_path = Path(output).expanduser() if output else root / Path(sources[0]).stem
        if not out_path.is_absolute():
            out_path = root / out_path
        if not within(out_path, cfg.workspace):  # the output itself, so a symlink there cannot redirect the write
            raise QnxMcpError("path_not_allowed", f"output {os.path.realpath(out_path)} is outside the workspace", hint=WORKSPACE_HINT)
        for src_ in sources:
            sp = Path(src_) if os.path.isabs(src_) else root / src_
            if not within(sp, cfg.workspace):
                raise QnxMcpError("path_not_allowed", f"source {os.path.realpath(sp)} is outside the workspace", hint=WORKSPACE_HINT)
    env = sdk_env(cfg)

    started = time.time()
    steps: list[dict[str, Any]] = []
    candidates: list[Path] = []

    def step(argv: list[str], extra_env: dict[str, str] | None = None) -> bool:
        e = dict(env)
        e.update(extra_env or {})
        r = run(argv, timeout=max(5.0, timeout_s - (time.time() - started)), env=e, cwd=root)
        combined = r.out + ("\n" if r.out and r.err else "") + r.err
        steps.append({"argv": argv, "exit_code": r.returncode, "timed_out": r.timed_out, "duration_s": round(r.duration_s, 3), "output": combined})
        return r.returncode == 0 and not r.timed_out

    if system == "qcc":
        is_cxx = any(s.endswith((".cpp", ".cc", ".cxx")) for s in sources)
        argv = [sdk_tool(env, "q++" if is_cxx else "qcc"), f"-V{variant}{'_cxx' if is_cxx else ''}", "-fdiagnostics-format=json"]
        argv += list(cflags) if cflags is not None else ["-g", "-O0", "-Wall"]
        argv += ["-o", str(out_path), *sources, *(ldflags or [])]
        ok = step(argv)
        candidates = [out_path]
    elif system == "make":
        cc = f"{sdk_tool(env, 'qcc')} -V{variant}"
        cxx = f"{sdk_tool(env, 'q++')} -V{variant}_cxx"
        ok = step([sdk_tool(env, "make"), "-C", str(root), f"-j{os.cpu_count() or 4}", *(make_args or [])], {"CC": cc, "CXX": cxx})
        candidates = _scan_elves(root, started)
    else:
        bdir = root / f"build-qnx-{arch}"
        if not within(bdir, cfg.workspace):  # a symlink named like the build directory must not move the build elsewhere
            raise QnxMcpError("path_not_allowed", f"build directory {os.path.realpath(bdir)} is outside the workspace", hint=WORKSPACE_HINT)
        bdir.mkdir(exist_ok=True)
        tc = bdir / "qnx-toolchain.cmake"
        # The workspace may hold a link at this name; write_atomic replaces it instead of writing through it.
        write_atomic(tc, _CMAKE_TOOLCHAIN.format(
            proc=proc, qcc=sdk_tool(env, "qcc"), qxx=sdk_tool(env, "q++"), variant=variant, sysroot=env["QNX_TARGET"]
        ).encode(), 0o644)
        cmake = shutil.which("cmake", path=env.get("PATH"))
        if not cmake:
            raise QnxMcpError("tool_missing", "cmake not found on the build host", hint="Install cmake 3.16+ or use system=make/qcc.")
        # Set on every configure, empty when omitted: they are cache variables, so a value from an earlier call would stay.
        cc_flags, ld_flags = " ".join(cflags or []), " ".join(ldflags or [])
        ok = step([cmake, "-S", str(root), "-B", str(bdir), f"-DCMAKE_TOOLCHAIN_FILE={tc}", "-DCMAKE_BUILD_TYPE=Debug",
                   f"-DCMAKE_C_FLAGS={cc_flags}", f"-DCMAKE_CXX_FLAGS={cc_flags}", f"-DCMAKE_EXE_LINKER_FLAGS={ld_flags}"])
        if ok:
            ok = step([cmake, "--build", str(bdir), f"-j{os.cpu_count() or 4}", *(make_args or [])])
        candidates = _scan_elves(bdir, started)

    all_output = "\n".join(s["output"] for s in steps)
    diags = parse_diagnostics(all_output, str(root))
    for s in steps:
        s["output"] = tail(s["output"], 3000)
    arts = _artifacts(candidates, started) if ok else []
    errors = sum(1 for d in diags if d["severity"] == "error")
    result: dict[str, Any] = {
        "ok": ok,
        "system": system,
        "arch": arch,
        "directory": str(root),
        "duration_s": round(time.time() - started, 3),
        "error_count": errors,
        "warning_count": sum(1 for d in diags if d["severity"] == "warning"),
        "diagnostics": diags,
        "artifacts": arts,
        "steps": steps,
    }
    if not ok:
        timed_out = any(s["timed_out"] for s in steps)
        result["error"] = {
            "code": "timeout" if timed_out else "build_failed",
            "message": "build timed out" if timed_out else f"build failed with {errors} error(s)",
            "hint": "Fix the diagnostics (file/line/column) and call build again." if errors else "See steps[].output.",
        }
    elif not arts:
        result["warning"] = "build succeeded but no new QNX ELF executable was found; pass output= or check the build tree"
    return result
