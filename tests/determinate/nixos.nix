{
  pkgs,
  profile,
  engine,
  nixd,
}:
let
  declared = pkgs.writeText "determinate-declared-input" "declared-input\n";
  builder = pkgs.writeText "determinate-sandbox-builder" ''
    set -eux
    test ! -e /etc/determinate-outside
    test "$(${pkgs.coreutils}/bin/id -u)" != 0
    ${pkgs.python3}/bin/python3 -c '
    import socket
    try:
        socket.create_connection(("127.0.0.1", 19877), timeout=1)
    except OSError:
        pass
    else:
        raise SystemExit("sandbox reached the guest external listener")
    '
    ${pkgs.coreutils}/bin/mkdir "$out"
    ${pkgs.coreutils}/bin/cat "$declared" > "$out/value"
    ${pkgs.coreutils}/bin/id -u > "$out/worker-uid"
    ${pkgs.coreutils}/bin/cat /proc/self/uid_map > "$out/worker-uid-map"
  '';
  expression = pkgs.writeText "determinate-build.nix" ''
    { nonce }:
    builtins.derivation {
      name = "determinate-sandbox-" + nonce;
      system = "${pkgs.stdenv.hostPlatform.system}";
      builder = (builtins.storePath "${pkgs.bash}") + "/bin/bash";
      args = [ "-e" (builtins.storePath "${builder}") ];
      declared = builtins.storePath "${declared}";
    }
  '';
in
pkgs.testers.runNixOSTest {
  name = "determinate-guest-daemon";
  # runNixOSTest already injects this exact package set into node.pkgs.
  globalTimeout = 600;
  nodes.machine = { lib, ... }: {
    imports = [ profile ];
    system.stateVersion = "26.05";
    nix = {
      settings = {
        substituters = lib.mkForce [ ];
        trusted-public-keys = lib.mkForce [ ];
        substitute = false;
      };
      registry = lib.mkForce { };
    };
    virtualisation = {
      memorySize = 2048;
      cores = 2;
      diskSize = 8192;
      restrictNetwork = true;
      useNixStoreImage = true;
      mountHostNixStore = false;
      writableStore = true;
      writableStoreUseTmpfs = false;
    };
    users.users.guest = {
      isNormalUser = true;
      uid = 1000;
    };
    environment.systemPackages = [ pkgs.python3 ];
    environment.etc."determinate-outside".text = "guest-only-positive-control\n";
    system.extraDependencies = [ expression ];
    systemd.services.outside-listener = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -m http.server 19877 --bind 127.0.0.1 --directory /run";
      serviceConfig.DynamicUser = true;
    };
  };
  testScript = ''
    import json
    import shlex

    machine.start(allow_reboot=True)
    machine.wait_for_unit("multi-user.target")
    machine.wait_for_unit("nix-daemon.socket")
    machine.wait_for_unit("determinate-nixd.socket")
    machine.wait_for_unit("outside-listener.service")

    def user(command):
        return "su - guest -c " + shlex.quote(command)

    with subtest("supported daemon and no-account configuration"):
        guest_info = json.loads(machine.succeed(user("${engine}/bin/nix store info --store daemon --json")))
        print("guest daemon handshake: " + json.dumps(guest_info, sort_keys=True))
        assert guest_info["trusted"] is False, guest_info
        root_info = json.loads(machine.succeed("${engine}/bin/nix store info --store daemon --json"))
        print("root daemon handshake: " + json.dumps(root_info, sort_keys=True))
        assert root_info["trusted"] is True, root_info
        machine.wait_for_unit("nix-daemon.service")
        machine.succeed("systemctl show nix-daemon -p ExecStart | grep -F '${nixd}/bin/determinate-nixd'")
        machine.succeed("systemctl show nix-daemon -p ExecStart | grep -F '${engine}/bin'")
        machine.succeed("systemctl show nix-daemon -p Environment | grep -F DETSYS_IDS_TELEMETRY=disabled")
        settings = json.loads(machine.succeed("cat /etc/determinate/config.json"))
        assert settings["garbageCollector"]["strategy"] == "disabled"
        assert settings["telemetry"]["sentry"]["endpoint"] is None
        assert settings["authentication"]["additionalNetrcSources"] == []
        machine.succeed(user("test -z \"$NIX_SENTRY_ENDPOINT\" && test \"$DETSYS_IDS_TELEMETRY\" = disabled"))
        machine.succeed("test ! -e /root/.netrc && test ! -e /home/guest/.netrc")
        effective_config = json.loads(machine.succeed(user("${engine}/bin/nix config show --json")))
        print("effective guest settings: " + json.dumps({name: effective_config[name]["value"] for name in ["substitute", "substituters", "trusted-substituters", "sandbox", "sandbox-fallback", "require-sigs", "trusted-users"]}, sort_keys=True))
        print("supplier key identifiers: " + json.dumps([key.split(":", 1)[0] for key in effective_config["trusted-public-keys"]["value"]]))
        print("generated guest nix.conf:\n" + machine.succeed("cat /etc/nix/nix.conf"))
        print("generated guest nix.custom.conf:\n" + machine.succeed("cat /etc/nix/nix.custom.conf"))
        assert effective_config["substitute"]["value"] is False
        assert effective_config["substituters"]["value"] == ["https://install.determinate.systems/"]
        assert set(effective_config["trusted-substituters"]["value"]) == {"https://cache.flakehub.com/", "https://install.determinate.systems/"}
        assert effective_config["sandbox"]["value"] is True
        assert effective_config["sandbox-fallback"]["value"] is False
        assert effective_config["require-sigs"]["value"] is True
        assert set(effective_config["trusted-users"]["value"]) == {"root"}

    with subtest("positive controls outside the build sandbox"):
        machine.succeed(user("grep -F guest-only-positive-control /etc/determinate-outside"))
        machine.succeed(user("${pkgs.python3}/bin/python3 -c 'import socket; socket.create_connection((\"127.0.0.1\", 19877), timeout=2).close()'"))

    outputs = []
    for nonce, options in [
        ("ordinary", ""),
        ("untrusted-overrides", "--option sandbox false --option require-sigs false --option trusted-users guest"),
    ]:
        with subtest("uncached untrusted build " + nonce):
            instantiate = "${engine}/bin/nix-instantiate ${expression} --argstr nonce " + nonce
            drv = machine.succeed(user(instantiate)).strip()
            payload = json.loads(machine.succeed(user("${engine}/bin/nix derivation show " + shlex.quote(drv))))
            derivations = payload.get("derivations", payload)
            assert len(derivations) == 1
            definition = next(iter(derivations.values()))
            sources = definition["inputs"]["srcs"] if "inputs" in definition else definition["inputSrcs"]
            sources = {path if path.startswith("/nix/store/") else "/nix/store/" + path for path in sources}
            assert sources == {"${pkgs.bash}", "${builder}", "${declared}"}, sources
            output = machine.succeed(user("${engine}/bin/nix-store --query --outputs " + shlex.quote(drv))).strip()
            machine.fail("test -e " + shlex.quote(output))
            build = "${engine}/bin/nix-build ${expression} --argstr nonce " + nonce + " --no-out-link " + options
            build_status, build_output = machine.execute(user(build + " > /home/guest/" + nonce + ".out 2> /home/guest/" + nonce + ".log"))
            build_stdout = machine.succeed("cat /home/guest/" + nonce + ".out")
            build_stderr = machine.succeed("cat /home/guest/" + nonce + ".log")
            print("build " + nonce + " exit status: " + str(build_status))
            print("build " + nonce + " command output:\n" + build_output)
            print("build " + nonce + " stdout:\n" + build_stdout)
            print("build " + nonce + " stderr:\n" + build_stderr)
            assert build_status == 0, (nonce, build_status, build_output, build_stderr)
            actual = machine.succeed("cat /home/guest/" + nonce + ".out").strip()
            assert actual == output, (actual, output)
            value = machine.succeed("cat " + shlex.quote(output + "/value"))
            assert value == "declared-input\n", value
            namespace_uid = int(machine.succeed("cat " + shlex.quote(output + "/worker-uid")).strip())
            raw_uid_map = machine.succeed("cat " + shlex.quote(output + "/worker-uid-map"))
            print("build " + nonce + " namespace uid: " + str(namespace_uid))
            print("build " + nonce + " uid map:\n" + raw_uid_map)
            assert namespace_uid != 0, namespace_uid
            uid_map = [list(map(int, line.split())) for line in raw_uid_map.splitlines()]
            assert uid_map and all(len(row) == 3 and min(row) >= 0 and row[2] > 0 for row in uid_map), uid_map
            matching = [row for row in uid_map if row[0] <= namespace_uid < row[0] + row[2]]
            assert len(matching) == 1, (namespace_uid, uid_map)
            inside, outside, _count = matching[0]
            guest_uid = outside + namespace_uid - inside
            print("build " + nonce + " mapped guest uid: " + str(guest_uid))
            assert guest_uid not in (0, 1000), (namespace_uid, guest_uid, uid_map)
            account = machine.succeed("getent passwd " + str(guest_uid)).strip().split(":")
            assert len(account) == 7 and account[0].startswith("nixbld") and int(account[2]) == guest_uid, account
            if options:
                for setting in ("sandbox", "require-sigs"):
                    assert "ignoring the client-specified setting '" + setting + "'" in build_stderr, build_stderr
                override_info = json.loads(machine.succeed(user("${engine}/bin/nix store info --store daemon --json --option trusted-users guest")))
                print("guest trust-override daemon handshake: " + json.dumps(override_info, sort_keys=True))
                assert override_info["trusted"] is False, override_info
            machine.succeed("ln -s " + shlex.quote(output) + " /nix/var/nix/gcroots/" + nonce)
            outputs.append(output)

    with subtest("daemon restart preserves registered outputs"):
        machine.succeed("systemctl restart nix-daemon.service")
        machine.wait_for_unit("nix-daemon.service")
        for output in outputs:
            machine.succeed(user("${engine}/bin/nix-store --verify-path " + shlex.quote(output)))
            machine.succeed(user("${engine}/bin/nix path-info --store daemon " + shlex.quote(output)))
    with subtest("guest reboot preserves its own writable store"):
        machine.succeed("findmnt -T /nix/.rw-store -n -o FSTYPE | grep -v -E 'tmpfs|9p'")
        machine.succeed("test -d /nix/var/nix/db && test -d /nix/var/nix/gcroots")
        machine.succeed("sync")
        machine.reboot()
        machine.wait_for_unit("multi-user.target")
        machine.succeed(user("${engine}/bin/nix store info --store daemon --json"))
        for output in outputs:
            machine.succeed(user("${engine}/bin/nix-store --verify-path " + shlex.quote(output)))
            machine.succeed("test -L /nix/var/nix/gcroots/" + output.split("determinate-sandbox-", 1)[1])
            value = machine.succeed("cat " + shlex.quote(output + "/value"))
            assert value == "declared-input\n", (output, value)
    machine.shutdown()
  '';
}
