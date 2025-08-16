#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import unicodedata
from typing import Any, Dict, Optional


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u3000", " ")
    return re.sub(r"[ ]+", " ", s.strip())


class KeiyakuInterpreter:
    _re_alias = re.compile(
        r"(?P<lhs>.+?)[（(]以下[「\"](?P<alias>[^」\"]+)[」\"]という。?[)）][。\.]?$"
    )
    _re_assign = re.compile(r"^(?P<var>[^は]+)は (?P<expr>.+) とする。?$")
    _re_print = re.compile(r"^(?P<var>.+)を出力する。?$")
    _re_add = re.compile(r"^(?P<x>.+)に (?P<y>.+) を加えた数を (?P<z>.+) とする。?$")
    _re_sub1 = re.compile(r"^(?P<x>.+)から (?P<y>.+) を減じた数を (?P<z>.+) とする。?$")
    _re_sub2 = re.compile(r"^(?P<x>.+)から (?P<y>.+) を差し引いた数を (?P<z>.+) とする。?$")
    _re_mul = re.compile(r"^(?P<x>.+)と (?P<y>.+) の積を (?P<z>.+) とする。?$")
    _re_div1 = re.compile(r"^(?P<x>.+)を (?P<y>.+) で除した数を (?P<z>.+) とする。?$")
    _re_div2 = re.compile(r"^(?P<x>.+)を (?P<y>.+) で割った数を (?P<z>.+) とする。?$")
    _re_loop_start = re.compile(r"^(?P<count>.+) 回、以下を行う。?$")
    _re_loop_end = re.compile(r"^以上。?$")
    _re_return = re.compile(r"^(?P<expr>.+)を返す。?$")
    _re_if_zero = re.compile(r"^もし (?P<expr>.+) が 0 なら(?:ば)?、以下を行う。?$")
    _re_if_nonzero = re.compile(r"^もし (?P<expr>.+) が 0 でなければ、以下を行う。?$")
    _re_else = re.compile(r"^そうでなければ(?:、以下を行う。?)?$")

    def __init__(self) -> None:
        self.env: Dict[str, Any] = {}
        self.outputs = []
        self.funcs: Dict[str, Dict[str, Any]] = {}
        self._toplevel_effect = False

    def _value_of(self, token: str) -> Any:
        token = token.strip()
        m_call = re.fullmatch(r"(?P<name>[^\s()]+)\((?P<args>.*)\)", token)
        if m_call and m_call.group("name") in self.funcs:
            name = m_call.group("name")
            args_raw = m_call.group("args").strip()
            args_list = []
            if args_raw:
                args_list = [a.strip() for a in self._split_args(args_raw)]
            arg_vals = [self._value_of(a) for a in args_list]
            return self._call_function(name, arg_vals)
        if (token.startswith("「") and token.endswith("」")) or (
            token.startswith("\"") and token.endswith("\"")
        ):
            return token[1:-1]
        if re.fullmatch(r"[+-]?\d+", token):
            return int(token)
        if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)", token):
            return float(token)
        if token in self.env:
            return self.env[token]
        raise NameError(f"未定義の識別子または解釈できない値です: {token}")

    def _split_args(self, s: str) -> list[str]:
        args = []
        buf = []
        depth_paren = 0
        in_dq = False
        in_jq = False
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == '"' and not in_jq:
                in_dq = not in_dq
                buf.append(ch)
            elif ch == '「' and not in_dq:
                in_jq = True
                buf.append(ch)
            elif ch == '」' and in_jq:
                in_jq = False
                buf.append(ch)
            elif ch == '(' and not in_dq and not in_jq:
                depth_paren += 1
                buf.append(ch)
            elif ch == ')' and not in_dq and not in_jq and depth_paren > 0:
                depth_paren -= 1
                buf.append(ch)
            elif ch == ',' and not in_dq and not in_jq and depth_paren == 0:
                args.append(''.join(buf).strip())
                buf = []
            else:
                buf.append(ch)
            i += 1
        if buf:
            args.append(''.join(buf).strip())
        return args

    class _ReturnSignal(Exception):
        def __init__(self, value: Any):
            super().__init__("return")
            self.value = value

    def _call_function(self, name: str, arg_vals: list[Any]) -> Any:
        if name not in self.funcs:
            raise NameError(f"未定義の関数です: {name}")
        params = self.funcs[name]["params"]
        body_lines = self.funcs[name]["body"]
        if len(params) != len(arg_vals):
            raise TypeError(f"関数 {name} の引数個数が一致しません: 期待 {len(params)} 実際 {len(arg_vals)}")
        saved_env = dict(self.env)
        try:
            for p, v in zip(params, arg_vals):
                self.env[p] = v
            src = "\n".join(body_lines)
            try:
                self.exec(src)
            except KeiyakuInterpreter._ReturnSignal as rs:  # type: ignore
                return rs.value
            return None
        finally:
            self.env = saved_env

    def _assign(self, name: str, value: Any) -> None:
        name = name.strip()
        if not name:
            raise ValueError("空の識別子には代入できません")
        self.env[name] = value

    def _binary_numeric(self, x: str, y: str, op: str) -> Any:
        xv = self._value_of(x)
        yv = self._value_of(y)
        if not isinstance(xv, (int, float)) or not isinstance(yv, (int, float)):
            raise TypeError("数値演算の対象は数値である必要があります")
        if op == "+":
            return xv + yv
        if op == "-":
            return xv - yv
        if op == "*":
            return xv * yv
        if op == "/":
            return xv / yv
        raise ValueError(f"未知の演算: {op}")

    def exec_line(self, raw: str) -> Optional[bool]:
        line = normalize_text(raw)
        if not line:
            return None
        if line.startswith("※") or line.startswith("(注)") or line.startswith("（注）"):
            return None

        # 1) Alias definition: <lhs>（以下「<alias>」という。）
        m = self._re_alias.search(line)
        if m:
            lhs = m.group("lhs").strip()
            alias = m.group("alias").strip()
            val = self._value_of(lhs)
            self._assign(alias, val)
            return True

        # 2) Arithmetic forms
        for regex, op in [
            (self._re_add, "+"),
            (self._re_sub1, "-"),
            (self._re_sub2, "-"),
            (self._re_mul, "*"),
            (self._re_div1, "/"),
            (self._re_div2, "/"),
        ]:
            m2 = regex.match(line)
            if m2:
                x, y, z = m2.group("x").strip(), m2.group("y").strip(), m2.group("z").strip()
                val = self._binary_numeric(x, y, op)
                self._assign(z, val)
                return True

        # 3) Assignment: A は B とする。
        m = self._re_assign.match(line)
        if m:
            var = m.group("var").strip()
            expr = m.group("expr").strip()
            val = self._value_of(expr)
            self._assign(var, val)
            return True

        # 4) Print: A を出力する。
        m = self._re_print.match(line)
        if m:
            var = m.group("var").strip()
            val = self._value_of(var)
            self.outputs.append(val)
            print(val)
            return True

        # 5) Return: A を返す。
        m = self._re_return.match(line)
        if m:
            expr = m.group("expr").strip()
            val = self._value_of(expr)
            raise KeiyakuInterpreter._ReturnSignal(val)

        # Not matched
        raise SyntaxError(f"解釈できない文です: {raw.strip()}")

    def exec(self, program: str, *, is_toplevel: bool = False) -> None:
        lines = program.splitlines()
        i = 0
        lineno = 0
        while i < len(lines):
            raw = lines[i]
            lineno += 1
            raw_s = raw.rstrip()
            if not raw_s:
                i += 1
                continue
            line = normalize_text(raw_s)
            # Function definition block: <Name>(args) を関数として定義する。
            m_func = re.match(r"^(?:関数 )?(?P<name>[^\s()]+)\((?P<params>[^)]*)\) を(?:関数として)?定義する。?$", line)
            if m_func:
                fname = m_func.group("name").strip()
                params_raw = m_func.group("params").strip()
                params = [p.strip() for p in params_raw.split(",") if p.strip()] if params_raw else []
                # Collect until '以上。'
                func_depth = 1
                loop_depth = 0
                if_depth = 0
                block_lines = []
                j = i + 1
                while j < len(lines):
                    candidate_raw = lines[j].rstrip()
                    candidate_norm = normalize_text(candidate_raw)
                    if re.match(r"^(?:関数 )?[^\s()]+\([^)]*\) を(?:関数として)?定義する。?$", candidate_norm):
                        func_depth += 1
                        block_lines.append(candidate_raw)
                        j += 1
                        continue
                    if self._re_loop_start.match(candidate_norm):
                        loop_depth += 1
                        block_lines.append(candidate_raw)
                        j += 1
                        continue
                    if self._re_if_zero.match(candidate_norm) or self._re_if_nonzero.match(candidate_norm):
                        if_depth += 1
                        block_lines.append(candidate_raw)
                        j += 1
                        continue
                    if self._re_else.match(candidate_norm):
                        if_depth += 1
                        block_lines.append(candidate_raw)
                        j += 1
                        continue
                    if self._re_loop_end.match(candidate_norm):
                        if loop_depth > 0:
                            loop_depth -= 1
                            block_lines.append(candidate_raw)
                            j += 1
                            continue
                        # else: this '以上。' may close the function
                    if re.match(r"^以上。?$", candidate_norm) and loop_depth == 0:
                        if if_depth > 0:
                            if_depth -= 1
                            block_lines.append(candidate_raw)
                            j += 1
                            continue
                        func_depth -= 1
                        if func_depth == 0:
                            break
                        else:
                            # Nested function end
                            block_lines.append(candidate_raw)
                            j += 1
                            continue
                    block_lines.append(candidate_raw)
                    j += 1
                if func_depth != 0:
                    raise SyntaxError(f"関数 {fname} の定義に対応する『以上。』が見つかりません (行 {lineno})")
                self.funcs[fname] = {"params": params, "body": block_lines}
                i = j + 1
                continue
            # Loop block handling
            m_loop = self._re_loop_start.match(line)
            if m_loop:
                count_expr = m_loop.group("count").strip()
                # Collect block lines until matching 以上。
                depth = 1
                block_lines = []
                j = i + 1
                while j < len(lines):
                    candidate_raw = lines[j].rstrip()
                    candidate_norm = normalize_text(candidate_raw)
                    if re.match(r"^(?:関数 )?[^\s()]+\([^)]*\) を(?:関数として)?定義する。?$", candidate_norm):
                        depth += 1
                    elif self._re_loop_start.match(candidate_norm):
                        depth += 1
                    elif self._re_if_zero.match(candidate_norm) or self._re_if_nonzero.match(candidate_norm):
                        depth += 1
                    elif self._re_else.match(candidate_norm):
                        depth += 1
                    elif self._re_loop_end.match(candidate_norm):
                        depth -= 1
                        if depth == 0:
                            break
                        else:
                            j += 1
                            continue
                    block_lines.append(candidate_raw)
                    j += 1
                if depth != 0:
                    raise SyntaxError(f"対応する『以上。』が見つかりません (行 {lineno})")
                # Execute block
                count_val = self._value_of(count_expr)
                if not isinstance(count_val, (int, float)):
                    raise TypeError(f"反復回数は数値である必要があります (行 {lineno})")
                count_int = int(count_val)
                if count_int < 0:
                    raise ValueError(f"反復回数は負にできません (行 {lineno})")
                block_src = "\n".join(block_lines)
                for _ in range(count_int):
                    self.exec(block_src)
                # Move index past the end marker
                i = j + 1
                continue
            # If block handling
            m_if0 = self._re_if_zero.match(line)
            m_ifnz = self._re_if_nonzero.match(line)
            if m_if0 or m_ifnz:
                cond_expr = (m_if0 or m_ifnz).group("expr").strip()
                # Collect THEN block until matching 以上。
                depth = 1
                then_lines: list[str] = []
                j = i + 1
                while j < len(lines):
                    candidate_raw = lines[j].rstrip()
                    candidate_norm = normalize_text(candidate_raw)
                    if re.match(r"^(?:関数 )?[^\s()]+\([^)]*\) を(?:関数として)?定義する。?$", candidate_norm):
                        depth += 1
                        then_lines.append(candidate_raw)
                        j += 1
                        continue
                    if self._re_loop_start.match(candidate_norm):
                        depth += 1
                        then_lines.append(candidate_raw)
                        j += 1
                        continue
                    if self._re_if_zero.match(candidate_norm) or self._re_if_nonzero.match(candidate_norm):
                        depth += 1
                        then_lines.append(candidate_raw)
                        j += 1
                        continue
                    if self._re_loop_end.match(candidate_norm):
                        depth -= 1
                        if depth == 0:
                            break
                        then_lines.append(candidate_raw)
                        j += 1
                        continue
                    then_lines.append(candidate_raw)
                    j += 1
                if depth != 0:
                    raise SyntaxError(f"対応する『以上。』が見つかりません (行 {lineno})")
                # Optionally collect ELSE block if present next
                k = j + 1
                # Skip blank lines
                while k < len(lines) and not normalize_text(lines[k].rstrip()):
                    k += 1
                else_lines: list[str] | None = None
                if k < len(lines):
                    next_norm = normalize_text(lines[k].rstrip())
                    if self._re_else.match(next_norm):
                        # Collect ELSE block similar to THEN
                        depth2 = 1
                        else_lines = []
                        k += 1
                        while k < len(lines):
                            cand_raw2 = lines[k].rstrip()
                            cand_norm2 = normalize_text(cand_raw2)
                            if re.match(r"^(?:関数 )?[^\s()]+\([^)]*\) を(?:関数として)?定義する。?$", cand_norm2):
                                depth2 += 1
                                else_lines.append(cand_raw2)
                                k += 1
                                continue
                            if self._re_loop_start.match(cand_norm2):
                                depth2 += 1
                                else_lines.append(cand_raw2)
                                k += 1
                                continue
                            if self._re_if_zero.match(cand_norm2) or self._re_if_nonzero.match(cand_norm2):
                                depth2 += 1
                                else_lines.append(cand_raw2)
                                k += 1
                                continue
                            if self._re_loop_end.match(cand_norm2):
                                depth2 -= 1
                                if depth2 == 0:
                                    break
                                else_lines.append(cand_raw2)
                                k += 1
                                continue
                            else_lines.append(cand_raw2)
                            k += 1
                        if depth2 != 0:
                            raise SyntaxError(f"対応する『以上。』が見つかりません (行 {lineno})")
                # Evaluate and execute
                cond_val = self._value_of(cond_expr)
                if not isinstance(cond_val, (int, float)):
                    raise TypeError(f"条件式は数値である必要があります (行 {lineno})")
                is_true = (cond_val == 0) if m_if0 else (cond_val != 0)
                exec_src = "\n".join(then_lines if is_true else (else_lines or []))
                if exec_src:
                    self.exec(exec_src)
                # Advance index past THEN (and ELSE if existed)
                i = (k if else_lines is not None else j) + 1
                continue
            # Normal single-line execution
            try:
                self.exec_line(raw_s)
                if is_toplevel:
                    self._toplevel_effect = True
            except KeiyakuInterpreter._ReturnSignal:
                # Propagate function return without wrapping
                raise
            except Exception as e:
                raise type(e)(f"{e} (行 {lineno}: {raw_s})") from e
            i += 1


def 主文(argv: list[str]) -> int:
    if len(argv) == 1:
        print("使い方: python keiyaku_lang.py <program.kei>")
        return 1
    path = argv[1]
    # Use utf-8-sig to gracefully handle BOM if present
    with open(path, "r", encoding="utf-8-sig") as f:
        src = f.read()
    interp = KeiyakuInterpreter()
    interp.exec(src, is_toplevel=True)
    # Auto-call 主文() if defined and no toplevel effects
    if "主文" in interp.funcs and not interp._toplevel_effect:
        _ = interp._call_function("主文", [])
    return 0


if __name__ == "__main__":
    raise SystemExit(主文(sys.argv))
