"""Main module."""

from lxml import etree, isoschematron
import os.path
from rich import print as rprint
class validator():
    """A Python Class for validating DIGGS instance files.
    """    

    def __init__(self, instance_path=None, schema_path = None, schematron_path=None):
        """Initialize the arguments within the validator class.

        Args:
            instance_path (string, optional): Relative or full path of the DIGGS instance file. Defaults to None.
            schema_path (string, optional): Relative or full path of the DIGGS schema file. Defaults to None.
            schematron_path (string, optional): Relative or full path of DIGGS schematron schema file. Defaults to None.
        """
        self.instance_path = instance_path     
        self.schema_path = schema_path
        self.schematron_path = schematron_path

        self.syntax_error_log = None
        self.schema_validation_log = None
        self.schema_error_log = None
        self.schematron_error_log = None
        self.schematron_validation_log = None


# =============================================================================
# Define schema_check function:
# =============================================================================
    def schema_check(self):

        if self.instance_path is not None:
            try:               
# Parse XML instance file:
                instance_doc = etree.parse(self.instance_path)
                rprint('[green]No syntax error is detected.[/green]')

# Parse DIGGS schema:
                if self.schema_path == None:
                    self.schema_path = os.path.dirname(__file__) + '/diggs-schema-2.5.a/Complete.xsd'
                schema_doc = etree.parse(self.schema_path)          
                diggs_schema = etree.XMLSchema(schema_doc)

# Validate against DIGGS schema:
                diggs_schema.validate(instance_doc)
                if diggs_schema.validate(instance_doc) == True:            
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

            except:
                rprint('[red]Unknown error.[/red]')
                            

# =============================================================================
# Define schematron_check function:
# =============================================================================
    def schematron_check(self):    

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
                diggs_schematron = isoschematron.Schematron(schematron_doc, store_report=True, error_finder=isoschematron.Schematron.ASSERTS_AND_REPORTS)
        
# Validate against Schematron schema
                diggs_schematron.validate(instance_doc)
                if  diggs_schematron.validate(instance_doc) == True:            
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

            except:
                rprint('[red]Unknown error.[/red]')