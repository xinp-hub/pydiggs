"""A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

This module provides functionality for validating DIGGS instance files against schemas,
dictionaries, and schematron rules.
"""

import os.path
import re
import xml.etree.ElementTree as ET

from lxml import etree, isoschematron  # type: ignore
from rich import print as rprint


class Validator:  # pylint: disable=too-many-instance-attributes
    """A Python Class for validating DIGGS instance files."""

    def __init__(
        self,
        instance_path=None,
        schema_path=None,
        dictionary_path=None,
        schematron_path=None
    ):
        """Initialize the arguments within the validator class.

        Args:
            instance_path (str, optional): Path of the DIGGS instance file. Defaults to None.
            schema_path (str, optional): Path of the DIGGS schema file. Defaults to None.
            dictionary_path (str, optional): Path of the DIGGS dictionary file. Defaults to None.
            schematron_path (str, optional): Path of DIGGS schematron schema file. Defaults to None.
        """
        self.instance_path = instance_path
        self.schema_path = schema_path
        self.dictionary_path = dictionary_path
        self.schematron_path = schematron_path

        self.syntax_error_log = None
        self.schema_validation_log = None
        self.schema_error_log = None
        self.dictionary_error_log = None
        self.dictionary_validation_log = None
        self.schematron_error_log = None
        self.schematron_validation_log = None

    def schema_check(self):
        """Check schema definition (i.e. .xsd)."""
        if self.instance_path is None:
            return

        try:
            # Parse XML instance file:
            instance_doc = etree.parse(self.instance_path)
            rprint('[green]No syntax error is detected.[/green]')

            # Parse DIGGS schema:
            if self.schema_path is None:
                schema_dir = os.path.dirname(__file__)
                schema_file = 'schemas/diggs-schema-2.6/Diggs.xsd'
                self.schema_path = os.path.join(schema_dir, schema_file)

            schema_doc = etree.parse(self.schema_path)
            diggs_schema = etree.XMLSchema(schema_doc)

            # Validate against DIGGS schema:
            if diggs_schema.validate(instance_doc):
                rprint('[green]No schema validation error is detected.[/green]')
            else:
                self.schema_validation_log = diggs_schema.error_log
                msg = (
                    '[red]DIGGS Schema validation error, '
                    'see [bold]schema_validation.log[/bold] file in detail.[/red]'
                )
                rprint(msg)
                with open('schema_validation.log', 'w', encoding='utf-8') as error_log_file:
                    error_log_file.write(str(self.schema_validation_log))

        except IOError:
            rprint('[red]Invalid file path or file name.[/red]')

        except etree.XMLSyntaxError as err:
            msg = '[red]XML syntax error, see [bold]syntax_error.log[/bold] file in detail.[/red]'
            rprint(msg)
            self.syntax_error_log = err
            with open('syntax_error.log', 'w', encoding='utf-8') as error_log_file:
                error_log_file.write(str(self.syntax_error_log))

        except etree.XMLSchemaParseError as err:
            msg = (
                '[red]Schema parse error, '
                'see [bold]schema_parse_error.log[/bold] file in detail.[/red]'
            )
            rprint(msg)
            self.schema_error_log = err
            with open('schema_parse_error.log', 'w', encoding='utf-8') as error_log_file:
                error_log_file.write(str(self.schema_error_log))

    def dictionary_check(self):
        """Verify that definitions used in DIGGS files exist in the dictionary."""
        if self.instance_path is None:
            return

        try:
            rprint('[green]No syntax error is detected.[/green]')
            property_class_set = self._get_property_class_values()

            if self.dictionary_path is None:
                dict_dir = os.path.dirname(__file__)
                dict_file = 'dictionaries/properties.xml'
                self.dictionary_path = os.path.join(dict_dir, dict_file)

            definition_id_set = self._get_definitions_from_dictionary()
            undefined_properties = property_class_set.difference(definition_id_set)

            if undefined_properties:
                msg = (
                    '[red]Check failed!\n'
                    'The following properties were not found in the dictionary:[/red]'
                )
                rprint(msg)
                self.dictionary_validation_log = []

                for item in sorted(undefined_properties):
                    temp = re.split(r'\s+|_+', item)
                    matches = (
                        val for val in definition_id_set if any(x in val for x in temp)
                    )
                    potential_matches = sorted(matches)
                    error_msg = (
                        f"{item:<25}(Did you mean any of these? {', '.join(potential_matches)})"
                    )
                    self.dictionary_validation_log.append(error_msg)
                    rprint(f'[red]  {error_msg}[/red]')

                with open('dictionary_validation.log', 'w', encoding='utf-8') as error_log_file:
                    error_log_file.write('\n'.join(self.dictionary_validation_log))
            else:
                rprint('[green]Check passed![/green]')

        except IOError:
            rprint('[red]Invalid file path or file name.[/red]')

    def schematron_check(self):
        """Check schematron rules."""
        if self.instance_path is None or self.schematron_path is None:
            return

        try:
            instance_doc = etree.parse(self.instance_path)
            rprint('[green]No syntax error is detected.[/green]')

            schematron_doc = etree.parse(self.schematron_path)
            diggs_schematron = isoschematron.Schematron(
                schematron_doc,
                store_report=True,
                error_finder=isoschematron.Schematron.ASSERTS_AND_REPORTS
            )

            if diggs_schematron.validate(instance_doc):
                rprint('[green]No schematron validation error is detected.[/green]')
            else:
                self.schematron_validation_log = diggs_schematron.error_log
                msg = (
                    '[red]DIGGS schematron validation Error, '
                    'see [bold]schematron_validation.log[/bold] file in detail.[/red]'
                )
                rprint(msg)
                with open('schematron_validation.log', 'w', encoding='utf-8') as error_log_file:
                    error_log_file.write(str(self.schematron_validation_log))

        except IOError:
            rprint('[red]Invalid file path or file name.[/red]')

        except etree.XMLSyntaxError as err:
            msg = '[red]XML syntax error, see [bold]syntax_error.log[/bold] file in detail.[/red]'
            rprint(msg)
            self.syntax_error_log = err
            with open('syntax_error.log', 'w', encoding='utf-8') as error_log_file:
                error_log_file.write(str(self.syntax_error_log))

        except etree.SchematronParseError as err:
            msg = (
                '[red]Schematron parse error, '
                'see [bold]schematron_parse_error.log[/bold] file in detail.[/red]'
            )
            rprint(msg)
            self.schematron_error_log = err
            with open('schematron_parse_error.log', 'w', encoding='utf-8') as error_log_file:
                error_log_file.write(str(self.schematron_error_log))

    def _get_definitions_from_dictionary(self):
        """Extract test definitions from DIGGS dictionary.

        Returns:
            set: A set of definition IDs from the dictionary.
        """
        dictionary_tree = etree.parse(self.dictionary_path)
        dictionary_root = dictionary_tree.getroot()
        dictionary_ns = dict(ET.iterparse(self.dictionary_path, events=['start-ns']))

        return {
            child.attrib['{http://www.opengis.net/gml/3.2}id']
            for child in dictionary_root.findall('.//diggs:Definition', dictionary_ns)
        }

    def _get_property_class_values(self):
        """Extract all propertyClass entries from data file.

        Returns:
            set: A set of property class values from the instance file.
        """
        data_tree = ET.parse(self.instance_path)
        data_root = data_tree.getroot()
        data_ns = dict(ET.iterparse(self.instance_path, events=['start-ns']))

        return {
            child.text for child in data_root.findall('.//propertyClass', data_ns)
        }
