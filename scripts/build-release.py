import argparse
import gzip
import io
import tarfile
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory


CLIENT_MODULES = (
    '__init__.py',
    'adb.py',
    'api.py',
    'cli.py',
    'connections.py',
    'credentials.py',
    'login.py',
    'relay.py',
)


def main():
    parser = argparse.ArgumentParser(description='Build the Cloud Devices CLI source archive.')
    parser.add_argument('output_directory', type=Path)
    args = parser.parse_args()

    script = Path(__file__)
    repo = script.resolve().parents[1]
    manifest = repo / 'pyproject.toml'
    config = manifest.read_text(encoding='utf-8')
    project = tomllib.loads(config)['project']
    prefix = f"{project['name']}-{project['version']}"
    files = {'pyproject.toml': manifest}
    for name in CLIENT_MODULES:
        path = f'clients/{name}'
        files[path] = repo / path

    args.output_directory.mkdir(parents=True, exist_ok=True)
    output = args.output_directory / f'{prefix}.tar.gz'
    with TemporaryDirectory(dir=args.output_directory) as directory:
        temporary = Path(directory) / output.name
        with (
            temporary.open('wb') as raw,
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive,
        ):
            for path, source in sorted(files.items()):
                data = source.read_bytes()
                entry = tarfile.TarInfo(f'{prefix}/{path}')
                entry.size = len(data)
                entry.mode = 0o644
                content = io.BytesIO(data)
                archive.addfile(entry, content)
        temporary.replace(output)

    print(output)


if __name__ == '__main__':
    main()
