# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""All the action we need during build"""
import json
import os
import pathlib
import re
import urllib.request as url_lib
from typing import List

import nox


def _install_bundle(session: nox.Session) -> None:
    session.install(
        "-t",
        "./bundled/libs",
        "--no-cache-dir",
        "--implementation",
        "py",
        "--no-deps",
        "--upgrade",
        "-r",
        "./requirements.txt",
    )


def _check_files(names: List[str]) -> None:
    root_dir = pathlib.Path(__file__).parent
    for name in names:
        file_path = root_dir / name
        lines: List[str] = file_path.read_text().splitlines()
        if any(line for line in lines if line.startswith("# TODO:")):
            raise Exception(f"Please update {os.fspath(file_path)}.")


def _update_pip_packages(session: nox.Session) -> None:
    session.run("pip-compile", "--generate-hashes", "--upgrade", "./requirements.in")
    session.run(
        "pip-compile",
        "--generate-hashes",
        "--upgrade",
        "./src/test/python_tests/requirements.in",
    )


def _get_package_data(package):
    json_uri = f"https://registry.npmjs.org/{package}"
    with url_lib.urlopen(json_uri) as response:
        return json.loads(response.read())


def _update_npm_packages(session: nox.Session) -> None:
    pinned = {
        "vscode-languageclient",
        "@types/vscode",
        "@types/node",
    }
    package_json_path = pathlib.Path(__file__).parent / "package.json"
    package_json = json.loads(package_json_path.read_text(encoding="utf-8"))

    for package in package_json["dependencies"]:
        if package not in pinned:
            data = _get_package_data(package)
            latest = "^" + data["dist-tags"]["latest"]
            package_json["dependencies"][package] = latest

    for package in package_json["devDependencies"]:
        if package not in pinned:
            data = _get_package_data(package)
            latest = "^" + data["dist-tags"]["latest"]
            package_json["devDependencies"][package] = latest

    # Ensure engine matches the package
    if (
        package_json["engines"]["vscode"]
        != package_json["devDependencies"]["@types/vscode"]
    ):
        print(
            "Please check VS Code engine version and @types/vscode version in package.json."
        )

    new_package_json = json.dumps(package_json, indent=4)
    # JSON dumps uses \n for line ending on all platforms by default
    if not new_package_json.endswith("\n"):
        new_package_json += "\n"
    package_json_path.write_text(new_package_json, encoding="utf-8")

    session.run("npm", "audit", "fix", external=True)
    session.run("npm", "install", external=True)


def _setup_template_environment(session: nox.Session) -> None:
    session.install("wheel", "pip-tools")
    _update_pip_packages(session)
    _install_bundle(session)


@nox.session(python="3.7")
def install_bundled_libs(session):
    """Installs the libraries that will be bundled with the extension."""
    session.install("wheel")
    _install_bundle(session)


@nox.session(python="3.7")
def setup(session: nox.Session) -> None:
    """Sets up the extension for development."""
    _setup_template_environment(session)


@nox.session()
def tests(session: nox.Session) -> None:
    """Runs all the tests for the extension."""
    session.install("-r", "src/test/python_tests/requirements.txt")
    session.run("pytest", "src/test/python_tests")

    session.install("freezegun")
    session.run("pytest", "build")


@nox.session()
def lint(session: nox.Session) -> None:
    """Runs linter and formatter checks on python files."""
    session.install("-r", "./requirements.txt")
    session.install("-r", "src/test/python_tests/requirements.txt")

    session.install("flake8")
    session.run("flake8", "./bundled/tool")
    session.run(
        "flake8",
        "--extend-exclude",
        "./src/test/python_tests/test_data",
        "./src/test/python_tests",
    )
    session.run("flake8", "noxfile.py")

    # check formatting using black
    session.install("black")
    session.run("black", "--check", "./bundled/tool")
    session.run("black", "--check", "./src/test/python_tests")
    session.run("black", "--check", "noxfile.py")

    # check import sorting using isort
    session.install("isort")
    session.run("isort", "--check", "./bundled/tool")
    session.run("isort", "--check", "./src/test/python_tests")
    session.run("isort", "--check", "noxfile.py")

    # check typescript code
    session.run("npm", "run", "lint", external=True)


@nox.session()
def build_package(session: nox.Session) -> None:
    """Builds VSIX package for publishing."""
    _check_files(["README.md", "LICENSE", "SECURITY.md", "SUPPORT.md"])
    _setup_template_environment(session)
    session.run("npm", "install", external=True)
    session.run("npm", "run", "vsce-package", external=True)


@nox.session()
def update_build_number(session: nox.Session) -> None:
    """Updates build number for the extension."""
    if len(session.posargs) == 0:
        session.log("No updates to package version")
        return

    package_json_path = pathlib.Path(__file__).parent / "package.json"
    session.log(f"Reading package.json at: {package_json_path}")

    package_json = json.loads(package_json_path.read_text(encoding="utf-8"))

    parts = re.split("\\.|-", package_json["version"])
    major, minor = parts[:2]

    version = f"{major}.{minor}.{session.posargs[0]}"
    version = version if len(parts) == 3 else f"{version}-{''.join(parts[3:])}"

    session.log(f"Updating version from {package_json['version']} to {version}")
    package_json["version"] = version
    package_json_path.write_text(json.dumps(package_json, indent=4), encoding="utf-8")


def _get_module_name() -> str:
    package_json_path = pathlib.Path(__file__).parent / "package.json"
    package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
    return package_json["serverInfo"]["module"]


@nox.session()
def validate_readme(session: nox.Session) -> None:
    """Ensures the linter version in 'requirements.txt' matches 'readme.md'."""
    requirements_file = pathlib.Path(__file__).parent / "requirements.txt"
    readme_file = pathlib.Path(__file__).parent / "README.md"

    lines = requirements_file.read_text(encoding="utf-8").splitlines(keepends=False)
    module = _get_module_name()
    linter_ver = [line for line in lines if line.startswith(module)][0]
    name, version = linter_ver.split(" ")[0].split("==")

    session.log(f"Looking for {name}={version} in README.md")
    content = readme_file.read_text(encoding="utf-8")
    if f"{name}={version}" not in content:
        raise ValueError(f"Linter info {name}={version} was not found in README.md.")
    session.log(f"FOUND {name}={version} in README.md")


def _update_readme() -> None:
    requirements_file = pathlib.Path(__file__).parent / "requirements.txt"
    lines = requirements_file.read_text(encoding="utf-8").splitlines(keepends=False)
    module = _get_module_name()
    linter_ver = [line for line in lines if line.startswith(module)][0]
    _, version = linter_ver.split(" ")[0].split("==")

    readme_file = pathlib.Path(__file__).parent / "README.md"
    content = readme_file.read_text(encoding="utf-8")
    regex = r"\`([a-zA-Z0-9]+)=([0-9]+\.[0-9]+\.[0-9]+)\`"
    result = re.sub(regex, f"`{module}={version}`", content, 0, re.MULTILINE)
    content = readme_file.write_text(result, encoding="utf-8")


@nox.session()
def update_packages(session: nox.Session) -> None:
    """Update pip and npm packages."""
    session.install("wheel", "pip-tools")
    _update_pip_packages(session)
    _update_npm_packages(session)
    _update_readme()
