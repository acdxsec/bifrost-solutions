"""Build an install ZIP from committed source, with manifests at archive root."""
import argparse
import io
from pathlib import Path
import subprocess
import zipfile


REQUIRED = {
    "bifrost.solution.yaml", ".bifrost/apps.yaml", ".bifrost/workflows.yaml",
    ".bifrost/tables.yaml", ".bifrost/connections.yaml",
}


def validate_archive(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        missing = REQUIRED - set(archive.namelist())
        if missing:
            raise ValueError("Install ZIP must have descriptor and manifests at archive root; missing: " + ", ".join(sorted(missing)))
        if archive.testzip():
            raise ValueError("Corrupt ZIP entry")


def build_package(repo: Path, ref: str = "HEAD") -> bytes:
    # Bifrost reads the extraction root directly. Never add git archive --prefix.
    data = subprocess.check_output(["git", "archive", "--format=zip", ref], cwd=repo)
    validate_archive(data)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ref", default="HEAD", help="Committed Git revision to package")
    args = parser.parse_args()
    args.output.write_bytes(build_package(Path(__file__).resolve().parents[1], args.ref))
    print(f"Created install package: {args.output}")
