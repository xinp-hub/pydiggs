"""
Copyright 2021-2025 Xin Peng

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

__author__ = """Xin Peng"""
__email__ = 'xin_peng@outlook.com'
__version__ = '0.1.5'

"""A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

This package provides tools for validating DIGGS instance files against:
1. XML Schema (XSD)
2. DIGGS Dictionary
3. Schematron Rules

For more information about DIGGS, visit: http://www.diggsml.org/
"""

from .pydiggs import validator

__all__ = ['validator']
