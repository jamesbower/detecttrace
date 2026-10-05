"""A small element tree from the standard-library HTML parser, for structure tests."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | None]
    children: list["Node | str"] = field(default_factory=list)

    def iter(self) -> Iterator["Node"]:
        """This node and every element below it, in document order."""
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def find_all(self, predicate: Callable[["Node"], bool]) -> list["Node"]:
        return [node for node in self.iter() if predicate(node)]

    def find(self, predicate: Callable[["Node"], bool]) -> "Node":
        """The first match; raises LookupError when there is none."""
        for node in self.iter():
            if predicate(node):
                return node
        raise LookupError("no matching element")

    def text(self) -> str:
        return "".join(child if isinstance(child, str) else child.text() for child in self.children)

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()


def parse_html(text: str) -> Node:
    parser = _TreeBuilder()
    parser.feed(text)
    parser.close()
    return parser.root


def has_tag(tag: str, **attrs: str) -> Callable[[Node], bool]:
    return lambda node: node.tag == tag and all(node.attrs.get(k) == v for k, v in attrs.items())


def read_terms(definitions: Node) -> dict[str, str]:
    """A <dl>'s terms and their descriptions, as text, for a list of dt/dd pairs."""
    cells = [child for child in definitions.children if isinstance(child, Node)]
    return {term.text(): value.text() for term, value in zip(cells[::2], cells[1::2], strict=True)}


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, dict(attrs))
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(Node(tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        # Pop to the matching open element; a stray end tag is ignored as a browser would.
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)
