# Repository instructions

## Markdown

- Do not hard-wrap `.md` prose at 80 columns or any other fixed width.
- Keep each prose paragraph on one source line, including paragraphs inside list items and blockquotes. Use editor soft wrapping for readability.
- Preserve blank lines between paragraphs, list nesting, explicit Markdown hard breaks, code blocks, tables, front matter, and other content whose line breaks carry meaning.
- Apply this convention when writing new Markdown and editing existing prose.

## Concurrent agents

Several agents and the operator can work in this checkout at the same time, so the tree may change between two steps of one task.

- Re-check `git status` before staging or committing instead of assuming the tree matches what you last saw.
- Stage explicit paths. Do not run `git add -A` or `git add .` from the repository root.
- Commit only files you authored in this task, and confirm afterwards with `git show --stat`.
- Never revert, reformat, or stage another agent's in-progress work. Report it instead.
- When one file holds both your change and someone else's, ask before rewriting it.
