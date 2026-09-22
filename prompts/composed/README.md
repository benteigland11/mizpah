# composed

Each seat's prompt as the engine composes it from the pieces, written out whole so it can be read the way the
seat reads it. Generated — do not edit here; edit the pieces. Regenerate with:

    .venv/bin/python -c "import sys; sys.path.insert(0,'engine/mizpah/src'); from mizpah import prompts; \
    [open(f'prompts/composed/{s}.md','w').write(prompts.compose(s,'prompts')) for s in ('worker','controller','reviewer')]"
