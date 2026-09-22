{
  lib,
  stdenvNoCC,
  python3,
  gitMinimal,
  makeWrapper,
}:
stdenvNoCC.mkDerivation {
  pname = "git-attribution";
  version = "0.1.0";
  dontUnpack = true;
  nativeBuildInputs = [ makeWrapper ];
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/libexec" "$out/bin" "$out/share/git-attribution/template/hooks"
    cp ${../scripts/git-attribution.py} "$out/libexec/git-attribution.py"
    cp ${../share/git-attribution-instructions.md} "$out/share/git-attribution/agent-instructions.md"
    makeWrapper ${python3}/bin/python3 "$out/bin/git-attribution" \
      --add-flags "$out/libexec/git-attribution.py" \
      --prefix PATH : ${lib.makeBinPath [ gitMinimal ]} \
      --set GIT_ATTRIBUTION_EXECUTABLE "$out/bin/git-attribution" \
      --set GIT_ATTRIBUTION_SHELL ${stdenvNoCC.shell}
    for hook in prepare-commit-msg commit-msg; do
      cat > "$out/share/git-attribution/template/hooks/$hook" <<EOF
    #!${stdenvNoCC.shell}
    # Managed by git-attribution.
    exec "$out/bin/git-attribution" hook "$hook" "\$@"
    EOF
      chmod +x "$out/share/git-attribution/template/hooks/$hook"
    done
    runHook postInstall
  '';
  meta = {
    description = "Shared Git co-author formatting, validation and composable commit hooks";
    mainProgram = "git-attribution";
    platforms = lib.platforms.unix;
  };
}
