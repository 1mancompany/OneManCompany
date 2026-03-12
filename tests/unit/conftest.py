"""Shared test fixtures — in-memory store bridge for unit tests.

Production code reads/writes via ``store.load_employee()`` / ``store.save_employee()``.
Tests that set up data via ``cs.employees[...] = emp`` get automatic bridging:
the conftest patches store read functions to fall back to whatever ``company_state``
is currently active (the global singleton or a test-replaced one).

Legacy fields (employees, ex_employees, activity_log, etc.) were removed from
CompanyState dataclass fields in Task 10 but are set as instance attributes in
``__post_init__`` for backward compatibility. The conftest bridge reads from
these instance attributes.
"""

from __future__ import annotations

import pytest


def _emp_obj_to_dict(emp) -> dict:
    """Convert an Employee dataclass to a plain dict like store.load_employee() returns."""
    d = {}
    for field in ("id", "name", "nickname", "role", "skills", "level", "department",
                  "permissions", "tool_permissions", "work_principles", "guidance_notes",
                  "status", "is_listening", "current_task_summary", "desk_position",
                  "sprite", "employee_number", "current_quarter_tasks",
                  "performance_history", "remote", "probation", "onboarding_completed",
                  "okrs", "pip", "api_online", "needs_setup"):
        val = getattr(emp, field, None)
        if val is not None:
            d[field] = val
    d.setdefault("runtime", {
        "status": getattr(emp, "status", "idle"),
        "is_listening": getattr(emp, "is_listening", False),
        "current_task_summary": getattr(emp, "current_task_summary", ""),
        "api_online": getattr(emp, "api_online", False),
        "needs_setup": getattr(emp, "needs_setup", False),
    })
    return d


def _get_cs():
    """Dynamically look up company_state from the module to respect monkeypatches."""
    import onemancompany.core.state as state_mod
    return state_mod.company_state


@pytest.fixture(autouse=True)
def _bridge_store_to_company_state(monkeypatch):
    """Auto-patch store functions to fall back to company_state legacy attrs for tests.

    When store.load_employee() can't find disk data, falls back to
    company_state.employees (legacy instance attr).  This supports both
    the global singleton and test-replaced CompanyState objects.
    """
    from onemancompany.core import store as store_mod

    _orig_load_employee = store_mod.load_employee
    _orig_load_all = store_mod.load_all_employees
    _orig_load_ex = store_mod.load_ex_employees
    _orig_load_activity = store_mod.load_activity_log
    _orig_load_culture = store_mod.load_culture
    _orig_load_direction = store_mod.load_direction
    _orig_load_guidance = store_mod.load_employee_guidance
    _orig_save_employee = store_mod.save_employee
    _orig_save_ex_fn = store_mod.save_ex_employee
    _orig_append_activity_fn = store_mod.append_activity
    _orig_append_activity_sync_fn = store_mod.append_activity_sync
    _orig_save_runtime_fn = store_mod.save_employee_runtime

    # Capture default singleton id to detect test-replaced company_state
    _default_cs_id = id(_get_cs())

    def _is_test_cs():
        """True when a test monkeypatched company_state with its own object."""
        return id(_get_cs()) != _default_cs_id

    def _patched_load_employee(emp_id):
        cs = _get_cs()
        employees = getattr(cs, "employees", {})
        emp = employees.get(emp_id)
        if emp:
            return _emp_obj_to_dict(emp) if hasattr(emp, "to_dict") else emp
        # If test replaced company_state, don't fall back to disk
        if _is_test_cs():
            return None
        result = _orig_load_employee(emp_id)
        return result if result else None

    def _patched_load_all():
        cs = _get_cs()
        employees = getattr(cs, "employees", {})
        # If test replaced company_state, use only in-memory (even if empty)
        if _is_test_cs():
            return {eid: (_emp_obj_to_dict(e) if hasattr(e, "to_dict") else e)
                    for eid, e in employees.items()}
        if employees:
            return {eid: (_emp_obj_to_dict(e) if hasattr(e, "to_dict") else e)
                    for eid, e in employees.items()}
        result = _orig_load_all()
        return result if result else {}

    def _patched_load_ex():
        cs = _get_cs()
        ex_employees = getattr(cs, "ex_employees", {})
        if _is_test_cs():
            return {eid: (_emp_obj_to_dict(e) if hasattr(e, "to_dict") else e)
                    for eid, e in ex_employees.items()}
        if ex_employees:
            return {eid: (_emp_obj_to_dict(e) if hasattr(e, "to_dict") else e)
                    for eid, e in ex_employees.items()}
        result = _orig_load_ex()
        return result if result else {}

    def _patched_load_activity():
        cs = _get_cs()
        log = getattr(cs, "activity_log", [])
        if _is_test_cs():
            return list(log)
        if log:
            return list(log)
        result = _orig_load_activity()
        return result if result else []

    def _patched_load_culture():
        cs = _get_cs()
        culture = getattr(cs, "company_culture", [])
        if _is_test_cs():
            return list(culture)
        if culture:
            return list(culture)
        result = _orig_load_culture()
        return result if result else []

    def _patched_load_direction():
        cs = _get_cs()
        direction = getattr(cs, "company_direction", "")
        if _is_test_cs():
            return direction
        if direction:
            return direction
        result = _orig_load_direction()
        return result if result else ""

    def _patched_load_guidance(emp_id):
        cs = _get_cs()
        employees = getattr(cs, "employees", {})
        emp = employees.get(emp_id)
        if emp:
            notes = getattr(emp, "guidance_notes", []) if hasattr(emp, "guidance_notes") else (emp.get("guidance_notes", []) if isinstance(emp, dict) else [])
            if notes:
                return notes
        if _is_test_cs():
            return []
        result = _orig_load_guidance(emp_id)
        return result if result else []

    async def _patched_save_employee(emp_id, updates):
        """Bridge save_employee to update in-memory Employee if present."""
        cs = _get_cs()
        employees = getattr(cs, "employees", {})
        emp = employees.get(emp_id)
        if emp and isinstance(updates, dict) and hasattr(emp, "to_dict"):
            for key, val in updates.items():
                if hasattr(emp, key) and not isinstance(
                    getattr(type(emp), key, None), property
                ):
                    try:
                        setattr(emp, key, val)
                    except (AttributeError, TypeError):
                        pass
        if not _is_test_cs():
            try:
                await _orig_save_employee(emp_id, updates)
            except (FileNotFoundError, OSError):
                pass

    async def _patched_save_ex_employee(emp_id, data):
        """Bridge save_ex_employee to also update in-memory ex_employees."""
        cs = _get_cs()
        employees = getattr(cs, "employees", {})
        ex_employees = getattr(cs, "ex_employees", {})
        emp = employees.pop(emp_id, None)
        if emp:
            ex_employees[emp_id] = emp
        if not _is_test_cs():
            try:
                await _orig_save_ex_fn(emp_id, data)
            except (FileNotFoundError, OSError):
                pass

    async def _patched_append_activity(entry):
        """Bridge append_activity to also update in-memory activity_log."""
        cs = _get_cs()
        log = getattr(cs, "activity_log", None)
        from datetime import datetime
        if isinstance(entry, dict) and "timestamp" not in entry:
            entry["timestamp"] = datetime.now().isoformat()
        if log is not None:
            log.append(entry)
        if not _is_test_cs():
            try:
                await _orig_append_activity_fn(entry)
            except (FileNotFoundError, OSError):
                pass

    def _patched_append_activity_sync(entry):
        """Bridge append_activity_sync to also update in-memory activity_log."""
        cs = _get_cs()
        log = getattr(cs, "activity_log", None)
        from datetime import datetime
        if isinstance(entry, dict) and "timestamp" not in entry:
            entry["timestamp"] = datetime.now().isoformat()
        if log is not None:
            log.append(entry)
        if not _is_test_cs():
            try:
                _orig_append_activity_sync_fn(entry)
            except (FileNotFoundError, OSError):
                pass

    async def _patched_save_employee_runtime(emp_id, **fields):
        """Bridge save_employee_runtime to update in-memory Employee."""
        cs = _get_cs()
        employees = getattr(cs, "employees", {})
        emp = employees.get(emp_id)
        if emp and hasattr(emp, "to_dict"):
            for key, val in fields.items():
                if hasattr(emp, key) and not isinstance(
                    getattr(type(emp), key, None), property
                ):
                    try:
                        setattr(emp, key, val)
                    except (AttributeError, TypeError):
                        pass
        if not _is_test_cs():
            try:
                await _orig_save_runtime_fn(emp_id, **fields)
            except (FileNotFoundError, OSError):
                pass

    monkeypatch.setattr(store_mod, "load_employee", _patched_load_employee)
    monkeypatch.setattr(store_mod, "load_all_employees", _patched_load_all)
    monkeypatch.setattr(store_mod, "load_ex_employees", _patched_load_ex)
    monkeypatch.setattr(store_mod, "load_activity_log", _patched_load_activity)
    monkeypatch.setattr(store_mod, "load_culture", _patched_load_culture)
    monkeypatch.setattr(store_mod, "load_direction", _patched_load_direction)
    monkeypatch.setattr(store_mod, "load_employee_guidance", _patched_load_guidance)
    monkeypatch.setattr(store_mod, "save_employee", _patched_save_employee)
    monkeypatch.setattr(store_mod, "save_ex_employee", _patched_save_ex_employee)
    monkeypatch.setattr(store_mod, "append_activity", _patched_append_activity)
    monkeypatch.setattr(store_mod, "append_activity_sync", _patched_append_activity_sync)
    monkeypatch.setattr(store_mod, "save_employee_runtime", _patched_save_employee_runtime)
