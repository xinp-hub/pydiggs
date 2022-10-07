"""Main module."""

from lxml import etree, isoschematron
import os.path
from rich import print as rprint
import xml.etree.ElementTree as ET
import re


class validator():
    """A Python Class for validating DIGGS instance files.
    """

    def __init__(self, instance_path=None, schema_path=None, dictionary_path=None, schematron_path=None):
        """Initialize the arguments within the validator class.

        Args:
            instance_path (string, optional): Relative or full path of the DIGGS instance file. Defaults to None.
            schema_path (string, optional): Relative or full path of the DIGGS schema file. Defaults to None.
            dictionary_path (string, optional): Relative or full path of the DIGGS dictionary file. Defaults to None.
            schematron_path (string, optional): Relative or full path of DIGGS schematron schema file. Defaults to None.
        """

        self.instance_path = instance_path
        self.schema_path = schema_path
        self.dictionary_path = dictionary_path
        self.schematron_path = schematron_path

        self.syntax_error_log = None
        self.schema_validation_log = None
        self.schema_error_log = None
        self.dictionary_error_log = None
        self.schematron_error_log = None
        self.schematron_validation_log = None

    def schema_check(self):
        """Function to check schema definition (i.e. .xsd).
        """

        if self.instance_path is not None:
            try:
                # Parse XML instance file:
                instance_doc = etree.parse(self.instance_path)
                rprint('[green]No syntax error is detected.[/green]')

                # Parse DIGGS schema:
                if self.schema_path is None:
                    self.schema_path = os.path.dirname(__file__) + '/diggs-schema-2.5.a/Complete.xsd'
                schema_doc = etree.parse(self.schema_path)
                diggs_schema = etree.XMLSchema(schema_doc)

                # Validate against DIGGS schema:
                diggs_schema.validate(instance_doc)
                if diggs_schema.validate(instance_doc) is True:
                    rprint('[green]No schema validation error is detected.[/green]')
                else:
                    self.schema_validation_log = diggs_schema.error_log
                    rprint('[red]DIGGS Schema validation error, see [bold]schema_validation.log[/bold] file in detail. [/red]')
                    with open('schema_validation.log', 'w') as error_log_file:
                        error_log_file.write(str(self.schema_validation_log))

            # Check for file IO error:
            except IOError:
                rprint('[red]Invalid file path or file name. [/red]')

            # Check for XML syntax errors:
            except etree.XMLSyntaxError as err:
                rprint('[red]XML syntax error, see [bold]syntax_error.log[/bold] file in detail. [/red]')
                self.syntax_error_log = err
                with open('syntax_error.log', 'w') as error_log_file:
                    error_log_file.write(str(self.syntax_error_log))

            # Check for schema parse error:
            except etree.XMLSchemaParseError as err:
                rprint('[red]Schema parse error, see [bold]schema_parse_error.log[/bold] file in detail. [/red]')
                self.schema_error_log = err
                with open('schema_parse_error.log', 'w') as error_log_file:
                    error_log_file.write(str(self.schema_error_log))

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
                    self.dictionary_path = os.path.dirname(__file__) + '/DIGGSTestPropertyDefinitions.xml'

                definition_id_set = self._get_definitions_from_dictionary()

                # Check definitions
                undefined_properties = propertyClass_set.difference(definition_id_set)

                if undefined_properties:
                    rprint('[red]Check failed!\nFollowing propertyClass entries were not found in the standard dictionary:[/red]')
                    for item in undefined_properties:
                        # Find potential matches
                        temp = re.split(r'\s+|_+', item)
                #         potential_matches = [val for val in definition_id_set if temp in val]
                        potential_matches = [val for val in definition_id_set if any(x in val for x in temp)]

                        rprint(f'[red]  {item:<25}(Did you mean any of these? {", ".join(potential_matches)})[/red]')
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
                    rprint('[red]DIGGS schematron validation Error, see [bold]schematron_validation.log[/bold] file in detail. [/red]')
                    with open('schematron_validation.log', 'w') as error_log_file:
                        error_log_file.write(str(self.schematron_validation_log))

            # check for file IO error
            except IOError:
                rprint('[red]Invalid file path or file name. [/red]')

            # Check for XML syntax errors:
            except etree.XMLSyntaxError as err:
                rprint('[red]XML syntax error, see [bold]syntax_error.log[/bold] file in detail. [/red]')
                self.syntax_error_log = err
                with open('syntax_error.log', 'w') as error_log_file:
                    error_log_file.write(str(self.syntax_error_log))

            # Check for schematron parse error:
            except etree.SchematronParseError as err:
                rprint('[red]Schematron parse error, see [bold]schematron_parse_error.log[/bold] file in detail. [/red]')
                self.schematron_error_log = err
                with open('schematron_parse_error.log', 'w') as error_log_file:
                    error_log_file.write(str(self.schematron_error_log))

    def _get_definitions_from_dictionary(self):
        """Extract test definitions from DIGGS dictionary.
        """

        dictionary_tree = etree.parse(self.dictionary_path)
        dictionary_root = dictionary_tree.getroot()

        # Get namespaces used in the dictionary file
        dictionary_ns = dict([node for _, node in ET.iterparse(self.dictionary_path, events=['start-ns'])])

        # Extract definitions in the dictionary file to a Python set
        definition_id_set = set()

        for child in dictionary_root.findall('.//Definition/gml:identifier', dictionary_ns):
            definition_id_set.add(child.text.strip())

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
