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

## Agent skills

`skills/` is a copy of [space-skills](https://github.com/Hugo-SEQUIER/space-skills), and `skills/.source` records the commit it came from. Edit the skills in space-skills, then run `scripts/sync-skills.sh` here and commit the result. Never edit `skills/` directly: `scripts/sync-skills.sh --check` fails on any difference.

## Hermes review loop

Every pull request gets an automatic **Hermes review** comment about 10 minutes after each push: code review items `C1, C2...`, simplify items `S1...` (AI slop to cut, never blocking) and a security verdict. Before you call a pull request done:

1. Wait for the review of your current head: the `head` in the comment's hidden `hermes-pr-review-data` JSON block must equal `gh pr view <n> --json headRefOid`. The read command is in [review-loop.md](https://github.com/Hugo-SEQUIER/ci-workflows/blob/main/docs/review-loop.md).
2. Fix every item with `blocking: true`. Apply each simplify item you agree with, and decline the others with a concrete reason.
3. Run the checks, push one commit `refactor: apply Hermes review (round <n>)`, then post one comment starting with `<!-- hermes-pr-fix head=<new sha> reviewed=<reviewed sha> -->` that lists `Applied:` and `Declined:` IDs.

At most 2 fix rounds per pull request. Never merge, approve or enable auto-merge: Hugo merges.
