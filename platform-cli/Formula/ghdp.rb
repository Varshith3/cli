class Ghdp < Formula
  desc "Guardant Dev Platform CLI (GHDP)"
  homepage "https://example.local/ghdp"
  version "0.0.1"

  url "file:////Users/mshyam/Downloads/GithubRepos/dp-tools-local-setup/platform-cli/artifacts/ghdp-0.0.1-darwin-arm64.tar.gz"
  # url "https://github.com/gh-org-data-platform/dp-tools-local-setup/raw/refs/heads/feature/EPPE-6092-ENHANCEMENT-cli-v0.1-dryrun/platform-cli/artifacts/ghdp-0.0.1-darwin-arm64.tar.gz"
  sha256 "2b332f7b90b158b42eca8071b0dce80e43a1e844a5c825948221a18763f26f46"

  def install
    bin.install "ghdp"
  end

  test do
    system "#{bin}/ghdp", "ghdp"
  end
end
