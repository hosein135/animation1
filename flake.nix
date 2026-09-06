{
  description = "GPU/CPU-accelerated pelican bicycle animation (Blender + FFmpeg), nixos-25.05";

  nixConfig = {
    extra-substituters = [ "https://cache.nixos.org" ];
    extra-trusted-public-keys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
    ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };

          projectSrc = pkgs.runCommand "animation-src" { } ''
            mkdir -p $out/scripts $out/data
            cp -r ${./scripts}/* $out/scripts/
            cp -r ${./data}/* $out/data/
          '';

          animateScript = pkgs.writeShellApplication {
            name = "animate";
            runtimeInputs = [
              pkgs.python3
              pkgs.blender
              pkgs.ffmpeg-full
              pkgs.coreutils
              pkgs.findutils
            ];
            text = ''
              set -euo pipefail

              if [[ -f "./scripts/pipeline.py" && -d "./data" ]]; then
                PROJECT="$(pwd)"
                DATA_DIR="$PROJECT/data"
                echo "==> Using project data from: $DATA_DIR"
              else
                PROJECT="${projectSrc}"
                DATA_DIR="$PROJECT/data"
                echo "==> Using packaged data from Nix store"
              fi

              OUTPUT_DIR="$(pwd)/output"
              export PROJECT_ROOT="$PROJECT"
              export DATA_DIR
              export OUTPUT_DIR
              mkdir -p "$OUTPUT_DIR/frames"

              # e.g. --renderer blender --engine eevee|cycles --workers 4
              python3 "$PROJECT/scripts/pipeline.py" "$@"
            '';
          };
        in
        {
          default = animateScript;
          animate = animateScript;
        }
      );

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.animate}/bin/animate";
        };
        animate = {
          type = "app";
          program = "${self.packages.${system}.animate}/bin/animate";
        };
      });

      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python3
              pkgs.blender
              pkgs.ffmpeg-full
              pkgs.curl
            ];
            shellHook = ''
              echo "devShell (nixos-25.05): python3, blender, ffmpeg-full"
              echo "Run: ./run.sh   or   nix run .#animate -- --renderer blender"
            '';
          };
        }
      );
    };
}
