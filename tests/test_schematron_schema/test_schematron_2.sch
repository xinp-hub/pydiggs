<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <sch:ns prefix="diggs" uri="http://diggsml.org/schemas/2.6"/>
  <sch:ns prefix="gml" uri="http://www.opengis.net/gml/3.2"/>

<sch:pattern>
  <sch:rule context="diggs:totalSampleRecoveryLength">
    <sch:let name="length" value="number(.)"/>      
    <sch:assert test="$length >=0 ">'totalSampleRecoveryLength' must be greater than or equal to 0.</sch:assert>
  </sch:rule>
</sch:pattern>


</sch:schema>
