<?xml version="1.0" encoding="utf-8"?>
<!--
  Adapted from DIGGSml/validation/modules/diggs_schematron_rules.sch for lxml / ISO Schematron.

  Changes from upstream:
  - queryBinding xslt3 -> xslt1 (lxml.isoschematron)
  - contexts use local-name() so DIGGS 2.5.a, 2.6, and 3.0 (schemas/3) all match
  - removed XSLT3 unit-conversion API function and the casing diameter
    cross-unit rule that depended on it (same-uom comparison retained)
  - element order adjusted for lxml's ISO Schematron RELAX NG
-->
<schema xmlns="http://purl.oclc.org/dsdl/schematron"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  queryBinding="xslt1">

  <ns prefix="gml" uri="http://www.opengis.net/gml/3.2"/>
  <ns prefix="xlink" uri="http://www.w3.org/1999/xlink"/>
  <ns prefix="xsi" uri="http://www.w3.org/2001/XMLSchema-instance"/>

  <pattern id="DIGGS-validation-rules">
    <rule context="//*[local-name()='Borehole']/*[local-name()='totalMeasuredDepth']">
      <assert test="number(.) &gt; 0">totalMeasuredDepth must be positive</assert>
    </rule>
    <rule context="//*[local-name()='DriveSet']/*[local-name()='penetration']">
      <assert test="number(.) &gt;= 0">Penetration depth must be positive</assert>
    </rule>
    <rule context="//*[local-name()='DrivenPenetrationTest']/*[local-name()='hammerEfficiency']">
      <assert test="number(.) &gt;= 0 and number(.) &lt;= 100">Energy efficiency must be between 0 and 100</assert>
    </rule>
    <rule context="//*[local-name()='Casing']/*[local-name()='casingOutsideDiameter']">
      <assert test="number(.) &gt; 0">casingOutsideDiameter must be positive</assert>
    </rule>
    <rule context="//*[local-name()='Casing']/*[local-name()='casingInsideDiameter']">
      <assert test="number(.) &gt; 0">casingInsideDiameter must be positive</assert>
    </rule>
    <rule context="//*[local-name()='AbstractLinearSamplingFeature']/*[local-name()='plunge']">
      <assert test="number(.) &gt;= -45 and number(.) &lt;= -10">Plunge must be between -45 and -10 degrees</assert>
    </rule>
    <rule context="//*[local-name()='CasagrandeTrial']/*[local-name()='waterContent']">
      <assert test="number(.) &gt;= 0">waterContent must be non-negative</assert>
    </rule>
    <rule context="//*[local-name()='CasagrandeTrial']/*[local-name()='blowCount']">
      <assert test="number(.) &gt;= 0">blowCount must be non-negative</assert>
    </rule>
    <rule context="//*[local-name()='FieldProperties']/*[local-name()='plasticity']">
      <assert test="number(.) &gt;= 0">plasticity must be non-negative</assert>
    </rule>
    <rule context="//*[local-name()='Cement']/*[local-name()='weight']">
      <assert test="number(.) &gt; 0">weight must be positive</assert>
    </rule>
    <rule context="//*[local-name()='Cement']/*[local-name()='specificGravity']">
      <assert test="number(.) &gt; 0">specificGravity must be positive</assert>
    </rule>
    <rule context="//*[local-name()='SpecimenConditions']/*[local-name()='voidRatio']">
      <assert test="number(.) &gt;= 0">voidRatio must be non-negative</assert>
    </rule>
    <rule context="//*[local-name()='Grading']/*[local-name()='percentPassing']">
      <assert test="number(.) &gt;= 0">percentPassing must be non-negative</assert>
    </rule>
    <rule context="//*[local-name()='Grading']/*[local-name()='percentRetained']">
      <assert test="number(.) &gt;= 0">percentRetained must be non-negative</assert>
    </rule>
    <rule context="//*[local-name()='Grading']/*[local-name()='weightRetained']">
      <assert test="number(.) &gt;= 0">weightRetained must be non-negative</assert>
    </rule>
    <rule context="//*[local-name()='Grading']/*[local-name()='particleSize']">
      <assert test="number(.) &gt; 0">particleSize must be positive</assert>
    </rule>
    <rule context="//*[local-name()='AbstractTrialGroutBatch']/*[local-name()='specificGravityMix']">
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
