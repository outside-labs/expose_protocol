"""Build and exercise a fresh wheel outside the source checkout."""

from email import message_from_bytes
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from zipfile import ZipFile

REPOSITORY = Path(__file__).resolve().parents[1]


def run(argv, *, cwd, capture=False):
    result = subprocess.run([str(value) for value in argv], cwd=cwd, check=True,
                            text=True, capture_output=capture, timeout=90)
    return result.stdout.strip() if capture else None


def main():
    with tempfile.TemporaryDirectory(prefix="expose-protocol-distribution-") as directory:
        root = Path(directory).resolve()
        dist = root / "dist"
        run(["uv", "build", "--out-dir", dist], cwd=REPOSITORY)
        wheel, = dist.glob("*.whl")
        source, = dist.glob("*.tar.gz")
        with ZipFile(wheel) as archive:
            metadata_name, = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            metadata = message_from_bytes(archive.read(metadata_name))
            assert not metadata.get_all("Requires-Dist"), "unexpected runtime dependencies"
            assert metadata["Requires-Python"] == ">=3.14"
        with tarfile.open(source) as archive:
            assert any(name.endswith("/src/expose_protocol/__init__.py") for name in archive.getnames())
        environment = root / "environment"
        run(["uv", "venv", environment, "--python", sys.executable], cwd=root)
        executable_directory = environment / ("Scripts" if os.name == "nt" else "bin")
        python = executable_directory / ("python.exe" if os.name == "nt" else "python")
        command = executable_directory / ("expose-protocol.exe" if os.name == "nt" else "expose-protocol")
        run(["uv", "pip", "install", "--python", python, wheel], cwd=root)
        location = Path(run([python, "-I", "-c", "import expose_protocol; print(expose_protocol.__file__)"], cwd=root, capture=True))
        assert location.is_relative_to(environment), location
        run([command, "--help"], cwd=root, capture=True)
        run([python, "-I", "-m", "expose_protocol", "--help"], cwd=root, capture=True)
        script = root / "capture.py"
        script.write_text("import json, pathlib, sys\npathlib.Path('result.json').write_text(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
        config = root / "profile.toml"
        config.write_text('schema = 1\nscheme = "expose-smoke"\nroot = "."\n'
                          f'command = {json.dumps([str(python), "-I", str(script), "{value}"])}\n'
                          '[parameters.value]\ntype = "string"\n', encoding="utf-8")
        value = "café + O'Brien;$(touch SENTINEL)`echo bad` & *"
        url = run([command, "link", "--config", config, f"value={value}"], cwd=root, capture=True)
        plan = json.loads(run([python, "-I", "-m", "expose_protocol", "inspect", "--config", config, url], cwd=root, capture=True))
        assert plan["argv"][-1] == value
        run([command, "run", "--config", config, url, "--yes"], cwd=root)
        assert json.loads((root / "result.json").read_text()) == [value]
        assert not (root / "SENTINEL").exists()
        if sys.platform == "darwin":
            app = root / "Installed Wheel App.app"
            run([command, "build", "--config", config, "--output", app], cwd=root, capture=True)
            result = subprocess.run([python, "-I", str(app / "Contents/Resources/launcher.py"),
                                     "expose-smoke://open?value=%00"], cwd=root, text=True, capture_output=True)
            assert result.returncode == 2, result.stderr
        print("Distribution verified: wheel, source archive, isolated import, both entry points and literal argv execution.")


if __name__ == "__main__":
    main()
