{ lib, nixConfig }:
let
  endpoint = "https://devenv.cachix.org";
  keyPrefix = "devenv.cachix.org-1:";
  words =
    value:
    if builtins.isString value then
      lib.filter (word: word != "") (lib.splitString " " value)
    else
      value;
  urls = words (nixConfig.extra-substituters or [ ]);
  keys = words (nixConfig.extra-trusted-public-keys or [ ]);
  stringList = value: builtins.isList value && builtins.all builtins.isString value;
  selectedUrls = lib.filter (url: url == endpoint) urls;
  selectedKeys = lib.filter (lib.hasPrefix keyPrefix) keys;
in
assert lib.assertMsg (
  stringList urls && stringList keys
) "devenv cache declarations must be strings or lists of strings";
assert lib.assertMsg (
  builtins.length selectedUrls == 1
) "pinned devenv must declare its HTTPS cache exactly once";
assert lib.assertMsg (
  builtins.length selectedKeys == 1
  && builtins.match "devenv[.]cachix[.]org-1:[A-Za-z0-9+/]{43}=" (builtins.head selectedKeys) != null
) "pinned devenv must declare exactly one well-formed public cache key";
{
  substituters = selectedUrls;
  trusted-public-keys = selectedKeys;
}
