# Copyright 2021-2025 Xin Peng
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""DIGGS instance validation (XSD, dictionary, Schematron)."""

from __future__ import annotations

from pathlib import Path

from lxml import etree, isoschematron  # type: ignore
from rich import print as rprint

from pydiggs.dictionary import DictionarySemanticValidator
from pydiggs.semantic import validate_context
from pydiggs.units import UnitConverter, casing_outside_gt_inside

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_SCHEMA_26 = _PACKAGE_DIR / "schemas" / "diggs-schema-2.6" / "Diggs.xsd"
_DEFAULT_DICTIONARY = _PACKAGE_DIR / "dictionaries" / "properties.xml"
_DEFAULT_SCHEMATRON = _PACKAGE_DIR / "schematron" / "diggs_schematron_rules_2.6.sch"


class validator:  # noqa: N801 — published public API name
    """A Python Class for validating DIGGS instance files."""

    def __init__(
        self,
        instance_path=None,
        schema_path=None,
        dictionary_path=None,
        schematron_path=None,
        output_log=True,
    ):
        """Initialize the arguments within the validator class.

        Args:
            instance_path (string, optional): Relative or full path of the DIGGS instance file.
            schema_path (string, optional): Relative or full path of the DIGGS schema file.
            dictionary_path (string, optional): Relative or full path of the DIGGS dictionary file.
            schematron_path (string, optional): Relative or full path of DIGGS schematron schema file.
                If omitted, the bundled DIGGS lxml-adapted rules are used for schematron_check().
            output_log (boolean, optional): Whether to write log files to the CWD. Defaults to True.
        """

        self.instance_path = instance_path
        self.schema_path = schema_path
        self.dictionary_path = dictionary_path
        self.schematron_path = schematron_path
        self.output_log = output_log

        self.syntax_error_log = None
        self.schema_validation_log = None
        self.schema_error_log = None
        self.dictionary_error_log = None
        self.dictionary_validation_log = None
        self.context_validation_log = None
        self.schematron_error_log = None
        self.schematron_validation_log = None

    def schema_check(self) -> bool:
        """Validate the instance against an XSD schema. Returns True on success."""
        if self.instance_path is None:
            return False

        try:
            instance_doc = etree.parse(self.instance_path)
            rprint("[green]No syntax error is detected.[/green]")

            if self.schema_path is None:
                self.schema_path = str(_DEFAULT_SCHEMA_26)

            schema_doc = etree.parse(self.schema_path)
            diggs_schema = etree.XMLSchema(schema_doc)

            ok = diggs_schema.validate(instance_doc)
            if ok:
                rprint("[green]No schema validation error is detected.[/green]")
                return True

            self.schema_validation_log = diggs_schema.error_log
            if self.output_log:
                rprint(
                    "[red]DIGGS Schema validation error, see [bold]schema_validation.log[/bold] "
                    "file in detail. [/red]"
                )
                with Path("schema_validation.log").open("w", encoding="utf-8") as error_log_file:
                    error_log_file.write(str(self.schema_validation_log))
            else:
                rprint("[red]DIGGS Schema validation error:[/red]")
                print(self.schema_validation_log)
            return False

        except OSError:
            rprint("[red]Invalid file path or file name. [/red]")
            return False

        except etree.XMLSyntaxError as err:
            self.syntax_error_log = err
            if self.output_log:
                rprint(
                    "[red]XML syntax error, see [bold]syntax_error.log[/bold] file in detail. [/red]"
                )
                with Path("syntax_error.log").open("w", encoding="utf-8") as error_log_file:
                    error_log_file.write(str(self.syntax_error_log))
            else:
                rprint("[red]XML syntax error:[/red]")
                print(self.syntax_error_log)
            return False

        except etree.XMLSchemaParseError as err:
            self.schema_error_log = err
            if self.output_log:
                rprint(
                    "[red]Schema parse error, see [bold]schema_parse_error.log[/bold] "
                    "file in detail. [/red]"
                )
                with Path("schema_parse_error.log").open("w", encoding="utf-8") as error_log_file:
                    error_log_file.write(str(self.schema_error_log))
            else:
                print("Schema parse error:", self.schema_error_log)
            return False

    def dictionary_check(self) -> bool:
        """Run DIGGSml-compatible semantic dictionary validation. Returns True if no ERRORs."""
        if self.instance_path is None:
            return False

        try:
            semantic = DictionarySemanticValidator(
                dictionary_path=self.dictionary_path or _DEFAULT_DICTIONARY,
            )
            result = semantic.validate_instance(self.instance_path)
            self.dictionary_validation_log = [
                f"[{m.severity}] {m.element_path}: {m.text}" for m in result.messages
            ]

            errors = [m for m in result.messages if m.severity == "ERROR"]
            warnings = [m for m in result.messages if m.severity == "WARNING"]
            infos = [m for m in result.messages if m.severity == "INFO"]

            if errors:
                rprint(
                    f"[red]Dictionary check failed with {len(errors)} error(s), "
                    f"{len(warnings)} warning(s), {len(infos)} info message(s).[/red]"
                )
                for msg in errors[:20]:
                    first = msg.text.splitlines()[0]
                    rprint(f"[red]  [{msg.severity}] check {msg.check}: {first}[/red]")
            elif warnings:
                rprint(
                    f"[yellow]Dictionary check passed with {len(warnings)} warning(s), "
                    f"{len(infos)} info message(s).[/yellow]"
                )
            else:
                suffix = f" ({len(infos)} info message(s))" if infos else "!"
                rprint(f"[green]Dictionary check passed{suffix}[/green]")

            if self.output_log and self.dictionary_validation_log:
                with Path("dictionary_validation.log").open("w", encoding="utf-8") as fh:
                    fh.write("\n\n".join(self.dictionary_validation_log))
            return result.ok

        except OSError:
            rprint("[red]Invalid file path or file name. [/red]")
            return False
        except etree.XMLSyntaxError:
            rprint("[red]XML syntax error during dictionary check.[/red]")
            return False

    def context_check(self) -> bool:
        """Run structure / geometry SRS / href / dataBlock semantic checks."""
        if self.instance_path is None:
            return False
        try:
            result = validate_context(self.instance_path)
            self.context_validation_log = [
                f"[{m.severity}] {m.element_path}: {m.text}" for m in result.messages
            ]
            errors = [m for m in result.messages if m.severity == "ERROR"]
            if errors:
                rprint(f"[red]Context check failed with {len(errors)} error(s).[/red]")
                for msg in errors[:20]:
                    rprint(f"[red]  {msg.text}[/red]")
            else:
                rprint("[green]Context check passed![/green]")
            if self.output_log and self.context_validation_log:
                with Path("context_validation.log").open("w", encoding="utf-8") as fh:
                    fh.write("\n\n".join(self.context_validation_log))
            return result.ok
        except OSError:
            rprint("[red]Invalid file path or file name. [/red]")
            return False
        except etree.XMLSyntaxError:
            rprint("[red]XML syntax error during context check.[/red]")
            return False

    def schematron_check(self) -> bool:
        """Validate against Schematron rules. Returns True on success."""
        if self.instance_path is None:
            return False

        schemapath = self.schematron_path or str(_DEFAULT_SCHEMATRON)
        try:
            instance_doc = etree.parse(self.instance_path)
            rprint("[green]No syntax error is detected.[/green]")

            schematron_doc = etree.parse(schemapath)
            diggs_schematron = isoschematron.Schematron(
                schematron_doc,
                store_report=True,
                error_finder=isoschematron.Schematron.ASSERTS_AND_REPORTS,
            )

            ok = diggs_schematron.validate(instance_doc)
            casing_ok, casing_msgs = self._check_casing_diameters(instance_doc)
            if ok and casing_ok:
                rprint("[green]No schematron validation error is detected.[/green]")
                return True

            parts: list[str] = []
            if not ok:
                parts.append(str(diggs_schematron.error_log))
            parts.extend(casing_msgs)
            self.schematron_validation_log = "\n".join(parts)
            if self.output_log:
                rprint(
                    "[red]DIGGS schematron validation Error, see "
                    "[bold]schematron_validation.log[/bold] file in detail. [/red]"
                )
                with Path("schematron_validation.log").open(
                    "w", encoding="utf-8"
                ) as error_log_file:
                    error_log_file.write(str(self.schematron_validation_log))
            else:
                rprint("[red]DIGGS schematron validation Error:[/red]")
                print(self.schematron_validation_log)
            return False

        except OSError:
            rprint("[red]Invalid file path or file name. [/red]")
            return False

        except etree.XMLSyntaxError as err:
            self.syntax_error_log = err
            if self.output_log:
                rprint(
                    "[red]XML syntax error, see [bold]syntax_error.log[/bold] file in detail. [/red]"
                )
                with Path("syntax_error.log").open("w", encoding="utf-8") as error_log_file:
                    error_log_file.write(str(self.syntax_error_log))
            else:
                rprint("[red]XML syntax error:[/red]")
                print(self.syntax_error_log)
            return False

        except etree.SchematronParseError as err:
            self.schematron_error_log = err
            if self.output_log:
                rprint(
                    "[red]Schematron parse error, see [bold]schematron_parse_error.log[/bold] "
                    "file in detail. [/red]"
                )
                with Path("schematron_parse_error.log").open(
                    "w", encoding="utf-8"
                ) as error_log_file:
                    error_log_file.write(str(self.schematron_error_log))
            else:
                rprint("[red]Schematron parse error:[/red]")
                print(self.schematron_error_log)
            return False

    @staticmethod
    def _check_casing_diameters(instance_doc: etree._ElementTree) -> tuple[bool, list[str]]:
        """Apply DiggsUomDictionary-backed outside > inside casing rule."""
        converter = UnitConverter()
        messages: list[str] = []
        for casing in instance_doc.xpath("//*[local-name()='Casing']"):
            outside_el = next(
                (
                    c
                    for c in casing
                    if isinstance(c.tag, str)
                    and etree.QName(c).localname == "casingOutsideDiameter"
                ),
                None,
            )
            inside_el = next(
                (
                    c
                    for c in casing
                    if isinstance(c.tag, str) and etree.QName(c).localname == "casingInsideDiameter"
                ),
                None,
            )
            if outside_el is None or inside_el is None:
                continue
            try:
                outside = float((outside_el.text or "").strip())
                inside = float((inside_el.text or "").strip())
            except ValueError:
                messages.append("Casing diameter values must be numeric.")
                continue
            ok, msg = casing_outside_gt_inside(
                outside,
                outside_el.get("uom") or "",
                inside,
                inside_el.get("uom") or "",
                converter,
            )
            if not ok:
                messages.append(msg)
        return (len(messages) == 0), messages
