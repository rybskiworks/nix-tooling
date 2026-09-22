## Git attribution

Use the workload's configured human co-authors for commits and pull requests.
Preserve the primary author, committer, credentials and signing configuration.
Do not add an agent or model as a co-author unless the operator explicitly asks.

For an existing checkout, install the shared hooks with `git-attribution install`.
If another hook manager owns `core.hooksPath`, preserve it and follow that
manager's composition instructions. Do not disable existing validation hooks.

Write a PR description to a file, then run `git-attribution format --file FILE`
and `git-attribution check --file FILE` before passing it to `gh pr create` or
`gh pr edit` with `--body-file`. Keep the co-author trailer block at the end.

Before an authorized squash merge, format the exact final body with
`git-attribution format --file FILE --from-commits BASE_SHA..HEAD_SHA`, check it,
and supply that file explicitly to the merge command with the reviewed head SHA.
Local commit hooks do not run on GitHub's squash merge. Verify the resulting
commit message and signature after publication.

Attribution does not authorize publication or supply signing credentials. Follow
the repository's required checks, review and host signing workflow.
