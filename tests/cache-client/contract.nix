{
  pkgs,
  nixpkgs,
}:
let
  inherit (pkgs) lib;
  endpoint = "https://cache.example.org";
  publicKey = "cache.example.org-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  consumerKey = "consumer.invalid-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  loopbackEndpoint = "http://127.0.0.1:8080";
  loopbackKey = "loopback.invalid-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  select =
    spec:
    import ../../lib/cache-client.nix (
      {
        inherit lib;
      }
      // spec
    );
  valid = {
    inherit endpoint publicKey;
  };
  rejects = spec: !(builtins.tryEval (builtins.deepSeq (select spec) true)).success;
  guest = import ../../lib/guest { inherit nixpkgs; };
  module = import ../../nixosModules/cache-client.nix;
  system =
    modules:
    guest.mkNixosSystem {
      inherit pkgs;
      stateVersion = "26.05";
      modules = [ { nix.enable = true; } ] ++ modules;
    };
  evaluate = modules: (system modules).config;
  # Force both the declared clients and the resulting settings, so an invalid
  # client or an unknown client option fails the whole evaluation.
  rejectsConfiguration =
    modules:
    let
      configured = evaluate modules;
    in
    !(builtins.tryEval (
      builtins.deepSeq [
        configured.nix.cacheClients
        configured.nix.settings
      ] true
    )).success;
  base = evaluate [ ];
  inert = evaluate [ module ];
  enabled = evaluate [
    module
    { nix.cacheClients.example = valid; }
  ];
  consumer = evaluate [
    module
    {
      nix.cacheClients.example = valid;
      nix.settings.substituters = [ "https://cache.consumer.invalid" ];
      nix.settings.trusted-public-keys = [ consumerKey ];
    }
  ];
  pair = evaluate [
    module
    {
      nix.cacheClients.primary = valid;
      nix.cacheClients.local = {
        endpoint = loopbackEndpoint;
        publicKey = loopbackKey;
        insecureLocalEndpoint = true;
      };
    }
  ];
  cacheSettings = value: builtins.removeAttrs value [ "substituters" "trusted-public-keys" ];
  sort = lib.sort builtins.lessThan;
  # Enumerate every option the module declares, so the trust surface is
  # asserted closed instead of assumed.
  declaredOptions =
    options:
    lib.concatMap (
      name:
      if (options.${name}._type or "") == "option" then
        [ options.${name}.name ]
      else
        declaredOptions options.${name}
    ) (builtins.attrNames (builtins.removeAttrs options [ "_module" ]));
  clientOptions = declaredOptions (
    (system [ module ]).options.nix.cacheClients.type.getSubOptions [
      "nix"
      "cacheClients"
    ]
  );
  cacheEntries = enabled.nix.settings.substituters ++ enabled.nix.settings.trusted-public-keys;
  assertions = {
    fragment =
      select valid == {
        substituters = [ endpoint ];
        trusted-public-keys = [ publicKey ];
      };
    endpointCanonical = select (valid // { endpoint = "${endpoint}/"; }) == select valid;
    localExceptionAccepted =
      select {
        endpoint = loopbackEndpoint;
        publicKey = loopbackKey;
        insecureLocalEndpoint = true;
      }
      == {
        substituters = [ loopbackEndpoint ];
        trusted-public-keys = [ loopbackKey ];
      };
    emptyEndpointRejected = rejects (valid // { endpoint = ""; });
    endpointTypeRejected = rejects (valid // { endpoint = 42; });
    whitespaceEndpointRejected = rejects (
      valid // { endpoint = "${endpoint} https://other.example.org"; }
    );
    commaEndpointRejected = rejects (
      valid // { endpoint = "${endpoint},https://other.example.org"; }
    );
    relativeEndpointRejected = rejects (valid // { endpoint = "cache.example.org"; });
    wildcardEndpointRejected = rejects (valid // { endpoint = "*"; });
    wildcardHostRejected = rejects (valid // { endpoint = "https://*.example.org"; });
    credentialsRejected = rejects (
      valid // { endpoint = "https://reader:secret@cache.example.org"; }
    );
    userinfoRejected = rejects (valid // { endpoint = "https://token@cache.example.org"; });
    queryTokenRejected = rejects (valid // { endpoint = "${endpoint}?token=secret"; });
    fragmentTokenRejected = rejects (valid // { endpoint = "${endpoint}#secret"; });
    plainHttpRejected = rejects (valid // { endpoint = "http://cache.example.org"; });
    plainHttpRemoteWithLocalExceptionRejected = rejects (
      valid
      // {
        endpoint = "http://cache.example.org";
        insecureLocalEndpoint = true;
      }
    );
    plainHttpWithoutLocalExceptionRejected = rejects (valid // { endpoint = loopbackEndpoint; });
    unsupportedSchemeRejected = rejects (valid // { endpoint = "s3://bucket/cache"; });
    fileEndpointRejected = rejects (valid // { endpoint = "file:///srv/cache"; });
    malformedAuthorityRejected = rejects (valid // { endpoint = "https://cache.example.org:port"; });
    emptyKeyRejected = rejects (valid // { publicKey = ""; });
    keyTypeRejected = rejects (valid // { publicKey = [ publicKey ]; });
    wildcardKeyRejected = rejects (valid // { publicKey = "*"; });
    secondKeyRejected = rejects (valid // { publicKey = "${publicKey} ${consumerKey}"; });
    commaSeparatedKeysRejected = rejects (valid // { publicKey = "${publicKey},${consumerKey}"; });
    keyWithoutCounterRejected = rejects (
      valid // { publicKey = "cache.example.org:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="; }
    );
    malformedKeyRejected = rejects (valid // { publicKey = "cache.example.org-1:invalid"; });
    shortKeyRejected = rejects (valid // { publicKey = "cache.example.org-1:AAAA="; });
    trustedSubstitutersRejected = rejects (valid // { trusted-substituters = [ "*" ]; });
    trustedSubstitutersCamelRejected = rejects (valid // { trustedSubstituters = [ "*" ]; });
    trustedUsersRejected = rejects (valid // { trustedUsers = [ "alice" ]; });
    requireSigsRejected = rejects (valid // { require-sigs = false; });
    sandboxRejected = rejects (valid // { sandbox = false; });
    moduleInertWithoutClients = inert.nix.settings == base.nix.settings;
    selectedCacheAdded =
      sort enabled.nix.settings.substituters == sort (base.nix.settings.substituters ++ [ endpoint ]);
    selectedKeyAdded =
      sort enabled.nix.settings.trusted-public-keys
      == sort (base.nix.settings.trusted-public-keys ++ [ publicKey ]);
    consumerCachePreserved =
      sort consumer.nix.settings.substituters
      == sort (enabled.nix.settings.substituters ++ [ "https://cache.consumer.invalid" ]);
    consumerKeyPreserved =
      sort consumer.nix.settings.trusted-public-keys
      == sort (enabled.nix.settings.trusted-public-keys ++ [ consumerKey ]);
    namedClientsAdditive =
      sort pair.nix.settings.substituters == sort (base.nix.settings.substituters ++ [
        endpoint
        loopbackEndpoint
      ]);
    nothingElseChanged = cacheSettings base.nix.settings == cacheSettings enabled.nix.settings;
    sandboxRetained = enabled.nix.settings.sandbox && !enabled.nix.settings.sandbox-fallback;
    signaturesRetained = enabled.nix.settings.require-sigs;
    rootOnlyTrust = lib.unique enabled.nix.settings.trusted-users == [ "root" ];
    noExtraTrustedSubstituters = enabled.nix.settings.trusted-substituters == [ ];
    trustSurfaceClosed =
      clientOptions == [
        "nix.cacheClients.*.endpoint"
        "nix.cacheClients.*.insecureLocalEndpoint"
        "nix.cacheClients.*.publicKey"
      ];
    noCredentialOrWildcardEntry = builtins.all (
      entry: !lib.hasInfix "@" entry && !lib.hasInfix "*" entry
    ) cacheEntries;
    cannotTrustEverything =
      rejectsConfiguration [
        module
        { nix.cacheClients.example = { endpoint = "*"; publicKey = "*"; }; }
      ]
      && rejectsConfiguration [
        module
        { nix.cacheClients.example.trusted-substituters = [ "*" ]; }
      ]
      && rejectsConfiguration [
        module
        { nix.cacheClients.example.trustedUsers = [ "alice" ]; }
      ]
      && rejectsConfiguration [ module { nix.cacheClients.example.requireSigs = false; } ]
      && rejectsConfiguration [ module { nix.cacheClients.example.sandbox = false; } ]
      && enabled.nix.settings.trusted-substituters == [ ]
      && lib.unique enabled.nix.settings.trusted-users == [ "root" ];
    keylessClientRejected =
      rejectsConfiguration [ module { nix.cacheClients.example = { inherit endpoint; }; } ];
    endpointlessClientRejected =
      rejectsConfiguration [ module { nix.cacheClients.example = { inherit publicKey; }; } ];
    credentialEndpointRejected = rejectsConfiguration [
      module
      {
        nix.cacheClients.example = {
          endpoint = "https://reader:secret@cache.example.org";
          inherit publicKey;
        };
      }
    ];
  };
in
assert lib.assertMsg (builtins.all (value: value) (builtins.attrValues assertions)) (
  "signed cache client contract failed: "
  + lib.concatStringsSep ", " (builtins.attrNames (lib.filterAttrs (_name: value: !value) assertions))
);
pkgs.writeText "cache-client-contract.json" (builtins.toJSON assertions)
