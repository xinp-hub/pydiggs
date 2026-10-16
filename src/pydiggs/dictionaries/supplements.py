"""Inventory of the specialised-dictionary codes used by official 3.0 goldens.

These codes live in ``codes/mwd_properties.xml`` / ``codes/pil_properties.xml``, not in
the primary ``properties.xml``. Both files were local supplements in pyDIGGS 1.1.1 and
earlier; diggsml.org/def now publishes them, and pyDIGGS vendors the official copies
(see ``PROVENANCE.toml``). Upstream requests:

* MWD — https://github.com/DIGGSml/def/issues/13
* Pile-driving — https://github.com/DIGGSml/def/issues/14
"""

from __future__ import annotations

# Codes referenced by tests/fixtures/official/3.0/MWDExample.xml that are absent from
# properties.xml (fragment ids on mwd_properties.xml).
MWD_GOLDEN_CODES: frozenset[str] = frozenset(
    {
        "event_new_rod",
        "fluid_injection_pressure",
        "fluid_injection_volume_rate",
        "holdback_pressure",
        "hydraulic_crowd_pressure",
        "hydraulic_torque_pressure",
        "penetration_rate",
        "rotation_shaft",
    }
)

# Codes referenced by tests/fixtures/official/3.0/PileDrivingExample.xml that are absent
# from properties.xml (fragment ids on pil_properties.xml).
PIL_GOLDEN_CODES: frozenset[str] = frozenset(
    {
        "bl_no",
        "bpm",
        "csx_avg",
        "csx_max",
        "csx_min",
        "emx_avg",
        "emx_max",
        "emx_min",
        "rmx_avg",
        "rmx_max",
        "rmx_min",
        "stroke",
        "tsx_avg",
        "tsx_max",
        "tsx_min",
    }
)
