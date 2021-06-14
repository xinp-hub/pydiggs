"""Main module."""

from lxml import etree
import os.path
from rich import print as rprint


def validation(instance_file_name):
    """ A basic function to check and report XML syntax and DIGGS schema errors in diggs instance files.
        Schema errors will be checked until all the syntex errors have been fixed.

    Args:
        instance_file_name (str, required): The file name or the absolute file path of the diggs instance file to validate.

    Returns:
        'error_syntax.log' file if any XML syntax error is detected;
        'error_schema.log' file if any DIGGS schema error is detected;
   
    """    

    diggs_schema_path ='/diggs-schema-2.5.a/Complete.xsd'
    diggs_schema_doc = etree.parse(os.path.dirname(__file__) + diggs_schema_path)
    diggs_schema = etree.XMLSchema(diggs_schema_doc)

# Get the current working directory
    cwd = os.getcwd()
# get instance file path
    if cwd not in instance_file_name:
        instance_file_path = cwd + '/' + instance_file_name
    else:
        instance_file_path = instance_file_name
# parse xml
    try:
        doc = etree.parse(instance_file_path)
        rprint('[green]No syntax error is detected.[/green]')
    
# check for file IO error
    except IOError:
        rprint('[red]Invalid File[/red]')
    
# check for XML syntax errors:
    except etree.XMLSyntaxError as err:
        rprint('[red]XML Syntax Error, see [bold]error_syntax.log[/bold] file in detail. [/red]')
        with open('error_syntax.log', 'w') as error_log_file:
            error_log_file.write(str(err.error_log))
    
    except:
        rprint('[red]Unknown error, exiting.[/red]')
    
# validate against DIGGS schema:
    try:
        diggs_schema.assertValid(doc)
        rprint('[green]No schema error is detected.[/green]')
    
    except etree.DocumentInvalid as err:
        rprint('[red]DIGGS Schema validation Error, see [bold]error_schema.log[/bold] file in detail. [/red]')
        with open('error_schema.log', 'w') as error_log_file:
            error_log_file.write(str(err.error_log))
    
    except:
        rprint('[red]Unknown error![/red]')