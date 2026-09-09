{ mkImage, mkNixosSystem }:
let
  registeredImage =
    {
      pkgs,
      name,
      tag,
      contents,
      rootfs,
      config,
      extraCommands,
      fromImage,
      maxLayers,
      registrationName,
      finalCommands ? "",
    }:
    let
      # Materialize caller customizations before computing their runtime closure.
      # The registration itself must not become one of its own roots.
      payload = pkgs.symlinkJoin {
        name = "${registrationName}-guest-payload";
        paths = contents ++ pkgs.lib.optional (rootfs != null) rootfs;
        postBuild = ''
          cd "$out"
          ${extraCommands}
        '';
      };
      runtimeConfig = pkgs.writeText "${registrationName}-guest-config.json" (builtins.toJSON config);
      roots = [
        payload
        runtimeConfig
      ];
      closure = pkgs.closureInfo { rootPaths = roots; };
      rootsText = pkgs.lib.concatMapStrings (root: "${root}\n") roots;
      archive = mkImage {
        inherit
          pkgs
          name
          tag
          config
          fromImage
          maxLayers
          ;
        contents = [ payload ];
        # These literal paths are consumed by the system unit. The system does
        # not reference this closureInfo derivation, avoiding a toplevel cycle.
        extraCommands = ''
          mkdir -p nix/guest-registrations
          cp ${closure}/registration nix/guest-registrations/${registrationName}.registration
          cat > nix/guest-registrations/${registrationName}.roots <<'ROOTS'
          ${rootsText}ROOTS
          chmod 0444 nix/guest-registrations/${registrationName}.{registration,roots}
          ${finalCommands}
        '';
      };
    in
    assert builtins.match "[a-z][a-z0-9-]*" registrationName != null;
    archive.overrideAttrs (old: {
      passthru = (old.passthru or { }) // {
        guestRegistration = {
          inherit
            closure
            roots
            payload
            runtimeConfig
            registrationName
            ;
        };
      };
    });
in
{
  mkNixosImage =
    {
      pkgs,
      name,
      stateVersion,
      tag ? null,
      modules ? [ ],
      maxLayers ? 100,
    }:
    let
      system = mkNixosSystem {
        inherit pkgs stateVersion;
        modules = [ ../../nixosModules/microsandbox-guest.nix ] ++ modules;
      };
      toplevel = system.config.system.build.toplevel;
      rootfs = pkgs.runCommand "nixos-guest-rootfs" { } ''
        mkdir -p "$out"/{etc/ssl/certs,nix/var/nix,nix/var/determinate,root,tmp,run}
        chmod 1777 "$out/tmp"
        ln -s ${toplevel}/init "$out/init"
        # Agentd appends runtime-delivered CA certificates before activation.
        # This must be a regular writable image file, never a store symlink.
        cp ${system.config.security.pki.caBundle} "$out/etc/ssl/certs/ca-certificates.crt"
        chmod 0644 "$out/etc/ssl/certs/ca-certificates.crt"
      '';
      archive = registeredImage {
        inherit
          pkgs
          name
          tag
          rootfs
          maxLayers
          ;
        registrationName = "base";
        contents = [ ];
        fromImage = null;
        config = {
          Env = [
            "PATH=/run/current-system/sw/bin"
            "NIX_REMOTE=daemon"
          ];
          WorkingDir = "/";
        };
        # symlinkJoin links ordinary files too; detach the bundle in the final
        # payload before the runtime opens it for appending.
        extraCommands = "";
        finalCommands = ''
          cp --remove-destination ${system.config.security.pki.caBundle} etc/ssl/certs/ca-certificates.crt
          chmod 0644 etc/ssl/certs/ca-certificates.crt
        '';
      };
    in
    assert system.config.boot.isContainer && !system.config.boot.kernel.enable;
    assert system.config.nix.enable;
    archive.overrideAttrs (old: {
      passthru = (old.passthru or { }) // {
        guestSystem = system;
        guestToplevel = toplevel;
        guestInit = {
          executable = "/init";
          args = [ ];
          env.container = "microsandbox";
          workingDir = "/";
        };
        guestRegistrationNames = [ "base" ];
        guestLayerBudget = maxLayers;
      };
    });

  # A leaf extends an existing archive, not a separately evaluated NixOS system.
  mkNixosLayer =
    {
      pkgs,
      base,
      name,
      registrationName,
      tag ? null,
      contents ? [ ],
      rootfs ? null,
      config ? { },
      extraCommands ? "",
      maxLayers ? 100,
    }:
    let
      archive = registeredImage {
        inherit
          pkgs
          name
          tag
          registrationName
          contents
          rootfs
          config
          maxLayers
          ;
        extraCommands = ''
          ${extraCommands}
          source ${./check-leaf.sh}
          check_leaf_root "$out"
        '';
        fromImage = base;
      };
    in
    assert base ? guestInit && base ? guestRegistrationNames && base ? guestLayerBudget;
    # Reserve one payload and one customization layer beyond the parent bound,
    # without realizing its archive during evaluation.
    assert maxLayers >= base.guestLayerBudget + 2;
    assert !(builtins.elem registrationName base.guestRegistrationNames);
    archive.overrideAttrs (old: {
      passthru = (old.passthru or { }) // {
        inherit (base) guestInit guestSystem guestToplevel;
        guestBase = base;
        guestRegistrationNames = base.guestRegistrationNames ++ [ registrationName ];
        guestLayerBudget = maxLayers;
      };
    });
}
