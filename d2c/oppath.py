"""Punktpfade in serialisierten Vorgängen, z. B. "profile.w" oder "positions.1.0"."""

from typing import Any


def _index(key: str) -> int:
    """Ein Listenindex in einem punktgetrennten Pfad: eine nicht negative ganze Zahl."""
    if not key.isdecimal():
        raise KeyError(key)
    return int(key)


def get_path(d: dict, path: str) -> Any:
    """Liest den Wert an einem punktgetrennten Pfad in einem serialisierten Vorgang, z. B. "profile.w" oder "positions.1.0"."""
    node: Any = d
    try:
        for key in path.split("."):
            node = node[_index(key)] if isinstance(node, (list, tuple)) else node[key]
    except (KeyError, IndexError, TypeError):
        raise KeyError(path) from None
    return node


def set_path(d: dict, path: str, value: float) -> None:
    """Schreibt einen Wert an einen vorhandenen punktgetrennten Pfad im serialisierten Vorgang. Tupel auf dem Pfad werden neu aufgebaut."""

    def put(node: Any, keys: list[str]) -> Any:
        key = _index(keys[0]) if isinstance(node, (list, tuple)) else keys[0]
        old = node[key]  # Löst aus, wenn der Pfad nicht existiert
        child = value if len(keys) == 1 else put(old, keys[1:])
        if isinstance(node, tuple):
            return node[:key] + (child,) + node[key + 1 :]
        node[key] = child
        return node

    try:
        put(d, path.split("."))
    except (KeyError, IndexError, TypeError):
        raise KeyError(path) from None
