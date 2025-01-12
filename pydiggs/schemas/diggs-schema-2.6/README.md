This is a new production release of DIGGS, with many enhancements to the schema as compared to v. 2.5.a. Legacy instance documents should verify under v. 2.6, but new instances should be written to this standard.

Please post any issues with this release (compatibility or otherwise) to: [https://github.com/DIGGSml/schema-dev/issues](https://github.com/DIGGSml/schema-dev/issues)

## Resources
### schemaLocation for this version
http://diggsml.org/schemas/2.6/Diggs.xsd (Note: this is changed from Complete.xsd or Kernel.xsd in 2.5.a)
### XML Namespace
 http://diggsml.org/schemas/2.6
### Example instances
https://github.com/DIGGSml/diggs-examples/tree/master/2.6%20Example%20instances

## What's Changed
Latest patch involved updates to imports and includes in schema files. No impact to instances.
**Full Changelog from 2.5.a**: https://github.com/DIGGSml/schema-dev/compare/2.5.a...2.6
## Summary of Changes

- DIGGS now uses WITMSL 2.0 unit symbols and measure types, which greatly expands available units of measure. We have noted some incompatibility with WITSML 1.0 used in versions 2.5.a and have addressed those we've noted. Please post an [issue](https://github.com/DIGGSml/schema-dev/issues) if you note any problems.
- Two new DIGGS object classes (top level properties) have been added: constructionActivity and program. These extensions are designed to handle project construction activities as design and construction specifications.
- Extensions added to support rock, permeation, compaction, jet and deep soil mixing grouting. These extensions are in the new Construction.xsd  schema document.
- Extensions added to support processed and field geophysical measurements. These additions are in the Geophysics.xsd schema document.
- Updated Lithology object to better accommodate USCS and AASHTO group names and symbols.
- Many new test procedures have been added. Go [here](https://diggsml.org/docs/procedures.html) to view currently supported test procedures.
- Main schema file now changed to Diggs.xsd (from Complete.xsd or Kernel.xsd).
- Several GML time measure types have been added to gml3.2Profile_diggs.xsd.
- Added gml:File and gml:FileType to gml3.2Profile_diggs.xml.
- diggs:UnifitedDateTimeType deprecated and replaced with gml:TimePositionType.
- Numerous object definitions have been added or updated.
- Updated dictionary_diggs.xsd to include DIGGS Definition object 
- Numerous under the hood that have no effect on instances or validation (type element names, removal of orphaned elements.
- 
