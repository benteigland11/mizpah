"""Mizpah's rules for Python widgets, run by `cartograph validate` (and so at every check-in).

A widget is the general logic: it takes the thing it acts on as input and returns it changed. Whatever is
specific to this project — the notes of this piece, this file's name, this run's tempo — is glue: a short
script in the project that calls the widget with that material. A widget that carries the material is this
project's artifact wearing an instrument's name; the next project that installs it ships this project's piece
(attempt 4 of the piano benchmark rendered a Block B gym's nocturne note for note through `write_piece()`),
and the validator said "valid" because nothing checked.

Blocks:
  - a literal list or tuple in src/ of MANY or more numbers, or SOME or more tuples of numbers (a melody, a
    chord list, a velocity envelope written out);
  - a public function in src/ that writes a file and takes no input but paths and sizes (it can only be
    writing material it carries).
Warns:
  - a default argument that is a project file name (`path="piece.mid"`): the caller names the file.
"""
import ast, json, os, re, sys

MANY = 8    # numbers in one literal: a melody, an envelope, a bar of durations
SOME = 4    # tuples of numbers in one literal: a chord progression
PROJECT_FILE = re.compile(r'^[\w.-]+\.(mid|midi|md|svg|png|jpg|wav|mp3|pdf|csv|json|txt|ly)$', re.I)
PATH_WORDS = ('path', 'out', 'dest', 'file', 'dir', 'ticks', 'name', 'seed', 'width', 'height', 'size', 'dpi')


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
    only_paths = all(any(word in a.lower() for word in PATH_WORDS) for a in args)
    saves = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ('save', 'write', 'write_bytes', 'write_text', 'savefig', 'write_png', 'saveas')
                for n in ast.walk(func))
    return only_paths and saves and not func.name.startswith('_')


def project_file_defaults(func: ast.FunctionDef):
    defaults = list(func.args.defaults) + list(func.args.kw_defaults)
    for d in defaults:
        if isinstance(d, ast.Constant) and isinstance(d.value, str) and PROJECT_FILE.match(d.value):
            yield d.value


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
                blocks.append(f'{rel}:{line}: {what} — material baked into the instrument. A widget is the general logic: it '
                              'takes the notes, the chords, the strokes, the document as an argument; what is specific to this '
                              'project is glue, a short script in the project that calls the widget with it. Move the values '
                              'to the caller and take them as a parameter.')
            for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                if writes_without_input(func):
                    blocks.append(f'{rel}:{func.lineno}: `{func.name}` writes a file and takes nothing but paths and sizes, so what '
                                  'it writes is material it carries. Take the material as input (the notes, the plan, the '
                                  'strokes) and let the caller supply it.')
                for value in project_file_defaults(func):
                    warnings.append(f'{rel}:{func.lineno}: `{func.name}` defaults to the project file "{value}"; the caller '
                                    'names the file, the widget takes a path.')
    return {'blocks': blocks, 'warnings': warnings}


if __name__ == '__main__':
    print(json.dumps(main(sys.argv[1])))
