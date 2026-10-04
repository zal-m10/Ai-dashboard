"""Profile = Hermes profile asli. Discovery dari filesystem, bukan database terpisah."""
import os
import re
import shutil
import subprocess

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$")


def hermes_home():
    return os.environ.get("HERMES_HOME", "/root/.hermes")


def profiles_dir():
    return os.path.join(hermes_home(), "profiles")


def validate_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError("nama profile harus huruf kecil/angka/strip, 2-32 karakter")
    if name == "default":
        raise ValueError("profile 'default' dikunci")
    return name


def read_model(pdir):
    """Ambil model.default dari config.yaml tanpa pyyaml."""
    cfg = os.path.join(pdir, "config.yaml")
    try:
        with open(cfg, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    in_model = False
    for line in lines:
        if re.match(r"^model\s*:\s*$", line):
            in_model = True
            continue
        if in_model:
            m = re.match(r"^\s+default\s*:\s*(\S+)", line)
            if m:
                return m.group(1)
            if re.match(r"^\S", line):
                break
    return None


def write_model(pdir, model):
    """Tulis ulang model.default di config.yaml (format dipertahankan)."""
    cfg = os.path.join(pdir, "config.yaml")
    with open(cfg, encoding="utf-8") as f:
        text = f.read()
    new_text, n = re.subn(r"(?m)^(\s+default\s*:\s*)\S+",
                          lambda m: m.group(1) + model, text, count=1)
    if n == 0:
        raise RuntimeError("tidak menemukan model.default di config.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(new_text)


def list_profiles():
    base = profiles_dir()
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        pdir = os.path.join(base, name)
        if not os.path.isdir(pdir):
            continue
        out.append({
            "name": name,
            "model": read_model(pdir),
            "has_soul": os.path.isfile(os.path.join(pdir, "SOUL.md")),
        })
    return out


def profile_exists(name):
    return os.path.isdir(os.path.join(profiles_dir(), name))


def create_profile(name, description=""):
    validate_name(name)
    if profile_exists(name):
        raise ValueError(f"profile '{name}' sudah ada")
    r = subprocess.run(["hermes", "profile", "create", name,
                        "--description", description or f"Profile {name}"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"hermes profile create gagal: {(r.stderr or r.stdout).strip()[:300]}")
    return name


def delete_profile(name):
    validate_name(name)
    pdir = os.path.join(profiles_dir(), name)
    if not os.path.isdir(pdir):
        raise ValueError(f"profile '{name}' tidak ada")
    shutil.rmtree(pdir)


def get_soul(name):
    p = os.path.join(profiles_dir(), name, "SOUL.md")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def set_soul(name, text):
    if not profile_exists(name):
        raise ValueError(f"profile '{name}' tidak ada")
    if not isinstance(text, str) or len(text) > 200_000:
        raise ValueError("SOUL.md tidak valid / terlalu besar")
    with open(os.path.join(profiles_dir(), name, "SOUL.md"), "w", encoding="utf-8") as f:
        f.write(text)


def set_model(name, model):
    if not profile_exists(name):
        raise ValueError(f"profile '{name}' tidak ada")
    if not isinstance(model, str) or not re.match(r"^[\w][\w\-./:]{0,80}$", model):
        raise ValueError("nama model tidak valid")
    write_model(os.path.join(profiles_dir(), name), model)
