<?xml version="1.0" encoding="utf-8"?>
<!--
  Adapted from DIGGSml/validation/modules/diggs_schematron_rules.sch for lxml / ISO Schematron.

  Changes from upstream:
  - queryBinding xslt3 -> xslt1 (lxml.isoschematron)
  - contexts use local-name() so DIGGS 2.5.a, 2.6, and 3.0 (schemas/3) all match
  - removed XSLT3 unit-conversion API function and the casing diameter
    cross-unit rule that depended on it (same-uom comparison retained)
  - element order adjusted for lxml's ISO Schematron RELAX NG
  - parent/child contexts written as *[local-name()='C'][parent::*[local-name()='P']]
    rather than //*[local-name()='P']/*[local-name()='C']: the same elements match,
    but libxslt no longer slows down with the square of the number of siblings
-->
<schema xmlns="http://purl.oclc.org/dsdl/schematron"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  queryBinding="xslt1">

  <ns prefix="gml" uri="http://www.opengis.net/gml/3.2"/>
  <ns prefix="xlink" uri="http://www.w3.org/1999/xlink"/>
  <ns prefix="xsi" uri="http://www.w3.org/2001/XMLSchema-instance"/>

  <pattern id="DIGGS-validation-rules">
    <rule context="*[local-name()='totalMeasuredDepth'][parent::*[local-name()='Borehole']]">
      <assert test="number(.) &gt; 0">totalMeasuredDepth must be positive</assert>
    </rule>
    <rule context="*[local-name()='penetration'][parent::*[local-name()='DriveSet']]">
      <assert test="number(.) &gt;= 0">Penetration depth must be positive</assert>
    </rule>
    <rule context="*[local-name()='hammerEfficiency'][parent::*[local-name()='DrivenPenetrationTest']]">
      <assert test="number(.) &gt;= 0 and number(.) &lt;= 100">Energy efficiency must be between 0 and 100</assert>
    </rule>
    <rule context="*[local-name()='casingOutsideDiameter'][parent::*[local-name()='Casing']]">
      <assert test="number(.) &gt; 0">casingOutsideDiameter must be positive</assert>
    </rule>
    <rule context="*[local-name()='casingInsideDiameter'][parent::*[local-name()='Casing']]">
      <assert test="number(.) &gt; 0">casingInsideDiameter must be positive</assert>
    </rule>
    <rule context="*[local-name()='plunge'][parent::*[local-name()='AbstractLinearSamplingFeature']]">
      <assert test="number(.) &gt;= -45 and number(.) &lt;= -10">Plunge must be between -45 and -10 degrees</assert>
    </rule>
    <rule context="*[local-name()='waterContent'][parent::*[local-name()='CasagrandeTrial']]">
      <assert test="number(.) &gt;= 0">waterContent must be non-negative</assert>
    </rule>
    <rule context="*[local-name()='blowCount'][parent::*[local-name()='CasagrandeTrial']]">
      <assert test="number(.) &gt;= 0">blowCount must be non-negative</assert>
    </rule>
    <rule context="*[local-name()='plasticity'][parent::*[local-name()='FieldProperties']]">
      <assert test="number(.) &gt;= 0">plasticity must be non-negative</assert>
    </rule>
    <rule context="*[local-name()='weight'][parent::*[local-name()='Cement']]">
      <assert test="number(.) &gt; 0">weight must be positive</assert>
    </rule>
    <rule context="*[local-name()='specificGravity'][parent::*[local-name()='Cement']]">
      <assert test="number(.) &gt; 0">specificGravity must be positive</assert>
    </rule>
    <rule context="*[local-name()='voidRatio'][parent::*[local-name()='SpecimenConditions']]">
      <assert test="number(.) &gt;= 0">voidRatio must be non-negative</assert>
    </rule>
    <rule context="*[local-name()='percentPassing'][parent::*[local-name()='Grading']]">
      <assert test="number(.) &gt;= 0">percentPassing must be non-negative</assert>
    </rule>
    <rule context="*[local-name()='percentRetained'][parent::*[local-name()='Grading']]">
      <assert test="number(.) &gt;= 0">percentRetained must be non-negative</assert>
    </rule>
    <rule context="*[local-name()='weightRetained'][parent::*[local-name()='Grading']]">
      <assert test="number(.) &gt;= 0">weightRetained must be non-negative</assert>
    </rule>
    <rule context="*[local-name()='particleSize'][parent::*[local-name()='Grading']]">
      <assert test="number(.) &gt; 0">particleSize must be positive</assert>
    </rule>
    <rule context="*[local-name()='specificGravityMix'][parent::*[local-name()='AbstractTrialGroutBatch']]">
      <assert test="number(.) &gt; 1.0">Specific gravity mix must be greater than water (1.0)</assert>
    </rule>

    <!-- Same-uom diameter relationship (cross-unit Geosetta API rule dropped for lxml) -->
    <rule context="*[local-name()='Casing'][*[local-name()='casingOutsideDiameter'] and *[local-name()='casingInsideDiameter']]">
      <let name="outsideDiameter" value="number(*[local-name()='casingOutsideDiameter'])"/>
      <let name="insideDiameter" value="number(*[local-name()='casingInsideDiameter'])"/>
      <let name="outsideUom" value="string(*[local-name()='casingOutsideDiameter']/@uom)"/>
      <let name="insideUom" value="string(*[local-name()='casingInsideDiameter']/@uom)"/>
      <assert test="not(string($outsideDiameter) = 'NaN') and not(string($insideDiameter) = 'NaN')">
        Both casing diameter values must be numeric.
      </assert>
      <assert test="not($outsideUom = $insideUom) or $outsideDiameter &gt; $insideDiameter">
        casingOutsideDiameter must be greater than casingInsideDiameter when units match.
      </assert>
    </rule>
  </pattern>
</schema>
