# GitHub governance and staged activation

These are proposals, not live settings. The cleanup changes repository files only.
The common cross-repository threat model and activation plan is recorded in the
[companion Workestrate audit](https://github.com/rybskiworks/workestrate/blob/efc92ecc6572c232a8eaab7b3b92ab99632a57cc/docs/github-governance.md)
at an immutable documentation revision. That reference is not a build dependency.

## Supplier-specific contract

Main is the integration branch. Keep toolchain ownership in nix-tooling and promote
reviewed immutable revisions to consumers in separate tested PRs. Do not turn the
supplier into a self-referential consumer or advance Workestrate to this unmerged PR.

Readable repository/inherited ruleset enumeration returned an empty array during
the audit. Detailed administration settings were not comprehensively accessible;
re-read legacy/inherited rules before activation. A committed CODEOWNERS file,
workflow or ruleset JSON does not itself prove enforcement.

## Disabled ruleset proposals

[The JSON files](../.github/rulesets) use GitHub's repository ruleset shape. All have
`enforcement: disabled`, no bypass actors, and require deliberate activation.

| Proposal | Purpose | Activation dependency |
| :--- | :--- | :--- |
| main-integrity | Require PRs, resolved threads and strict CI / required from GitHub Actions; block main deletion/force pushes. | Observe actual check context/App and reconcile live rules. |
| main-independent-review | Require one approving code-owner review, stale dismissal and approval of the last push by another person. | Add a second eligible CODEOWNER/team with write access. |
| retained-source-history | Block deletion/rewrites of upstream/* and nested upstream/**/* refs. | Confirm retained import refs. |
| immutable-release-tags | Prevent updating/deleting existing v* tags. | Establish release identity and recovery. |

The first main stage deliberately needs zero approvals to avoid a one-maintainer
deadlock; it is not independent review. Do not enable the second rule with the author
as the only possible code owner. Merely requesting a second reviewer does not add
them to CODEOWNERS. Tag immutability does not authorize tag creation, and retained
history still permits fast-forward updates. No branch is deleted here.

The proposed emitting App is GitHub Actions ID 15368, observed in this organization's
checks. App binding is not workflow binding: editable workflow code can emit the
same context. Require real review of CI changes or a trusted organization workflow.

## Activation sequence

1. Review/merge cleanup after selected checks pass. Confirm actual CI / required
   emission on fresh PRs and merge groups; adding an event does not enable a queue.
2. Export/reconcile repository, inherited and legacy rules, bypass actors and merge
   methods. Import these proposals disabled, never blindly over existing policy.
3. Protect upstream history before enabling automatic merged-branch deletion. Test
   main integrity against failing checks, direct pushes and protected-ref deletion.
4. Add a second eligible CODEOWNER/team, verify self-approval rejection, then activate
   independent review. Keep recovery outside agent credentials.
5. Verify agent/release identity and workflow-execution restrictions before enabling
   unattended promotion, publication or a merge queue.

Prefer squash merge with a conventional PR title, review other merge-method users
before disabling them, and leave auto-merge off until gates/reviews are proven.
Require signed commits only after a working signing path exists for every writer;
these Git-data-API cleanup commits are unsigned.

## Actions and identity boundaries

Keep default tokens read-only, PR-write in metadata-only automation, source jobs
secretless, remote actions/reusable workflows SHA-pinned, and major Action upgrades
reviewed separately. Limit allowed Actions and runner groups at organization level.
Verify private reporting, secret scanning/push protection and dependency alerts with
an administrator; this PR does not enable or claim to verify those settings.

Same-repository agent pushes are not equivalent to secretless fork PRs. A writer
able to change push workflows can request permissions before review; read-only
defaults are not an immutable ceiling. Avoid broad App/signing/operator keys in that
context. Use narrow identities, protected environments and a trusted proposal flow.
Do not assume private/internal push/file-path protections exist on public repositories.

Pilot central actor/event [workflow execution protections](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/actions-policies/workflow-execution-protections)
in evaluate mode where available; this is a public-preview administrative feature,
not an agent-editable CI check. Verify maintainers, agent identities, Dependabot and
intended events before enforcing it. A branch prefix alone restricts neither push
authority nor workflow execution.

Current GitHub documentation distinguishes GITHUB_TOKEN-created PR activity: opened,
synchronize and reopened create approval-required runs; other PR activity types do
not. Approve intended runs deliberately rather than introducing a broad token to
avoid approval friction.

Use disposable hosted workers for PR code. Native/KVM checks require a separately
trusted ephemeral boundary without operator homes, SSH agents, signing keys or
release/cache-write secrets inherited by guests. Record exact source/runtime/host
and test tiers. Separate trusted cache writers from readers, keep verification and
bounded retention, and do not upload live homes or guest disks as routine artifacts.

## Publication and remaining policy

Define public exports and a tested consumer matrix before compatibility promises.
Source versions and release-note categories are not publication. A separate narrow
publisher needs matching source/tag metadata, same-SHA test evidence, complete
checksummed assets and SBOM/provenance in a verified draft. Keep credentials out of
PR builds and do not promote until required downstream checks exist.

The owner must choose the repository license; this cleanup does not copy a license
from Workestrate. See [the audit](repository-audit-2026-09-11.md) and
[CI/release contracts](ci-releases.md) for implemented versus pending work.

## References

- [Rules API](https://docs.github.com/en/rest/repos/rules)
- [Available rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
- [GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)
- [Secure Actions use](https://docs.github.com/en/actions/reference/security/secure-use)
