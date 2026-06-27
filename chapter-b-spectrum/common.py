# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter B · common — shared helpers
# MAGIC Pulled into the Chapter B notebooks (`explore.py`, `triage-agent/deploy.py`) with `%run`, so the
# MAGIC per-participant setup is written once. (The deployed agent in `triage-agent/agent.py` does NOT use
# MAGIC this — it's a logged model and reads its config from `model_config` instead.)

# COMMAND ----------
import re


def derive_prefix(spark, override=""):
    """Per-participant schema prefix: your email local-part with non-alphanumerics turned into '_'.
        klevis.aliaj@databricks.com -> klevis_aliaj   (override wins if provided)."""
    if override:
        return override
    me = spark.sql("SELECT current_user()").collect()[0][0]
    return re.sub(r"[^a-zA-Z0-9]", "_", me.split("@")[0]).lower()


class WorkshopContext:
    """me, catalog, and the schema names (intel / risk / cs / tools), computed once."""

    def __init__(self, spark, catalog, prefix_override=""):
        self.me = spark.sql("SELECT current_user()").collect()[0][0]
        self.catalog = catalog
        self.prefix = derive_prefix(spark, prefix_override)
        self.intel = f"{self.prefix}_ti_intel"
        self.risk = f"{self.prefix}_ti_risk"
        self.cs = f"{self.prefix}_ti_cs"
        self.tools = f"{self.prefix}_ti_tools"

    def __repr__(self):
        return (f"WorkshopContext(me={self.me}, catalog={self.catalog}, prefix={self.prefix}, "
                f"schemas={self.intel}/{self.risk}/{self.cs}/{self.tools})")


def workshop_context(spark, catalog, prefix_override=""):
    return WorkshopContext(spark, catalog, prefix_override)


def notebook_dir(dbutils):
    """Absolute /Workspace path of the folder holding the current notebook — used so models-from-code
    logging can find a sibling file (e.g. agent.py) regardless of the working directory."""
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    return "/Workspace" + ctx.notebookPath().get().rsplit("/", 1)[0]
