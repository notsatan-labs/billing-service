---
name: git-commit
description: Create and/or edit Git commits. Use when asked to prepare a commit message, create a commit, or run "/commit".
---

# Git Commit

Create one coherent, verified commit without disturbing unrelated work. For a
message-only request, analyze the changes and validate the message, but do not
stage or commit anything.

## Workflow

### 1. Review the Changes

Read the repository instructions. Inspect status, staged and unstaged diffs,
untracked files, and recent commits. Determine the single concern the commit
should contain.

If the boundary is unclear, unrelated changes are already staged, or multiple
commits are appropriate, ask before changing the index. One request to create a
commit authorizes one commit.

### 2. Verify and Stage

Run relevant tests or checks and report failures or skipped verification. Stage
only the intended files or hunks, never likely secrets or credentials. Review
the complete staged diff before writing the message.

### 3. Write the Message

Use this format; the scope and `!` are optional:

```text
<Type>(<Scope>)!: <Title Case Subject>

[optional body]
```

Choose the type that best represents the change:

| Type       | Purpose                        |
| ---------- | ------------------------------ |
| `Feat`     | New feature                    |
| `Fix`      | Bug fix                        |
| `Docs`     | Documentation only             |
| `Style`    | Formatting/style (no logic)    |
| `Refactor` | Code refactor (no feature/fix) |
| `Perf`     | Performance improvement        |
| `Test`     | Add/update tests               |
| `Build`    | Build system/dependencies      |
| `CI`       | CI/config changes              |
| `Chore`    | Maintenance/misc               |
| `Revert`   | Revert commit                  |

Apply these rules:

- Follow Conventional Commits. Title-case the type, scope, and subject's content
  words. Preserve the established casing of identifiers and acronyms.
- Keep articles, conjunctions, and prepositions of three letters or fewer
  lowercase. Also keep `they`, `with`, `from`, `into`, and `over` lowercase.
  This length rule does not apply to content words, identifiers, or acronyms.
- Use the present-tense imperative mood.
- Limit the entire title and every non-empty authored body or footer line to 68
  characters, including indentation and bullet markers. Wrap close to the limit
  without padding or reducing clarity.
- The body is optional. When present, separate it from the title with exactly one
  blank line. Explain what changed and why without filler or title repetition.
- The body may include code snippets when necessary. Their non-empty lines,
  including fences, must remain within 68 characters without changing meaning.
- Mark breaking changes with `!`, and a `BREAKING CHANGE:` footer.
- Do not add `Author`, `Authored-by`, `Co-author`, `Co-authored-by`, or the
  `Signed-off-by` trailers; Git supplies them.

### 4. Create and Validate the Message

Create the file in the platform-selected temporary directory:

```sh
python3 .agents/git-commit/scripts/validate_commit_message.py --create
```

Record the returned absolute path and write the complete message to that file
using the environment's safe file-editing mechanism. Review the wording and its
accuracy against the diff, then validate it:

```sh
python3 .agents/git-commit/scripts/validate_commit_message.py \
  "<message-file>"
```

For a repository-defined custom "type", add `--allow-type <Type>`. Revalidate
the file until it passes.

Run these commands from the repository root.

### 5. Commit

Recheck the staged diff, then use the validated file directly:

```sh
git commit --cleanup=verbatim --signoff --file "<message-file>"
```

Do not use `-m` or write the signoff manually. If a hook rejects the commit or
changes files, inspect the result, run affected checks, review the staged diff,
and revalidate the message before retrying.

### 6. Verify and Report

Inspect the commit and confirm its hash, files, authored message, and exactly one
`Signed-off-by` trailer. Report the commit, verification performed, and any
skipped checks. Delete the temporary message file after successful commit
verification; retain it for a message-only request.

Never amend, change Git configuration, bypass hooks, rewrite history, or push
unless explicitly requested. If repository rules conflict with this skill, ask
before committing.
