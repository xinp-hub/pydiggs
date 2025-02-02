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

"""A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).

This module provides functionality for validating DIGGS instance files against schemas,
dictionaries, and schematron rules.
"""

import os.path
import re
import xml.etree.ElementTree as ET
from lxml import etree, isoschematron  # type: ignore
from rich import print as rprint


class validator():
    """A Python Class for validating DIGGS instance files.
    """

    def __init__(self, instance_path=None, schema_path=None, dictionary_path=None, schematron_path=None, output_log=True):
        """Initialize the arguments within the validator class.

        Args:
            instance_path (string, optional): Relative or full path of the DIGGS instance file. Defaults to None.
            schema_path (string, optional): Relative or full path of the DIGGS schema file. Defaults to None.
            dictionary_path (string, optional): Relative or full path of the DIGGS dictionary file. Defaults to None.
            schematron_path (string, optional): Relative or full path of DIGGS schematron schema file. Defaults to None.
            output_log (boolean, optional): Whether to output the log files. Defaults to True.
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
        self.schematron_error_log = None
        self.schematron_validation_log = None


    def schema_check(self):
        """Function to check schema definition (i.e. .xsd).
        """
        print("self.output_log:", self.output_log)

        if self.instance_path is not None:
            try:
                # Parse XML instance file:
                instance_doc = etree.parse(self.instance_path)
                rprint('[green]No syntax error is detected.[/green]')

                # Parse DIGGS schema:
                if self.schema_path is None:
                    # self.schema_path = os.path.dirname(__file__) + '/schemas/diggs-schema-2.5.a/Complete.xsd'
                    self.schema_path = os.path.dirname(__file__) + '/schemas/diggs-schema-2.6/Diggs.xsd'

                schema_doc = etree.parse(self.schema_path)
                diggs_schema = etree.XMLSchema(schema_doc)

                # Validate against DIGGS schema:
                diggs_schema.validate(instance_doc)
                if diggs_schema.validate(instance_doc) is True:
                    rprint('[green]No schema validation error is detected.[/green]')
                else:
                    self.schema_validation_log = diggs_schema.error_log
                    if self.output_log:
                        rprint('[red]DIGGS Schema validation error, see [bold]schema_validation.log[/bold] file in detail. [/red]')
                        with open('schema_validation.log', 'w', encoding='utf-8') as error_log_file:
                            error_log_file.write(str(self.schema_validation_log))
                    else:
                        rprint('[red]DIGGS Schema validation error:[/red]')
                        print(self.schema_validation_log)

            # Check for file IO error:
            except IOError:
                rprint('[red]Invalid file path or file name. [/red]')

            # Check for XML syntax errors:
            except etree.XMLSyntaxError as err:
                self.syntax_error_log = err
                if self.output_log:
                    rprint('[red]XML syntax error, see [bold]syntax_error.log[/bold] file in detail. [/red]')
                    with open('syntax_error.log', 'w', encoding='utf-8') as error_log_file:
                        error_log_file.write(str(self.syntax_error_log))
                else:
                    rprint('[red]XML syntax error:[/red]')
                    print(self.syntax_error_log)

            # Check for schema parse error:
            except etree.XMLSchemaParseError as err:
                print("self.output_log:", self.output_log)
                # rprint('[red]Schema parse error, see [bold]schema_parse_error.log[/bold] file in detail. [/red]')
                self.schema_error_log = err
                if self.output_log:
                    rprint('[red]Schema parse error, see [bold]schema_parse_error.log[/bold] file in detail. [/red]')
                    with open('schema_parse_error.log', 'w', encoding='utf-8') as error_log_file:
                        error_log_file.write(str(self.schema_error_log))
                else:
                    print("Schema parse error:", self.schema_error_log)

    def dictionary_check(self):
        """Function to verify that definitions used in a DIGGS files.
        """

        if self.instance_path is not None:
            try:
                # Parse XML instance file:
                rprint('[green]No syntax error is detected.[/green]')
                propertyClass_set = self._get_propertyClass_values_from_instance_file()

                # Parse DIGGS dictionary
                if self.dictionary_path is None:
                    # self.dictionary_path = os.path.dirname(__file__) + '/dictionaries/DIGGSTestPropertyDefinitions.xml'
                    self.dictionary_path = os.path.dirname(__file__) + '/dictionaries/properties.xml'

                definition_id_set = self._get_definitions_from_dictionary()
                # Check definitions
                undefined_properties = propertyClass_set.difference(definition_id_set)

                if undefined_properties:
                    rprint('[red]Check failed!\nFollowing propertyClass entries were not found in the standard dictionary:[/red]')
                    self.dictionary_validation_log = []
                    # Sort undefined properties for consistent order
                    for item in sorted(undefined_properties):
                        # Find potential matches
                        temp = re.split(r'\s+|_+', item)
                        # Sort potential matches for consistent order
                        potential_matches = sorted([val for val in definition_id_set if any(x in val for x in temp)])
                        error_msg = f"{item:<25}(Did you mean any of these? {', '.join(potential_matches)})"
                        self.dictionary_validation_log.append(error_msg)
                        rprint(f'[red]  {error_msg}[/red]')
                    
                    if self.output_log:
                        with open('dictionary_validation.log', 'w', encoding='utf-8') as error_log_file:
                            error_log_file.write('\n'.join(self.dictionary_validation_log))
                else:
                    rprint('[green]Check passed![/green]')

            # Check for file IO error:
            except IOError:
                rprint('[red]Invalid file path or file name. [/red]')

    def schematron_check(self):
        """Function to check schematron rules.
        """

        if self.instance_path is not None and self.schematron_path is not None:
            try:
                # Parse XML instance file:
                self.instance_path = self.instance_path
                instance_doc = etree.parse(self.instance_path)
                rprint('[green]No syntax error is detected.[/green]')

                # Parse Schmeatron schema:
                self.schematron_path = self.schematron_path
                schematron_doc = etree.parse(self.schematron_path)

                # The 'error_finder' kwarg is specified to return both failed asserts and successful reports.
                # The default behavior is to only return failed asserts.
                diggs_schematron = isoschematron.Schematron(schematron_doc, store_report=True,
                                                            error_finder=isoschematron.Schematron.ASSERTS_AND_REPORTS)

                # Validate against Schematron schema
                diggs_schematron.validate(instance_doc)
                if diggs_schematron.validate(instance_doc) is True:
                    rprint('[green]No schematron validation error is detected.[/green]')
                else:
                    self.schematron_validation_log = diggs_schematron.error_log
                    if self.output_log:
                        rprint('[red]DIGGS schematron validation Error, see [bold]schematron_validation.log[/bold] file in detail. [/red]')
                        with open('schematron_validation.log', 'w', encoding='utf-8') as error_log_file:
                            error_log_file.write(str(self.schematron_validation_log))
                    else:
                        rprint('[red]DIGGS schematron validation Error:[/red]')
                        print(self.schematron_validation_log)

            # check for file IO error
            except IOError:
                rprint('[red]Invalid file path or file name. [/red]')

            # Check for XML syntax errors:
            except etree.XMLSyntaxError as err:
                self.syntax_error_log = err
                if self.output_log:
                    rprint('[red]XML syntax error, see [bold]syntax_error.log[/bold] file in detail. [/red]')
                    with open('syntax_error.log', 'w', encoding='utf-8') as error_log_file:
                        error_log_file.write(str(self.syntax_error_log))
                else:
                    rprint('[red]XML syntax error:[/red]')
                    print(self.syntax_error_log)

            # Check for schematron parse error:
            except etree.SchematronParseError as err:
                self.schematron_error_log = err
                if self.output_log:
                    rprint('[red]Schematron parse error, see [bold]schematron_parse_error.log[/bold] file in detail. [/red]')
                    with open('schematron_parse_error.log', 'w', encoding='utf-8') as error_log_file:
                        error_log_file.write(str(self.schematron_error_log))
                else:
                    rprint('[red]Schematron parse error:[/red]')
                    print(self.schematron_error_log)

    def _get_definitions_from_dictionary(self):
        """Extract test definitions from DIGGS dictionary.
        """

        dictionary_tree = etree.parse(self.dictionary_path)
        dictionary_root = dictionary_tree.getroot()

        # Get namespaces used in the dictionary file
        dictionary_ns = dict([node for _, node in ET.iterparse(self.dictionary_path, events=['start-ns'])])

        # Extract definitions in the dictionary file to a Python set
        definition_id_set = set()

        for child in dictionary_root.findall('.//diggs:Definition', dictionary_ns):
            definition_id_set.add(child.attrib['{http://www.opengis.net/gml/3.2}id'])

        return definition_id_set

    def _get_propertyClass_values_from_instance_file(self):
        """Extract all propertyClass entries from data file.
        """

        # Load data in to element tree
        data_tree = ET.parse(self.instance_path)
        data_root = data_tree.getroot()

        # Get namespaces used in the data file
        data_ns = dict([node for _, node in ET.iterparse(self.instance_path, events=['start-ns'])])

        # Extract propertyClass values in the data file in to a Python set
        propertyClass_set = set()

        for child in data_root.findall('.//propertyClass', data_ns):
            propertyClass_set.add(child.text)

        return propertyClass_set