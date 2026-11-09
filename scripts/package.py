"""Build dist/<author>-<name>-<version>.difypkg (+ SHA256SUMS.txt).

A .difypkg is a zip of the plugin directory, honoring .difyignore. This builds
it deterministically (sorted entries, fixed timestamps), checks that every
file the manifest and YAML point to is inside, and compiles every Python file.

    python scripts/package.py            # build
    python scripts/package.py --verify   # build, then install the package's
                                         # requirements into a fresh venv and
                                         # run both tools from the unpacked zip

--verify needs `uv` (or set UV=/path/to/uv) and network access to PyPI.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXED = (1980, 1, 1, 0, 0, 0)


def ignore_rules():
    rules = []
    for line in (ROOT / ".difyignore").read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rules.append(line)
    return rules


def ignored(rel: str, rules) -> bool:
    """gitignore-style: "dir/" matches a directory at any depth, "*.ext" or a
    bare name matches a file or directory at any depth, "/x" is anchored,
    "!x" re-includes. ``rel`` is a file path relative to the plugin root."""
    parts = rel.split("/")
    dirs = ["/".join(parts[: i + 1]) for i in range(len(parts) - 1)]
    hit = False
    for raw in rules:
        neg = raw.startswith("!")
        pat = raw[1:] if neg else raw
        dir_only = pat.endswith("/")
        anchored = pat.startswith("/")
        pat = pat.strip("/")
        targets = dirs if dir_only else dirs + [rel]
        for t in targets:
            if anchored or "/" in pat:
                ok = fnmatch.fnmatch(t, pat)
            else:
                ok = fnmatch.fnmatch(t.split("/")[-1], pat)
            if ok:
                hit = not neg
                break
    return hit


def collect():
    rules = ignore_rules()
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = os.path.relpath(dirpath, ROOT).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames if not ignored(rel_dir + d + "/x", rules) and d not in (".git",))
        for f in filenames:
            rel = rel_dir + f
            if not ignored(rel, rules) and f != ".DS_Store":
                files.append(rel)
    return sorted(files)


def check_references(files):
    present = set(files)
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text("utf-8"))
    need = {"manifest.yaml", "main.py", "requirements.txt", f"_assets/{manifest['icon']}", manifest.get("privacy", "PRIVACY.md")}
    for prov in manifest["plugins"].get("tools", []):
        need.add(prov)
        p = yaml.safe_load((ROOT / prov).read_text("utf-8"))
        need.add(p["extra"]["python"]["source"])
        for tool in p["tools"]:
            need.add(tool)
            t = yaml.safe_load((ROOT / tool).read_text("utf-8"))
            need.add(t["extra"]["python"]["source"])
    missing = sorted(need - present)
    if missing:
        sys.exit("package is missing: " + ", ".join(missing))
    for f in files:
        if f.endswith(".py"):
            compile((ROOT / f).read_text("utf-8"), f, "exec")  # syntax check, writes nothing
    return manifest


def build():
    files = collect()
    manifest = check_references(files)
    name = f"{manifest['author']}-{manifest['name']}-{manifest['version']}.difypkg"
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            info = zipfile.ZipInfo(f, FIXED)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, (ROOT / f).read_bytes())
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (dist / "SHA256SUMS.txt").write_text(f"{digest}  {name}\n", "utf-8")
    print(f"{name}  {len(files)} files  {out.stat().st_size / 1024:.1f} KiB\nsha256 {digest}")
    return out


VERIFY = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
reg = PluginRegistration(DifyPluginEnv())
def run(tool, params):
    inst = reg.get_tool_cls("dmn", tool).from_credentials({})
    return {m.message.variable_name: m.message.variable_value for m in inst._invoke(params) if str(getattr(m.type, "value", m.type)) == "variable"}
xml = open(os.environ["DMN_EXAMPLE"]).read()
ev = run("evaluate_decision_table", {"table": xml, "decision_id": "eligibility", "inputs": {"applicant": {"creditScore": 750, "dti": 0.3, "employment": "EMPLOYED"}}})
ck = run("check_decision_table", {"table": xml, "decision_id": "fees"})
assert ev["status"] == "MATCHED" and ev["outputs"]["decision"] == "APPROVE", ev
assert ck["valid"] is True, ck
print("verified from the unpacked package:", ev["status"], ev["outputs"], "check valid", ck["valid"])
"""


def verify(pkg: Path):
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        sys.exit("--verify needs uv (set UV=/path/to/uv)")
    with tempfile.TemporaryDirectory(prefix="dmn-verify-") as tmp:
        tmp = Path(tmp)
        app = tmp / "plugin"
        with zipfile.ZipFile(pkg) as z:
            z.extractall(app)
        subprocess.run([uv, "venv", "-q", "--python", "3.12", str(tmp / "venv")], check=True)
        py = tmp / "venv" / "bin" / "python"
        subprocess.run([uv, "pip", "install", "-q", "--python", str(py), "-r", str(app / "requirements.txt")], check=True)
        env = dict(os.environ, DMN_EXAMPLE=str(ROOT / "examples" / "loan-approval.dmn"), PYTHONWARNINGS="ignore")
        subprocess.run([str(py), "-c", VERIFY], cwd=app, check=True, env=env)


if __name__ == "__main__":
    pkg = build()
    if "--verify" in sys.argv:
        verify(pkg)
