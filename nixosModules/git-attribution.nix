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
        Public, consenting human co-author identities in Name <email> form. These
        values enter /etc/gitconfig and the Nix store. They are attribution,
        never credentials; author, committer and signing remain separate.
        Evaluation checks the basic shape; the CLI validates complete email
        syntax before changing a message.
      '';
    };
    strict = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Reject existing, imported or explicitly requested co-authors unless
        their name and email match coAuthors or allowedCoAuthors. Email matching
        ignores case; display names must match exactly. Rejection never removes
        credits or rewrites the input message.
      '';
    };
    allowedCoAuthors = lib.mkOption {
      type = lib.types.listOf identity;
      default = [ ];
      example = [ "Sam Example <sam@example.org>" ];
      description = ''
        Additional reviewed human identities permitted by strict admission.
        These public names and emails enter Git configuration and the Nix store.
        Unlike coAuthors, they are preserved when present but not required in
        every message. Never configure tool, model or automation identities here.
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
        {
          attribution = {
            coAuthor = cfg.coAuthors;
            inherit (cfg) strict;
            allowedCoAuthor = cfg.allowedCoAuthors;
          };
        }
        (lib.mkIf cfg.installTemplate {
          init.templateDir = "${cfg.package}/share/git-attribution/template";
        })
      ];
    };
  };
}
