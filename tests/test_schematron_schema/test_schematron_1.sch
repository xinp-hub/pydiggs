<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <sch:ns prefix="diggs" uri="http://diggsml.org/schemas/2.5.a"/>
  <sch:ns prefix="gml" uri="http://www.opengis.net/gml/3.2"/>

<sch:pattern>
  <sch:rule context="/*">
    <sch:assert test="name()='Diggs'">Name of root is not 'Diggs'.</sch:assert>
  </sch:rule>
</sch:pattern>

<sch:pattern>
  <sch:rule context="diggs:Project">
    <sch:assert test="@gml:id='Project_1111-2222-33'">Project ID incorrect.</sch:assert>
  </sch:rule>
</sch:pattern>

</sch:schema>
