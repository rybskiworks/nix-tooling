{ pkgs }:

pkgs.stdenv.mkDerivation rec {
  pname = "tombi";
  version = "1.2.5";

  src = pkgs.fetchurl {
    url = "https://github.com/tombi-toml/tombi/releases/download/v${version}/tombi-cli-${version}-x86_64-unknown-linux-musl.tar.gz";
    sha256 = "sha256-BThb30fRCCjB/CcZDuCtRLd3pX9W6pXJ71aMhF47eHI=";
  };

  installPhase = ''
    runHook preInstall

    if [ -f tombi-cli-${version}-x86_64-unknown-linux-musl/tombi ]; then
      install -Dm755 tombi-cli-${version}-x86_64-unknown-linux-musl/tombi $out/bin/.tombi-wrapped
    elif [ -f tombi ]; then
      install -Dm755 tombi $out/bin/.tombi-wrapped
    else
      echo "error: tombi not found in tarball" >&2
      exit 1
    fi

    cat > $out/bin/tombi <<'EOS'
#!/usr/bin/env bash
export TOMBI_OFFLINE="''${TOMBI_OFFLINE:-true}"
top="$(git rev-parse --show-toplevel 2>/dev/null || echo "/")"
if [ -z "$top" ]; then
  top="/"
fi
current_dir="$PWD"
while true; do
  if [ -f "$current_dir/tombi.toml" ] || [ -f "$current_dir/.tombi.toml" ] || [ -f "$current_dir/tombi/config.toml" ] || { [ -f "$current_dir/pyproject.toml" ] && grep -q "\[tool\.tombi\]" "$current_dir/pyproject.toml" 2>/dev/null; }; then
    exec "__WRAPPED__" "$@"
  fi
  if [ "$current_dir" = "$top" ] || [ "$current_dir" = "/" ]; then
    break
  fi
  current_dir="$(dirname "$current_dir")"
done
echo "tombi: no tombi.toml in scope (walked up to $top) — use 'nix fmt'/'treefmt' or add tombi.toml; this repo may use its own formatter (e.g. taplo)" >&2
exit 1
EOS
    substituteInPlace $out/bin/tombi --replace "__WRAPPED__" "$out/bin/.tombi-wrapped"
    chmod +x $out/bin/tombi

    runHook postInstall
  '';

  meta = with pkgs.lib; {
    description = "TOML formatter, linter, and language server";
    homepage = "https://github.com/tombi-toml/tombi";
    license = licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "tombi";
  };
}
