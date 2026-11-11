class Adc < Formula
  include Language::Python::Virtualenv

  desc "CLI for Margelo Cloud Devices"
  homepage "https://platform.margelo.com"
  url "https://github.com/margelo/adc/releases/download/v0.2.0/agent-device-cloud-cli-0.2.0.tar.gz"
  sha256 "2b36ef60ea506b0258c75746289d208640c54e9cc5f24d3a2000daa2a379a8b9"
  version "0.2.0"

  depends_on "python@3.14"

  resource "anyio" do
    url "https://files.pythonhosted.org/packages/a9/16/7757fb84c819accc7d7e9af5291b261e4abb8627ee040f99e7d35ef2457a/anyio-4.16.0-py3-none-any.whl"
    sha256 "c66ae634e0b8228396b801741beb3294326d4b6102f7bba4041c0dfb58c29026"
  end

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/32/d1/a3652fe2c19134d0d15922177821d863cbd7e842d53a21bbe7e8e3a1b5ac/certifi-2026.10.23-py3-none-any.whl"
    sha256 "e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1"
  end

  resource "h11" do
    url "https://files.pythonhosted.org/packages/04/4b/29cac41a4d98d144bf5f6d33995617b185d14b22401f75ca86f384e87ff1/h11-0.16.0-py3-none-any.whl"
    sha256 "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86"
  end

  resource "httpcore" do
    url "https://files.pythonhosted.org/packages/7e/f5/f66802a942d491edb555dd61e3a9961140fd64c90bce1eafd741609d334d/httpcore-1.0.9-py3-none-any.whl"
    sha256 "2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/2a/39/e50c7c3a983047577ee07d2a9e53faf5a69493943ec3f6a384bdc792deb2/httpx-0.28.1-py3-none-any.whl"
    sha256 "d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad"
  end

  resource "idna" do
    url "https://files.pythonhosted.org/packages/58/a2/bb081bab032533a855d44de1d56f8e8426114ff1ba5d1f07a438a0a654f8/idna-3.20-py3-none-any.whl"
    sha256 "ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c"
  end

  resource "setuptools" do
    url "https://files.pythonhosted.org/packages/9d/76/f789f7a86709c6b087c5a2f52f911838cad707cc613162401badc665acfe/setuptools-82.0.1-py3-none-any.whl"
    sha256 "a59e362652f08dcd477c78bb6e7bd9d80a7995bc73ce773050228a348ce2e5bb"
  end

  resource "typing_extensions" do
    url "https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl"
    sha256 "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8"
  end

  resource "websockets" do
    url "https://files.pythonhosted.org/packages/eb/27/9db924cb0dbf22eade78d98fbae77790f9bf6bc729e6ab29aef9d68e4156/websockets-17.4-py3-none-any.whl"
    sha256 "c6bd2c9781915592b209160d0f017d4c7c82ad53075e6b0c24d2a89299e7f9e9"
  end

  def install
    ENV["PIP_NO_INDEX"] = "1"
    python = Formula["python@3.14"].opt_bin/"python3.14"
    venv = virtualenv_create(libexec, python, system_site_packages: false)
    venv.pip_install resources, build_isolation: false
    venv.pip_install buildpath, build_isolation: false
    bin.install_symlink libexec/"bin/adc"
  end

  def caveats
    <<~EOS
      For commands that connect to devices with adb, install Android Platform Tools:
        brew install --cask android-platform-tools
    EOS
  end

  test do
    assert_match "On-demand Android devices", shell_output("#{bin}/adc --help")
    assert_match "--switch", shell_output("#{bin}/adc login --help")
    refute_predicate bin/"adc-mcp", :exist?
  end
end
