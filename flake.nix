{
  description = "benchctl — host orchestrator for unattended kernel iteration on a Pixel (felix/gs201)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python312;

        # benchctl has zero runtime dependencies (stdlib only), so the package
        # build only needs the uv build backend.
        benchctl = python.pkgs.buildPythonApplication {
          pname = "benchctl";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ python.pkgs.uv-build ];
          # Smoke-test the entry point at build time.
          pythonImportsCheck = [ "benchctl" "benchctl.cli" ];
          meta = {
            description = "Unattended flash/boot/UART-capture/recover loop for felix kernel iteration";
            mainProgram = "benchctl";
          };
        };
      in
      {
        packages.default = benchctl;
        packages.benchctl = benchctl;

        apps.default = {
          type = "app";
          program = "${benchctl}/bin/benchctl";
        };

        devShells.default = pkgs.mkShell {
          packages = [ pkgs.uv python pkgs.ruff ];
          shellHook = ''
            echo "benchctl dev shell — 'uv sync' then 'uv run pytest'"
          '';
        };
      });
}
