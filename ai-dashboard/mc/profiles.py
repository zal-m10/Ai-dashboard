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


def profile_dir(name):
    """Resolve nama profile ke direktorinya.

    Profile 'default' itu implisit di Hermes dan tinggal di HERMES_HOME
    itu sendiri; profile lain di HERMES_HOME/profiles/<nama>.
    """
    if name == "default":
        return hermes_home()
    return os.path.join(profiles_dir(), name)


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
    out = []
    # 'default' selalu ada (implisit di Hermes) — tinggal di HERMES_HOME.
    hh = hermes_home()
    if os.path.isdir(hh):
        out.append({
            "name": "default",
            "model": read_model(hh),
            "has_soul": os.path.isfile(os.path.join(hh, "SOUL.md")),
            "is_default": True,
        })
    base = profiles_dir()
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if name == "default":
            continue
        pdir = os.path.join(base, name)
        if not os.path.isdir(pdir):
            continue
        out.append({
            "name": name,
            "model": read_model(pdir),
            "has_soul": os.path.isfile(os.path.join(pdir, "SOUL.md")),
            "is_default": False,
        })
    return out


def profile_exists(name):
    if name == "default":
        return os.path.isdir(hermes_home())
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
    validate_name(name)  # 'default' ditolak di sini — tidak bisa dihapus
    pdir = profile_dir(name)
    if not os.path.isdir(pdir):
        raise ValueError(f"profile '{name}' tidak ada")
    shutil.rmtree(pdir)


def get_soul(name):
    p = os.path.join(profile_dir(name), "SOUL.md")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def set_soul(name, text):
    if name != "default" and not profile_exists(name):
        raise ValueError(f"profile '{name}' tidak ada")
    if not isinstance(text, str) or len(text) > 200_000:
        raise ValueError("SOUL.md tidak valid / terlalu besar")
    with open(os.path.join(profile_dir(name), "SOUL.md"), "w", encoding="utf-8") as f:
        f.write(text)


def set_model(name, model):
    if name != "default" and not profile_exists(name):
        raise ValueError(f"profile '{name}' tidak ada")
    if not isinstance(model, str) or not re.match(r"^[\w][\w\-./:]{0,80}$", model):
        raise ValueError("nama model tidak valid")
    write_model(profile_dir(name), model)
