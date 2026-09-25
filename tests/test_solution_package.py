"""Regression for the enclosing-folder ZIP that produced an empty install preview."""
import importlib.util
import io
from pathlib import Path
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_solution", ROOT / "scripts/package_solution.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_actual_git_package_has_installable_root(self):
        data = package.build_package(ROOT)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIn("bifrost.solution.yaml", archive.namelist())
            self.assertIn("functions/group_references.py", archive.namelist())

    def test_enclosing_folder_is_rejected_before_delivery(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name in package.REQUIRED:
                archive.writestr("bifrost-solutions/" + name, "placeholder")
        with self.assertRaisesRegex(ValueError, "at archive root"):
            package.validate_archive(data.getvalue())


if __name__ == "__main__":
    unittest.main()
