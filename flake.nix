{
  description = "Short data-driven Blender animation (Python + FFmpeg), pinned to nixos-25.05";

  nixConfig = {
    # Prefer Hydra/cache.nixos.org binaries over compiling from source.
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

          pythonEnv = pkgs.python3;

          # Ship scripts + sample data inside the derivation for reproducible runs.
          projectSrc = pkgs.runCommand "animation-src" { } ''
            mkdir -p $out/scripts $out/data
            cp -r ${./scripts}/* $out/scripts/
            cp -r ${./data}/* $out/data/
          '';

          animateScript = pkgs.writeShellApplication {
            name = "animate";
            runtimeInputs = [
              pythonEnv
              pkgs.blender
              pkgs.ffmpeg
              pkgs.coreutils
              pkgs.findutils
            ];
            text = ''
              set -euo pipefail

              # Prefer live project tree (editable data/) when present; else use packaged copy.
              if [[ -f "./scripts/render_animation.py" && -d "./data" ]]; then
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

              echo "==> Validating structured data..."
              python3 "$PROJECT/scripts/validate_data.py"

              echo "==> Rendering frames with Blender (headless)..."
              blender --background --python "$PROJECT/scripts/render_animation.py" -- \
                --data-dir "$DATA_DIR" \
                --output-dir "$OUTPUT_DIR"

              echo "==> Encoding video with FFmpeg..."
              python3 "$PROJECT/scripts/encode_video.py" \
                --frames-dir "$OUTPUT_DIR/frames" \
                --output "$OUTPUT_DIR/animation.mp4" \
                --scene "$DATA_DIR/scene.json"

              echo "==> Animation ready: $OUTPUT_DIR/animation.mp4"
              ls -la "$OUTPUT_DIR" || true
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
              pkgs.ffmpeg
              pkgs.curl
            ];
            shellHook = ''
              echo "devShell ready (nixos-25.05): python3, blender, ffmpeg"
              echo "Run: ./run.sh   or   nix run .#animate"
            '';
          };
        }
      );
    };
}
