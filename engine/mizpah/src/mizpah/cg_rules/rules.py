"""Mizpah's rules for Python widgets, run by `cartograph validate` (and so at every checkin).

A widget is an instrument: it takes the thing it acts on as input and returns it changed. The
material — a chord list, a melody, a velocity envelope written out note by note — is the
project's, not the library's. A widget that carries it is a finding wearing an instrument's name:
the next project that installs it ships this project's piece (attempt 4 of the piano benchmark
rendered a Block B gym's nocturne, note for note, through `write_piece("piece.mid")`).

Blocks: a literal list or tuple in `src/` of at least MANY numbers, or of at least SOME tuples of
numbers (chords), outside a test. Warns: a public function that writes a file and takes no input
but a path.
"""
import ast, json, os, sys

MANY = 8    # numbers in one literal: a melody, an envelope, a bar of durations
SOME = 4    # tuples of numbers in one literal: a chord progression


def numbers(node) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return numbers(node.operand)
    return 0


def is_numeric_tuple(node) -> bool:
    return isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) >= 2 and all(numbers(e) for e in node.elts)


def material(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        flat = sum(numbers(e) for e in node.elts)
        if flat >= MANY and flat == len(node.elts):
            yield node.lineno, f'{flat} numbers written out'
        chords = sum(is_numeric_tuple(e) for e in node.elts)
        if chords >= SOME and chords == len(node.elts):
            yield node.lineno, f'{chords} tuples of numbers written out'


def writes_without_input(func: ast.FunctionDef) -> bool:
    args = [a.arg for a in func.args.args + func.args.kwonlyargs if a.arg not in ('self', 'cls')]
    takes_only_paths = all(any(word in a.lower() for word in ('path', 'out', 'dest', 'file', 'ticks')) for a in args)
    saves = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('save', 'write', 'write_bytes', 'write_text')
                for n in ast.walk(func))
    return takes_only_paths and saves and not func.name.startswith('_')


def main(widget: str) -> dict:
    blocks, warnings = [], []
    src = os.path.join(widget, 'src')
    for base, _, files in os.walk(src):
        for name in files:
            if not name.endswith('.py'):
                continue
            path = os.path.join(base, name)
            try:
                tree = ast.parse(open(path, encoding='utf-8').read())
            except SyntaxError:
                continue
            rel = os.path.relpath(path, widget)
            for line, what in material(tree):
                blocks.append(f'{rel}:{line}: {what} — material baked into the instrument. A widget takes the thing it '
                              'acts on (the notes, the chords, the document) as an argument; the material stays in the '
                              'project. Move it to the caller and pass it in.')
            for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                if writes_without_input(func):
                    warnings.append(f'{rel}:{func.lineno}: `{func.name}` writes a file and takes nothing but a path — '
                                    'where does what it writes come from? Take it as input.')
    return {'blocks': blocks, 'warnings': warnings}


if __name__ == '__main__':
    print(json.dumps(main(sys.argv[1])))
