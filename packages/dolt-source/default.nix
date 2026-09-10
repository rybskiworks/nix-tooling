{
  lib,
  buildGoModule,
  fetchFromGitHub,
  icu,
  stdenvNoCC,
}:
let
  sourceRevision = "8c13902c0140dae63033c4d717b4117e579806cc";
  vitessRevision = "e3f9fa81284cc50261facd765338cc3f00a09226";
  patchedVitess = stdenvNoCC.mkDerivation {
    pname = "vitess-secure-transport-source";
    version = "2026-05-28";
    src = fetchFromGitHub {
      name = "vitess-${vitessRevision}-source";
      owner = "dolthub";
      repo = "vitess";
      rev = vitessRevision;
      hash = "sha256-97MsKFmvcQ1rzkDMtULWKYHeaF+JaSun7SS9ivWium8=";
    };
    patches = [ ./secure-transport.patch ];
    patchFlags = [
      "-p1"
      "--fuzz=0"
    ];
    dontConfigure = true;
    dontBuild = true;
    dontFixup = true;
    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      cp -R . "$out/"
      install -m644 ${./secure_transport_test.go} "$out/go/mysql/secure_transport_test.go"
      runHook postInstall
    '';
  };
in
buildGoModule {
  pname = "dolt-secure-transport";
  version = "2.1.0";
  src = fetchFromGitHub {
    name = "dolt-${sourceRevision}-source";
    owner = "dolthub";
    repo = "dolt";
    rev = sourceRevision;
    hash = "sha256-VSyRsfUaKNhk6cP72A3HcKsBwHT47YYZFpHUuYPJLnE=";
  };

  modRoot = "go";
  subPackages = [ "cmd/dolt" ];
  proxyVendor = true;
  vendorHash = "sha256-uLu0F62Tus2rhH3C9AleijteHFW7qTeBG4gIDYd2N40=";
  buildInputs = [ icu ];

  # Preserve upstream versions/checksums and replace Vitess only in build scratch.
  # Its package tests also need go-cmp, already selected by Protobuf and present
  # in Dolt's checksums but omitted from the application-only download set.
  postPatch = ''
    go -C go mod edit \
      -replace=github.com/dolthub/vitess=${patchedVitess} \
      -require=github.com/google/go-cmp@v0.7.0
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    go test -v -count=1 -timeout=30s -p "$NIX_BUILD_CORES" \
      -run '^TestSecureTransportRefusalIsTerminal$' github.com/dolthub/vitess/go/mysql

    # The same test must detect the original missing return, not just a client
    # error. Change only a private source copy and restore the module mapping.
    negativeRoot=$(mktemp -d "$TMPDIR/vitess-negative.XXXXXX")
    cp -R --no-preserve=mode ${patchedVitess}/. "$negativeRoot/"
    patch --directory="$negativeRoot" --reverse --fuzz=0 -p1 < ${./secure-transport.patch}
    cp go.mod "$TMPDIR/dolt-positive.go.mod"
    go mod edit -replace=github.com/dolthub/vitess="$negativeRoot"
    negativeStatus=0
    go test -v -count=1 -timeout=30s -p "$NIX_BUILD_CORES" \
      -run '^TestSecureTransportRefusalIsTerminal$' github.com/dolthub/vitess/go/mysql \
      > "$TMPDIR/transport-negative.log" 2>&1 || negativeStatus=$?
    cp "$TMPDIR/dolt-positive.go.mod" go.mod
    cat "$TMPDIR/transport-negative.log"
    test "$negativeStatus" -eq 1
    test "$(grep -c 'refusal was not terminal: bytes=1 error=<nil>' "$TMPDIR/transport-negative.log")" -eq 2
    test "$(grep -c -- '--- FAIL:' "$TMPDIR/transport-negative.log")" -eq 3
    grep -F -- '--- PASS: TestSecureTransportRefusalIsTerminal/optional-valid ' "$TMPDIR/transport-negative.log"
    grep -F -- '--- FAIL: TestSecureTransportRefusalIsTerminal/required-valid ' "$TMPDIR/transport-negative.log"
    grep -F -- '--- FAIL: TestSecureTransportRefusalIsTerminal/required-invalid ' "$TMPDIR/transport-negative.log"

    # Recheck the restored production mapping after the negative control.
    go test -v -count=1 -timeout=30s -p "$NIX_BUILD_CORES" \
      -run '^TestSecureTransportRefusalIsTerminal$' github.com/dolthub/vitess/go/mysql
    runHook postCheck
  '';

  passthru = {
    inherit sourceRevision vitessRevision patchedVitess;
  };

  meta = {
    description = "Source-built Dolt with terminal TLS-required refusal";
    homepage = "https://github.com/dolthub/dolt";
    license = lib.licenses.asl20;
    platforms = [ "x86_64-linux" ];
    mainProgram = "dolt";
    sourceProvenance = [ lib.sourceTypes.fromSource ];
  };
}
