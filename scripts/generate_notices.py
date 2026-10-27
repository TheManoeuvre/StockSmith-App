"""Regenerate frontend/src/assets/thirdPartyNotices.json — the list shown under Settings > About.

Collects the name, version and licence of everything that ships in the installer:

  * npm   - production packages recorded in frontend/package-lock.json
  * cargo - the Tauri shell's dependency graph for Windows (the only target we build)
  * pip   - the backend's non-dev packages, read from its synced virtualenv

Run it from the repo root after changing dependencies, and commit the result:

    python scripts/generate_notices.py

Needs cargo and uv on PATH. The output is a licence *list*, not the licence texts: it records
which licence each package is under and where to read it, which is what the About page shows.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "frontend" / "src" / "assets" / "thirdPartyNotices.json"

# Run inside the backend's own interpreter so the answer is what is actually installed.
_PIP_PROBE = r"""
import json
from importlib import metadata

def licence(meta):
    expr = meta.get("License-Expression")
    if expr:
        return expr
    classified = [
        c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    if classified:
        return " / ".join(c for c in classified if c != "OSI Approved")
    text = (meta.get("License") or "").strip()
    return text if text and len(text) <= 60 and "\n" not in text else "See package"

out = {}
for dist in metadata.distributions():
    name = dist.metadata["Name"]
    if name:
        out[name.lower()] = {"name": name, "version": dist.version, "license": licence(dist.metadata)}
print(json.dumps(sorted(out.values(), key=lambda p: p["name"].lower())))
"""


def npm_packages() -> list[dict]:
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    found = {}
    for path, info in lock["packages"].items():
        if not path or info.get("dev") or info.get("devOptional"):
            continue
        name = path.split("node_modules/")[-1]
        found[(name, info["version"])] = info.get("license") or "See package"
    return [{"name": n, "version": v, "license": lic} for (n, v), lic in sorted(found.items())]


def cargo_packages() -> list[dict]:
    result = subprocess.run(
        [
            "cargo", "metadata", "--format-version", "1", "--locked",
            "--filter-platform", "x86_64-pc-windows-msvc",
            "--manifest-path", str(ROOT / "frontend" / "src-tauri" / "Cargo.toml"),
        ],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    meta = json.loads(result.stdout)
    workspace = set(meta["workspace_members"])
    return [
        {"name": p["name"], "version": p["version"], "license": p.get("license") or "See package"}
        for p in sorted(meta["packages"], key=lambda p: (p["name"], p["version"]))
        if p["id"] not in workspace
    ]


def pip_packages() -> list[dict]:
    result = subprocess.run(
        ["uv", "run", "--no-dev", "--project", str(ROOT / "backend"), "python", "-I", "-c", _PIP_PROBE],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    ignore = {"stocksmith-backend", "pip", "setuptools", "wheel"}
    return [p for p in json.loads(result.stdout) if p["name"].lower() not in ignore]


def main() -> int:
    notices = {
        "Frontend (npm)": npm_packages(),
        "Desktop shell (Rust)": cargo_packages(),
        "Backend (Python)": pip_packages(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(notices, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for group, packages in notices.items():
        print(f"{group}: {len(packages)} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
