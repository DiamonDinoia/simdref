from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

from simdref.perf import best_cpi, best_latency
from simdref.queries import linked_instruction_records
from simdref.search import search_records
from simdref.storage import (
    load_intrinsic_from_db,
    load_instruction_from_db,
    open_db,
    search_intrinsic_candidates_from_db,
    search_instruction_candidates_from_db,
)


WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


@dataclass
class Session:
    documents: dict[str, str]


def _jsonrpc_write(payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _jsonrpc_read() -> dict | None:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, value = line.decode("ascii").split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body)


def _word_at(text: str, line: int, character: int) -> str | None:
    lines = text.splitlines()
    if line >= len(lines):
        return None
    current = lines[line]
    for match in WORD_RE.finditer(current):
        if match.start() <= character <= match.end():
            return match.group(0)
    return None


def _line_prefix(text: str, line: int, character: int) -> str:
    lines = text.splitlines()
    if line >= len(lines):
        return ""
    current = lines[line][:character]
    match = re.search(r"[A-Za-z_][A-Za-z0-9_.]*$", current)
    return match.group(0) if match else ""


def _hover_markdown(conn, word: str) -> str | None:
    intrinsic = load_intrinsic_from_db(conn, word)
    if intrinsic is not None:
        lines = [f"```c\n{intrinsic.signature}\n```"]
        if intrinsic.description:
            lines.append(intrinsic.description)
        meta = []
        if intrinsic.header:
            meta.append(f"header `{intrinsic.header}`")
        if intrinsic.isa:
            meta.append(f"ISA {', '.join(intrinsic.isa)}")
        if intrinsic.category:
            meta.append(f"category {intrinsic.category}")
        if intrinsic.url:
            meta.append(f"[source]({intrinsic.url})")
        if meta:
            lines.append(" | ".join(meta))
        if intrinsic.instructions:
            lines.append(f"Instructions: {', '.join(intrinsic.instructions[:6])}")
        linked = linked_instruction_records(None, intrinsic, conn=conn)
        if linked:
            latencies = [
                best_latency(item.arch_details)
                for item in linked
                if best_latency(item.arch_details) != "-"
            ]
            throughputs = [
                best_cpi(item.arch_details) for item in linked if best_cpi(item.arch_details) != "-"
            ]
            perf = []
            if latencies:
                perf.append(f"best latency {min(latencies, key=lambda value: float(value))} cycles")
            if throughputs:
                perf.append(f"best cycle/instr {min(throughputs, key=lambda value: float(value))}")
            if perf:
                lines.append("Performance: " + ", ".join(perf))
        return "\n\n".join(lines)

    instruction = load_instruction_from_db(conn, word)
    if instruction is not None:
        lines = [f"```asm\n{instruction.key}\n```"]
        if instruction.summary:
            lines.append(instruction.summary)
        meta = []
        if instruction.isa:
            meta.append(f"ISA {', '.join(instruction.isa)}")
        if instruction.metadata.get("category"):
            meta.append(f"category {instruction.metadata['category']}")
        if meta:
            lines.append(" | ".join(meta))
        if instruction.linked_intrinsics:
            lines.append(f"Intrinsics: {', '.join(instruction.linked_intrinsics[:6])}")
        perf = []
        lat = best_latency(instruction.arch_details)
        cpi = best_cpi(instruction.arch_details)
        if lat != "-":
            perf.append(f"best latency {lat} cycles")
        if cpi != "-":
            perf.append(f"best cycle/instr {cpi}")
        if perf:
            lines.append("Performance: " + ", ".join(perf))
        return "\n\n".join(lines)
    return None


def _completion_candidates(conn, prefix: str, limit: int = 50) -> list[dict]:
    prefix_folded = prefix.casefold()
    emitted: set[tuple[str, str]] = set()
    items: list[dict] = []
    candidate_limit = max(limit * 3, 100)
    intrinsics = search_intrinsic_candidates_from_db(conn, prefix or "_mm", limit=candidate_limit)
    instructions = search_instruction_candidates_from_db(
        conn, prefix or "_mm", limit=candidate_limit
    )
    for result in search_records(intrinsics, instructions, prefix or "_mm", limit=candidate_limit):
        label = result.title
        if prefix_folded and not label.casefold().startswith(prefix_folded):
            continue
        key = (result.kind, label)
        if key in emitted:
            continue
        emitted.add(key)
        kind = 3 if result.kind == "intrinsic" else 14
        items.append({"label": label, "kind": kind, "detail": result.subtitle, "insertText": label})
        if len(items) >= limit:
            return items
    return items


def main() -> int:
    conn = open_db()
    session = Session(documents={})
    while True:
        message = _jsonrpc_read()
        if message is None:
            return 0
        method = message.get("method")
        if method == "initialize":
            _jsonrpc_write(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "capabilities": {
                            "hoverProvider": True,
                            "textDocumentSync": 1,
                            "completionProvider": {
                                "resolveProvider": False,
                                "triggerCharacters": ["_", ".", "m", "v"],
                            },
                        }
                    },
                }
            )
        elif method == "initialized":
            continue
        elif method == "shutdown":
            _jsonrpc_write({"jsonrpc": "2.0", "id": message["id"], "result": None})
        elif method == "exit":
            return 0
        elif method == "textDocument/didOpen":
            params = message["params"]
            session.documents[params["textDocument"]["uri"]] = params["textDocument"]["text"]
        elif method == "textDocument/didChange":
            params = message["params"]
            session.documents[params["textDocument"]["uri"]] = params["contentChanges"][-1]["text"]
        elif method == "textDocument/didClose":
            params = message["params"]
            session.documents.pop(params["textDocument"]["uri"], None)
        elif method == "textDocument/hover":
            params = message["params"]
            uri = params["textDocument"]["uri"]
            text = session.documents.get(uri, "")
            word = _word_at(text, params["position"]["line"], params["position"]["character"])
            contents = None
            if word:
                body = _hover_markdown(conn, word)
                if body:
                    contents = {"kind": "markdown", "value": body}
            _jsonrpc_write(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"contents": contents} if contents else None,
                }
            )
        elif method == "textDocument/completion":
            params = message["params"]
            uri = params["textDocument"]["uri"]
            text = session.documents.get(uri, "")
            prefix = _line_prefix(text, params["position"]["line"], params["position"]["character"])
            items = _completion_candidates(conn, prefix)
            _jsonrpc_write(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"isIncomplete": False, "items": items},
                }
            )
    return 0
