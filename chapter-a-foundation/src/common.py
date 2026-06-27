# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter A · common — shared helpers
# MAGIC The other notebooks pull these in with `# MAGIC %run ./common` so the per-participant setup
# MAGIC (your schema prefix, your schema names) is written **once**, not copy-pasted into every notebook.
# MAGIC `%run` executes this notebook in the caller's namespace, so every function below becomes
# MAGIC available to the notebook that ran it.

# COMMAND ----------
import re


def derive_prefix(spark, override=""):
    """Return the per-participant schema prefix.

    It's the local part of your email, with anything that isn't a letter or digit turned into '_':
        klevis.aliaj@databricks.com  ->  klevis_aliaj
        klevis@dropbox.com           ->  klevis
    Pass `override` (the `user_prefix` widget) to force a specific prefix instead.
    """
    if override:
        return override
    me = spark.sql("SELECT current_user()").collect()[0][0]
    local_part = me.split("@")[0]
    return re.sub(r"[^a-zA-Z0-9]", "_", local_part).lower()


class WorkshopContext:
    """The handful of names every Chapter A notebook needs, computed once.

    Attributes: me (your email), prefix, catalog, and the four schema names
    (intel / risk / cs / tools). `print(ctx)` shows them.
    """

    def __init__(self, spark, catalog, prefix_override=""):
        self.me = spark.sql("SELECT current_user()").collect()[0][0]
        self.catalog = catalog
        self.prefix = derive_prefix(spark, prefix_override)
        self.intel = f"{self.prefix}_ti_intel"   # threat-intel feeds, IOCs, investigations, incidents
        self.risk = f"{self.prefix}_ti_risk"     # account risk scoring + protective actions
        self.cs = f"{self.prefix}_ti_cs"         # Customer-Service-safe governed views
        self.tools = f"{self.prefix}_ti_tools"   # the UC-function tools (built in notebook 02)

    def __repr__(self):
        return (f"WorkshopContext(me={self.me}, catalog={self.catalog}, prefix={self.prefix}, "
                f"schemas={self.intel}/{self.risk}/{self.cs}/{self.tools})")


def workshop_context(spark, catalog, prefix_override=""):
    """Build the WorkshopContext for this participant (see the class docstring)."""
    return WorkshopContext(spark, catalog, prefix_override)
