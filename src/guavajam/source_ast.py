"""Pure helpers for the parsed GFM source contract."""
def walk(value):
    if isinstance(value, dict):
        if isinstance(value.get("t"), str):
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def plain_text(value) -> str:
    bits: list[str] = []
    def visit(node):
        if isinstance(node, dict):
            token = node.get("t")
            if token in ("Str", "MetaString"):
                bits.append(node["c"])
            elif token in ("Space", "SoftBreak", "LineBreak"):
                bits.append(" ")
            elif token == "Code":
                bits.append(node["c"][1])
            elif token == "Math":
                bits.append(node["c"][1])
            else:
                visit(node.get("c", []))
        elif isinstance(node, list):
            for sub in node:
                visit(sub)
    visit(value)
    return "".join(bits).strip()

