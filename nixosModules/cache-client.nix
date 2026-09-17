{ config, lib, ... }:
let
  cfg = config.nix.cacheClients;
  # Each declared client is validated by the shared pure helper and then merged
  # into nix.settings, which keeps existing consumer caches and keys in place.
  fragment =
    client:
    import ../lib/cache-client.nix {
      inherit lib;
      inherit (client) endpoint publicKey insecureLocalEndpoint;
    };
in
{
  options.nix.cacheClients = lib.mkOption {
    default = { };
    description = ''
      Named signed binary caches to add on top of the cache configuration a
      consumer already declares. Every entry names one endpoint and its single
      public verification key; the result is merged into the existing
      `nix.settings` lists additively and never replaces, reorders or removes
      an entry. Importing this module without declaring a client changes
      nothing.
    '';
    type = lib.types.attrsOf (
      lib.types.submodule {
        options = {
          endpoint = lib.mkOption {
            type = lib.types.str;
            default = "";
            example = "https://cache.example.org";
            description = ''
              Absolute `https://` URL of the signed cache. Plain `http://` is
              accepted only for a loopback host and only together with
              `insecureLocalEndpoint`.
            '';
          };
          publicKey = lib.mkOption {
            type = lib.types.str;
            default = "";
            example = "cache.example.org-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
            description = ''
              The single public verification key of that endpoint, written as
              `<name>-<n>:<base64>`. A public key is not a secret. Signing keys
              and private reader credentials do not belong in the Nix store and
              have no option here.
            '';
          };
          insecureLocalEndpoint = lib.mkOption {
            type = lib.types.bool;
            default = false;
            description = ''
              Permit plain `http://` for a loopback endpoint, for example a
              local mirror or a test double. It never permits a remote
              plaintext endpoint.
            '';
          };
        };
      }
    );
  };

  config = lib.mkIf (cfg != { }) {
    nix.settings = lib.mkMerge (lib.mapAttrsToList (_name: fragment) cfg);
  };
}
