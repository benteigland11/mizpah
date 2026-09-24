"""The seats' prompts, composed from `prompts/`.

One file, one thing; a seat's prompt is the pieces in `order.txt`, each as the shared file (`name.md`, every
seat that runs the loop) followed by the seat's own (`name_<seat>.md`). The reviewer is a different kind of
seat and composes only from `order_reviewer.txt`, an explicit list. The deputy is kept apart in its own folder,
`deputy/`, whose `order.txt` is its list; a name there is looked up in that folder first, then in the shared one
(the glossary). A piece not yet finished lives in `wip/` and is taken from there with a note, so the loop runs
while the pieces are written.
"""
from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

SEATS = ('worker', 'controller', 'reviewer', 'deputy')
LISTED = ('reviewer', 'deputy')   # seats off the loop: an explicit list, no shared-then-own pairing


def _read(folders: list[Path], name: str) -> tuple[str | None, bool]:
    """The piece's text and whether it came from wip/; the first folder that has it wins."""
    for folder in folders:
        done = folder/(name+'.md')
        if done.exists():
            return done.read_text(), False
        wip = folder/'wip'/(name+'.md')
        if wip.exists():
            return wip.read_text(), True
    return None, False


def _plan(seat: str, folder: Path) -> tuple[Path, list[Path]]:
    """The seat's order file and the folders its names are looked up in, its own first."""
    if seat in LISTED and (folder/seat).is_dir():
        return folder/seat/'order.txt', [folder/seat, folder]
    return folder/(f'order_{seat}.txt' if seat in LISTED else 'order.txt'), [folder]


def compose(seat: str, folder: str | Path) -> str:
    if seat not in SEATS:
        raise ValueError('seat must be one of '+', '.join(SEATS))
    listed = seat in LISTED
    order_file, folders = _plan(seat, Path(folder))
    if not order_file.exists():
        raise FileNotFoundError(f'{order_file} names the pieces of the {seat} prompt; it does not exist')
    names = [line.strip() for line in order_file.read_text().splitlines() if line.strip() and not line.startswith('#')]
    pieces: list[str] = []
    for name in names:
        for candidate in ([name] if listed else [name, name+'_'+seat]):
            text, from_wip = _read(folders, candidate)
            if text is None:
                continue
            pieces.append(text.strip()+'\n')
    if not pieces:
        raise ValueError(f'the {seat} prompt composed to nothing from {order_file}')
    return '\n'.join(pieces)


def pieces_of(seat: str, folder: str | Path) -> list[tuple[str, bool]]:
    """What compose() would take, in order, with whether each came from wip/ — for the app and for a check."""
    listed = seat in LISTED
    order_file, folders = _plan(seat, Path(folder))
    names = [line.strip() for line in order_file.read_text().splitlines() if line.strip() and not line.startswith('#')]
    out: list[tuple[str, bool]] = []
    for name in names:
        for candidate in ([name] if listed else [name, name+'_'+seat]):
            text, from_wip = _read(folders, candidate)
            if text is not None:
                out.append((candidate, from_wip))
    return out


_MESSAGES_DIR: list[Path | None] = [None]


def set_messages_dir(folder: str | Path) -> None:
    _MESSAGES_DIR[0] = Path(folder)/'messages'


def message(name: str, **fields: Any) -> str:
    """A boundary message by name from prompts/messages/, its `$field`s filled. The folder is set at config load."""
    folder = _MESSAGES_DIR[0]
    if folder is None:
        raise RuntimeError('prompts.set_messages_dir was not called (load_config sets it)')
    text = (folder/(name+'.md')).read_text()
    return Template(text).safe_substitute({k: str(v) for k, v in fields.items()})
