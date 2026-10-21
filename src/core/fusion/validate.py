"""
Strict parser for the Lua subset Fusion uses in .setting/.comp files.

Used by tests/CI to guarantee every generated file is syntactically loadable,
and to check macro wiring (every SourceOp points at an existing tool, every
published InstanceInput targets a real tool).
"""
from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Tuple

_TOKEN = re.compile(r"""
    (?P<ws>\s+|--[^\n]*)
  | (?P<str>"(?:\\.|[^"\\])*")
  | (?P<num>-?(?:\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?))
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<sym>[{}\[\]=,;()])
""", re.X)


class LuaSyntaxError(ValueError):
    pass


class Node(dict):
    """Parsed table; `ctor` keeps the constructor name (TextPlus, Input ...)."""
    ctor: str = ""
    positional: List[Any]


def tokenize(src: str) -> List[Tuple[str, str]]:
    pos, toks = 0, []
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            line = src.count("\n", 0, pos) + 1
            raise LuaSyntaxError("Unexpected char %r at line %d" % (src[pos], line))
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        toks.append((kind, m.group(kind)))
    return toks


class Parser:
    def __init__(self, src: str):
        self.t = tokenize(src)
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("eof", "")

    def take(self, sym=None):
        tok = self.peek()
        if sym is not None and tok[1] != sym:
            raise LuaSyntaxError("Expected %r got %r (token %d)" % (sym, tok[1], self.i))
        self.i += 1
        return tok

    def value(self):
        kind, txt = self.peek()
        if kind == "str":
            self.take()
            return bytes(txt[1:-1], "utf-8").decode("unicode_escape", errors="ignore")
        if kind == "num":
            self.take()
            return float(txt)
        if kind == "name":
            if txt in ("true", "false"):
                self.take()
                return txt == "true"
            if txt == "nil":
                self.take()
                return None
            self.take()
            ctor = txt
            if self.peek()[1] == "(":
                self.take("(")
                self.take(")")
                ctor += "()"
            tbl = self.table()
            tbl.ctor = ctor
            return tbl
        if txt == "{":
            return self.table()
        raise LuaSyntaxError("Unexpected token %r" % (txt,))

    def table(self) -> Node:
        self.take("{")
        node = Node()
        node.positional = []
        while self.peek()[1] != "}":
            kind, txt = self.peek()
            if txt == "[":
                self.take("[")
                k = self.value()
                self.take("]")
                self.take("=")
                node[k] = self.value()
            elif kind == "name" and self.peek(1)[1] == "=":
                self.take()
                self.take("=")
                if txt in node:
                    raise LuaSyntaxError("Duplicate key %s" % txt)
                node[txt] = self.value()
            else:
                node.positional.append(self.value())
            if self.peek()[1] in (",", ";"):
                self.take()
            elif self.peek()[1] != "}":
                raise LuaSyntaxError("Expected , or } got %r" % (self.peek()[1],))
        self.take("}")
        return node


def parse(src: str):
    p = Parser(src)
    v = p.value()
    if p.peek()[0] != "eof":
        raise LuaSyntaxError("Trailing data after root table")
    return v


_EXPR_REF = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
_EXPR_GLOBALS = {"Point", "Text"}
# inputs that exist on the tool even if never written into the file
_IMPLICIT_INPUTS = {"Center", "Width", "Height", "Size", "Angle", "CornerRadius", "Delay", "Speed",
                    "Red1", "Green1", "Blue1", "Red2", "Green2", "Blue2", "StyledText"}


def _walk_sources(tools: Dict[str, Any], problems: List[str], where: str):
    names = set(tools.keys())
    for tname, tool in tools.items():
        if not isinstance(tool, Node):
            continue
        if tool.get("SourceOp") and tool["SourceOp"] not in names:
            problems.append("%s: instance %s -> missing %s" % (where, tname, tool["SourceOp"]))
        if tool.ctor != "MacroOperator":
            for iname, inp in (tool.get("Inputs") or {}).items():
                if not isinstance(inp, Node):
                    continue
                if "SourceOp" in inp and inp["SourceOp"] not in names:
                    problems.append("%s: %s.%s -> missing %s" % (where, tname, iname, inp["SourceOp"]))
                expr = inp.get("Expression")
                if isinstance(expr, str):
                    for ref_tool, ref_inp in _EXPR_REF.findall(expr):
                        if ref_tool in _EXPR_GLOBALS:
                            continue
                        target = tools.get(ref_tool)
                        if target is None:
                            problems.append("%s: %s.%s expression -> unknown tool %s" % (where, tname, iname, ref_tool))
                            continue
                        known = set((target.get("Inputs") or {}).keys()) | set((target.get("UserControls") or {}).keys())
                        if ref_inp not in known and ref_inp not in _IMPLICIT_INPUTS:
                            problems.append("%s: %s.%s expression -> %s.%s not defined (add it to Inputs)"
                                            % (where, tname, iname, ref_tool, ref_inp))
        else:
            inner = tool.get("Tools") or {}
            _walk_sources(inner, problems, where + "/" + tname)
            inner_names = set(inner.keys())
            for k, ii in (tool.get("Inputs") or {}).items():
                if ii.get("SourceOp") not in inner_names:
                    problems.append("%s: published %s -> missing %s" % (tname, k, ii.get("SourceOp")))
            for k, oo in (tool.get("Outputs") or {}).items():
                if oo.get("SourceOp") not in inner_names:
                    problems.append("%s: output %s -> missing %s" % (tname, k, oo.get("SourceOp")))


def check_file(path: str) -> List[str]:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        root = parse(src)
    except LuaSyntaxError as e:
        return ["%s: syntax: %s" % (path, e)]
    problems: List[str] = []
    tools = root.get("Tools") if isinstance(root, Node) else None
    if not tools:
        return ["%s: no Tools table" % path]
    _walk_sources(tools, problems, path)
    return problems


if __name__ == "__main__":
    bad = 0
    for p in sys.argv[1:]:
        errs = check_file(p)
        for e in errs:
            print("FAIL", e)
        bad += bool(errs)
        if not errs:
            print("ok  ", p)
    sys.exit(1 if bad else 0)
