=====
Usage
=====

DIGGS validation
--------------

To use pydiggs in a Python project::

    from pydiggs import validation

If the DIGGS file is in your current working directory::

    validation("DIGGS_File_Name")

If the DIGGS file is not in your current working directory::

    validation("Full_DIGGS_File_Path")



To use validate DIGGS file in the Command Line Interface::

    pydiggs check "DIGGS_File_Name" or "Full_DIGGS_File_Path"