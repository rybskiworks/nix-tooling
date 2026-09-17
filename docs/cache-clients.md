# Signed cache clients

`lib.cacheClient` and `nixosModules.cacheClient` are the reusable consumer side
of a signed binary cache. They add one reviewed cache on top of the caches a
configuration already declares. They do not read another project's cache
declaration, carry credentials, sign anything, or build, fetch and probe during
evaluation. The [pinned devenv cache](determinate-guests.md#opt-in-public-devenv-cache)
stays the special case that derives its endpoint and key from a locked supplier
flake.

## Fragment helper

`lib.cacheClient` takes one explicit endpoint, one explicit public verification
key and an optional loopback exception, and returns the Nix settings fragment
for that pair:

```nix
import inputs.tooling.lib.cacheClient {
  inherit lib;
  endpoint = "https://cache.example.org";
  publicKey = "cache.example.org-1:<base64>";
}
# { substituters = [ "https://cache.example.org" ];
#   trusted-public-keys = [ "cache.example.org-1:<base64>" ]; }
```

The helper is pure and fails closed. It rejects:

- an empty, non-string or malformed endpoint, or one without `scheme://host`;
- credentials or user information in the URL (`https://reader:secret@...`);
- a query string or fragment, so a token cannot ride along in the URL;
- plain `http://`, and plain `http://` to a remote host even with
  `insecureLocalEndpoint = true`, which is a loopback-only exception;
- any scheme other than `https://` or that loopback `http://`;
- wildcard endpoints (`*`, `https://*.example.org`);
- several endpoints in one string (whitespace or comma separated);
- an empty, non-string, malformed or non-32-byte key, a key without the
  `<name>-<n>` counter, and more than one key at a time;
- any additional argument, so `trusted-substituters`, `trusted-users`,
  `require-sigs` or `sandbox` cannot be smuggled through the same call.

A trailing slash is removed from the endpoint so one cache has one spelling.

## NixOS module

`nixosModules.cacheClient` declares the same pair per client name and merges the
fragment into `nix.settings` additively:

```nix
modules = [
  inputs.tooling.nixosModules.cacheClient
  {
    nix.cacheClients.workload = {
      endpoint = "https://cache.example.org";
      publicKey = "cache.example.org-1:<base64>";
    };
  }
];
```

Existing `nix.settings.substituters`, `trusted-public-keys` and every other
setting keep their values; several names can add several caches. The module
declares only `endpoint`, `publicKey` and `insecureLocalEndpoint`, so it has no
way to express a wildcard substituter, `trusted-substituters`, trust beyond
root, `require-sigs = false` or `sandbox = false`. Importing it without
declaring a client changes nothing. Nix keeps signature checking and sandboxing
at their defaults; this module does not touch them.

Public keys are not secrets. Private reader credentials and signing keys do not
belong in the Nix store and have no option here; keep them outside the
configuration. Adding a cache is explicit trust in its signing authority, not a
grant of network reachability, and it does not make a build succeed.

`checks.x86_64-linux.cache-client-contract` covers the fragment value, the
rejection cases above, additive composition with existing consumer caches, and
the closed option surface of the module. It evaluates the helper and one NixOS
configuration per case. It does not boot a guest, substitute anything, or prove
that a cache answers or that a bad signature is refused at runtime.
