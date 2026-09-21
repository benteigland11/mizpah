"""The seats' prompts, composed from `prompts/`.

One file, one thing; a seat's prompt is the pieces in `order.txt`, each as the shared file (`name.md`, every
seat that runs the loop) followed by the seat's own (`name_<seat>.md`). The reviewer is a different kind of
seat and composes only from `order_reviewer.txt`, an explicit list. A piece not yet finished lives in `wip/`
and is taken from there with a note, so the loop runs while the pieces are written.
"""
from __future__ import annotations

from pathlib import Path

SEATS = ('worker', 'controller', 'reviewer')


def _read(folder: Path, name: str) -> tuple[str | None, bool]:
    """The piece's text and whether it came from wip/."""
    done = folder/(name+'.md')
    if done.exists():
        return done.read_text(), False
    wip = folder/'wip'/(name+'.md')
    if wip.exists():
        return wip.read_text(), True
    return None, False


def compose(seat: str, folder: str | Path) -> str:
    if seat not in SEATS:
        raise ValueError('seat must be one of '+', '.join(SEATS))
    folder = Path(folder)
    if seat == 'reviewer':
        order_file, names = folder/'order_reviewer.txt', None
    else:
        order_file = folder/'order.txt'
    if not order_file.exists():
        raise FileNotFoundError(f'{order_file} names the pieces of the {seat} prompt; it does not exist')
    names = [line.strip() for line in order_file.read_text().splitlines() if line.strip() and not line.startswith('#')]
    pieces: list[str] = []
    for name in names:
        candidates = [name] if seat == 'reviewer' else [name, name+'_'+seat]
        for candidate in candidates:
            text, from_wip = _read(folder, candidate)
            if text is None:
                continue
            pieces.append(text.strip()+'\n')
    if not pieces:
        raise ValueError(f'the {seat} prompt composed to nothing from {order_file}')
    return '\n'.join(pieces)


def pieces_of(seat: str, folder: str | Path) -> list[tuple[str, bool]]:
    """What compose() would take, in order, with whether each came from wip/ — for the app and for a check."""
    folder = Path(folder)
    order_file = folder/('order_reviewer.txt' if seat == 'reviewer' else 'order.txt')
    names = [line.strip() for line in order_file.read_text().splitlines() if line.strip() and not line.startswith('#')]
    out: list[tuple[str, bool]] = []
    for name in names:
        for candidate in ([name] if seat == 'reviewer' else [name, name+'_'+seat]):
            text, from_wip = _read(folder, candidate)
            if text is not None:
                out.append((candidate, from_wip))
    return out
