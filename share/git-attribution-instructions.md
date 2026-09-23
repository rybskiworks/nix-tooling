## Git attribution

Use only the workload's reviewed human co-authors for commits and pull requests.
Keep `attribution.strict = true`; additional human collaborators need explicit
approval in `attribution.allowedCoAuthor`. Match their approved display names and
emails. An explicit `--co-author` selects credits but cannot grant that approval.
Never add agent, model or tool co-authors, generation claims or orchestration
metadata. Preserve the primary author, committer, credentials and signing
configuration. Review the complete artifact as well as its trailer block.

For an existing checkout, install the shared hooks with `git-attribution install`.
If another hook manager owns `core.hooksPath`, preserve it and follow that
manager's composition instructions. Do not disable existing validation hooks.

Write a PR description to a file, then run `git-attribution format --file FILE`
and `git-attribution check --file FILE` before passing it to `gh pr create` or
`gh pr edit` with `--body-file`. Keep the co-author trailer block at the end.

Before an authorized squash merge, format the exact final body with
`git-attribution format --file FILE --from-commits BASE_SHA..HEAD_SHA`, check it,
and supply that file explicitly to the merge command with the reviewed head SHA.
Unknown imported credits must be reviewed before proceeding; do not disable
strict admission, silently discard valid human credit or approve automation.
Local commit hooks do not run on GitHub's squash merge. Verify the resulting
commit message and signature after publication.

Attribution does not authorize publication or supply signing credentials. Follow
the repository's required checks, review and host signing workflow.
