#!/usr/bin/env python3
"""Generate an ActionScript 1 API skeleton from Clang's filtered AST JSON.

Clang's -ast-dump-filter output is a sequence of JSON objects, not a single
JSON document. The generated AS1 class uses prototypes; method bodies are
stubs because an AST declaration does not contain a JavaScript/AS1 binding.

Usage: python3 ast_to_as1.py [AST.json] [-o ../bullet_as1/orig/a/a.as]
"""
import argparse
import json
import re
import sys


def read_json_stream(path):
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos == len(text):
            break
        try:
            value, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at line {text.count(chr(10), 0, exc.pos) + 1}: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError("expected each AST record to be a JSON object")
        yield value


def as1_name(name):
    # C++ operators and destructor names are not callable AS1 identifiers.
    name = re.sub(r"[^A-Za-z0-9_$]", "_", name)
    if not name or name[0].isdigit():
        name = "_" + name
    return name


def type_default(qual_type):
    t = qual_type.replace("const ", "").strip()
    if "*" in t or "&" in t:
        return "null"
    if t == "void":
        return ""
    if t == "bool":
        return "false"
    if t in {"char", "signed char", "unsigned char", "short", "unsigned short",
             "int", "unsigned int", "long", "unsigned long", "long long",
             "unsigned long long", "float", "double", "btScalar", "size_t"}:
        return "0"
    return "null"


def params(node):
    result = []
    for child in node.get("inner", []):
        if child.get("kind") == "ParmVarDecl":
            result.append((as1_name(child.get("name") or f"arg{len(result)}"),
                           child.get("type", {}).get("qualType", "")))
    return result



def unwrap(node):
    transparent = {"ImplicitCastExpr", "ExprWithCleanups", "MaterializeTemporaryExpr",
                   "CXXBindTemporaryExpr", "ParenExpr", "ConstantExpr"}
    while node and node.get("kind") in transparent and node.get("inner"):
        node = node["inner"][0]
    return node


def expression(node):
    """Translate the expression subset used by btBoxShape's constructor."""
    if not node:
        return "/* missing expression */"
    kind = node.get("kind")
    if kind in {"ImplicitCastExpr", "ExprWithCleanups", "MaterializeTemporaryExpr",
                "CXXBindTemporaryExpr", "ParenExpr", "ConstantExpr", "CXXFunctionalCastExpr",
                "CStyleCastExpr"}:
        inner = node.get("inner", [])
        return expression(inner[0]) if inner else "undefined"
    if kind == "CXXThisExpr":
        return "this"
    if kind == "MemberExpr":
        receiver = (node.get("inner") or [{}])[0]
        receiver = expression(receiver)
        if receiver == "this":
            return "this." + as1_name(node.get("name", "member"))
        return receiver + "." + as1_name(node.get("name", "member"))
    if kind == "DeclRefExpr":
        return as1_name(node.get("referencedDecl", {}).get("name", "unknown"))
    if kind in {"IntegerLiteral", "FloatingLiteral", "CharacterLiteral", "CXXBoolLiteralExpr"}:
        return str(node.get("value", "0")).lower()
    if kind == "CXXDefaultArgExpr":
        return expression((node.get("inner") or [{}])[0])
    if kind == "CXXConstructExpr":
        t = node.get("type", {}).get("qualType", "Object").replace("const ", "").strip()
        args = node.get("inner", [])
        # A same-type single-argument construction is a C++ copy. In the
        # generated script, keep the returned object expression directly.
        if len(args) == 1:
            arg_type = args[0].get("type", {}).get("qualType", "")
            normalized_arg_type = arg_type.replace("const ", "").replace("&", "").strip()
            if normalized_arg_type == t:
                return expression(args[0])
        return "new " + as1_name(t) + "(" + ", ".join(expression(x) for x in args) + ")"
    if kind == "CXXMemberCallExpr":
        children = node.get("inner", [])
        callee = unwrap(children[0]) if children else {}
        if callee.get("kind") == "MemberExpr":
            name = expression(callee)
        else:
            name = as1_name(callee.get("name", "call"))
        args = ", ".join(expression(x) for x in children[1:])
        return name + "(" + args + ")"
    if kind == "CXXOperatorCallExpr":
        children = node.get("inner", [])
        callee = children[0] if children else {}
        while callee.get("inner"):
            callee = callee["inner"][0]
        op_name = callee.get("referencedDecl", {}).get("name", "")
        args = [expression(x) for x in children[1:]]
        if op_name == "operator=" and len(args) >= 2:
            return args[0] + " = " + args[1]
        helpers = {"operator*": "btVector3_mul", "operator-": "btVector3_sub"}
        if op_name in helpers and len(args) >= 2:
            return helpers[op_name] + "(" + args[0] + ", " + args[1] + ")"
        return "/* unsupported " + op_name + " */ " + ", ".join(args)
    if kind == "BinaryOperator":
        children = node.get("inner", [])
        if len(children) >= 2:
            return "(" + expression(children[0]) + " " + node.get("opcode", "=") + " " + expression(children[1]) + ")"
    return "/* unsupported " + kind + " */"


def statement(node, indent="\t"):
    kind = node.get("kind")
    if kind == "CompoundStmt":
        return [line for child in node.get("inner", []) for line in statement(child, indent)]
    if kind in {"BinaryOperator", "CXXOperatorCallExpr"}:
        return [indent + expression(node) + ";"]
    if kind == "DeclStmt":
        lines = []
        for decl in node.get("inner", []):
            if decl.get("kind") == "VarDecl":
                init = decl.get("inner", [])
                rhs = expression(init[0]) if init else "undefined"
                lines.append(indent + "var " + as1_name(decl.get("name", "value")) + " = " + rhs + ";")
        return lines
    if kind == "CXXMemberCallExpr":
        return [indent + expression(node) + ";"]
    if kind in {"ExprWithCleanups", "CXXBindTemporaryExpr", "MaterializeTemporaryExpr"} and node.get("inner"):
        return statement(node["inner"][0], indent)
    return [indent + "// TODO translate " + kind]

def generate_record(record, out_of_line_defs=(), include_constructor=True):
    class_name = record["name"]
    access = "private" if record.get("tagUsed") == "class" else "public"
    ctor = None
    methods = []
    seen = set()
    for child in record.get("inner", []):
        kind = child.get("kind")
        if kind == "AccessSpecDecl":
            access = child.get("access", access)
            continue
        if access != "public" or child.get("isImplicit"):
            continue
        name = child.get("name", "")
        if kind == "CXXConstructorDecl" and name == class_name:
            if ctor is None:
                ctor = params(child)
        elif kind == "CXXMethodDecl" and name and not name.startswith("operator"):
            signature = child.get("type", {}).get("qualType", "")
            key = (name, signature)
            if key not in seen:
                seen.add(key)
                definition = next((d for d in out_of_line_defs
                                   if d.get("kind") == "CXXMethodDecl"
                                   and d.get("mangledName") == child.get("mangledName")
                                   and any(c.get("kind") == "CompoundStmt" for c in d.get("inner", []))), None)
                methods.append((name, params(child), signature, definition))

    ctor_def = next((d for d in out_of_line_defs
                     if d.get("kind") == "CXXConstructorDecl" and d.get("name") == class_name
                     and d.get("parentDeclContextId") == record.get("id")), None)
    base_calls = []
    body = []
    if ctor_def:
        for item in ctor_def.get("inner", []):
            if item.get("kind") == "CXXCtorInitializer" and item.get("baseInit"):
                base = item["baseInit"].get("qualType", "")
                base_calls.append(as1_name(base) + ".call(this);")
            elif item.get("kind") == "CompoundStmt":
                body = statement(item)
    out = ["// Generated from Clang AST. Translated method bodies are emitted where supported; remaining methods use stubs."]
    if include_constructor:
        out.append(f"function {class_name}({', '.join(p[0] for p in (ctor or []))}) {{")
        out.extend("\t" + call for call in base_calls)
        out.extend(body)
        out.extend(["}", ""])
    used = {}
    for name, args, signature, definition in methods:
        safe = as1_name(name)
        used[safe] = used.get(safe, 0) + 1
        if used[safe] > 1:
            safe += "_" + str(used[safe])
        return_type = signature.split("(", 1)[0].strip()
        out.append(f"{class_name}.prototype.{safe} = function({', '.join(p[0] for p in args)}) {{")
        method_body = next((c for c in definition.get("inner", []) if c.get("kind") == "CompoundStmt"), None) if definition else None
        if method_body is not None:
            out.extend(statement(method_body))
        else:
            default = type_default(return_type)
            if default:
                out.append(f"\treturn {default};")
        out.extend(["};", ""])
    return "\n".join(out).rstrip() + "\n"


def walk_nodes(node):
    if isinstance(node, dict):
        yield node
        for child in node.get("inner", []):
            yield from walk_nodes(child)


def project_classes(roots, requested_class=None):
    """Walk Clang's nested TranslationUnitDecl and keep definitions in bullet/."""
    found = []

    def walk(node, current_file=""):
        if not isinstance(node, dict):
            return
        loc = node.get("loc", {})
        file_name = loc.get("file") or node.get("range", {}).get("begin", {}).get("file") or current_file
        if (node.get("kind") == "CXXRecordDecl" and node.get("completeDefinition")
                and node.get("name") and file_name.startswith(("bullet/", "src/", "bullet3/src/"))
                and (requested_class is None or node.get("name") == requested_class)):
            found.append(node)
        for child in node.get("inner", []):
            file_name = walk(child, file_name)
        return file_name

    current_file = ""
    for root in roots:
        current_file = walk(root, current_file)
    unique = {}
    for record in found:
        unique.setdefault(record["name"], record)
    return list(unique.values())


def owner_from_mangled_name(mangled):
    """Return the immediate class name from a simple Itanium nested name."""
    if not mangled or not mangled.startswith("_ZN"):
        return None
    pos = 3
    # Skip cv/ref qualifiers that may follow the nested-name marker.
    while pos < len(mangled) and mangled[pos] in "KVRrO":
        pos += 1
    components = []
    while pos < len(mangled) and mangled[pos] != "E":
        start = pos
        while pos < len(mangled) and mangled[pos].isdigit():
            pos += 1
        if start == pos:
            return None
        size = int(mangled[start:pos])
        component = mangled[pos:pos + size]
        if len(component) != size:
            return None
        components.append(component)
        pos += size
    return components[-2] if len(components) >= 2 else None


def stub_classes_from_methods(roots, requested_class=None):
    """Build minimal class records around filtered method declarations."""
    stubs = {}
    for root in roots:
        for node in walk_nodes(root):
            if node.get("kind") != "CXXMethodDecl":
                continue
            owner = owner_from_mangled_name(node.get("mangledName", ""))
            if not owner or (requested_class and owner != requested_class):
                continue
            # A synthetic public record lets generate_record reuse its regular
            # method-signature and body translation path.
            record = stubs.setdefault(owner, {
                "kind": "CXXRecordDecl", "name": owner, "tagUsed": "class",
                "completeDefinition": True,
                "inner": [{"kind": "AccessSpecDecl", "access": "public"}],
            })
            record["inner"].append(node)
    return list(stubs.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="../a.json", help="AST JSON (default: ../a.json; use - for stdin)")
    parser.add_argument("-o", "--output", default="../bullet_as1/orig/a/a.as", help="output file (default: ../bullet_as1/orig/a/a.as; use - for stdout)")
    parser.add_argument("--class", dest="class_name", help="emit only this class")
    parser.add_argument("--stub-class", type=int, choices=(0, 1, 2), default=1,
                        help="no complete class: 0=error, 1=write nothing (default), 2=synthesize a stub class")
    args = parser.parse_args()
    try:
        records = list(read_json_stream(args.input))
        classes = project_classes(records, args.class_name)
        include_constructor = True
        if not classes:
            if args.stub_class in (1, 2):
                classes = stub_classes_from_methods(records, args.class_name)
                if not classes:
                    if args.stub_class == 1:
                        result = ""
                    else:
                        raise ValueError("no complete class or method suitable for a stub class found in the AST")
                elif args.stub_class == 1:
                    include_constructor = False
            else:
                raise ValueError("no matching complete project class definitions found in the AST")
        if classes:
            definitions = [r for root in records for r in walk_nodes(root)
                           if r.get("kind") in {"CXXConstructorDecl", "CXXMethodDecl"}]
            result = "\n".join(generate_record(record, definitions, include_constructor) for record in classes)
            if any("btVector3_mul(" in result or "btVector3_sub(" in result for _ in [0]):
                result += "\nfunction btVector3_mul(a, b) {\n\treturn new btVector3(a.x() * b.x(), a.y() * b.y(), a.z() * b.z());\n}\n"
                result += "function btVector3_sub(a, b) {\n\treturn new btVector3(a.x() - b.x(), a.y() - b.y(), a.z() - b.z());\n}\n"
        if args.output == "-":
            sys.stdout.write(result)
        else:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
