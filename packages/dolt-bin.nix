{
  lib,
  stdenvNoCC,
  fetchurl,
  autoPatchelfHook,
  stdenv,
}:

stdenvNoCC.mkDerivation {
  pname = "dolt-bin";
  version = "2.1.0";

  src = fetchurl {
    url = "https://github.com/dolthub/dolt/releases/download/v2.1.0/dolt-linux-amd64.tar.gz";
    hash = "sha256-DOu0rIXn1nswYDdzXoVcwkk5xKJ64E1xKYmzJd4yGCY=";
  };
  sourceRoot = "dolt-linux-amd64";
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];
  dontConfigure = true;
  dontBuild = true;
  dontStrip = true;

  installPhase = ''
    runHook preInstall
    install -Dm755 bin/dolt "$out/bin/dolt"
    install -Dm644 LICENSES "$out/share/doc/dolt/LICENSES"
    runHook postInstall
  '';

  passthru.sourceRevision = "8c13902c0140dae63033c4d717b4117e579806cc";

  meta = {
    description = "Pinned upstream Dolt binary for Beads SQL-server compatibility";
    homepage = "https://github.com/dolthub/dolt";
    license = lib.licenses.asl20;
    platforms = [ "x86_64-linux" ];
    mainProgram = "dolt";
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
