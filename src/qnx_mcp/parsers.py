# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Pure parsers: compiler diagnostics, GDB/MI, pidin, slog2info and ELF headers.

Everything here is side-effect free so it can be unit-tested without a target.
"""

from __future__ import annotations

import json
import os
import re
import struct
from typing import Any

# ---------------------------------------------------------------- compiler diagnostics

_SEVERITIES = {"error": "error", "fatal error": "error", "warning": "warning", "note": "note"}
_TEXT_DIAG = re.compile(
    r"^(?P<file>[^:\n]+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s+"
    r"(?P<sev>fatal error|error|warning|note):\s+(?P<msg>.*?)(?:\s+\[(?P<opt>-W[^\]]+|-f[^\]]+)\])?$"
)
_IN_FUNCTION = re.compile(r"^(?P<file>[^:\n]+?): In function [‘'`](?P<func>[^’'`]+)[’'`]:$")
_LINK_REF = re.compile(r"^(?P<file>[^:\n]+?):(?:\((?P<section>[^)]*)\)|(?P<line>\d+)):\s+(?P<msg>(?:undefined reference|multiple definition).*)$")
_DRIVER_ERR = re.compile(r"^(?P<tool>[\w.+-]*(?:ld|collect2|cc|qcc|gcc|q\+\+|make(?:\[\d+\])?)):\s+(?P<sev>fatal error|error|warning)?:?\s*(?P<msg>.+)$")


def _abs(path: str, base_dir: str | None) -> str:
    if base_dir and not os.path.isabs(path):
        return os.path.normpath(os.path.join(base_dir, path))
    return path


def _from_gcc_json(items: list[dict[str, Any]], base_dir: str | None) -> list[dict[str, Any]]:
    out = []
    for it in items:
        loc = (it.get("locations") or [{}])[0].get("caret", {})
        d: dict[str, Any] = {
            "file": _abs(loc["file"], base_dir) if loc.get("file") else None,
            "line": loc.get("line"),
            "column": loc.get("column"),
            "severity": _SEVERITIES.get(it.get("kind", ""), it.get("kind", "error")),
            "message": it.get("message", ""),
            "option": it.get("option"),
            "source": "gcc-json",
        }
        notes = [
            {
                "file": _abs(c_loc["file"], base_dir) if (c_loc := (c.get("locations") or [{}])[0].get("caret", {})).get("file") else None,
                "line": c_loc.get("line"),
                "column": c_loc.get("column"),
                "message": c.get("message", ""),
            }
            for c in it.get("children", [])
        ]
        if notes:
            d["notes"] = notes
        fixits = [
            {"line": f.get("start", {}).get("line"), "column": f.get("start", {}).get("column"), "replace_with": f.get("string")}
            for f in it.get("fixits", [])
        ]
        if fixits:
            d["fixits"] = fixits
        out.append(d)
    return out


def parse_diagnostics(output: str, base_dir: str | None = None) -> list[dict[str, Any]]:
    """Turn gcc/qcc/ld/make output into diagnostic objects.

    Accepts gcc's -fdiagnostics-format=json lines (one JSON array per compiler
    invocation, possibly interleaved with make output) and the classic
    `file:line:col: severity: message [-Wopt]` text format, plus linker errors.
    """
    diags: list[dict[str, Any]] = []
    func: str | None = None
    for raw in output.splitlines():
        line = raw.rstrip()
        if line.startswith("[{") or line == "[]":
            try:
                items = json.loads(line)
            except ValueError:
                items = None
            if isinstance(items, list):
                diags.extend(_from_gcc_json(items, base_dir))
                continue
        m = _IN_FUNCTION.match(line)
        if m:
            func = m.group("func")
            continue
        m = _TEXT_DIAG.match(line)
        if m:
            sev = _SEVERITIES[m.group("sev")]
            d = {
                "file": _abs(m.group("file"), base_dir),
                "line": int(m.group("line")),
                "column": int(m.group("col")) if m.group("col") else None,
                "severity": sev,
                "message": m.group("msg"),
                "option": m.group("opt"),
                "source": "gcc-text",
            }
            if func:
                d["function"] = func
            if sev == "note" and diags:
                diags[-1].setdefault("notes", []).append({k: d[k] for k in ("file", "line", "column", "message")})
            else:
                diags.append(d)
            continue
        m = _LINK_REF.match(line)
        if m:
            diags.append(
                {
                    "file": _abs(m.group("file"), base_dir),
                    "line": int(m.group("line")) if m.group("line") else None,
                    "column": None,
                    "severity": "error",
                    "message": m.group("msg"),
                    "option": None,
                    "source": "linker",
                    **({"function": func} if func else {}),
                }
            )
            continue
        m = _DRIVER_ERR.match(line)
        if m and (m.group("sev") or "error" in m.group("msg").lower()):
            diags.append(
                {
                    "file": None,
                    "line": None,
                    "column": None,
                    "severity": _SEVERITIES.get(m.group("sev") or "error", "error"),
                    "message": f"{m.group('tool')}: {m.group('msg')}",
                    "option": None,
                    "source": "toolchain",
                }
            )
    return diags


# ---------------------------------------------------------------- GDB/MI


class MIParseError(ValueError):
    pass


def _mi_cstring(s: str, i: int) -> tuple[str, int]:
    if i >= len(s) or s[i] != '"':
        raise MIParseError(f"expected a c-string at {i}")
    i += 1
    buf = []
    esc = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "e": "\x1b"}
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in esc:
                buf.append(esc[nxt])
                i += 2
                continue
            if nxt.isdigit():  # octal escape
                j = i + 1
                while j < len(s) and j < i + 4 and s[j] in "01234567":
                    j += 1
                buf.append(chr(int(s[i + 1 : j], 8)))
                i = j
                continue
            buf.append(nxt)
            i += 2
            continue
        if c == '"':
            return "".join(buf), i + 1
        buf.append(c)
        i += 1
    raise MIParseError("unterminated c-string")


def _mi_value(s: str, i: int) -> tuple[Any, int]:
    c = s[i]
    if c == '"':
        return _mi_cstring(s, i)
    if c == "{":
        i += 1
        res: dict[str, Any] = {}
        if s[i] == "}":
            return res, i + 1
        while True:
            k, v, i = _mi_result(s, i)
            res[k] = v
            if s[i] == ",":
                i += 1
                continue
            if s[i] == "}":
                return res, i + 1
            raise MIParseError(f"bad tuple at {i}")
    if c == "[":
        i += 1
        items: list[Any] = []
        if s[i] == "]":
            return items, i + 1
        while True:
            if s[i] in '"{[':
                v, i = _mi_value(s, i)
                items.append(v)
            else:
                k, v, i = _mi_result(s, i)
                items.append({k: v})
            if s[i] == ",":
                i += 1
                continue
            if s[i] == "]":
                return items, i + 1
            raise MIParseError(f"bad list at {i}")
    raise MIParseError(f"unexpected {c!r} at {i}")


def _mi_result(s: str, i: int) -> tuple[str, Any, int]:
    j = s.find("=", i)
    if j <= i or not re.fullmatch(r"[A-Za-z0-9_-]+", s[i:j]):
        raise MIParseError(f"expected name=value at {i}")
    if j + 1 >= len(s):
        raise MIParseError("value missing at end of record")
    v, k = _mi_value(s, j + 1)
    return s[i:j], v, k


def parse_mi_results(body: str) -> dict[str, Any]:
    """Parse the `a=..,b=..` part of an MI record into a dict (raises MIParseError when malformed)."""
    res: dict[str, Any] = {}
    i = 0
    try:
        while i < len(body):
            k, v, i = _mi_result(body, i)
            res[k] = v
            if i < len(body):
                if body[i] != ",":
                    raise MIParseError(f"expected ',' at {i}")
                i += 1
    except IndexError:
        raise MIParseError("record ends in the middle of a value") from None
    return res


_MI_RECORD = re.compile(r"(?P<token>\d*)(?P<kind>[\^*+=])(?P<cls>[a-z-]+)(?:,(?P<body>.*))?")


def parse_mi_output(text: str) -> dict[str, Any]:
    """Split GDB/MI output into result records by token, stream records and async records.

    A result record whose body does not parse is kept as {"class": ..., "malformed": True}
    so callers can refuse it instead of treating it as an empty success. Results without
    a token are kept apart from tokened ones, and a token seen twice is reported.
    """
    results: dict[str, dict[str, Any]] = {}
    untokened: list[dict[str, Any]] = []
    duplicates: list[str] = []
    console: list[str] = []
    log: list[str] = []
    asyncs: list[dict[str, Any]] = []
    unparsed: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line or line.startswith("(gdb)"):
            continue
        if line[0] in "~@&":
            try:
                textv = _mi_cstring(line, 1)[0]
            except MIParseError:
                unparsed.append(line[:200])
                continue
            (log if line[0] == "&" else console).append(textv)
            continue
        m = _MI_RECORD.fullmatch(line)
        if not m:
            unparsed.append(line[:200])
            continue
        try:
            rec = {"class": m.group("cls"), **(parse_mi_results(m.group("body")) if m.group("body") else {})}
        except MIParseError as e:
            rec = {"class": m.group("cls"), "malformed": True, "parse_error": str(e), "raw": (m.group("body") or "")[:300]}
        if m.group("kind") != "^":
            asyncs.append(rec)
        elif not m.group("token"):
            untokened.append(rec)
        elif m.group("token") in results:
            duplicates.append(m.group("token"))
        else:
            results[m.group("token")] = rec
    return {"results": results, "untokened": untokened, "duplicates": duplicates, "console": console, "log": log,
            "async": asyncs, "unparsed": unparsed}


# ---------------------------------------------------------------- pidin

_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMG]?)$")
_POLICIES = {"f": "FIFO", "r": "RR", "o": "OTHER", "s": "SPORADIC"}


def parse_size(text: str) -> int | None:
    m = _SIZE_RE.match(text.strip())
    if not m:
        return None
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}[m.group(2)]
    return int(float(m.group(1)) * mult)


def parse_pidin(proc_text: str, thread_text: str) -> list[dict[str, Any]]:
    """Parse two `pidin -F` outputs.

    proc_text uses  "%a|%e|%U|%c|%d|%N|%n|%A"  (one line per process),
    thread_text uses "%a|%b|%p|%J|%B"           (one line per thread).
    """
    procs: dict[int, dict[str, Any]] = {}
    for line in proc_text.splitlines()[1:]:
        parts = line.split("|", 7)
        if len(parts) < 8 or not parts[0].strip().isdigit():
            continue
        pid = int(parts[0])
        procs[pid] = {
            "pid": pid,
            "ppid": int(parts[1]) if parts[1].strip().isdigit() else None,
            "uid": int(parts[2]) if parts[2].strip().isdigit() else None,
            "code_bytes": parse_size(parts[3]),
            "data_bytes": parse_size(parts[4]),
            "name": parts[5].strip(),
            "path": parts[6].strip(),
            "args": parts[7].strip(),
            "threads": [],
        }
    for line in thread_text.splitlines()[1:]:
        parts = line.split("|", 4)
        if len(parts) < 5 or not parts[0].strip().isdigit():
            continue
        pid = int(parts[0])
        prio = parts[2].strip()
        m = re.match(r"^(\d+)([a-z]?)$", prio)
        th = {
            "tid": int(parts[1]) if parts[1].strip().isdigit() else None,
            "priority": int(m.group(1)) if m else None,
            "policy": _POLICIES.get(m.group(2), m.group(2) or None) if m else None,
            "state": parts[3].strip(),
            "blocked_on": parts[4].strip() or None,
        }
        procs.setdefault(pid, {"pid": pid, "threads": []})["threads"].append(th)
    return [procs[k] for k in sorted(procs)]


# ---------------------------------------------------------------- slog2info

SEVERITY_NAMES = ["shutdown", "critical", "error", "warning", "notice", "info", "debug1", "debug2"]
# Lines of `slog2info -f -s -t`: epoch.ms  bufset.pid[..n]  buffer[*]  tid  code  severity  message
_SLOG_LINE = re.compile(r"^(?P<ts>\d{9,}\.\d+)\s+(?P<bset>\S+)\s+(?P<buf>\S+)\s+(?P<tid>\d+)\s+(?P<code>\d+)\s+(?P<sev>\d)\s(?P<msg>.*)$")
_BSET = re.compile(r"^(?P<name>.+?)\.(?P<pid>\d+)(?:\.\.\d+)?$")


def parse_slog2(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _SLOG_LINE.match(line)
        if not m:
            if entries and line.strip():
                entries[-1]["message"] += "\n" + line.strip()
            continue
        bm = _BSET.match(m.group("bset"))
        sev = int(m.group("sev"))
        entries.append(
            {
                "time": float(m.group("ts")),
                "process": bm.group("name") if bm else m.group("bset"),
                "pid": int(bm.group("pid")) if bm else None,
                "buffer_set": m.group("bset"),
                "buffer": m.group("buf").rstrip("*"),
                "thread": int(m.group("tid")),
                "code": int(m.group("code")),
                "severity": sev,
                "severity_name": SEVERITY_NAMES[sev] if sev < len(SEVERITY_NAMES) else str(sev),
                "message": m.group("msg").strip(),
            }
        )
    return entries


# ---------------------------------------------------------------- ELF

_MACHINES = {62: "x86_64", 183: "aarch64le", 3: "x86", 40: "arm"}


def elf_info(path: str) -> dict[str, Any] | None:
    """Minimal ELF reader: architecture, type, interpreter and debug info presence."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 64 or data[:4] != b"\x7fELF":
        return None
    is64 = data[4] == 2
    end = "<" if data[5] == 1 else ">"
    e_type, e_machine = struct.unpack_from(end + "HH", data, 16)
    if is64:
        e_phoff, e_shoff = struct.unpack_from(end + "QQ", data, 32)
        e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(end + "HHHHH", data, 54)
    else:
        e_phoff, e_shoff = struct.unpack_from(end + "II", data, 28)
        e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(end + "HHHHH", data, 42)
    interp = None
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type = struct.unpack_from(end + "I", data, off)[0]
        if p_type == 3:  # PT_INTERP
            if is64:
                p_offset, _, _, p_filesz = struct.unpack_from(end + "QQQQ", data, off + 8)
            else:
                p_offset, _, _, p_filesz = struct.unpack_from(end + "IIII", data, off + 4)
            interp = data[p_offset : p_offset + p_filesz].rstrip(b"\0").decode("ascii", "replace")
    names: list[str] = []
    build_id = None
    try:
        def sh(idx: int) -> tuple[int, int, int]:
            off = e_shoff + idx * e_shentsize
            if is64:
                name, _, _, _, s_off, s_size = struct.unpack_from(end + "IIQQQQ", data, off)
            else:
                name, _, _, _, s_off, s_size = struct.unpack_from(end + "IIIIII", data, off)
            return name, s_off, s_size

        _, str_off, str_size = sh(e_shstrndx)
        strtab = data[str_off : str_off + str_size]
        for i in range(e_shnum):
            n_off, s_off, s_size = sh(i)
            names.append(strtab[n_off : strtab.index(b"\0", n_off)].decode("ascii", "replace"))
            if names[-1] == ".note.gnu.build-id":
                for owner, ntype, desc in _notes(data[s_off : s_off + s_size], end):
                    if owner == b"GNU" and ntype == 3:  # NT_GNU_BUILD_ID
                        build_id = desc.hex()
    except (struct.error, ValueError, IndexError):
        pass
    return {
        "arch": _MACHINES.get(e_machine, f"machine-{e_machine}"),
        "type": {1: "relocatable", 2: "executable", 3: "shared-object-or-pie", 4: "core"}.get(e_type, str(e_type)),
        "interpreter": interp,
        "qnx": None if interp is None else ("ldqnx" in interp),
        "debug_info": ".debug_info" in names,
        "build_id": build_id,
        "size": len(data),
    }


def _notes(blob: bytes, end: str) -> list[tuple[bytes, int, bytes]]:
    """Generic ELF note records (namesz, descsz, type, name, desc; 4-byte aligned)."""
    out = []
    i = 0
    while i + 12 <= len(blob):
        namesz, descsz, ntype = struct.unpack_from(end + "III", blob, i)
        name_end = i + 12 + namesz
        desc_start = i + 12 + ((namesz + 3) & ~3)
        desc_end = desc_start + descsz
        if desc_end > len(blob):
            break
        out.append((blob[i + 12 : name_end].rstrip(b"\0"), ntype, blob[desc_start:desc_end]))
        i = desc_start + ((descsz + 3) & ~3)
    return out


def core_note_bytes(path: str, limit: int = 16 << 20) -> bytes | None:
    """Concatenated PT_NOTE segments of an ELF core file (generic ELF program headers only)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 64 or head[:4] != b"\x7fELF" or head[4] != 2:
                return None
            end = "<" if head[5] == 1 else ">"
            (e_phoff,) = struct.unpack_from(end + "Q", head, 32)
            e_phentsize, e_phnum = struct.unpack_from(end + "HH", head, 54)
            f.seek(e_phoff)
            ph = f.read(e_phentsize * e_phnum)
            out = bytearray()
            for n in range(e_phnum):
                p_type, _, p_offset, _, _, p_filesz = struct.unpack_from(end + "IIQQQQ", ph, n * e_phentsize)
                if p_type == 4 and p_filesz <= limit - len(out):
                    f.seek(p_offset)
                    out += f.read(p_filesz)
            return bytes(out)
    except (OSError, struct.error):
        return None
