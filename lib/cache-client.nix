# One reviewed signed binary cache, named explicitly by the consumer.
#
# Pure: the caller supplies the endpoint and the single public verification
# key; nothing is read from the ambient configuration and nothing is written.
# The strict argument set is the trust boundary, so a second key, a wildcard
# substituter, a reader credential or a trust grant beyond root cannot be
# expressed here at all.
{
  lib,
  endpoint,
  publicKey,
  insecureLocalEndpoint ? false,
}:
let
  # scheme://authority/path: the authority ends at the first path separator or
  # query/fragment delimiter, so credentials cannot hide inside it.
  url =
    if builtins.isString endpoint then
      builtins.match "([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]+)(.*)" endpoint
    else
      null;
  scheme = if url == null then "" else builtins.elemAt url 0;
  authority = if url == null then "" else builtins.elemAt url 1;
  bracketed = lib.hasPrefix "[" authority;
  host =
    if bracketed then
      builtins.head (lib.splitString "]" (lib.removePrefix "[" authority))
    else
      builtins.head (lib.splitString ":" authority);
  afterHost = lib.removePrefix (if bracketed then "[${host}]" else host) authority;
  hostForm =
    if bracketed then
      builtins.match "[0-9A-Fa-f:]+" host != null
    else
      builtins.match "[A-Za-z0-9._-]+" host != null;
  loopbackHost = host == "localhost" || host == "127.0.0.1" || host == "::1";
  secured = scheme == "https";
  # One canonical spelling, so a consumer and a module cannot add the same
  # cache under two different strings.
  substituter = if lib.hasSuffix "/" endpoint then lib.removeSuffix "/" endpoint else endpoint;
  keyForm = "([A-Za-z0-9][A-Za-z0-9._-]*)-[0-9]+:[A-Za-z0-9+/]{43}=";
in
assert lib.assertMsg (builtins.isString endpoint) "cache endpoint must be a string";
assert lib.assertMsg (builtins.isBool insecureLocalEndpoint) "insecureLocalEndpoint must be a boolean";
assert lib.assertMsg (endpoint != "") "cache endpoint must not be empty";
assert lib.assertMsg (
  builtins.match "[^ \t\n\r]+" endpoint != null
) "cache endpoint must be exactly one URL without whitespace";
assert lib.assertMsg (!lib.hasInfix "," endpoint) "cache endpoint must not be a comma-separated list";
assert lib.assertMsg (url != null) "cache endpoint must be an absolute scheme://host URL";
assert lib.assertMsg (
  !lib.hasInfix "@" authority
) "cache endpoint must not carry credentials or user information";
assert lib.assertMsg (!lib.hasInfix "*" endpoint) "cache endpoint must not be a wildcard substituter";
assert lib.assertMsg (
  !lib.hasInfix "?" endpoint && !lib.hasInfix "#" endpoint
) "cache endpoint must not carry a query string or fragment";
assert lib.assertMsg (
  hostForm && (afterHost == "" || builtins.match ":[0-9]+" afterHost != null)
) "cache endpoint must name a host with an optional numeric port";
assert lib.assertMsg (
  secured || scheme == "http"
) "cache endpoint must use the https:// scheme";
assert lib.assertMsg (
  secured || insecureLocalEndpoint
) "cache endpoint requires https:// unless insecureLocalEndpoint explicitly allows plain http";
assert lib.assertMsg (
  secured || loopbackHost
) "insecureLocalEndpoint is a local exception and cannot allow a non-loopback http:// endpoint";
assert lib.assertMsg (builtins.isString publicKey) "cache public key must be a string";
assert lib.assertMsg (publicKey != "") "cache public key must not be empty";
assert lib.assertMsg (
  builtins.match "[^ \t\n\r]+" publicKey != null && !lib.hasInfix "," publicKey
) "exactly one public key must be named, without whitespace or commas";
assert lib.assertMsg (
  builtins.match keyForm publicKey != null
) "cache public key must be <name>-<n>:<base64>, one 32-byte ed25519 key";
{
  substituters = [ substituter ];
  trusted-public-keys = [ publicKey ];
}
