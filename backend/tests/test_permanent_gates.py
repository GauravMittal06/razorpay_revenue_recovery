"""
Permanent gates -- re-checked every phase, not owned by any single one.

Source: "Permanent gates for every phase" in the acceptance document. These
are the invariants a later phase is most likely to break by accident, which
is exactly why they live in their own file and run on every invocation
rather than being folded into a phase-specific module.
"""

import ast
import hashlib
import importlib
import pkgutil
import re
import subprocess
from pathlib import Path

import pytest

from backend.tests.conftest import BACKEND_DIR, PROJECT_ROOT

# Modules intentionally excluded from the bulk-import check:
#   api.server prints and loads .env at import time (harmless, but noisy);
#   it is imported for real by the api_client fixture instead, which is
#   stronger evidence than a bare import anyway.
IMPORT_CHECK_SKIP = {"backend.api.server"}


def _first_party_modules() -> list[str]:
    mods = []
    for pkg in ("api", "data", "db", "engine", "llm", "ml"):
        pkg_path = BACKEND_DIR / pkg
        if not pkg_path.is_dir():
            continue
        mods.append(f"backend.{pkg}")
        for info in pkgutil.iter_modules([str(pkg_path)]):
            if not info.ispkg:
                mods.append(f"backend.{pkg}.{info.name}")
    return sorted(m for m in mods if m not in IMPORT_CHECK_SKIP)


# --------------------------------------------------------------------------
# Import hygiene
# --------------------------------------------------------------------------

@pytest.mark.gate("permanent.import_hygiene")
def test_no_syspath_manipulation_remains(source_files):
    """
    Phase 0 removed the `sys.path.append` hack from engine/*.py and unified
    on `backend.`-prefixed imports. Nothing may reintroduce it -- a single
    path hack makes the import root ambiguous again, which is how the
    original split arose.
    """
    offenders = []
    for path in source_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"sys\.path\s*\.\s*(append|insert|extend)", stripped):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {stripped}")
    assert not offenders, "sys.path manipulation reintroduced:\n" + "\n".join(offenders)


@pytest.mark.gate("permanent.import_hygiene")
@pytest.mark.parametrize("module_name", _first_party_modules())
def test_every_module_imports_under_package_convention(module_name):
    """Every module must import as `backend.<pkg>.<mod>` with no fixups."""
    importlib.import_module(module_name)


@pytest.mark.gate("permanent.import_hygiene")
def test_relative_or_bare_intra_project_imports_are_absent(source_files):
    """
    Guards the *other* half of the Phase 0 fix: a module importing
    `from engine.classify import ...` (treating backend/ as root) instead of
    `from backend.engine.classify import ...` would only work under one
    launch condition, which is the exact fragility that was removed.
    """
    bare_roots = ("api", "db", "engine", "llm", "ml")
    offenders = []
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    offenders.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: relative import")
                elif node.module and node.module.split(".")[0] in bare_roots:
                    offenders.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: "
                        f"bare `from {node.module} import ...`")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in bare_roots:
                        offenders.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: "
                            f"bare `import {alias.name}`")
    assert not offenders, "non-`backend.`-rooted intra-project imports:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------
# Credential hygiene -- re-checked every phase, not only at Phase 0
# --------------------------------------------------------------------------

# Provider key shapes. Kept narrow on purpose: a loose pattern that matches
# any long alphanumeric string would fire on model hashes and be silenced.
SECRET_PATTERNS = {
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{32,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "razorpay_key": re.compile(r"rzp_(live|test)_[A-Za-z0-9]{10,}"),
}


@pytest.mark.gate("permanent.credential_hygiene")
def test_no_secret_literal_in_tracked_source(source_files):
    """No credential value may appear in a .py file, only in .env."""
    offenders = []
    for path in source_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {name}")
    assert not offenders, "credential literal in source:\n" + "\n".join(offenders)


@pytest.mark.gate("permanent.credential_hygiene")
def test_env_is_gitignored():
    gitignore = BACKEND_DIR / ".gitignore"
    assert gitignore.exists(), "backend/.gitignore missing"
    patterns = {
        line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert any(p in patterns for p in (".env", "*.env", "backend/.env")), \
        f".env not ignored; .gitignore patterns = {sorted(patterns)}"


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(PROJECT_ROOT),
                          capture_output=True, text=True, timeout=30)


@pytest.mark.gate("permanent.credential_hygiene")
def test_env_is_not_tracked_in_the_git_index():
    """
    .gitignore only helps for a file that was never added. This asserts the
    stronger property: git is not currently tracking it.
    """
    try:
        probe = _git("rev-parse", "--is-inside-work-tree")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("git unavailable")
    if probe.returncode != 0:
        pytest.skip("not a git work tree")

    tracked = _git("ls-files", "--", "backend/.env", ".env").stdout.strip()
    assert not tracked, f"secret file tracked by git: {tracked!r}"


@pytest.mark.gate("permanent.credential_hygiene")
def test_exposed_key_history_exposure_is_documented():
    """
    Phase 0's [NEW] clause: confirm whether the exposed key ever reached
    version-control *history*, not just the working tree, and document it
    even if rewriting history is out of scope. This test asserts the
    documentation exists and names the outcome -- it cannot itself decide
    whether a rotation happened.
    """
    notes = BACKEND_DIR / "BOOTSTRAP_NOTES.md"
    assert notes.exists(), "BOOTSTRAP_NOTES.md missing"
    text = notes.read_text(encoding="utf-8").lower()
    assert "rotate" in text, "notes do not record the key-rotation decision"
    assert any(k in text for k in ("git history", "version-control history",
                                  "version control history", "commit history")), (
        "Phase 0 gate [NEW] requires an explicit statement of whether the "
        "exposed GEMINI_API_KEY reached git history, not just the working "
        "tree. BOOTSTRAP_NOTES.md documents rotation but is silent on "
        "history exposure."
    )


# --------------------------------------------------------------------------
# Silent failures / swallowed exceptions
# --------------------------------------------------------------------------

BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}

# Any call that makes a failure observable to a human.
SIGNALLING_CALL_NAMES = {
    "print", "log", "debug", "info", "warn", "warning", "error",
    "exception", "critical", "format_exc",
}


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:  # bare `except:`
        return True
    nodes = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    return any(isinstance(n, ast.Name) and n.id in BROAD_EXCEPTION_NAMES for n in nodes)


def _handler_signals(handler: ast.ExceptHandler) -> bool:
    """
    True if the handler leaves *some* trace of the failure: it re-raises,
    logs, or hands an error signal back to the caller -- either a status
    string literal (e.g. "persist_failed") or the caught exception itself.
    A handler that does none of these has destroyed the information.
    """
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name in SIGNALLING_CALL_NAMES:
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip():
            return True
        if handler.name and isinstance(node, ast.Name) and node.id == handler.name:
            return True
    return False


def _broad_handler_inventory(source_files) -> list[dict]:
    out = []
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not _is_broad(node):
                continue
            enclosing = [f for f in funcs
                         if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)]
            out.append({
                "file": path.relative_to(PROJECT_ROOT).as_posix(),
                "lineno": node.lineno,
                "function": (max(enclosing, key=lambda f: f.lineno).name
                             if enclosing else "<module>"),
                "bare": node.type is None,
                "silent": not _handler_signals(node),
            })
    return out


@pytest.mark.gate("permanent.no_silent_failures")
def test_no_bare_except_clause(source_files):
    """A bare `except:` also swallows KeyboardInterrupt and SystemExit."""
    bare = [f"{h['file']}:{h['lineno']} in {h['function']}()"
            for h in _broad_handler_inventory(source_files) if h["bare"]]
    assert not bare, "bare `except:` found:\n" + "\n".join(bare)


# The silent-swallow sites present at the Phase 1 hand-off. Listed so the
# failure message can distinguish a known finding from a new regression.
KNOWN_SILENT_SWALLOWS = {
    ("backend/engine/decide_action.py", "_load_ml_model"),
    ("backend/engine/decide_action.py", "_get_recovery_probability"),
}


@pytest.mark.gate("permanent.no_silent_failures")
def test_no_broad_handler_discards_the_failure_silently(source_files):
    """
    Permanent gate: no silent failures, no swallowed exceptions.

    EXPECTED TO FAIL against the Phase 1 hand-off, and left failing on
    purpose: `_load_ml_model` and `_get_recovery_probability` catch every
    exception and return None with no log line and no signal to the caller.

    The consequence is not cosmetic. A missing or corrupt xgb_model.joblib,
    a joblib/scikit-learn version mismatch, or a train/serve feature-contract
    break all degrade every decision to ml_recovery_probability=None while
    the batch run still reports success -- and Phase 4's ranking consumes
    that same value. Per the gate document this is NOT COMPLETE rather than
    something to relax the test for.
    """
    silent = [h for h in _broad_handler_inventory(source_files) if h["silent"]]
    detail = "\n".join(
        f"  {h['file']}:{h['lineno']} in {h['function']}()"
        f"  [{'known Phase 1 finding' if (h['file'], h['function']) in KNOWN_SILENT_SWALLOWS else 'NEW REGRESSION'}]"
        for h in silent
    )
    assert not silent, (
        f"{len(silent)} broad exception handler(s) neither log, re-raise, nor "
        f"return an error signal:\n{detail}"
    )


@pytest.mark.gate("permanent.no_silent_failures")
def test_no_new_silent_swallow_beyond_the_recorded_findings(source_files):
    """
    Companion to the test above: that one stays red until the two known sites
    are fixed, so on its own it cannot detect a *third* site being added.
    This one can -- it is the regression guard that must stay green.
    """
    new = [f"{h['file']}:{h['lineno']} in {h['function']}()"
           for h in _broad_handler_inventory(source_files)
           if h["silent"] and (h["file"], h["function"]) not in KNOWN_SILENT_SWALLOWS]
    assert not new, "newly introduced silent exception swallowing:\n" + "\n".join(new)


# --------------------------------------------------------------------------
# Authority boundary -- exactly one component may grant permission
# --------------------------------------------------------------------------

@pytest.mark.gate("permanent.single_authority")
def test_only_the_rule_engine_grants_allowed(source_files):
    """
    `allowed` is the permission bit. decide_action.py is the sole authority
    permitted to set it True. A second writer -- an optimizer, an API
    handler, an LLM adapter -- would mean a bounded action can be approved
    in two places, which is the exact failure the authority matrix exists to
    prevent, and it would be invisible in the audit trail.
    """
    offenders = []
    pattern = re.compile(r"""["']allowed["']\s*:\s*True|\ballowed\s*=\s*True""")
    for path in source_files:
        if path.name == "decide_action.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT).as_posix()}:{lineno}: {line.strip()}")
    assert not offenders, "`allowed: True` granted outside the rule engine:\n" + "\n".join(offenders)


@pytest.mark.gate("permanent.single_authority")
def test_method_change_has_no_reachable_executor_path(source_files):
    """
    method_change is deliberately outside the executable action set: no code
    path may select it, map it to a status, or execute it. A live branch
    would let the system mutate a customer's payment instrument -- an
    authority nobody granted it. Comments and docstrings are excluded,
    since the boundary is allowed to be *documented*, only not implemented.

    AMENDED 2026-09-04 (Phase 5, W7 / ruling A10). The match was a plain
    substring test, so it flagged the legitimate `"method_changed"` feature
    key -- the generator's flag for "this candidate carries a different
    payment method" -- in data_factory/ and ml/. 14 false-positive offenders,
    every one of them a feature name, none of them an action.

    Tightened to a word-boundary match. This is a correction of a broken
    matcher, NOT a loosened bar: `(?<![0-9A-Za-z_])method_change(?![0-9A-Za-z_])`
    still flags any real `method_change` token and now correctly ignores
    `method_changed` (trailing `d`) and `_is_method_change` (leading `_`).

    READ THIS BEFORE TRUSTING A PASS. Even tightened, this test proves very
    little, because **there is no `method_change` action type in this system
    at all** -- the token it searches for structurally cannot appear. A
    method change is `action_type="retry"` carrying a `method` different from
    the opportunity's current one, so the boundary is a property of the
    (action, method) pair and no string search can establish it. It is
    retained only as a cheap tripwire against someone introducing such a
    token. The boundary is verified for real by the behavioural and
    structural tests in tests/test_phase5_fallthrough.py, by
    phase5_config.METHOD_CHANGE_IS_EXECUTABLE's import-time raise, and by
    test_the_dispatcher_cannot_fire_a_method_change in
    tests/test_phase5_dispatch.py. See PHASE5_NOTES.md section 0.1.
    """
    token = re.compile(r"(?<![0-9A-Za-z_])method_change(?![0-9A-Za-z_])")
    offenders = []
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        docstrings = {id(ast.get_docstring(n, clean=False))
                      for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef,
                                        ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and token.search(node.value)
                    and id(node.value) not in docstrings):
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT).as_posix()}:{node.lineno}")
    assert not offenders, ("`method_change` referenced in executable code:\n"
                          + "\n".join(offenders))


@pytest.mark.gate("permanent.single_authority")
def test_executor_action_set_matches_the_decider():
    """
    The executor must be able to carry out exactly the actions the rule engine
    can select, and no others. An extra key in STATUS_MAP is an executable
    action with no compliance branch behind it.

    AMENDED 2026-09-02 (Phase 5, W5). The set was pinned to the four actions
    {retry, reminder, escalate, stop} that the pre-Phase-5 hardcoded lookup
    could produce. Phase 5 adds `payment_link`, which EXECUTION_PLAN.md:206
    names in the executable vocabulary verbatim -- "retry, reminder (with a
    channel attribute), payment link, escalate, stop" -- and which has been a
    first-class optimizer candidate since Phase 4 with its own cost term and
    eligibility rules. Before the amendment the optimizer's top-ranked pick
    could be structurally undispatchable.

    The bar was not loosened to accommodate the change: the assertion is now
    tied to phase5_config.EXECUTABLE_ACTIONS, the declared vocabulary, instead
    of to a second hardcoded literal. That is strictly stronger than what it
    replaced -- the executor and the declaration can no longer drift apart in
    either direction, and widening the vocabulary now requires a visible edit
    to a config file whose own tests assert it against EXECUTION_PLAN.md.
    """
    from backend.engine.execute_action import STATUS_MAP
    from backend.engine.phase5_config import EXECUTABLE_ACTIONS

    assert set(STATUS_MAP) == set(EXECUTABLE_ACTIONS), (
        f"executor action set {sorted(STATUS_MAP)} does not match the declared "
        f"executable vocabulary {sorted(EXECUTABLE_ACTIONS)}")


# --------------------------------------------------------------------------
# Optimizer authority boundary (added Phase 4)
#
# The single highest-severity boundary in the system, and the reason it lives
# here rather than in test_phase4_optimizer.py: the Phase 4 acceptance gate
# requires it be "verified by a static import/call-graph check, RE-RUN IN
# PHASE 9, not just asserted once here." A permanent gate re-runs on every
# invocation by construction.
#
# The optimizer ranks. It proposes. It writes one audit table. Anything that
# lets it reach execution authority -- an import, a name reference, or a
# write to a table that carries compliance or execution meaning -- is a
# failure, checked mechanically rather than by code-review convention.
# --------------------------------------------------------------------------

OPTIMIZER_MODULES = ("optimize.py", "optimizer_config.py", "intervention_cost.py")

# Names and modules that carry execution or compliance authority.
FORBIDDEN_AUTHORITY_NAMES = {
    "execute_action", "decide_action", "mark_opportunity_recovered",
    "mark_payment_recovered", "run_cycle", "trigger_event",
    "handle_customer_reply", "deliver_recovery_message",
}
FORBIDDEN_AUTHORITY_MODULES = {
    "backend.engine.execute_action", "engine.execute_action",
    "backend.engine.decide_action", "engine.decide_action",
    "backend.engine.core_loop", "engine.core_loop",
    "backend.engine.trigger_event", "engine.trigger_event",
    "backend.engine.mark_opportunity_recovered", "engine.mark_opportunity_recovered",
    "backend.api.actions", "api.actions",
}

# The optimizer may write exactly one table.
OPTIMIZER_WRITABLE_TABLES = {"recovery_candidates"}
WRITE_STATEMENT = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+[\"'`\[]?(\w+)",
    re.IGNORECASE)


def _optimizer_paths() -> list[Path]:
    return [BACKEND_DIR / "engine" / name for name in OPTIMIZER_MODULES]


@pytest.mark.gate("permanent.single_authority")
def test_optimizer_modules_exist_where_the_authority_check_expects_them():
    """If the optimizer is renamed or moved, the check below would silently
    scan nothing and pass. This is what stops that."""
    missing = [p.name for p in _optimizer_paths() if not p.exists()]
    assert not missing, f"optimizer modules not found: {missing}"


@pytest.mark.gate("permanent.single_authority")
def test_optimizer_imports_nothing_with_execution_authority():
    offenders = []
    for path in _optimizer_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_AUTHORITY_MODULES:
                        offenders.append(f"{rel}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in FORBIDDEN_AUTHORITY_MODULES:
                    offenders.append(f"{rel}:{node.lineno}: from {module}")
                for alias in node.names:
                    if alias.name in FORBIDDEN_AUTHORITY_NAMES:
                        offenders.append(
                            f"{rel}:{node.lineno}: imports {alias.name}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_AUTHORITY_NAMES:
                offenders.append(f"{rel}:{node.lineno}: references {node.id}")
            elif isinstance(node, ast.Attribute) and \
                    node.attr in FORBIDDEN_AUTHORITY_NAMES:
                offenders.append(f"{rel}:{node.lineno}: calls .{node.attr}")
    assert not offenders, (
        "the optimizer has a path to execution authority:\n" + "\n".join(offenders))


@pytest.mark.gate("permanent.single_authority")
def test_optimizer_writes_only_the_audit_table():
    """recovery_candidates is a proposal/audit table with no execution
    authority. A write to recovery_decisions, recovery_executions,
    opportunities, payments or experiment_assignment from here would mean the
    optimizer can move compliance, execution or business state."""
    offenders = []
    for path in _optimizer_paths():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            for _, table in WRITE_STATEMENT.findall(line):
                if table.lower() not in OPTIMIZER_WRITABLE_TABLES:
                    offenders.append(f"{rel}:{lineno}: writes {table}")
        # also catch a write statement split across lines in a constant
        for match in WRITE_STATEMENT.finditer(text):
            if match.group(2).lower() not in OPTIMIZER_WRITABLE_TABLES:
                offenders.append(f"{rel}: writes {match.group(2)}")
    assert not offenders, (
        "the optimizer writes outside its audit table:\n" + "\n".join(sorted(set(offenders))))


@pytest.mark.gate("permanent.single_authority")
def test_optimizer_never_grants_the_permission_bit():
    """Belt-and-braces alongside test_only_the_rule_engine_grants_allowed:
    that test scans every module, this one names the optimizer explicitly so
    the failure message points straight at the boundary that was crossed."""
    pattern = re.compile(r"""["']allowed["']\s*:\s*True|\ballowed\s*=\s*True""")
    offenders = []
    for path in _optimizer_paths():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT).as_posix()}:{lineno}")
    assert not offenders, (
        "the optimizer granted the `allowed` permission bit:\n" + "\n".join(offenders))


@pytest.mark.gate("permanent.single_authority")
def test_optimizer_does_not_open_a_second_scoring_path():
    """There is exactly one scoring path in the system: ml/inference.py. The
    optimizer must not load a model artifact or call a predictor directly,
    or training-time and serving-time behaviour could diverge again -- the
    precise failure Phase 3's single-module design exists to prevent."""
    forbidden = {"joblib", "predict", "predict_proba", "load_model",
                 "build_feature_row", "XGBClassifier", "XGBRegressor"}
    offenders = []
    for path in _optimizer_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                offenders.append(f"{rel}:{node.lineno}: .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in forbidden:
                offenders.append(f"{rel}:{node.lineno}: {node.id}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                for name in names:
                    if name.split(".")[0] in forbidden:
                        offenders.append(f"{rel}:{node.lineno}: imports {name}")
    assert not offenders, (
        "the optimizer opened a second scoring path:\n" + "\n".join(offenders))


# --------------------------------------------------------------------------
# Phase 6 module authority -- the experiment modules propose and record;
# they never act
# --------------------------------------------------------------------------
#
# EXECUTION_PLAN Phase 6 puts assign_experiment_group.py and observe_outcome.py
# under the same static-check discipline as optimize.py, so they are checked by
# the same machinery above rather than by a parallel set of rules that could
# drift from it.
#
# The two are bounded differently because they do different jobs:
#
#   * assign_experiment_group records which arm an opportunity is in. It has
#     no business touching any other table, and in particular must never write
#     an outcome -- an assigner that could resolve an opportunity could
#     manufacture the very result the experiment exists to measure.
#   * observe_outcome is the SOLE writer of the business-outcome fields, so it
#     must be able to write `opportunities`. It still holds no compliance or
#     execution authority: it records what happened, it never decides what may
#     happen next. It joins this table at X4, when it exists -- listing a
#     module here before it is written would make the existence check below
#     fail for the whole of X2 and X3, which is noise, not a gate.

PHASE6_WRITABLE_TABLES = {
    "assign_experiment_group.py": {"experiment_assignment"},
}


def _phase6_paths() -> list[Path]:
    return [BACKEND_DIR / "engine" / name for name in PHASE6_WRITABLE_TABLES]


@pytest.mark.gate("permanent.single_authority")
def test_phase6_modules_exist_where_the_authority_check_expects_them():
    """If either module is renamed or moved, the checks below would silently
    scan nothing and pass. This is what stops that."""
    missing = [p.name for p in _phase6_paths() if not p.exists()]
    assert not missing, f"Phase 6 modules not found: {missing}"


@pytest.mark.gate("permanent.single_authority")
def test_phase6_modules_import_nothing_with_execution_authority():
    offenders = []
    for path in _phase6_paths():
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_AUTHORITY_MODULES:
                        offenders.append(f"{rel}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in FORBIDDEN_AUTHORITY_MODULES:
                    offenders.append(f"{rel}:{node.lineno}: from {module}")
                for alias in node.names:
                    if alias.name in FORBIDDEN_AUTHORITY_NAMES:
                        offenders.append(
                            f"{rel}:{node.lineno}: imports {alias.name}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_AUTHORITY_NAMES:
                offenders.append(f"{rel}:{node.lineno}: references {node.id}")
            elif isinstance(node, ast.Attribute) and \
                    node.attr in FORBIDDEN_AUTHORITY_NAMES:
                offenders.append(f"{rel}:{node.lineno}: calls .{node.attr}")
    assert not offenders, (
        "a Phase 6 module has a path to execution authority:\n"
        + "\n".join(offenders))


@pytest.mark.gate("permanent.single_authority")
def test_phase6_modules_write_only_their_declared_table():
    offenders = []
    for path in _phase6_paths():
        if not path.exists():
            continue
        allowed = PHASE6_WRITABLE_TABLES[path.name]
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for match in WRITE_STATEMENT.finditer(text):
            table = match.group(2).lower()
            if table not in allowed:
                offenders.append(f"{rel}: writes {table} (allowed: {sorted(allowed)})")
    assert not offenders, (
        "a Phase 6 module writes outside its declared table:\n"
        + "\n".join(sorted(set(offenders))))


@pytest.mark.gate("permanent.single_authority")
def test_the_assigner_can_never_write_a_business_outcome():
    """
    Narrower than the table check above and worth stating separately: an
    assigner able to resolve an opportunity could manufacture the very result
    the experiment exists to measure. `opportunities` is absent from its
    allowed set, and these column names must not appear in it at all.
    """
    path = BACKEND_DIR / "engine" / "assign_experiment_group.py"
    text = path.read_text(encoding="utf-8")
    forbidden = ("recovered_bool", "partial_recovery_amount", "recovered_at",
                 "time_to_recovery", "resolution_type")
    present = [c for c in forbidden if c in text]
    assert not present, (
        f"assign_experiment_group.py references outcome columns: {present}")


@pytest.mark.gate("permanent.single_authority")
def test_only_the_creation_entry_point_assigns_an_experiment_group():
    """
    Assignment is creation work. `core_loop` and `handle_customer_reply`
    operate on opportunities that already exist, so an assignment call in
    either would be assigning something mid-flight -- after it may already
    have been treated, which silently corrupts the arm it lands in.
    """
    callers = []
    for path in sorted((BACKEND_DIR / "engine").glob("*.py")) + \
            sorted((BACKEND_DIR / "api").glob("*.py")):
        if path.name in ("assign_experiment_group.py", "trigger_event.py"):
            continue
        if "assign_experiment_group(" in path.read_text(encoding="utf-8"):
            callers.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert not callers, (
        "assign_experiment_group() is called outside the creation entry "
        f"point: {callers}")


# --------------------------------------------------------------------------
# Dataset provenance
# --------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


@pytest.mark.gate("permanent.dataset_provenance")
def test_training_corpus_is_not_ambiguously_duplicated():
    """
    Two files named training_corpus.csv exist in the tree. Only
    backend/ml/data/ is read by train_risk_model.py and verify_sensitivity.py;
    the other is unreferenced by any module. If their contents differ, the
    repository contains two candidate answers to "what was the shipped model
    trained on", and nothing on disk says which. Identical contents would be
    merely redundant; differing contents make the provenance claim
    unverifiable.
    """
    corpora = sorted(BACKEND_DIR.rglob("training_corpus.csv"))
    if len(corpora) < 2:
        pytest.skip(f"only one corpus present: {[str(c) for c in corpora]}")
    digests = {c.relative_to(PROJECT_ROOT).as_posix(): _sha256(c) for c in corpora}
    assert len(set(digests.values())) == 1, (
        "multiple non-identical training corpora; the trainer reads "
        "backend/ml/data/training_corpus.csv and the other copy is dead "
        f"weight that can be mistaken for it:\n"
        + "\n".join(f"  {k}  sha256={v[:16]}..." for k, v in digests.items())
    )


@pytest.mark.gate("permanent.dataset_provenance")
def test_training_corpus_content_hash_is_recorded_somewhere():
    """
    BOOTSTRAP_NOTES.md states the corpus was produced with seed=42 and
    n_cases=8000. What is missing is any hash tying that description to the
    bytes actually on disk -- so the description cannot be checked, only
    believed. EXPECTED TO FAIL: this is the concrete gap behind the
    "dataset provenance" gate.

    Context: the `dataset_registry` table exists in the Phase 1 DDL as the
    intended home for exactly this record and is scheduled to be populated
    in Phase 2. So this is a known deferral, not an oversight -- but the
    permanent gate applies to any dataset in use *now*, and the shipped
    models were trained on this corpus in Phase 0.
    """
    corpus = BACKEND_DIR / "ml" / "data" / "training_corpus.csv"
    assert corpus.exists(), f"{corpus} missing"
    digest = _sha256(corpus)
    haystack = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in list(BACKEND_DIR.glob("*.md")) + list(BACKEND_DIR.rglob("*provenance*"))
        if p.is_file()
    ).lower()
    assert digest.lower() in haystack, (
        f"no committed record contains sha256={digest} for "
        "ml/data/training_corpus.csv, so the stated seed/row-count provenance "
        "cannot be verified against the file that is actually present."
    )


@pytest.mark.gate("permanent.dataset_provenance")
def test_seed_generator_persists_its_own_provenance(seed_data_dir):
    """
    generate_seed_data.py *prints* `generator=... seed=... now=...` and writes
    four JSON files that carry none of it. Once the JSON is loaded into
    recovery.db, nothing on disk or in the database records which generator
    version or clock produced the rows -- the provenance lives only in
    whatever terminal scrollback the operator happened to keep.

    EXPECTED TO FAIL. Runs against a freshly generated dataset rather than the
    checked-in one, because the gate is about the generator's behaviour.
    """
    from backend.data.generate_seed_data import GENERATOR_VERSION, RNG_SEED

    emitted = sorted(p.name for p in seed_data_dir.iterdir() if p.is_file())
    blob = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in seed_data_dir.iterdir() if p.is_file())
    assert GENERATOR_VERSION in blob and str(RNG_SEED) in blob, (
        f"seed dataset carries no provenance. Files emitted: {emitted}. "
        f"Expected generator version {GENERATOR_VERSION!r} and seed "
        f"{RNG_SEED} to be written into the dataset (a manifest file, or a "
        f"header record), not only printed to stdout."
    )


# --------------------------------------------------------------------------
# Bootstrap idempotency (Phase 0 [NEW], re-checked permanently)
# --------------------------------------------------------------------------

@pytest.mark.gate("permanent.idempotency")
def test_bootstrap_sequence_is_idempotent(db_path, seed_data_dir):
    """
    Running the bootstrap twice must leave the same end state -- not doubled
    rows, not a partially-doubled database, and not a crash on the second
    run. The loaders use INSERT OR REPLACE, so this holds by construction
    today; the test exists because that is a property of the current
    implementation rather than a guarantee, and a later phase adding an
    append-only or audit-style loader would break it silently.
    """
    from backend.db.db import (create_schema, get_connection, load_customers,
                               load_merchants, load_opportunities,
                               load_payments)

    tables = ("merchants", "customers", "opportunities", "payments")

    def bootstrap_once():
        conn = get_connection()
        create_schema(conn)
        load_merchants(conn)
        load_customers(conn)
        load_opportunities(conn)
        load_payments(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in tables}
        digest = conn.execute(
            "SELECT group_concat(opportunity_id || ':' || status || ':' || "
            "amount_at_risk, '|') FROM (SELECT * FROM opportunities "
            "ORDER BY opportunity_id)"
        ).fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        return counts, digest, fk_violations

    first_counts, first_digest, first_fk = bootstrap_once()
    assert not first_fk, f"FK violations after first bootstrap: {first_fk}"
    assert all(v > 0 for v in first_counts.values()), \
        f"bootstrap produced empty tables: {first_counts}"

    second_counts, second_digest, second_fk = bootstrap_once()
    assert not second_fk, f"FK violations after second bootstrap: {second_fk}"
    assert second_counts == first_counts, (
        f"row counts changed on re-run: {first_counts} -> {second_counts}")
    assert second_digest == first_digest, \
        "opportunity contents differ after a second bootstrap run"
