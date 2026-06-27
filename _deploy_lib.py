# Databricks notebook source
# MAGIC %md
# MAGIC # Shared bootstrap — `deploy_workshop` / `teardown_workshop`
# MAGIC Pulled into both runner notebooks with `# MAGIC %run ./_deploy_lib`, so the CLI bootstrap and
# MAGIC auth are written **once** (the same idea as each chapter's `%run ./src/common`).
# MAGIC
# MAGIC ### Why a notebook drives the DAB at all
# MAGIC The "Deploy" button in the workspace only *deploys* a bundle — it can't **run** its jobs/apps,
# MAGIC and this workshop needs both (deploy the bundle, then run the build job, start the app, …). The
# MAGIC supported no-laptop way to run `databricks bundle` is the **web terminal**; this notebook is just
# MAGIC a friendlier, one-button wrapper around the exact same CLI commands.
# MAGIC
# MAGIC ### Why we *download* the CLI instead of using the one already on the box
# MAGIC Serverless notebook compute ships a `/usr/local/bin/databricks` that is **deliberately guarded** —
# MAGIC it refuses to run outside the web terminal ("only supported for interactive use from the web
# MAGIC terminal on x86 compute"). The plain **public** CLI from GitHub has no such guard and runs fine
# MAGIC here, so we install that to `~/bin` and always call it **by absolute path** (calling bare
# MAGIC `databricks` would resolve to the guarded one on `PATH`).

# COMMAND ----------
import os, re, subprocess, urllib.request  # noqa: F401

INSTALL_SH = "https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh"


def ensure_cli():
    """Install the public Databricks CLI to ~/bin (once) and return its absolute path.

    Returns the path to a CLI binary that is NOT the guarded /usr/local/bin one. Raises with the
    installer output if the download/install fails (e.g. egress to GitHub is blocked — in that case
    use the web terminal's pre-authenticated CLI instead).
    """
    target = os.path.expanduser("~/bin/databricks")
    if os.path.exists(target):
        return target
    subprocess.run(f"curl -fsSL --max-time 60 {INSTALL_SH} -o /tmp/install_cli.sh",
                   shell=True, check=True)
    out = subprocess.run("sh /tmp/install_cli.sh", shell=True, capture_output=True, text=True)
    blob = (out.stdout or "") + (out.stderr or "")
    # The installer prints "Installed Databricks CLI vX.Y.Z at <path>." — trust that path.
    m = re.search(r"at (\S+/databricks)", blob)
    if m and os.path.exists(m.group(1)):
        target = m.group(1)
    if not os.path.exists(target):
        raise RuntimeError("Databricks CLI install failed. Installer output:\n" + blob +
                           "\n\nIf GitHub egress is blocked, deploy from the web terminal instead.")
    ver = subprocess.run(f"'{target}' version", shell=True, capture_output=True, text=True)
    print(f"Databricks CLI ready: {target}  ({(ver.stdout or '').strip()})")
    return target


def auth_env():
    """Env vars that authenticate the CLI as *you* (the notebook user), via the notebook context token.

    No profile, no PAT to manage — the token is the same identity running this notebook, so the bundle
    deploys under your name and the masks/grants apply to you.
    """
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()  # noqa: F821
    host = "https://" + spark.conf.get("spark.databricks.workspaceUrl")       # noqa: F821
    return dict(os.environ, DATABRICKS_HOST=host, DATABRICKS_TOKEN=ctx.apiToken().get())


def repo_root():
    """Absolute /Workspace path of the folder holding this notebook (the cloned repo root).

    Chapter folders (chapter-a-foundation/ …) are siblings of this notebook, so the runner cd's into
    `<repo_root>/chapter-*` to run `databricks bundle` there.
    """
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()  # noqa: F821
    return "/Workspace" + os.path.dirname(ctx.notebookPath().get())


def run_cli(cli, args, cwd, env):
    """Run one CLI command, streaming its output into the cell. Raises on non-zero exit.

    `args` is the list AFTER the binary, e.g. ["bundle", "deploy", "-t", "dev", "--var=catalog=foo"].
    """
    print(f"\n$ databricks {' '.join(args)}\n  (in {cwd})")
    p = subprocess.Popen([cli] + args, cwd=cwd, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        print(line, end="")
    p.wait()
    if p.returncode != 0:
        raise RuntimeError(f"`databricks {' '.join(args)}` failed (exit {p.returncode}) — see output above.")


def require(**values):
    """Fail fast with a clear message if any required widget value is blank."""
    missing = [k for k, v in values.items() if not (v or "").strip()]
    if missing:
        raise ValueError("Set these widget(s) at the top before running: " + ", ".join(missing))
