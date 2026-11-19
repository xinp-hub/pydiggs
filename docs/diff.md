# Comparing two files

`pydiggs diff` and `pydiggs.diff.compare()` say what changed between two DIGGS files:
which boreholes, samples, tests and other records were added or removed, and which values
changed in the ones both files hold. The comparison is of what the files **describe**, not
of how they are written: element order, number formatting, line numbers and the `gml:id`
of a record do not matter. Records stay paired when their `gml:id`s change, so two exports
of the same data with newly generated ids are identical. The renamed ids are counted, and
listed on request.

<!-- tutorial: diff -->

**Compare the files.** Given two files (paths or bytes), the comparison is the full
one: it covers what [`pydiggs.read`](model.md) puts in a model and, beyond that, the
content of each record that a model does not keep. Given two `Model`s, it compares the
two models, and can only see what a model holds; a file and a `Model` are compared as
two models. See [Files and models](#files-and-models).

Typical uses: checking a revised deliverable against the previous one, checking that an
export from a new software version still holds the same data, and checking a file after a
round trip through another format.

!!! info "Stable from pyDIGGS 1.13"
    `pydiggs.diff` and `pydiggs diff` are stable from pyDIGGS 1.13 (a preview in 1.12)
    and follow [Semantic Versioning](https://semver.org/): what they compare, what
    they report and the report's format change only in a major release, except that a
    minor release may report a difference that was missed. The report is a
    **diggs-diff 1.0** document; the 1.0-beta.1 documents of pyDIGGS 1.12 are still
    read (see [The JSON document](#the-json-document)).

It needs
nothing beyond the core install, and it reads any DIGGS version
[`pydiggs.read`](model.md) reads. The two files may be different DIGGS versions.

- **JSON Schema of the result:**
  [`schemas/diggs-diff-1.0.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json)
  (also `pydiggs.diff.diff_schema()`; `pydiggs.diff.DIFF_SCHEMA_URL` and
  `pydiggs.diff.DIFF_SCHEMA_VERSION`).
- **API reference:** [Comparing files](reference.md#comparing-files).

## Comparing

=== "Command line"

    ```console
    $ pydiggs diff site-rev1.xml site-rev2.xml
    Compared site-rev1.xml (DIGGS 3.0.0) with site-rev2.xml (DIGGS 3.0.0)
    The two files differ.
    document: 0 added, 0 removed, 1 changed, 0 unchanged
    projects: 0 added, 0 removed, 0 changed, 1 unchanged
    features: 0 added, 0 removed, 1 changed, 0 unchanged
    measurements: 0 added, 0 removed, 0 changed, 1 unchanged
    others: 0 added, 0 removed, 0 changed, 1 unchanged
    components: 0 added, 0 removed, 0 changed, 2 unchanged
    changed document Bdoc1, matched by gml:id
        creation_date: 2022-08-17 -> 2022-09-02
    changed features GP_ArealSurvey_bbg_yyp_r3c (Aeromag Survey Area), matched by gml:id
        params.totalSurveyArea: 1 km2 -> 1.2 km2
    $ echo $?
    1
    ```

    The first file is the earlier one (`OLD`), the second the later one (`NEW`):
    "added" means in `NEW` only. See [the command](#the-command) for the options and
    the exit codes.

=== "Python"

    ```python
    from pydiggs import diff

    d = diff.compare("site-rev1.xml", "site-rev2.xml")
    d.identical  # False
    d.complete  # True: no limit stopped the comparison
    d.summary["features"]  # {'added': 0, 'removed': 0, 'changed': 1, 'unchanged': 0}
    for change in d.changes:  # a record that was added, removed or changed
        print(change.op, change.collection, change.new_key or change.old_key)
        for value in change.values:  # each value that differs in it
            print("   ", value.path, value.old, "->", value.new)
    d.to_text()  # the same report as the command prints, with rich markup escaped
    d.to_json(indent=2)  # a diggs-diff document
    ```

    `compare()` takes two paths, the bytes of two files, or two
    [`Model`](model.md) objects already read, in any mix. `from pydiggs import diff`
    loads the module; `import pydiggs` alone does not.

## What is compared

Both files are read with [`pydiggs.read`](model.md), and the two models are compared:

- every record of every collection: the document information, projects, associates,
  features (boreholes, soundings, wells …), sampling activities, samples, observations,
  lithology layers, measurements (tests), other objects, and the components nested in
  them (casing, backfill, drive sets, water strikes, test apparatus);
- each record's name, description and remarks, its parameters (`params`), its typed
  fields (depths, dates, locations, intervals) and its links to other records;
- everything the model keeps as `extra` (elements pyDIGGS has no typed field for),
  compared by structure;
- the result tables: their columns and every cell;
- **between two files, what the model does not keep.** A typed field reads part of an
  element (the position out of a point location, the cells out of a result set) and the
  model keeps that part. The rest is compared too, as part of the record, under paths
  that start with `xml.`: the unit labels, axis labels and dimension of a location, the
  geometry of a layer, a grid's origin and offset vectors, source and receiver
  positions, the files of a data package, a column's property class, declared type and
  index, the reason of a null value, the separators and decimal mark of a result set,
  and any other attribute or text no field holds. See
  [Files and models](#files-and-models);
- **between two files, what the file holds outside every record**: the attributes of
  its root element (other than `xsi:schemaLocation` and its `gml:id`) and any text
  before, between or after its members. They belong to the document, and are compared
  with its document information (see [Outside every record](#outside-every-record)).

A link is compared by what it points to, not by the text of its `href`: if a sample
still belongs to the same borehole, renaming that borehole's `gml:id` is not a change of
the sample. See [Ids and references](#ids-and-references).

### Files and models

A model is what `pydiggs.read` makes of a file: every record, with typed fields for what
pyDIGGS understands and `extra` for the elements it has no field for. An element a typed
field reads from is not kept whole. A borehole's `referencePoint` gives the model the
position and its coordinate system, and the model has no place for the `uomLabels` next
to them.

When `compare` is given **two files**, the reader hands it, with each model, what the
file holds of every record that the model does not: each attribute and each text that
no typed field holds, in every element that is not kept in `extra`. It is compared like
`extra`, value by value:

```console
$ pydiggs diff rev1.xml rev2.xml
…
changed features bh_TEST_HOLE_1 (TEST HOLE 1), matched by gml:id
    xml.Borehole[1].referencePoint[1].PointLocation[1]@uomLabels: deg deg m -> deg deg ft
```

- A path starts with `xml.` and follows the elements of the file below the record
  (`@name` for an attribute, `#text` for a text). Same-named siblings are compared in
  any order, ids are not compared and a reference is compared by what it points to, as
  everywhere else.
- A value a typed field holds is compared once, by its field, with the tolerance and
  with no regard to how a number is written. It is not compared again as text.
- A text is compared word by word, and a word that is a number as a number, within the
  same tolerance. So the coordinates of a layer's geometry or of a grid's origin are
  numbers here too, and the space between them is not compared.
- A result value no column holds is kept as it is written, with its row and column
  (`….dataValues[1].value[1]`): a value that is not a number in a numeric column (the
  model reads it as null, so `abc` and `xyz` would be the same null), and a value
  beyond the declared columns, empty or not (the model cuts the row). A row with fewer
  values than columns has its number of values written down (`….dataValues[1].row[1]`),
  so one more separator at the end of a row is one more value.
- Text after a comment, a processing instruction or a child element
  (`<gml:name>B<!-- note -->01</gml:name>`: the model reads `B`) is compared as
  `#tail`, in elements kept in `extra` too. The comment itself is not compared.
- What is left of an element stays with what typed fields hold of it. Two `gml:name`
  that exchange their `codeSpace`, or two columns that exchange their declared type,
  are a difference; the same elements in another order are not. The price: where
  same-named siblings both change a typed value and their order, what is left of them
  is compared in the order of the file and may be listed.
- An element whose whole content typed fields hold has no value listed again. It is
  still an element of the file: one that is renamed, added or removed is listed as a
  count (`xml.Borehole.remark.Remark#elements: 3 -> 2`).
- A link (`xlink:href`) is compared as one of the record's `links`. Where its element
  has more than the link, the link is also compared with the element, by what it names,
  so that two such elements that exchange their links are seen. A changed link is
  listed once, in `links` (see [Each edit once](#each-edit-once)).
- This content counts toward the reader's work limit (100,000 elements plus one per
  200 bytes of the file) and the time budget. A file with more of it is compared in
  part, and the comparison is incomplete (`old_read_incomplete`,
  `new_read_incomplete`).

### Each edit once

Some content of the file is read by a typed field and kept as it is written as well. An
edit to it is listed once, where the typed field reports it:

- A column's declared type (`typeData`): the column's `type` says what kind of value
  it reads (`number`, `boolean`, `datetime`, `string`). `boolean` changed to `string`
  is listed as the column's `type`, not again as `xml.….typeData[1]#text`. A change
  that the column does not show (`double` to `integer`, both numbers) is listed as the
  `typeData` text, and `double` against `float` is only written differently
  (see [What does not count as a change](#what-does-not-count-as-a-change)).
- Unit labels (`uomLabels`) that give a result table's index or extent its units: the
  index column's or the extent's unit is listed (`….domain.index[position].uom`); a
  label no column reads stays content of the file and is listed there.
- A link on an element that holds more than the link (`<projectRef xlink:href="#p"
  role="owner"/>`), in `extra` or in the file: listed in `links`. Two such elements
  that exchange their links leave `links` the same, and are listed at the elements.

A repeat is dropped only when the typed change it repeats is listed in the same record:
one listed change takes away one repeat. Anything that cannot be matched to one is
listed. The counts (`value_changes`) count each edit once too.

### Outside every record

A file holds a few things outside every record: the attributes of its root element
(`<Diggs … note="rev 2">`) and any text before, between or after its members. Between
two files they are compared as one more element, named as the root, with the
document's own record (`documentInformation`, which DIGGS requires):

```console
changed document di_1, matched by gml:id
    xml.Diggs[1]@note: rev 1 -> rev 2
```

When the two files have no pair of document records (one has none, or they were not
paired), the root is listed on its own, as element `Diggs` of collection `document`
with the key `@/Diggs[1]`, matched by natural key, and counted as a changed document if
it differs. The text outside the members is
compared word by word, a number as a number, and joined in the order of the file, so
moving it from one gap between members to another is not a change. Not compared: the
root's `gml:id` (an id), `xsi:schemaLocation` (where the schema is), namespace
declarations, comments, processing instructions and white space.

When `compare` is given **two `Model`s**, none of this exists, and the comparison says
whether the two models differ. Two files that differ only in content a model does not
keep give two equal models: comparing the models reports no difference, and comparing
the files reports one. A file and a `Model` are compared as two models. This is also why
a reference to an element the model does not keep can only be established between two
files (see [Ids and references](#ids-and-references)).

A `Model` from `pydiggs.read` carries the sha256 of the file it was read from, and the
report gives it as `old.sha256` and `new.sha256`. **A `Model` you build yourself** must
set `source.sha256` to a 64-digit lowercase hex digest, as the
[diggs-model schema](model.md) requires: the comparison copies it into the report
unchanged, and a report holding any other value (such as an empty string) is not a valid
diggs-diff 1.0 document.

## What does not count as a change

- **The `gml:id` of a record.** An id says which record is which inside one file; it is
  not data about the borehole or the sample. When two records are paired by their natural
  key (see [Matching](#matching)) and their ids differ, the record is *renamed*, not
  changed: it counts as unchanged unless one of its values differs. Renamed records are
  counted (`Diff.renamed`) and listed only on request (`--verbose`, `show_renamed=True`),
  each with its old and new id. Two files that differ only in this way are identical.
  With `--match gml-id` there is no pairing by natural key, so a record whose id changed
  is removed and added.
- **The `gml:id` of anything else**: a geometry, a time instant or any other element
  inside a record. These ids are not compared and not counted.
- **A link whose target was only renamed.** A link is compared by the record or element
  it points to, so renaming a borehole's `gml:id` changes none of the samples that refer
  to it. See [Ids and references](#ids-and-references).
- **The order** of elements in the file, and of rows in a table whose rows are matched by
  depth.
- **How a number is written**, where pyDIGGS reads a number: a quantity with a unit
  (`<totalMeasuredDepth uom="ft">1.50</totalMeasuredDepth>` and `1.5`), a coordinate and
  a numeric cell of a result table. `1.50` and `1.5` are then the same value. A quantity
  written differently is counted as *reformatted* and listed only on request
  (`--verbose`, `show_reformatted=True`). Two files that differ only in this way are
  identical. A value pyDIGGS keeps as text is compared as text: a parameter with no unit
  (`<someDepth>1.50</someDepth>` against `1.5`) is listed as changed.
- **Where a record sits in the file**: line numbers and XPaths.
- **Namespace prefixes** of elements (`gml:pos` and `g:pos` for the same namespace).
- **The file itself**: its name, size, encoding, indentation and other white space,
  comments, processing instructions and `schemaLocation`. The DIGGS version of each file is recorded in the result but is not a
  change.
- **How a result set's values are written**, when every value reads the same. Another
  column separator (`cs`) or row separator (`ts`), with the values written with it,
  and a separator written out with its default value (`cs=","`) or left out, are
  *reformatted*. "Reads the same" means that pyDIGGS reads, with each file's own
  separators, the same rows of the same values: each cell is compared, a number as a
  number within the tolerance, and a value no column holds as it is written. If one
  value differs, that value is a change, and the separators are still only
  reformatted; a separator changed while the values were left as they were splits
  them differently, and those values are changes. The decimal mark (`decimal`) is not
  read: pyDIGGS reads a number written with a point. Writing `decimal="."` out or
  leaving it out is reformatted; another decimal mark (`","`) is a change, and so are
  values written with it (`0,5` is not a number pyDIGGS reads).
- **How a column's type is declared**, when it reads the same values: `double` and
  `float` (both binary floating-point numbers), and the same name in other letter case
  (`Double`), are *reformatted*. Any other change of `typeData` is a change, also when
  the column reads the same kind of value (`double` to `integer`).

A **unit change is always a change**. `5 ft` against `1.524 m` is reported as changed:
nothing is converted, because a comparison that converts would hide exactly the kind of
edit a reviewer needs to see.

Text is compared exactly, as [`pydiggs.read`](model.md) gives it, upper and lower case
included. A code is compared by its value and its code space.

The namespace prefix of an attribute in `extra` is compared as written, because pyDIGGS
does not interpret it: a file that renames a prefix shows the attribute as removed and
added. The id attribute is the exception: `gml:id` is recognised under any prefix.

### Ids and references

An id is a label inside one file, so no `gml:id` is compared: not a record's, and not
that of a geometry, a time instant or any other element inside a record (in `extra`). An
element that has an id in one file and none in the other is not a change either.
`Diff.renamed` counts records only.

A reference to something in the same file (`xlink:href="#x"`, `srsName="#x"`) is
compared by **what it points to**, never by the text of the id. Only those two
attributes are treated as references: an id written another way (in another attribute,
as the text of an element, or as `file.xml#x`) is compared as text.

| the reference names | it is the same reference when |
|---|---|
| a record | the two records are paired |
| an element inside a record that the model keeps in `extra` (a geometry, a time instant) | the two elements sit in paired records, at the same place among the elements compared |
| an element inside a record that pyDIGGS reads into typed fields and does not keep (a borehole's centre line or reference point) | the two elements sit in paired records, at the same path below the record, and no step of that path could be another element (see below) |
| such an element with no path of its own (one of two `centerLine`) | the two elements sit in paired records, at the same position among their siblings, and hold the same content (see below) |
| a borehole's own depth system (its linear referencing system) | the two boreholes are paired |
| a coordinate system a record keeps in `extra` (a site grid defined in the document information) | as an element inside `extra`: paired records, the same place |
| a coordinate system defined in the file that no record keeps | it sits at the same path below paired records (as the row for typed fields), or pyDIGGS reads the same name, unit and axes for both and the file defines no other system, referenced or not, with that name and unit. This needs the two files |

So a file that was exported again with every `gml:id` generated afresh, and every
reference following, compares as the same data. The common case of the third row is a
borehole's linear referencing system naming the borehole's centre line (`linearElement`).

The model does not keep an element it reads into typed fields, nor its id. When
`compare` is given the two **files** (paths or bytes), the reader notes, as it reads,
where each such element sits: the record that holds it and its path below the record
(`centerLine/LinearExtent`). A path counts only when every element on it is the only
child of its parent with that name, whatever its namespace: then it names one element
whatever the order of the file. An element with a same-named sibling anywhere on the
way (the second of two `centerLine`) has no such path, and neither has one more than 16
elements below its record. Nothing is read twice and no file is parsed a second time.

An element with no such path is told by its **position and its content**: the record
that holds it, its position among its siblings at every step below the record, and what
it holds (its attributes, text and children, in any order, without its `gml:id`). The
text of its id never decides. So when two centre lines exchange their ids, a reference
that kept its text is listed as changed: it names the other line. The content is part
of the place because positions alone can mislead: two same-named siblings may change
places while the reference moves to the other one, and it would name "the first" in
both files. The same holds for an id that two elements of the file have (the reference
is compared by where both sit and what both hold) and for a file with more identified
elements than the reader's work limit (100,000 plus one per 200 bytes of the file).

This errs on the side of reporting. A reference to such an element is also listed as
changed when the element only moved among its same-named siblings, when its content was
edited (the edit is listed as well, if the model keeps that content), or when its
namespace prefix changed. The text is then the same on both sides and the element it
names is not, so the new side says so: `#a -> #a (another target)`.

Two repeated elements that are equal except for what their references point to are not
equal: if the ids of two points change places, a reference that kept its text names
another point and is listed as changed. The same holds for an element nested deeper than
the 64 levels compared one by one: it is compared whole, by what its references point
to.

Repeated elements that hold such references and come in another order, with new ids
(two `usedPoint` with the roles `top` and `base`, reversed), are the same data when each
still names its target. One limit: if any of those repeated elements has a `gml:id`
inside, they are compared in the order of the file, and a reordering of them is listed
as changes. An element with an id is placed by its order, so taking its siblings out of
that order could make references to two different elements look alike.

**Only the files can establish what such a reference points to.** A `Model` keeps
neither an element pyDIGGS read into typed fields nor its id, so with a `Model` on
either side the reference is compared by the record that holds the element and the text
of the id (see below). That has two consequences. Two models of a file exported again
with new ids list those references as changed. And if two such elements of **one
record** exchange their ids (a borehole's reference point and its centre line), the two
models are equal: comparing them reports no difference, where comparing the two files
does. The same goes for two coordinate systems no record keeps that read the same. When
a reference to such an element matters, give `compare` the files.

A link to an element inside a record is shown as the record's name and the link as
written (`B-1 #p2`), so that two links into one record can be told apart.

A reference that names nothing pyDIGGS can place is compared **as text**, as it is
written:

- a link to another document, or to an id that is not in the file;
- a link to a record that only one of the two files has (the record is listed as added
  or removed);
- two elements with exactly the same content at the same place in the `extra` of one
  record: nothing tells them apart, so a reference to one of them is compared by its id;
- a reference to an element that pyDIGGS reads into typed fields and does not keep,
  **when either side is a `Model`** and not a file: a model no longer has the file, so
  there is nothing to place the element by. Two models of a re-exported file with
  boreholes therefore list, per borehole, the reference of its depth system to its
  centre line
  (`extra.linearReferencing[1].LinearSpatialReferenceSystem[1].linearElement[1]@xlink:href`
  and `links.linearElement`), and any other link to an element the model does not keep;
  give `compare` the files to have them resolved;
- a coordinate system that no record keeps, when either side is a `Model` (a model lists
  the systems that are referenced, not those the file defines, so it cannot say that a
  grid is the only one of its kind), or when the file defines two that read the same.

Comparing as text reports a difference when only an id changed. Between two files it
does not hide one: every element of a file is placed by one of the rows above, and what
is left to its text names nothing in the file, a record that is listed as added or
removed anyway, or one of two elements that nothing tells apart. Between two models it
can hide one, in the case described above: two elements that the model does not keep
exchange their ids.

A link the model lists in `links` to an element whose place cannot be established is
compared by the record that holds the element **and** the text of the link. A link
moved to an element of another record is therefore always a difference; the price is
that it is also listed when only the id changed.

Resolving by path can also report a difference where a person would see none: if one
file has a single `centerLine` and the other has two, the reference has a path in one
file and a position in the other, and is listed as changed.

Records that only their `gml:id` told apart (two layers with no depths in the same
observation) cannot be paired once the ids change: they share a natural key, are listed
as removed and added, and a `diff.ambiguous-match` note says so (see
[Matching](#matching)).

## Matching

Records are paired in two passes (`--match auto`, the default):

1. **By `gml:id`.** Two records with the same `gml:id` and the same element name are the
   same record.
2. **By natural key**, for the records still unpaired on both sides:

    | record | natural key |
    |---|---|
    | feature (borehole, sounding …) | its kind and name |
    | sampling activity, sample | its borehole, name and top depth |
    | observation | its borehole, classification system and name |
    | lithology layer | its borehole (through its observation), top and base |
    | measurement (test) | its borehole, test type, depth and samples |
    | component | the record that holds it, its role, and its index or position |
    | anything else | its name |

    The element name is part of every key, so a `Borehole` is never paired with a
    `Sounding` of the same name.

The report says how each listed record was paired (`matched by gml:id` or
`matched by natural key`). A record paired by its natural key whose `gml:id` differs is
*renamed*. That is [not a change](#what-does-not-count-as-a-change): the summary counts it
as unchanged if its values are the same, and as changed if one differs. The report gives
the number of renamed records, and `--verbose` (`show_renamed=True`) lists them:

```console
$ pydiggs diff export-monday.xml export-tuesday.xml --verbose
Compared export-monday.xml (DIGGS 3.0.0) with export-tuesday.xml (DIGGS 3.0.0)
The two files are identical.
features: 0 added, 0 removed, 0 changed, 1 unchanged
samples: 0 added, 0 removed, 0 changed, 1 unchanged
2 record(s) have a different gml:id (matched by natural key; not a change).
renamed features bh-7f3a (B-1), matched by natural key (bh-01 -> bh-7f3a)
    gml_id: renamed: bh-01 -> bh-7f3a
renamed samples sa-91c2 (S-1), matched by natural key (sa-01 -> sa-91c2)
    gml_id: renamed: sa-01 -> sa-91c2
$ echo $?
0
```

A natural key that more than one record has, in either file, is **never matched**: two
boreholes both named `B-1` with different ids cannot be told apart, so pyDIGGS does not
guess. Those records are listed as removed and added, and a note
`diff.ambiguous-match` names the key. At most 50 such notes are written, then one that
counts the rest.

`--match gml-id` (`match="gml-id"`, or `gml_id` as the report spells `matched_by`) runs
the first pass only: use it when the ids are known to be stable and a record whose id
changed should show as removed and added. The report gives the option as `gml-id`. `--match natural` runs the
second pass only: use it when the ids of the two files are unrelated, so that an id which
happens to appear in both does not pair two different records.

## Tolerances

Two numbers are equal when

```text
|a − b| ≤ max(abs_tol, rel_tol × max(|a|, |b|))
```

The defaults are `rel_tol = 1e-9` and `abs_tol = 0`: exact, apart from the noise of
binary floating point. `rel_tol` is 0 or more and less than 1 (at 1 every number would
equal 0), and `abs_tol` is 0 or more; anything else is refused. To ignore differences
below a reporting precision, raise one of them:

```bash
pydiggs diff old.xml new.xml --abs-tol 0.005     # values within 0.005 of each other are equal
pydiggs diff old.xml new.xml --rel-tol 0.001     # values within 0.1 % are equal
```

`abs_tol` is in each value's own unit and applies to every number in the file, depths
and results alike, so choose it with the smallest quantity in mind. A tolerance never
makes two different units equal.

## Result tables

Columns are paired by their property key (the DIGGS dictionary key), or by name when a
column has no key: data columns and index columns alike, so their order is not data. A
column only one file has is an added or removed column.

Rows are paired by the table's **depth index** (a CPT's depth, an interval's top and
base) when every row of both tables has a different index value. Rows are then matched
by depth, within the tolerance, in whatever order they are written, and a reading
inserted in the middle of a sounding shows as one added row rather than as a change of
every row after it. When the index is missing or repeats, or the two tables are not
indexed by the same columns, rows are paired by row number.

A changed cell is listed with its column and row, for example
`results[0].columns[tip_resistance].cells[@1.5]` (the cell at depth 1.5) or
`cells[12]` (row 12, when rows are paired by number). A row only one file has is listed
as an added or removed cell for each of its columns.

## Limits and incomplete comparisons

Both files are untrusted input, so the work is bounded:

| limit | default | when it is reached |
|---|---|---|
| `--max-bytes`, `max_bytes` | 100 MiB per file | the file is refused (exit 2, `InstanceReadError`) |
| `--max-changes`, `max_changes` | 10,000 | the list of changes is cut; the counts in the summary stay exact; note `diff.limit`. Renamed ids and reformatted values take room in the list only when they are listed (`--verbose`) |
| `--time-budget`, `time_budget` | none | the comparison stops, reading included; the counts are partial; note `diff.timeout` |

A value longer than 200 characters is shortened in the report and marked (`truncated`
in JSON, `(shortened)` in text). A `path` is at most 1,000 characters, however deep the
element it names and however long the names on the way: a longer one keeps its first
200 and its last 400 characters around a digest of the whole path (`…#` and 32
hexadecimal digits), so two different paths stay different. The XPath in `at` is
bounded in the same way.

When a limit cuts the list or stops the work, or something could not be looked at, the
result is **incomplete**: `Diff.complete` is false, `Diff.limits_hit` names why, and
`Diff.identical` is false even if no difference was found, because an incomplete
comparison cannot say the files are the same. The command exits 1.

| `limits_hit` | meaning |
|---|---|
| `max_changes` | the list of changes was cut |
| `time_budget` | the time budget ran out |
| `old_read_incomplete`, `new_read_incomplete` | that file could only be partly read, or it holds more content outside the model than the reader's work limit allows (see [Files and models](#files-and-models)) |
| `old_results_not_read`, `new_results_not_read` | that side is a `Model` read with `results=False`: its result tables have columns and no values, so their cells were not compared. Only the columns and the row counts were. Two such models are never identical; read with results, or give `compare` the files |

`limits_hit` is empty exactly when `complete` is true. A later version may add a value.

The notes (`Diff.notes`) are [findings](findings.md) of step `diff`:
`diff.ambiguous-match`, `diff.limit` and `diff.timeout`.

## The command

```text
pydiggs diff OLD NEW [--format text|json] [-o FILE] [--match auto|gml-id|natural]
                     [--rel-tol R] [--abs-tol A] [--max-changes N]
                     [--time-budget SECONDS] [--max-bytes N] [--verbose]
```

| option | meaning |
|---|---|
| `OLD`, `NEW` | the earlier and the later DIGGS file |
| `--format text\|json` | the report: text (the default), or a [diggs-diff](#the-json-document) document |
| `-o FILE`, `--output FILE` | write the report to `FILE` instead of stdout. An existing file is replaced; a symlink, or one of the two inputs, is refused |
| `--match auto\|gml-id\|natural` | how records are paired (see [Matching](#matching)) |
| `--rel-tol R`, `--abs-tol A` | the number tolerance (see [Tolerances](#tolerances)); `R` is less than 1 |
| `--max-changes N` | list at most `N` changes (default 10,000) |
| `--time-budget SECONDS` | stop after this long (default: no limit) |
| `--max-bytes N` | refuse a file larger than `N` bytes (default 100 MiB) |
| `--verbose` | also list what is not a change: the values that are only written differently, and the records whose `gml:id` changed. Both are always counted |

**Exit codes**, the same as `diff(1)` and with the same three-way split as
`pydiggs validate`:

| code | meaning |
|---|---|
| **0** | the two files are identical, and the comparison finished. Numbers written differently and records whose `gml:id` changed are not differences |
| **1** | the files differ, **or** the comparison is incomplete |
| **2** | a usage error, a file that cannot be read or is not a DIGGS file, or a report that cannot be written |

What 0 means depends on what was compared. The command compares files, and for two
files 0 means that they describe the same data as far as pyDIGGS reads XML content:
every element, attribute and text of every record, and the attributes of the root
element and the text between its members, were compared, with these deliberate
exclusions: ids (`gml:id`; a reference is compared by its target), the order of
elements where the model does not keep an order, how a number is written, how the
values of a result set are separated and a column's type spelled when every value
reads the same, white space, comments, namespace prefixes and `schemaLocation`. In
Python, with two `Model`s, an identical result means the two models are equal, no more
(see [Files and models](#files-and-models)).

So a script can rely on 0 meaning "the same data":

```bash
if pydiggs diff previous.xml current.xml -o changes.txt; then
    echo "no change in the data"
fi
```

Text from the files is data, not formatting: control characters and
direction-override characters in a name or value are printed spelled out (`\x1b`,
`‮`), so a file cannot rewrite what the terminal shows.

The report is never lost to an encoding. `-o FILE` is always UTF-8. On stdout the report
is written in the encoding of the stream, and a character that encoding cannot write (a
redirected stdout on Windows is cp1252; `φ` is not in it) is written as an escape: a
backslash escape in the text report (`\u03c6`), and JSON's own `\uXXXX` in the JSON
report, which is then plain ASCII and the same document. A report that cannot be written
at all is exit 2 with one line on stderr.

## The JSON document

`pydiggs diff --format json` and `Diff.to_json()` write a **diggs-diff 1.0** document.
Keys are sorted and the same comparison always gives the same bytes.

```json
{
  "$schema": "https://xinp-hub.github.io/pydiggs/schemas/diggs-diff-1.0.schema.json",
  "format": "diggs-diff",
  "schema_version": "1.0",
  "tool": {"name": "pydiggs", "version": "1.13.0"},
  "identical": false,
  "complete": true,
  "limits_hit": [],
  "old": {"name": "site-rev1.xml", "diggs_version": "3.0.0", "size": 14278, "sha256": "9923e9ac…"},
  "new": {"name": "site-rev2.xml", "diggs_version": "3.0.0", "size": 14280, "sha256": "051980ed…"},
  "options": {"match": "auto", "rel_tol": 1e-09, "abs_tol": 0.0, "max_changes": 10000, "time_budget": null},
  "summary": {
    "features": {"added": 0, "removed": 0, "changed": 1, "unchanged": 0}
  },
  "value_changes": 2,
  "reformatted": 0,
  "renamed": 0,
  "changes": [
    {
      "op": "changed",
      "collection": "features",
      "element": "GP_ArealSurvey",
      "name": "Aeromag Survey Area",
      "old_key": "GP_ArealSurvey_bbg_yyp_r3c",
      "new_key": "GP_ArealSurvey_bbg_yyp_r3c",
      "matched_by": "gml_id",
      "truncated": false,
      "values": [
        {
          "op": "changed",
          "path": "params.totalSurveyArea",
          "old": "1 km2",
          "new": "1.2 km2",
          "at": "/Diggs[1]/samplingFeature[1]/GP_ArealSurvey[1]",
          "truncated": false
        }
      ]
    }
  ],
  "notes": []
}
```

| field | meaning |
|---|---|
| `identical` | true only when nothing differs and the comparison finished. A number written differently and a record's own `gml:id` are not differences. Between two files this covers the content a model does not keep; between two models it says the models are equal |
| `complete`, `limits_hit` | whether the comparison finished and looked at everything, and if not, why (see [Limits](#limits-and-incomplete-comparisons)). `limits_hit` is empty exactly when `complete` is true |
| `old`, `new` | each file's name, DIGGS version, size and sha256 |
| `options` | the matching mode, tolerances and limits that were used |
| `summary` | per collection, how many records were `added`, `removed`, `changed` and `unchanged` |
| `value_changes`, `reformatted` | how many values differ, each edit counted once, and how many are only written differently (a number, a result set's separators, a column's declared type: see [What does not count as a change](#what-does-not-count-as-a-change)) |
| `renamed` | how many records were paired by natural key and have a different `gml:id` in the two files. Counted whether listed or not |
| `changes[]` | one entry per record added, removed or changed, ordered by collection, then key. `op` is `added`, `removed` or `changed`. On request, the records that did not change are listed too: `renamed` (its `gml:id` differs) and `reformatted` (it only has numbers written differently). `old_key` and `new_key` are the record's keys in the two files, its `gml:id`s when it has them (null on the side that lacks the record); `matched_by` is `gml_id` or `natural` |
| `changes[].values[]` | each value that differs: its `op` (`added`, `removed`, `changed`, or `reformatted` and `renamed` when those are listed), its `path` in the record (`gml_id` is the record's own id; a path that starts with `xml.` is content of the file that the model does not keep), the `old` and `new` values (a quantity as text with its unit, null on the side that lacks it), and `at`, the XPath of the record in the new file. `path` and `at` are at most 1,000 characters |
| `notes[]` | findings of step `diff`, in the [diggs-findings](findings.md) form |

`Diff.from_dict()` reads a document back into a `Diff`. It refuses, with `ValueError`,
a document the report could not be made from or one that contradicts itself: a summary
entry without its four counts, a count that is not an integer of 0 or more, an `op` or
`matched_by` the format does not define, a `limits_hit` that is not text or does not
agree with `complete`, and `identical` set to true for an incomplete comparison or one
that counts or lists a difference. The JSON Schema says the same.

diggs-diff 1.0 is frozen, and versioned like the other pyDIGGS documents: a consumer of
1.0 accepts any 1.x document, a new optional field or a new value of an open
vocabulary (a collection, a value of `limits_hit`) is a minor version, and anything
else is a new major. `Diff.from_dict()` also reads the **1.0-beta.1** documents
pyDIGGS 1.12 wrote; their schema stays published at
[`schemas/preview/diggs-diff-1.0-beta.1.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-diff-1.0-beta.1.schema.json),
the URL their `$schema` names. A 1.0 document has the fields of a beta one; besides
`$schema` and `schema_version`, what differs is what pyDIGGS 1.13 compares and lists
(the root element and the text between members, each edit once, and more kinds of
*reformatted*).

## In an application

[`pydiggs.capabilities()`](capabilities.md) lists the comparison as the operation
`diff`, with its options. It runs in the browser under [Pyodide](browser.md) as it does
in CPython. From pyDIGGS 1.13 it is offered on the other surfaces too, each answering
with the same diggs-diff 1.0 document `compare(...).to_json()` gives, each file under its
basename:

| surface | how |
|---|---|
| [HTTP API](http-api.md#post-v1diff) | `POST /v1/diff`, a multipart body with the parts `old` and `new` |
| [Stdio sidecar](sidecar.md#diff) | method `diff`, protocol 1.3 (`old_base64` or `old_path`, `new_base64` or `new_path`) |
| [`@pydiggs/client`](javascript.md) | `diff(old, new, options)` over HTTP (`PydiggsHttpClient`) and in the browser or Node engines |

Each takes the options above except `max_bytes` and `time_budget`, which are the
server's or the sidecar's own limits: each file is held to the per-file limit
(`max_body_bytes`, or `--max-bytes` and `--max-path-bytes`), and one time limit covers
reading both files and comparing them. A file that is not a DIGGS document is an
`invalid-document` error naming the file (`old` or `new`), where the CLI exits 2. The
MCP server does not offer the comparison yet.
