"""Inventory of local DIGGS dictionary supplements required by official 3.0 goldens.

These codes live in ``codes/mwd_properties.xml`` / ``codes/pil_properties.xml`` and are
not present in the primary ``properties.xml``. Track upstream so the local bundle can
shrink when diggsml.org/def publishes replacements:

* MWD — https://github.com/DIGGSml/def/issues/13
* Pile-driving — https://github.com/DIGGSml/def/issues/14
* Downstream tracker — https://github.com/xinp-hub/pydiggs/issues/221
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
