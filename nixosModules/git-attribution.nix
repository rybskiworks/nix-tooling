{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.gitAttribution;
  identity = lib.types.addCheck lib.types.str (
    value: builtins.match "[^<>[:cntrl:]]+ <[^<>[:space:]@]+@[^<>[:space:]@]+>" value != null
  );
in
{
  options.programs.gitAttribution = {
    enable = lib.mkEnableOption "shared co-author trailers for agent and developer Git workflows";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../packages/git-attribution.nix { };
      description = "The shared git-attribution formatter and validator package.";
    };
    coAuthors = lib.mkOption {
      type = lib.types.listOf identity;
      default = [ ];
      example = [ "Pat Example <pat@example.org>" ];
      description = ''
        Public, consenting co-author identities in Name <email> form. These
        values enter /etc/gitconfig and the Nix store. They are attribution,
        never credentials; author, committer and signing remain separate.
        Evaluation checks the basic shape; the CLI validates complete email
        syntax before changing a message.
      '';
    };
    installTemplate = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Select the package's init.templateDir for future clones and git init.
        Existing repositories require an explicit git-attribution install.
        Disable this when composing the hooks into another Git template.
        core.hooksPath is never set or replaced.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.coAuthors != [ ];
        message = "programs.gitAttribution requires at least one explicitly configured coAuthor";
      }
    ];
    environment.systemPackages = [ cfg.package ];
    programs.git = {
      enable = true;
      config = lib.mkMerge [
        { attribution.coAuthor = cfg.coAuthors; }
        (lib.mkIf cfg.installTemplate {
          init.templateDir = "${cfg.package}/share/git-attribution/template";
        })
      ];
    };
  };
}
